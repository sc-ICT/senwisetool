import os
import copy
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc, String, delete, update
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.models.resource.resource import Resource, ResourceStatus
from app.models.resource.resource_version import ResourceVersion, ResourceVersionStatus
from app.models.resource.resource_item import ResourceItem
from app.models.organization.organization import Organization
from app.models.organization.member import OrganizationMember
from app.models.organization.role import OrganizationRole
from app.models.plugin.plugin import Plugin, OrgPluginStatus
from app.models.user.user import User, UserRole
from app.schemas.resource.resource import (
    ResourceCreate,
    ResourceUpdate,
    ResourceVersionCloneRequest,
    ResourceVersionUpdate,
    ResourceItemCreate,
    ResourceItemUpdate,
    ResourceBulkImportPayload,
)
from app.services.organization.organization_service import organization_service
from app.services.storage.storage_service import storage_service
from app.services.resource.schema_engine import (
    parse_schema_from_bytes,
    generate_schema_excel_template,
    reconcile_schema_coherence,
    generate_resource_data_template,
    parse_data_file,
    validate_and_reconcile_data_rows,
    generate_resource_data_export,
    bundle_export_zip,
    generate_resource_schema_export,
    is_media_field,
    ALL_RESOURCE_TYPES,
)

logger = logging.getLogger(__name__)


class ResourceService:

    async def _resolve_user_org_and_permissions(
        self,
        db: AsyncSession,
        user: User,
        action: str,  # "view", "create_edit", "delete"
        requested_org_id: Optional[int] = None
    ) -> Tuple[Optional[Organization], bool]:
        """
        Résout souverainement l'organisation rattachée et vérifie les permissions RBAC.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"

        if is_super_admin and requested_org_id:
            org_res = await db.execute(select(Organization).where(Organization.id == requested_org_id))
            org = org_res.scalars().first()
            if not org:
                raise HTTPException(status_code=404, detail="Organisation introuvable.")
            return org, True

        if is_super_admin:
            return None, True

        org = await organization_service.get_user_organization(db, user)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous devez être rattaché à une organisation active pour accéder aux ressources."
            )

        is_owner = org.owner_id == user.id or (org.contact_email and org.contact_email.lower() == user.email.lower())
        is_org_admin = user.role == UserRole.ADMIN_ORG or str(user.role).upper() == "ADMIN_ORG"

        if is_owner or is_org_admin:
            return org, True

        # Vérification par rôle dans l'organisation
        member_res = await db.execute(
            select(OrganizationMember)
            .options(selectinload(OrganizationMember.role_rel))
            .where(
                OrganizationMember.user_id == user.id,
                OrganizationMember.organization_id == org.id,
                OrganizationMember.is_active == True
            )
        )
        member = member_res.scalars().first()
        if not member or not member.role_rel:
            if action == "view":
                return org, True
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous n'avez pas les droits nécessaires sur les ressources de cette organisation."
            )

        role: OrganizationRole = member.role_rel
        has_permission = False
        perms = role.permissions or {}
        resources_perm = perms.get("resources", "none")

        if resources_perm in ["all", "manage"]:
            has_permission = True
        elif action == "view" and resources_perm in ["view", "all", "manage", "read_only"]:
            has_permission = True
        elif action in ["create_edit", "delete"] and resources_perm in ["all", "manage"]:
            has_permission = True

        if not has_permission and action != "view":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permissions insuffisantes pour modifier ou créer des ressources."
            )

        return org, True

    async def _resolve_resource_governance(
        self,
        db: AsyncSession,
        resource: Resource,
        user: User
    ) -> Dict[str, Any]:
        """
        Résout souverainement les permissions appliquées à une ressource en respectant
        la préséance hiérarchique absolue :
        Option du Module (spécifique à la ressource) > Option de la Fonctionnalité (globale) > Défaut.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        if is_super_admin:
            return {
                "can_create_resource": True,
                "can_edit_schema": True,
                "can_add_fields": True,
                "can_delete_resource": True,
                "can_add_data": True,
                "can_update_data": True,
                "can_delete_data": True,
                "can_export": True,
                "can_import": True,
                "immutable_fields": [],
                "source": "super_admin",
                "reason": None,
                "campaign_id": (resource.settings or {}).get("campaign_id"),
                "campaign_code": (resource.settings or {}).get("campaign_code"),
            }

        # Valeurs initiales
        source = "default"
        can_edit_schema = not resource.is_immutable
        can_add_fields = not resource.is_immutable
        can_delete_resource = not resource.is_immutable
        can_add_data = True
        can_update_data = True
        can_delete_data = True
        can_export = True
        can_import = True
        immutable_fields = list(resource.immutable_fields or [])

        # Rechercher le plugin rattaché
        if resource.organization_id:
            installed_ops = []

            # 1. ÉTAPE A : PRIORITÉ MODULE (Ressource spécifique)
            mod_cfg = None
            for op in installed_ops:
                op_modules = (op.local_settings or {}).get("modules") or {}
                mod_cfg = op_modules.get(resource.code) or op_modules.get(str(resource.id)) or op_modules.get(resource.id)
                if mod_cfg:
                    break

            if not mod_cfg:
                mod_cfg = resource.settings or {}

            mod_opts = mod_cfg.get("options") or mod_cfg.get("parameters") or mod_cfg
            mod_perms = mod_cfg.get("permissions") or {}

            has_module_override = False

            # Schéma & Champs
            if "allow_schema_modification" in mod_opts:
                can_edit_schema = bool(mod_opts["allow_schema_modification"])
                has_module_override = True
            elif "allow_schema_update" in mod_opts:
                can_edit_schema = bool(mod_opts["allow_schema_update"])
                has_module_override = True
            elif "can_edit_schema" in mod_perms:
                can_edit_schema = bool(mod_perms["can_edit_schema"])
                has_module_override = True

            if "allow_field_addition" in mod_opts:
                can_add_fields = bool(mod_opts["allow_field_addition"])
                has_module_override = True
            elif "can_add_fields" in mod_perms:
                can_add_fields = bool(mod_perms["can_add_fields"])
                has_module_override = True

            if "allow_resource_deletion" in mod_opts:
                can_delete_resource = bool(mod_opts["allow_resource_deletion"])
                has_module_override = True
            elif "can_delete_resource" in mod_perms:
                can_delete_resource = bool(mod_perms["can_delete_resource"])
                has_module_override = True

            # Données / Lignes
            if "allow_data_addition" in mod_opts:
                can_add_data = bool(mod_opts["allow_data_addition"])
                has_module_override = True
            elif "allow_creation" in mod_opts:
                can_add_data = bool(mod_opts["allow_creation"])
                has_module_override = True
            elif "can_add_data" in mod_perms:
                can_add_data = bool(mod_perms["can_add_data"])
                has_module_override = True

            if "allow_data_update" in mod_opts:
                can_update_data = bool(mod_opts["allow_data_update"])
                has_module_override = True
            elif "can_update_data" in mod_perms:
                can_update_data = bool(mod_perms["can_update_data"])
                has_module_override = True

            if "allow_data_deletion" in mod_opts:
                can_delete_data = bool(mod_opts["allow_data_deletion"])
                has_module_override = True
            elif "can_delete_data" in mod_perms:
                can_delete_data = bool(mod_perms["can_delete_data"])
                has_module_override = True

            if "allow_export" in mod_opts:
                can_export = bool(mod_opts["allow_export"])
                has_module_override = True
            elif "can_export" in mod_perms:
                can_export = bool(mod_perms["can_export"])
                has_module_override = True

            if "allow_import" in mod_opts:
                can_import = bool(mod_opts["allow_import"])
                has_module_override = True
            elif "can_import" in mod_perms:
                can_import = bool(mod_perms["can_import"])
                has_module_override = True

            if "immutable_fields" in mod_opts and isinstance(mod_opts["immutable_fields"], list):
                for f_name in mod_opts["immutable_fields"]:
                    if f_name not in immutable_fields:
                        immutable_fields.append(f_name)

            if has_module_override:
                source = "module"

            # 2. ÉTAPE B : SI NON PRÉCISÉ SUR LE MODULE, HÉRITER DE LA FONCTIONNALITÉ DU PLUGIN
            if not has_module_override:
                for op in installed_ops:
                    feat_res = (op.local_settings or {}).get("features", {}).get("resources") or (op.local_settings or {}).get("features", {}).get("dynamic_resources")
                    if feat_res:
                        source = "feature"
                        f_opts = feat_res.get("options") or feat_res.get("parameters") or {}
                        f_perms = feat_res.get("permissions") or {}

                        if "allow_schema_modification" in f_opts:
                            can_edit_schema = bool(f_opts["allow_schema_modification"])
                        if "allow_field_addition" in f_opts:
                            can_add_fields = bool(f_opts["allow_field_addition"])
                        if "allow_resource_deletion" in f_opts:
                            can_delete_resource = bool(f_opts["allow_resource_deletion"])

                        if "allow_data_addition" in f_opts:
                            can_add_data = bool(f_opts["allow_data_addition"])
                        elif "allow_creation" in f_opts:
                            can_add_data = bool(f_opts["allow_creation"])

                        if "allow_data_update" in f_opts:
                            can_update_data = bool(f_opts["allow_data_update"])
                        if "allow_data_deletion" in f_opts:
                            can_delete_data = bool(f_opts["allow_data_deletion"])
                        if "allow_export" in f_opts:
                            can_export = bool(f_opts["allow_export"])
                        if "allow_import" in f_opts:
                            can_import = bool(f_opts["allow_import"])
                        break

        # Immuabilité structurelle
        if resource.is_immutable:
            for def_locked in ("code", "version"):
                if def_locked not in immutable_fields:
                    immutable_fields.append(def_locked)

        reason = None
        if not can_edit_schema:
            reason = f"Structure verrouillée par la gouvernance du {source}."
        elif not can_update_data:
            reason = f"Modification des données interdite par la gouvernance du {source}."

        camp_id = (resource.settings or {}).get("campaign_id")
        camp_code = (resource.settings or {}).get("campaign_code")

        return {
            "can_create_resource": True,
            "can_edit_schema": can_edit_schema,
            "can_add_fields": can_add_fields,
            "can_delete_resource": can_delete_resource,
            "can_add_data": can_add_data,
            "can_update_data": can_update_data,
            "can_delete_data": can_delete_data,
            "can_export": can_export,
            "can_import": can_import,
            "immutable_fields": immutable_fields,
            "source": source,
            "reason": reason,
            "campaign_id": camp_id,
            "campaign_code": camp_code,
        }

    async def get_resource_governance(
        self,
        db: AsyncSession,
        resource_id: int,
        user: User,
    ) -> Dict[str, Any]:
        """
        Expose la gouvernance résolue d'une ressource (priorité module > feature > défaut).
        """
        resource = await self.get_resource_by_id(db, resource_id, user)
        return await self._resolve_resource_governance(db, resource, user)

    async def list_resources(
        self,
        db: AsyncSession,
        user: User,
        status_filter: Optional[str] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
        org_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Liste les ressources accessibles selon l'utilisateur et l'organisation.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        org, _ = await self._resolve_user_org_and_permissions(db, user, action="view", requested_org_id=org_id)
        target_org_id = org.id if org else None

        query = (
            select(Resource)
            .options(selectinload(Resource.versions), selectinload(Resource.items))
            .order_by(desc(Resource.created_at))
        )
        count_stmt = select(func.count(Resource.id))
        active_stmt = select(func.count(Resource.id)).where(Resource.status == ResourceStatus.ACTIVE.value)
        draft_stmt = select(func.count(Resource.id)).where(Resource.status == ResourceStatus.DRAFT.value)
        archived_stmt = select(func.count(Resource.id)).where(Resource.status == ResourceStatus.ARCHIVED.value)

        if is_super_admin and not target_org_id:
            # Super Admin sur son espace Plateforme : UNIQUEMENT les ressources maîtres de la plateforme
            # Aucune ressource clonée ou créée par une organisation cliente ne doit apparaître !
            org_condition = Resource.organization_id.is_(None)
            query = query.where(org_condition)
            count_stmt = count_stmt.where(org_condition)
            active_stmt = active_stmt.where(org_condition)
            draft_stmt = draft_stmt.where(org_condition)
            archived_stmt = archived_stmt.where(org_condition)
        elif target_org_id:
            # Organisation cliente (ou Super Admin ciblant explicitement target_org_id):
            # 1. Ses propres ressources (créées ou clonées) : Resource.organization_id == target_org_id
            # 2. Ressources maîtres de plateforme (Resource.organization_id IS NULL) SEULEMENT si partagées (is_shared == True)
            #    ET n'ayant pas été clonées localement pour cette organisation (aucun doublon !)
            # 3. INTERDICTION STRICTE : aucune ressource d'une autre organisation cliente ne peut fuiter !
            cloned_subquery = select(Resource.parent_template_id).where(
                Resource.organization_id == target_org_id,
                Resource.parent_template_id.is_not(None)
            )

            installed_plugin_ids = []

            if installed_plugin_ids:
                org_condition = or_(
                    Resource.organization_id == target_org_id,
                    and_(
                        Resource.organization_id.is_(None),
                        Resource.is_shared == True,
                        Resource.plugin_id.in_(installed_plugin_ids),
                        Resource.id.notin_(cloned_subquery)
                    )
                )
            else:
                org_condition = (Resource.organization_id == target_org_id)

            query = query.where(org_condition)
            count_stmt = count_stmt.where(org_condition)
            active_stmt = active_stmt.where(org_condition)
            draft_stmt = draft_stmt.where(org_condition)
            archived_stmt = archived_stmt.where(org_condition)
        else:
            org_condition = and_(Resource.organization_id.is_(None), Resource.is_shared == True)
            query = query.where(org_condition)
            count_stmt = count_stmt.where(org_condition)
            active_stmt = active_stmt.where(org_condition)
            draft_stmt = draft_stmt.where(org_condition)
            archived_stmt = archived_stmt.where(org_condition)

        if status_filter and status_filter != "all":
            if status_filter in ("published", "active"):
                query = query.where(Resource.status.in_([ResourceStatus.ACTIVE.value, ResourceStatus.PUBLISHED.value]))
            else:
                query = query.where(Resource.status == status_filter)

        if category and category != "all":
            query = query.where(Resource.category == category)

        if search and search.strip():
            s = f"%{search.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(Resource.name).like(s),
                    func.lower(Resource.code).like(s),
                    func.lower(Resource.description).like(s),
                )
            )

        total_res = await db.execute(count_stmt)
        total = total_res.scalar() or 0

        active_res = await db.execute(active_stmt)
        draft_res = await db.execute(draft_stmt)
        archived_res = await db.execute(archived_stmt)

        counts = {
            "total": total,
            "active": active_res.scalar() or 0,
            "draft": draft_res.scalar() or 0,
            "archived": archived_res.scalar() or 0,
        }

        query = query.offset(skip).limit(limit)
        items_res = await db.execute(query)
        items = list(items_res.scalars().all())

        return {"items": items, "total": total, "counts": counts}

    async def get_resource_by_id(
        self,
        db: AsyncSession,
        resource_id: int,
        user: User,
    ) -> Resource:
        """
        Retourne une ressource par son identifiant avec validation de l'isolation tenant.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        res = await db.execute(
            select(Resource)
            .options(selectinload(Resource.versions), selectinload(Resource.items))
            .where(Resource.id == resource_id)
        )
        resource = res.scalars().first()
        if not resource:
            raise HTTPException(status_code=404, detail="Ressource introuvable.")

        if is_super_admin:
            return resource

        org, _ = await self._resolve_user_org_and_permissions(db, user, action="view")
        if not org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous devez être rattaché à une organisation active pour accéder aux ressources."
            )

        if resource.organization_id is not None:
            if resource.organization_id != org.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès interdit aux ressources d'une autre organisation."
                )
        else:
            if not resource.is_shared:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Cette ressource de plateforme n'est pas partagée."
                )

        return resource

    async def create_resource(
        self,
        db: AsyncSession,
        data: ResourceCreate,
        user: User,
        org_id: Optional[int] = None,
    ) -> Resource:
        """
        Crée une nouvelle ressource (Mode A ou Super Admin pour Mode B).
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        org, _ = await self._resolve_user_org_and_permissions(
            db, user, action="create_edit", requested_org_id=org_id
        )

        final_org_id = org.id if org else (org_id if is_super_admin else None)

        # Contrôle de gouvernance : vérification si la fonctionnalité interdit la création
        if not is_super_admin and final_org_id:
            installed_ops = []
            for op in installed_ops:
                feat_res = (op.local_settings or {}).get("features", {}).get("resources") or (op.local_settings or {}).get("features", {}).get("dynamic_resources")
                if feat_res:
                    f_opts = feat_res.get("options") or feat_res.get("parameters") or {}
                    f_perms = feat_res.get("permissions") or {}
                    if f_opts.get("allow_creation") is False or f_perms.get("can_create") is False or f_perms.get("can_create_resource") is False:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail="La création de nouvelles ressources est désactivée par la politique du plugin installé."
                        )

        slug = data.code.strip().lower()

        # Vérifier l'unicité du slug
        unique_q = select(Resource).where(
            Resource.organization_id == final_org_id,
            Resource.code == slug
        )
        existing = await db.execute(unique_q)
        if existing.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Une ressource avec le code '{slug}' existe déjà dans cet espace."
            )

        fields_data = [f.model_dump() if hasattr(f, "model_dump") else (f.dict() if hasattr(f, "dict") else f) for f in data.schema_fields]

        initial_version_number = (data.version or "1.0.0").strip()

        # Règle d'or : La ressource est TOUJOURS initialisée en DRAFT
        new_resource = Resource(
            organization_id=final_org_id,
            plugin_id=data.plugin_id,
            plugin_code=data.plugin_code,
            scope=data.scope or ("tenant" if final_org_id else "plugin_shared"),
            is_shared=bool(data.is_shared),
            is_immutable=bool(data.is_immutable),
            immutable_fields=data.immutable_fields or [],
            name=data.name.strip(),
            code=slug,
            category=data.category or "general",
            visibility=data.visibility or "public",
            authorized_organization_ids=data.authorized_organization_ids or [],
            cover_image_url=data.cover_image_url,
            description=data.description.strip() if data.description else None,
            icon=data.icon or "Database",
            status=ResourceStatus.DRAFT.value,
            version=initial_version_number,
            schema_history=[],
            schema_fields=fields_data,
            settings=data.settings or {},
            metrics={"item_count": 0},
            created_by_id=user.id,
        )

        db.add(new_resource)
        await db.flush()

        initial_version = ResourceVersion(
            resource_id=new_resource.id,
            version_number=initial_version_number,
            status=ResourceVersionStatus.DRAFT.value,
            description=data.description.strip() if data.description else None,
            changelog=data.changelog or "Version initiale créée.",
            schema_fields=fields_data,
            metrics={"item_count": 0},
            settings=data.settings or {},
            template_governance=data.template_governance or {},
            created_by_id=user.id,
        )
        db.add(initial_version)

        await db.commit()
        await db.refresh(new_resource)
        return await self.get_resource_by_id(db, new_resource.id, user)

    async def update_resource(
        self,
        db: AsyncSession,
        resource_id: int,
        data: ResourceUpdate,
        user: User,
    ) -> Resource:
        """
        Met à jour une ressource en respectant le versionnage et la cohérence absolue avec les données existantes.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        resource = await self.get_resource_by_id(db, resource_id, user)

        # Contrôle de gouvernance et d'immuabilité (Mode B ou Plugin)
        if not is_super_admin:
            gov = await self._resolve_resource_governance(db, resource, user)

            if data.schema_fields is not None:
                if not gov.get("can_edit_schema", True):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="La structure et le schéma de cette ressource sont scellés et ne peuvent pas être modifiés."
                    )

                incoming_fields_list = [
                    f.model_dump() if hasattr(f, "model_dump") else (f.dict() if hasattr(f, "dict") else f)
                    for f in data.schema_fields
                ]
                incoming_names = {f.get("name") for f in incoming_fields_list if f.get("name")}
                existing_map = {f.get("name"): f for f in (resource.schema_fields or []) if f.get("name")}
                existing_names = set(existing_map.keys())

                # Vérifier si l'ajout de nouveaux champs est interdit
                if not gov.get("can_add_fields", True):
                    added_names = incoming_names - existing_names
                    if added_names:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"L'ajout de nouveaux champs ({', '.join(added_names)}) est désactivé pour cette ressource."
                        )

                # Vérifier les champs immuables
                for imm_name in gov.get("immutable_fields", []):
                    if imm_name in existing_map:
                        if imm_name not in incoming_names:
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Le champ immuable '{imm_name}' ne peut pas être retiré du schéma."
                            )
                        # Contrôler que le type ne change pas
                        new_f = next((f for f in incoming_fields_list if f.get("name") == imm_name), None)
                        if new_f and new_f.get("type") != existing_map[imm_name].get("type"):
                            raise HTTPException(
                                status_code=status.HTTP_400_BAD_REQUEST,
                                detail=f"Le type du champ immuable '{imm_name}' ne peut pas être altéré."
                            )

            immutable_keys = resource.immutable_fields or []
            if "name" in immutable_keys and data.name is not None and data.name != resource.name:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Le nom certifié de cette ressource est verrouillé."
                )

        if data.name is not None:
            resource.name = data.name.strip()
        if data.description is not None:
            resource.description = data.description.strip()
        if data.category is not None:
            resource.category = data.category
        if data.visibility is not None:
            resource.visibility = data.visibility
        if data.authorized_organization_ids is not None:
            resource.authorized_organization_ids = data.authorized_organization_ids
        if data.cover_image_url is not None:
            resource.cover_image_url = data.cover_image_url
        if data.icon is not None:
            resource.icon = data.icon
        if data.is_lts is not None:
            resource.is_lts = data.is_lts

        # Activation ou changement de statut
        if data.status is not None and data.status != resource.status:
            if data.status == ResourceStatus.ACTIVE.value:
                # Validation obligatoire avant activation
                if not resource.schema_fields or len(resource.schema_fields) == 0:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail="Impossible d'activer une ressource sans aucun champ défini dans son schéma."
                    )
                resource.status = ResourceStatus.ACTIVE.value
                resource.version = resource.version or "1.0.0"
            else:
                resource.status = data.status

        # Mise à jour du schéma avec versionnage et cohérence absolue
        if data.schema_fields is not None:
            incoming_raw = [
                f.model_dump() if hasattr(f, "model_dump") else (f.dict() if hasattr(f, "dict") else f)
                for f in data.schema_fields
            ]

            # Récupération des enregistrements existants de la ressource
            items_res = await db.execute(
                select(ResourceItem).where(ResourceItem.resource_id == resource.id)
            )
            items_objs = list(items_res.scalars().all())
            existing_items = [{"id": it.id, "data": it.data} for it in items_objs]

            # Récupération des autres ressources pour analyse des dépendances (relations croisées)
            stmt_other = select(Resource).where(Resource.id != resource.id)
            other_records = (await db.execute(stmt_other)).scalars().all()
            other_resources = [
                {
                    "id": r.id,
                    "name": r.name,
                    "code": r.code,
                    "schema_fields": r.schema_fields or [],
                }
                for r in other_records
            ]

            report = reconcile_schema_coherence(
                existing_fields=resource.schema_fields or [],
                incoming_fields=incoming_raw,
                existing_items=existing_items,
                current_version=resource.version or "1.0.0",
                is_active=(resource.status == ResourceStatus.ACTIVE.value),
                other_resources=other_resources,
                current_resource_id=resource.id,
                current_resource_code=resource.code,
                current_resource_name=resource.name,
                unique_population_strategies=data.unique_population_strategies,
            )

            if not report["is_coherent"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "message": "Incohérence du schéma détectée avec les données existantes ou les ressources dépendantes.",
                        "errors": report["errors"],
                        "warnings": report["warnings"],
                    }
                )

            # 1. Purge des champs supprimés des enregistrements existants
            for del_f in report.get("deleted_fields", []):
                fname = del_f["name"]
                for item_obj in items_objs:
                    cur_data = dict(item_obj.data or {})
                    if fname in cur_data:
                        del cur_data[fname]
                        item_obj.data = cur_data
                        flag_modified(item_obj, "data")

            # 2. Remplissage des valeurs par défaut pour les nouveaux champs
            for added_f in report.get("added_fields", []):
                fname = added_f["name"]
                dval = added_f.get("default_value")
                if dval is not None and str(dval).strip() != "":
                    for item_obj in items_objs:
                        cur_data = dict(item_obj.data or {})
                        if fname not in cur_data or cur_data[fname] is None:
                            cur_data[fname] = dval
                            item_obj.data = cur_data
                            flag_modified(item_obj, "data")

            # 3. Application des stratégies de valorisation unique
            for fname, mapping in (report.get("unique_population_actions") or {}).items():
                for item_obj in items_objs:
                    if item_obj.id in mapping:
                        cur_data = dict(item_obj.data or {})
                        cur_data[fname] = mapping[item_obj.id]
                        item_obj.data = cur_data
                        flag_modified(item_obj, "data")

            # 4. Archivage de version si active et modifications
            has_changes = bool(report["added_fields"] or report["updated_fields"] or report.get("deleted_fields"))
            if resource.status == ResourceStatus.ACTIVE.value and has_changes:
                snapshot = {
                    "version": resource.version,
                    "archived_at": datetime.now(timezone.utc).isoformat(),
                    "schema_fields": resource.schema_fields,
                }
                resource.schema_history = (resource.schema_history or []) + [snapshot]
                resource.version = report["new_version"]

            resource.schema_fields = report["resulting_schema"]

        if data.settings is not None:
            resource.settings = data.settings
        if is_super_admin and data.is_immutable is not None:
            resource.is_immutable = data.is_immutable
        if is_super_admin and data.immutable_fields is not None:
            resource.immutable_fields = data.immutable_fields

        await db.commit()
        await db.refresh(resource)
        return resource

    async def reconcile_schema(
        self,
        db: AsyncSession,
        resource_id: int,
        incoming_fields: List[Dict[str, Any]],
        user: User,
        apply_changes: bool = False,
        unique_population_strategies: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Analyse et réconcilie un lot de champs entrants (ex: depuis Excel ou JSON)
        avec le schéma existant d'une ressource. Vérifie l'intégrité absolue avec les données existantes
        et les dépendances entre ressources.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)
        items_res = await db.execute(
            select(ResourceItem).where(ResourceItem.resource_id == resource.id)
        )
        items_objs = list(items_res.scalars().all())
        existing_items = [{"id": it.id, "data": it.data} for it in items_objs]

        # Récupération des autres ressources pour analyse des dépendances
        stmt_other = select(Resource).where(Resource.id != resource.id)
        other_records = (await db.execute(stmt_other)).scalars().all()
        other_resources = [
            {
                "id": r.id,
                "name": r.name,
                "code": r.code,
                "schema_fields": r.schema_fields or [],
            }
            for r in other_records
        ]

        report = reconcile_schema_coherence(
            existing_fields=resource.schema_fields or [],
            incoming_fields=incoming_fields,
            existing_items=existing_items,
            current_version=resource.version or "1.0.0",
            is_active=(resource.status == ResourceStatus.ACTIVE.value),
            other_resources=other_resources,
            current_resource_id=resource.id,
            current_resource_code=resource.code,
            current_resource_name=resource.name,
            unique_population_strategies=unique_population_strategies,
        )

        if apply_changes and report["is_coherent"]:
            is_active = (resource.status == ResourceStatus.ACTIVE.value)
            has_changes = bool(report["added_fields"] or report["updated_fields"] or report.get("deleted_fields"))

            if is_active and has_changes:
                # Archivage de la version précédente
                snapshot = {
                    "version": resource.version,
                    "archived_at": datetime.now(timezone.utc).isoformat(),
                    "schema_fields": resource.schema_fields,
                }
                resource.schema_history = (resource.schema_history or []) + [snapshot]
                resource.version = report["new_version"]

            # 1. Purge des champs supprimés des enregistrements existants
            for del_f in report.get("deleted_fields", []):
                fname = del_f["name"]
                for item_obj in items_objs:
                    cur_data = dict(item_obj.data or {})
                    if fname in cur_data:
                        del cur_data[fname]
                        item_obj.data = cur_data
                        flag_modified(item_obj, "data")

            # 2. Remplissage automatique des valeurs par défaut pour les champs obligatoires ajoutés
            for added_f in report.get("added_fields", []):
                fname = added_f["name"]
                dval = added_f.get("default_value")
                if dval is not None and str(dval).strip() != "":
                    for item_obj in items_objs:
                        cur_data = dict(item_obj.data or {})
                        if fname not in cur_data or cur_data[fname] is None:
                            cur_data[fname] = dval
                            item_obj.data = cur_data
                            flag_modified(item_obj, "data")

            # 3. Application des stratégies de valorisation unique
            for fname, mapping in (report.get("unique_population_actions") or {}).items():
                for item_obj in items_objs:
                    if item_obj.id in mapping:
                        cur_data = dict(item_obj.data or {})
                        cur_data[fname] = mapping[item_obj.id]
                        item_obj.data = cur_data
                        flag_modified(item_obj, "data")

            resource.schema_fields = report["resulting_schema"]

            await db.commit()
            await db.refresh(resource)

        return report

    def parse_schema_file(self, file_bytes: bytes, filename: str) -> List[Dict[str, Any]]:
        """Parse un fichier Excel, CSV ou JSON et retourne la liste des champs normalisés."""
        return parse_schema_from_bytes(file_bytes, filename)

    def get_schema_excel_template(self) -> bytes:
        """Génère le gabarit Excel officiel pour l'import de schéma."""
        return generate_schema_excel_template()

    async def delete_resource(
        self,
        db: AsyncSession,
        resource_id: int,
        user: User,
    ) -> Dict[str, Any]:
        """
        Supprime une ressource si elle est au statut draft ou sans données, sinon l'archive.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)

        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        if not is_super_admin:
            gov = await self._resolve_resource_governance(db, resource, user)
            if not gov.get("can_delete_resource", True):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="La suppression de cette ressource est interdite par la politique de gouvernance."
                )

        # Vérifier si la ressource contient des enregistrements
        count_res = await db.execute(
            select(func.count(ResourceItem.id)).where(ResourceItem.resource_id == resource.id)
        )
        item_count = count_res.scalar() or 0

        if item_count > 0 and resource.status != ResourceStatus.DRAFT.value:
            # Archivage conforme pour préserver la traçabilité
            resource.status = ResourceStatus.ARCHIVED.value
            await db.commit()
            return {"deleted": False, "archived": True, "message": "Ressource archivée avec succès."}

        # Suppression SQL directe sans cascades ORM pour prévenir tout conflit MissingGreenlet
        await db.execute(delete(ResourceItem).where(ResourceItem.resource_id == resource.id))
        await db.execute(delete(ResourceVersion).where(ResourceVersion.resource_id == resource.id))
        try:
            db.expunge(resource)
        except Exception:
            pass
        await db.execute(delete(Resource).where(Resource.id == resource.id))
        await db.commit()
        return {"deleted": True, "archived": False, "message": "Ressource supprimée avec succès."}

    # =========================================================================
    # GESTION DES VERSIONS DE LA RESSOURCE (VERSIONING PATTERN)
    # =========================================================================

    @classmethod
    def _populate_latest_version_attributes(cls, resource: Resource) -> None:
        if not resource.versions:
            return
        latest = resource.versions[0]
        resource.version = latest.version_number
        resource.status = latest.status
        if latest.description:
            resource.description = latest.description
        if latest.schema_fields:
            resource.schema_fields = latest.schema_fields
        if latest.settings:
            resource.settings = latest.settings
        if latest.metrics:
            resource.metrics = latest.metrics

    async def clone_resource_version(
        self,
        db: AsyncSession,
        resource_id: int,
        req: ResourceVersionCloneRequest,
        user: User,
    ) -> Resource:
        resource = await self.get_resource_by_id(db, resource_id, user)
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"

        # Règle Métier 1 : Interdiction d'avoir plusieurs brouillons simultanés
        draft_chk = await db.execute(
            select(ResourceVersion).where(
                ResourceVersion.resource_id == resource_id,
                ResourceVersion.status == ResourceVersionStatus.DRAFT.value
            )
        )
        existing_draft = draft_chk.scalars().first()
        if existing_draft:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sécurité de versionnage : Une version en brouillon existe déjà pour cette ressource (« v{existing_draft.version_number} »). Vous devez obligatoirement publier ou supprimer ce brouillon avant de pouvoir cloner ou créer une nouvelle version."
            )

        new_v_num = req.new_version_number.strip()
        chk_ver = await db.execute(
            select(ResourceVersion).where(
                ResourceVersion.resource_id == resource_id,
                ResourceVersion.version_number == new_v_num
            )
        )
        if chk_ver.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La version '{new_v_num}' existe déjà pour cette ressource."
            )

        # Déterminer la version source
        source_version: Optional[ResourceVersion] = None
        if req.source_version_number:
            src_res = await db.execute(
                select(ResourceVersion).where(
                    ResourceVersion.resource_id == resource_id,
                    ResourceVersion.version_number == req.source_version_number.strip()
                )
            )
            source_version = src_res.scalars().first()
            if not source_version:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Version source '{req.source_version_number}' introuvable."
                )
        else:
            if resource.versions and len(resource.versions) > 0:
                source_version = resource.versions[0]

        if source_version and source_version.status == ResourceVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action non autorisée : Impossible de cloner la version source « v{source_version.version_number} » car elle est encore en brouillon. Vous devez d'abord publier cette version pour pouvoir la cloner."
            )

        new_version = ResourceVersion(
            resource_id=resource.id,
            version_number=new_v_num,
            status=ResourceVersionStatus.DRAFT.value,
            description=source_version.description if source_version else (resource.description or ""),
            changelog=req.changelog or f"Cloné depuis la version {source_version.version_number if source_version else 'initiale'}",
            schema_fields=copy.deepcopy(source_version.schema_fields or []) if source_version else copy.deepcopy(resource.schema_fields or []),
            settings=copy.deepcopy(source_version.settings or {}) if source_version else copy.deepcopy(resource.settings or {}),
            template_governance=copy.deepcopy(source_version.template_governance or {}) if source_version else {},
            metrics={"item_count": 0},
            created_by_id=user.id,
        )
        db.add(new_version)
        await db.flush()

        # Dupliquer les enregistrements de la version source vers la nouvelle version
        cloned_count = 0
        if source_version:
            src_items_stmt = select(ResourceItem).where(
                ResourceItem.resource_id == resource.id,
                ResourceItem.resource_version_id == source_version.id
            )
            src_items = (await db.execute(src_items_stmt)).scalars().all()
            for s_it in src_items:
                cloned_it = ResourceItem(
                    resource_id=resource.id,
                    resource_version_id=new_version.id,
                    organization_id=s_it.organization_id,
                    data=copy.deepcopy(s_it.data),
                    created_by_id=user.id,
                )
                db.add(cloned_it)
            cloned_count = len(src_items)

        new_version.metrics = {"item_count": cloned_count}
        await db.commit()
        await db.refresh(new_version)
        await db.refresh(resource)
        self._populate_latest_version_attributes(resource)
        return await self.get_resource_by_id(db, resource.id, user)

    async def publish_resource_version(
        self,
        db: AsyncSession,
        resource_id: int,
        version_id: int,
        user: User,
    ) -> Resource:
        resource = await self.get_resource_by_id(db, resource_id, user)
        ver_stmt = select(ResourceVersion).where(
            ResourceVersion.id == version_id,
            ResourceVersion.resource_id == resource_id
        )
        ver_res = await db.execute(ver_stmt)
        version_obj = ver_res.scalars().first()
        if not version_obj:
            raise HTTPException(status_code=404, detail="Version introuvable pour cette ressource.")

        # Si la version est déjà publiée ou active, rien à faire
        if version_obj.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active"):
            return await self.get_resource_by_id(db, resource.id, user)

        version_obj.status = ResourceVersionStatus.PUBLISHED.value
        version_obj.updated_at = datetime.now(timezone.utc)
        resource.status = ResourceStatus.ACTIVE.value

        await db.commit()
        await db.refresh(version_obj)
        await db.refresh(resource)
        self._populate_latest_version_attributes(resource)
        return await self.get_resource_by_id(db, resource.id, user)

    async def update_resource_version(
        self,
        db: AsyncSession,
        resource_id: int,
        version_id: int,
        data: ResourceVersionUpdate,
        user: User,
    ) -> Resource:
        resource = await self.get_resource_by_id(db, resource_id, user)
        ver_stmt = select(ResourceVersion).where(
            ResourceVersion.id == version_id,
            ResourceVersion.resource_id == resource_id
        )
        ver_res = await db.execute(ver_stmt)
        version_obj = ver_res.scalars().first()
        if not version_obj:
            raise HTTPException(status_code=404, detail="Version introuvable pour cette ressource.")

        # Contrôle strict d'immuabilité : pour une version publiée, seul le schéma des champs est scellé.
        # Les métadonnées, paramètres et gouvernance des permissions (template_governance) peuvent être ajustés.
        is_sealed = version_obj.status in (ResourceVersionStatus.PUBLISHED.value, ResourceVersionStatus.ACTIVE.value)
        update_dict = data.model_dump(exclude_unset=True)

        if is_sealed and "schema_fields" in update_dict and update_dict["schema_fields"] is not None:
            current_fields = version_obj.schema_fields or []
            if update_dict["schema_fields"] != current_fields:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Impossible de modifier le schéma des champs de la version v{version_obj.version_number} : cette version est publiée et scellée pour garantir la non-régression. Créez ou clonez une nouvelle version en brouillon pour faire évoluer le schéma."
                )
        if "description" in update_dict and update_dict["description"] is not None:
            version_obj.description = update_dict["description"]
        if "changelog" in update_dict and update_dict["changelog"] is not None:
            version_obj.changelog = update_dict["changelog"]
        if "schema_fields" in update_dict and update_dict["schema_fields"] is not None:
            version_obj.schema_fields = list(update_dict["schema_fields"])
            flag_modified(version_obj, "schema_fields")
        if "settings" in update_dict and update_dict["settings"] is not None:
            version_obj.settings = dict(update_dict["settings"])
            flag_modified(version_obj, "settings")
        if "template_governance" in update_dict and update_dict["template_governance"] is not None:
            version_obj.template_governance = dict(update_dict["template_governance"])
            flag_modified(version_obj, "template_governance")

        version_obj.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(version_obj)
        await db.refresh(resource)
        self._populate_latest_version_attributes(resource)
        return await self.get_resource_by_id(db, resource.id, user)

    async def delete_resource_version(
        self,
        db: AsyncSession,
        resource_id: int,
        version_id: int,
        user: User,
    ) -> Dict[str, Any]:
        resource = await self.get_resource_by_id(db, resource_id, user)
        ver_stmt = select(ResourceVersion).where(
            ResourceVersion.id == version_id,
            ResourceVersion.resource_id == resource_id
        )
        ver_res = await db.execute(ver_stmt)
        version_obj = ver_res.scalars().first()
        if not version_obj:
            raise HTTPException(status_code=404, detail="Version introuvable pour cette ressource.")

        version_num = str(version_obj.version_number)
        resource_name = str(resource.name)

        # Règle de sécurité stricte : Une version publiée ne peut jamais être supprimée.
        if version_obj.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sécurité de versionnage : Impossible de supprimer la version v{version_num} car elle est publiée. Une version publiée est scellée et immuable pour préserver l'historique et l'intégrité des données. Seules les versions en brouillon peuvent être supprimées."
            )

        versions_count_res = await db.execute(
            select(func.count(ResourceVersion.id)).where(ResourceVersion.resource_id == resource_id)
        )
        v_count = versions_count_res.scalar() or 0

        if v_count <= 1:
            # Détacher tous les objets ORM avant les opérations SQL directes pour prévenir tout MissingGreenlet
            db.expunge_all()
            await db.execute(delete(ResourceItem).where(ResourceItem.resource_id == resource_id))
            await db.execute(delete(ResourceVersion).where(ResourceVersion.resource_id == resource_id))
            await db.execute(delete(Resource).where(Resource.id == resource_id))
            await db.commit()
            return {
                "action": "resource_deleted",
                "message": f"Dernière version supprimée. La ressource « {resource_name} » a été entièrement supprimée car c'était son unique version.",
                "resource_id": resource_id,
            }
        else:
            # 1. Déterminer la version fallback parmi les versions restantes
            remaining_vers_stmt = select(ResourceVersion).where(
                ResourceVersion.resource_id == resource_id,
                ResourceVersion.id != version_id
            ).order_by(ResourceVersion.id.desc())
            remaining_vers = (await db.execute(remaining_vers_stmt)).scalars().all()
            fallback_v = None
            if remaining_vers:
                fallback_v = next(
                    (v for v in remaining_vers if v.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active")),
                    remaining_vers[0]
                )

            # 2. Compter les items restants qui ne sont pas de cette version
            rem_count_res = await db.execute(
                select(func.count(ResourceItem.id)).where(
                    ResourceItem.resource_id == resource_id,
                    ResourceItem.resource_version_id != version_id
                )
            )
            remaining_items_count = rem_count_res.scalar() or 0

            # 3. Supprimer directement les items liés à cette version
            await db.execute(
                delete(ResourceItem).where(
                    ResourceItem.resource_id == resource_id,
                    ResourceItem.resource_version_id == version_id
                )
            )

            # 4. Supprimer la version spécifique
            await db.execute(
                delete(ResourceVersion).where(
                    ResourceVersion.id == version_id,
                    ResourceVersion.resource_id == resource_id
                )
            )

            # 5. Préparer les nouvelles valeurs maîtres de la ressource
            r_metrics = dict(resource.metrics or {})
            r_metrics["item_count"] = remaining_items_count

            update_values: Dict[str, Any] = {
                "metrics": r_metrics,
                "updated_at": datetime.now(timezone.utc),
            }
            if fallback_v:
                update_values["version"] = fallback_v.version_number
                update_values["schema_fields"] = fallback_v.schema_fields
                update_values["status"] = fallback_v.status

            # Détacher tous les objets ORM avant le commit pour éviter tout lazy load ou cascade de session
            db.expunge_all()

            # Mettre à jour la ressource maîtresse via SQL direct
            await db.execute(
                update(Resource).where(Resource.id == resource_id).values(**update_values)
            )
            await db.commit()

            return {
                "action": "version_deleted",
                "message": f"La version « {version_num} » ainsi que toutes ses données, son schéma et ses configurations ont été supprimés avec succès.",
                "version_id": version_id,
                "resource_id": resource_id,
            }

    # =========================================================================
    # GESTION DES DONNÉES / ITEMS DE LA RESSOURCE
    # =========================================================================

    async def list_items(
        self,
        db: AsyncSession,
        resource_id: int,
        user: User,
        version_id: Optional[int] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        Liste les enregistrements de données dynamiques d'une ressource (filtrés par version spécifique si demandée).
        """
        resource = await self.get_resource_by_id(db, resource_id, user)

        target_v_id = version_id
        if target_v_id is None:
            t_vers = resource.versions or []
            v_match = next((v for v in t_vers if v.version_number == resource.version), None)
            if not v_match and t_vers:
                v_match = next((v for v in t_vers if v.status in ("published", "active")), t_vers[0])
            if v_match:
                target_v_id = v_match.id

        query = select(ResourceItem).where(ResourceItem.resource_id == resource.id)
        count_stmt = select(func.count(ResourceItem.id)).where(ResourceItem.resource_id == resource.id)

        if target_v_id is not None:
            query = query.where(ResourceItem.resource_version_id == target_v_id)
            count_stmt = count_stmt.where(ResourceItem.resource_version_id == target_v_id)

        query = query.order_by(desc(ResourceItem.created_at))

        # Si recherche textuelle
        if search and search.strip():
            # Recherche dans le JSON textuellement
            s = f"%{search.strip().lower()}%"
            query = query.where(func.lower(func.cast(ResourceItem.data, String)).like(s))
            count_stmt = count_stmt.where(func.lower(func.cast(ResourceItem.data, String)).like(s))

        total_res = await db.execute(count_stmt)
        total = total_res.scalar() or 0

        items_res = await db.execute(query.offset(skip).limit(limit))
        items = list(items_res.scalars().all())

        return {"items": items, "total": total, "page": (skip // limit) + 1, "limit": limit}

    async def _validate_item_data(
        self,
        db: AsyncSession,
        resource: Resource,
        data: Dict[str, Any],
        exclude_item_id: Optional[int] = None,
        version_id: Optional[int] = None,
        schema_fields: Optional[List[Dict[str, Any]]] = None,
        existing_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Valide l'intégrité, les champs obligatoires et l'unicité des données d'un enregistrement pour sa version.
        Si la valeur d'un champ unique n'a pas été modifiée par rapport à existing_data, le contrôle anti-doublon est ignoré.
        """
        target_v_id = version_id
        if target_v_id is None:
            t_vers = resource.versions or []
            v_match = next((v for v in t_vers if v.version_number == resource.version), None)
            if not v_match and t_vers:
                v_match = next((v for v in t_vers if v.status in ("published", "active")), t_vers[0])
            if v_match:
                target_v_id = v_match.id

        fields = schema_fields if schema_fields is not None else (resource.schema_fields or [])
        for field in fields:
            fname = field.get("name")
            val = data.get(fname)

            # 1. Validation de l'obligation
            if field.get("required") and (val is None or val == ""):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Le champ obligatoire '{field.get('label') or fname}' est manquant."
                )

            # 2. Validation de l'unicité pour garantir l'authenticité dans la version
            if field.get("unique") and val is not None and val != "":
                # Si l'enregistrement en cours de modification possède déjà cette valeur inchangée,
                # aucune duplication n'a été introduite.
                if existing_data is not None and str(existing_data.get(fname, "")).strip() == str(val).strip():
                    continue

                unique_stmt = select(ResourceItem.id).where(
                    ResourceItem.resource_id == resource.id,
                    func.trim(func.json_extract_path_text(ResourceItem.data, fname)) == str(val).strip()
                )
                if target_v_id is not None:
                    unique_stmt = unique_stmt.where(ResourceItem.resource_version_id == target_v_id)
                else:
                    unique_stmt = unique_stmt.where(ResourceItem.resource_version_id.is_(None))
                if exclude_item_id is not None:
                    unique_stmt = unique_stmt.where(ResourceItem.id != int(exclude_item_id))
                exists_res = await db.execute(unique_stmt)
                if exists_res.scalars().first():
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"La valeur '{val}' pour le champ '{field.get('label') or fname}' existe déjà dans cette version. Ce champ exige une valeur unique pour assurer l'authenticité des données."
                    )

    async def _compute_auto_increment(
        self, db: AsyncSession, resource_id: int, field_name: str, version_id: Optional[int] = None
    ) -> int:
        """Calcule la valeur suivante pour un champ auto-incrémenté."""
        stmt = select(ResourceItem.data).where(ResourceItem.resource_id == resource_id)
        if version_id is not None:
            stmt = stmt.where(ResourceItem.resource_version_id == version_id)
        items_res = await db.execute(stmt)
        existing_numbers = []
        for (d,) in items_res.all():
            if isinstance(d, dict) and field_name in d:
                val = d[field_name]
                try:
                    existing_numbers.append(int(float(str(val))))
                except (ValueError, TypeError):
                    pass
        return (max(existing_numbers) if existing_numbers else 0) + 1

    async def create_item(
        self,
        db: AsyncSession,
        resource_id: int,
        payload: ResourceItemCreate,
        user: User,
        version_id: Optional[int] = None,
    ) -> ResourceItem:
        """
        Insère un nouvel enregistrement de données dynamiques avec auto-incrémentation
        et validation d'unicité et de schéma associés à la version spécifique.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)
        target_version_id = payload.resource_version_id or version_id
        target_version: Optional[ResourceVersion] = None

        if target_version_id:
            ver_res = await db.execute(
                select(ResourceVersion).where(
                    ResourceVersion.id == target_version_id,
                    ResourceVersion.resource_id == resource.id
                )
            )
            target_version = ver_res.scalars().first()
        elif resource.versions:
            target_version = resource.versions[0]
            target_version_id = target_version.id

        schema_fields = target_version.schema_fields if target_version and target_version.schema_fields else (resource.schema_fields or [])
        gov = (target_version.template_governance if target_version and target_version.template_governance else {}) or {}

        # Règle de sécurité stricte : Une version publiée est scellée en lecture seule.
        if target_version and target_version.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Sécurité de versionnage : La version v{target_version.version_number} est publiée et scellée en lecture seule. L'ajout de nouvelles entrées y est strictement interdit. Veuillez créer ou cloner une nouvelle version à partir de celle-ci pour effectuer des ajouts."
            )

        if gov.get("allow_add_records") is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="L'ajout de nouvelles données dans cette version de ressource est désactivé par la politique de permissions."
            )

        item_data = dict(payload.data)
        for field in schema_fields:
            if field.get("auto_increment"):
                fname = field.get("name")
                if item_data.get(fname) is None or str(item_data.get(fname)).strip() == "":
                    item_data[fname] = await self._compute_auto_increment(db, resource.id, fname, version_id=target_version_id)

        # Validation de l'intégrité et de l'authenticité selon les champs de la version
        await self._validate_item_data(db, resource, item_data, version_id=target_version_id, schema_fields=schema_fields)

        new_item = ResourceItem(
            resource_id=resource.id,
            resource_version_id=target_version_id,
            organization_id=resource.organization_id,
            data=item_data,
            created_by_id=user.id,
        )

        db.add(new_item)

        # Mettre à jour les métriques
        if target_version:
            v_metrics = dict(target_version.metrics or {})
            v_metrics["item_count"] = (v_metrics.get("item_count") or 0) + 1
            target_version.metrics = v_metrics
            flag_modified(target_version, "metrics")

        current_metrics = dict(resource.metrics or {})
        current_metrics["item_count"] = (current_metrics.get("item_count") or 0) + 1
        resource.metrics = current_metrics
        flag_modified(resource, "metrics")

        await db.commit()
        await db.refresh(new_item)
        return new_item

    async def bulk_import_items(
        self,
        db: AsyncSession,
        resource_id: int,
        payload: ResourceBulkImportPayload,
        user: User,
        version_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Import en masse d'enregistrements pour pré-charger la ressource pour une version spécifique.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)
        target_version_id = version_id
        target_version: Optional[ResourceVersion] = None

        if target_version_id:
            ver_res = await db.execute(
                select(ResourceVersion).where(
                    ResourceVersion.id == target_version_id,
                    ResourceVersion.resource_id == resource.id
                )
            )
            target_version = ver_res.scalars().first()
        elif resource.versions:
            target_version = resource.versions[0]
            target_version_id = target_version.id

        schema_fields = target_version.schema_fields if target_version and target_version.schema_fields else (resource.schema_fields or [])
        gov = (target_version.template_governance if target_version and target_version.template_governance else {}) or {}

        # Règle de sécurité stricte : Une version publiée est scellée en lecture seule.
        if target_version and target_version.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active"):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Sécurité de versionnage : La version v{target_version.version_number} est publiée et scellée en lecture seule. L'importation de données y est strictement interdite. Veuillez créer ou cloner une nouvelle version pour importer des données."
            )

        if gov.get("allow_add_records") is False or gov.get("allow_import_data") is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="L'importation de données dans cette version de ressource est désactivée par la politique de gouvernance."
            )

        auto_fields = [f.get("name") for f in schema_fields if f.get("auto_increment")]
        auto_counters = {}
        for af in auto_fields:
            auto_counters[af] = await self._compute_auto_increment(db, resource.id, af, version_id=target_version_id)

        new_items = []
        for row in payload.items:
            row_dict = dict(row)
            for af in auto_fields:
                if row_dict.get(af) is None or str(row_dict.get(af)).strip() == "":
                    row_dict[af] = auto_counters[af]
                    auto_counters[af] += 1

            item = ResourceItem(
                resource_id=resource.id,
                resource_version_id=target_version_id,
                organization_id=resource.organization_id,
                data=row_dict,
                created_by_id=user.id,
            )
            new_items.append(item)
            db.add(item)

        if target_version:
            v_metrics = dict(target_version.metrics or {})
            v_metrics["item_count"] = (v_metrics.get("item_count") or 0) + len(new_items)
            target_version.metrics = v_metrics
            flag_modified(target_version, "metrics")

        current_metrics = dict(resource.metrics or {})
        current_metrics["item_count"] = (current_metrics.get("item_count") or 0) + len(new_items)
        resource.metrics = current_metrics
        flag_modified(resource, "metrics")
        current_metrics["last_import_at"] = datetime.now(timezone.utc).isoformat()
        resource.metrics = current_metrics

        await db.commit()
        return {"imported_count": len(new_items), "resource_id": resource.id}

    async def update_item(
        self,
        db: AsyncSession,
        resource_id: int,
        item_id: int,
        payload: ResourceItemUpdate,
        user: User,
        version_id: Optional[int] = None,
    ) -> ResourceItem:
        """
        Met à jour un enregistrement spécifique avec validation d'unicité et préservation des auto-incréments.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)

        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        if not is_super_admin:
            gov = await self._resolve_resource_governance(db, resource, user)
            if not gov.get("can_update_data", True):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="La modification des données existantes de cette ressource est interdite par la politique de gouvernance."
                )

        res = await db.execute(
            select(ResourceItem).where(
                ResourceItem.id == item_id,
                ResourceItem.resource_id == resource_id
            )
        )
        item = res.scalars().first()
        if not item:
            raise HTTPException(status_code=404, detail="Enregistrement introuvable.")

        # Garantir le rattachement ferme à la version ciblée
        effective_version_id = item.resource_version_id or version_id
        if effective_version_id is None and resource.versions:
            effective_version_id = resource.versions[0].id
        if item.resource_version_id is None and effective_version_id is not None:
            item.resource_version_id = effective_version_id

        # Contrôle strict de gouvernance et d'immuabilité de la version
        ver_obj = None
        if effective_version_id:
            ver_res = await db.execute(select(ResourceVersion).where(ResourceVersion.id == effective_version_id))
            ver_obj = ver_res.scalars().first()
            if ver_obj:
                # Règle de sécurité stricte : Une version publiée est scellée en lecture seule.
                if ver_obj.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active"):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Sécurité de versionnage : La version v{ver_obj.version_number} est publiée et scellée en lecture seule. La modification des données existantes y est strictement interdite. Veuillez créer une nouvelle version pour modifier les données."
                    )

                if ver_obj.template_governance and ver_obj.template_governance.get("allow_edit_records") is False:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="La modification des données dans cette version de ressource est désactivée par la politique de gouvernance."
                    )

        # Fusionner et valider les données
        updated_data = dict(item.data or {})
        updated_data.update(payload.data)

        # Préserver les valeurs des champs auto-incrémentés existants
        for field in resource.schema_fields or []:
            if field.get("auto_increment"):
                fname = field.get("name")
                if fname in (item.data or {}):
                    updated_data[fname] = item.data[fname]

        # Préserver les colonnes protégées en lecture seule définies dans la gouvernance (excluded_record_edit_fields)
        excluded_edit_cols = []
        if ver_obj and ver_obj.template_governance:
            excluded_edit_cols = ver_obj.template_governance.get("excluded_record_edit_fields") or []
        if not excluded_edit_cols and resource.versions:
            latest_v = resource.versions[0]
            if latest_v.template_governance:
                excluded_edit_cols = latest_v.template_governance.get("excluded_record_edit_fields") or []

        for ex_field in excluded_edit_cols:
            if ex_field in (item.data or {}):
                updated_data[ex_field] = item.data[ex_field]

        item_schema_fields = (ver_obj.schema_fields if ver_obj and ver_obj.schema_fields else None) or (resource.schema_fields or [])

        await self._validate_item_data(
            db,
            resource,
            updated_data,
            exclude_item_id=item_id,
            version_id=effective_version_id,
            schema_fields=item_schema_fields,
            existing_data=item.data,
        )
        item.data = updated_data

        await db.commit()
        await db.refresh(item)
        return item

        await db.commit()
        await db.refresh(item)
        return item

    async def delete_item(
        self,
        db: AsyncSession,
        resource_id: int,
        item_id: int,
        user: User,
    ) -> Dict[str, Any]:
        """
        Supprime un enregistrement spécifique.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)

        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        if not is_super_admin:
            gov = await self._resolve_resource_governance(db, resource, user)
            if not gov.get("can_delete_data", True):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="La suppression de données de cette ressource est interdite par la politique de gouvernance."
                )

        res = await db.execute(
            select(ResourceItem).where(
                ResourceItem.id == item_id,
                ResourceItem.resource_id == resource_id
            )
        )
        item = res.scalars().first()
        if not item:
            raise HTTPException(status_code=404, detail="Enregistrement introuvable.")

        # Contrôle strict de gouvernance et d'immuabilité de la version
        if item.resource_version_id:
            ver_res = await db.execute(select(ResourceVersion).where(ResourceVersion.id == item.resource_version_id))
            ver_obj = ver_res.scalars().first()
            if ver_obj:
                # Règle de sécurité stricte : Une version publiée est scellée en lecture seule.
                if ver_obj.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active"):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Sécurité de versionnage : La version v{ver_obj.version_number} est publiée et scellée en lecture seule. La suppression d'enregistrements y est strictement interdite."
                    )

                if ver_obj.template_governance and ver_obj.template_governance.get("allow_delete_records") is False:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="La suppression de données dans cette version de ressource est désactivée par la politique de gouvernance."
                    )

        await db.delete(item)

        current_metrics = dict(resource.metrics or {})
        current_metrics["item_count"] = max(0, (current_metrics.get("item_count") or 1) - 1)
        resource.metrics = current_metrics

        if item.resource_version_id and ver_obj:
            v_metrics = dict(ver_obj.metrics or {})
            v_metrics["item_count"] = max(0, (v_metrics.get("item_count") or 1) - 1)
            ver_obj.metrics = v_metrics
            flag_modified(ver_obj, "metrics")

        await db.commit()
        return {"deleted": True, "item_id": item_id}

    async def bulk_delete_items(
        self,
        db: AsyncSession,
        resource_id: int,
        item_ids: List[int],
        user: User,
    ) -> Dict[str, Any]:
        """
        Supprime plusieurs enregistrements d'une ressource en une seule transaction
        avec vérification des permissions et gouvernance.
        """
        if not item_ids:
            return {"deleted_count": 0, "item_ids": []}

        resource = await self.get_resource_by_id(db, resource_id, user)

        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        if not is_super_admin:
            gov = await self._resolve_resource_governance(db, resource, user)
            if not gov.get("can_delete_data", True):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="La suppression de données de cette ressource est interdite par la politique de gouvernance."
                )

        stmt = select(ResourceItem).where(
            ResourceItem.id.in_(item_ids),
            ResourceItem.resource_id == resource_id
        )
        res = await db.execute(stmt)
        items_to_delete = list(res.scalars().all())

        if not items_to_delete:
            return {"deleted_count": 0, "item_ids": []}

        # Contrôles de gouvernance de version pour chaque item
        version_cache: Dict[int, Optional[ResourceVersion]] = {}
        for item in items_to_delete:
            if item.resource_version_id:
                if item.resource_version_id not in version_cache:
                    ver_res = await db.execute(select(ResourceVersion).where(ResourceVersion.id == item.resource_version_id))
                    version_cache[item.resource_version_id] = ver_res.scalars().first()
                ver_obj = version_cache[item.resource_version_id]
                if ver_obj:
                    # Règle de sécurité stricte : Une version publiée est scellée en lecture seule.
                    if ver_obj.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active"):
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"Sécurité de versionnage : L'enregistrement #{item.id} appartient à la version v{ver_obj.version_number} qui est publiée et scellée en lecture seule. La suppression y est strictement interdite."
                        )

                    if ver_obj.template_governance and ver_obj.template_governance.get("allow_delete_records") is False:
                        raise HTTPException(
                            status_code=status.HTTP_403_FORBIDDEN,
                            detail=f"La suppression de l'enregistrement #{item.id} est interdite par la politique de gouvernance de la version v{ver_obj.version_number}."
                        )

        deleted_ids = []
        for item in items_to_delete:
            deleted_ids.append(item.id)
            await db.delete(item)

        # Mettre à jour les métriques
        current_metrics = dict(resource.metrics or {})
        current_metrics["item_count"] = max(0, (current_metrics.get("item_count") or len(deleted_ids)) - len(deleted_ids))
        resource.metrics = current_metrics

        await db.commit()
        return {"deleted_count": len(deleted_ids), "item_ids": deleted_ids}

    async def get_related_items(
        self,
        db: AsyncSession,
        resource_id: int,
        item_id: int,
        user: User,
    ) -> Dict[str, Any]:
        """
        Trouve tous les enregistrements d'autres ressources qui font référence à cet enregistrement.
        Ex: Pour un Planteur (id=15), trouve toutes ses Plantations où plantation.data['planteur_id'] == 15.
        """
        source_resource = await self.get_resource_by_id(db, resource_id, user)

        # Vérifier que l'item source existe
        item_res = await db.execute(
            select(ResourceItem).where(
                ResourceItem.id == item_id,
                ResourceItem.resource_id == source_resource.id
            )
        )
        source_item = item_res.scalars().first()
        if not source_item:
            raise HTTPException(status_code=404, detail="Enregistrement source introuvable.")

        # Trouver toutes les ressources accessibles dans le même tenant ou partagées
        query = select(Resource).where(
            Resource.id != source_resource.id,
            Resource.status != ResourceStatus.ARCHIVED.value
        )
        if source_resource.organization_id:
            query = query.where(
                or_(
                    Resource.organization_id == source_resource.organization_id,
                    Resource.is_shared == True
                )
            )

        all_res = await db.execute(query)
        other_resources = list(all_res.scalars().all())

        relations_data = []

        for other_res in other_resources:
            for field in (other_res.schema_fields or []):
                if field.get("type") == "relation":
                    target_id = field.get("target_resource_id")
                    target_code = field.get("target_resource_code")

                    # Vérifier si ce champ pointe vers notre ressource source
                    is_target = False
                    if target_id is not None:
                        try:
                            is_target = int(target_id) == source_resource.id
                        except (ValueError, TypeError):
                            pass
                    if not is_target and target_code:
                        is_target = (target_code.strip().lower() == source_resource.code.strip().lower())

                    if is_target:
                        fname = field.get("name")
                        str_id = str(item_id)
                        matching_stmt = select(ResourceItem).where(
                            ResourceItem.resource_id == other_res.id,
                            or_(
                                func.json_extract_path_text(ResourceItem.data, fname) == str_id,
                                func.json_extract_path_text(ResourceItem.data, fname, "id") == str_id
                            )
                        ).order_by(desc(ResourceItem.created_at)).limit(100)

                        matching_items = list((await db.execute(matching_stmt)).scalars().all())

                        serialized_items = [
                            {
                                "id": it.id,
                                "resource_id": it.resource_id,
                                "organization_id": it.organization_id,
                                "data": it.data,
                                "created_by_id": it.created_by_id,
                                "created_at": it.created_at.isoformat() if it.created_at else None,
                                "updated_at": it.updated_at.isoformat() if it.updated_at else None,
                            }
                            for it in matching_items
                        ]

                        relations_data.append({
                            "resource_id": other_res.id,
                            "resource_name": other_res.name,
                            "resource_code": other_res.code,
                            "field_name": fname,
                            "field_label": field.get("label") or fname,
                            "items": serialized_items,
                            "total": len(matching_items)
                        })

        return {
            "item_id": item_id,
            "resource_id": resource_id,
            "relations": relations_data
        }

    async def get_relation_options(
        self,
        db: AsyncSession,
        resource_id: int,
        user: User,
        display_field: Optional[str] = None,
        search: Optional[str] = None,
        limit: int = 100,
        target_version_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Retourne une liste simplifiée des options d'une ressource pour les champs sélecteurs de relations,
        strictement filtrée par la version cible pour assurer l'intégrité relationnelle entre versions.
        """
        target_res = await self.get_resource_by_id(db, resource_id, user)
        df = display_field
        if not df:
            for f in (target_res.schema_fields or []):
                fname = f.get("name", "").lower()
                if f.get("type") in ["text", "email", "phone"] or "nom" in fname or "titre" in fname or "label" in fname:
                    df = f.get("name")
                    break
            if not df and target_res.schema_fields:
                df = target_res.schema_fields[0].get("name")

        target_v = None
        if target_version_id:
            target_v_stmt = select(ResourceVersion).where(
                ResourceVersion.id == target_version_id,
                ResourceVersion.resource_id == target_res.id,
                ResourceVersion.status.in_(["published", "active"])
            )
            target_v = (await db.execute(target_v_stmt)).scalars().first()
            if not target_v:
                # La version ciblée n'est pas publiée / active : les dépendances exigent des versions scellées
                return []
        if not target_v:
            t_vers = target_res.versions or []
            target_v = next((v for v in t_vers if v.version_number == target_res.version and v.status in ("published", "active")), None)
            if not target_v and t_vers:
                target_v = next((v for v in t_vers if v.status in ("published", "active")), None)

        if not target_v:
            # Aucune version publiée disponible pour servir de dépendance scellée
            return []

        query = select(ResourceItem).where(
            ResourceItem.resource_id == target_res.id,
            ResourceItem.resource_version_id == target_v.id
        )

        query = query.order_by(desc(ResourceItem.created_at))
        if search and search.strip():
            s = f"%{search.strip().lower()}%"
            query = query.where(func.lower(func.cast(ResourceItem.data, String)).like(s))

        query = query.limit(limit)
        items = list((await db.execute(query)).scalars().all())

        options = []
        for item in items:
            data = item.data or {}
            label_val = data.get(df) if df else None
            if not label_val:
                label_val = next((str(v) for v in data.values() if v), f"Entrée #{item.id}")
            options.append({
                "id": item.id,
                "label": str(label_val),
                "data": data,
                "version_id": target_v.id if target_v else item.resource_version_id,
                "version_number": target_v.version_number if target_v else None,
            })
        return options

    async def create_new_version(
        self,
        db: AsyncSession,
        resource_id: int,
        new_version_str: Optional[str],
        description: Optional[str],
        is_lts: bool,
        user: User,
    ) -> Resource:
        """
        Crée une nouvelle version modifiable (draft) à partir d'une ressource active.
        Archive la version active actuelle dans schema_history.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)
        if resource.status != ResourceStatus.ACTIVE.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Une nouvelle version ne peut être créée qu'à partir d'une version active certifiée."
            )

        curr_ver = resource.version or "1.0.0"
        if not new_version_str or not new_version_str.strip():
            try:
                parts = curr_ver.split(".")
                if len(parts) >= 2:
                    minor = int(parts[1]) + 1
                    target_ver = f"{parts[0]}.{minor}.0"
                else:
                    target_ver = f"{curr_ver}.1"
            except Exception:
                target_ver = f"{curr_ver}-v2"
        else:
            target_ver = new_version_str.strip()

        # Archiver la version active actuelle dans schema_history
        snapshot = {
            "version": curr_ver,
            "is_lts": resource.is_lts,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "note": description or f"Archivé avant passage à la version {target_ver}",
            "schema_fields": resource.schema_fields,
        }
        resource.schema_history = (resource.schema_history or []) + [snapshot]

        # La ressource passe en nouvelle version au statut brouillon modifiable
        resource.version = target_ver
        resource.status = ResourceStatus.DRAFT.value
        resource.is_lts = is_lts

        await db.commit()
        await db.refresh(resource)
        return resource

    async def set_lts(
        self,
        db: AsyncSession,
        resource_id: int,
        is_lts: bool,
        user: User,
    ) -> Resource:
        """Définit le statut LTS (Long Term Support) de la ressource."""
        resource = await self.get_resource_by_id(db, resource_id, user)
        resource.is_lts = is_lts
        await db.commit()
        await db.refresh(resource)
        return resource

    async def get_resource_data_template(
        self,
        db: AsyncSession,
        resource_id: int,
        user: Optional[User] = None,
        version_id: Optional[int] = None,
    ) -> bytes:
        """Génère le gabarit Excel spécifique aux données de cette ressource."""
        if user:
            resource = await self.get_resource_by_id(db, resource_id, user)
        else:
            resource = await db.get(Resource, resource_id)
            if not resource:
                raise NotFoundException(f"Ressource #{resource_id} non trouvée.")

        active_fields = resource.schema_fields or []
        if version_id:
            ver = await db.get(ResourceVersion, version_id)
            if ver and ver.resource_id == resource.id and ver.schema_fields:
                active_fields = ver.schema_fields

        return generate_resource_data_template(resource.name, active_fields)

    async def validate_and_import_data_file(
        self,
        db: AsyncSession,
        resource_id: int,
        file_bytes: bytes,
        filename: str,
        update_existing: bool,
        dry_run: bool,
        user: User,
        version_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Analyse et valide de manière stricte l'intégralité d'un fichier de données.
        Si dry_run=False et tout est valide, insère ou met à jour les données.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)

        target_version = None
        if version_id:
            target_version = await db.get(ResourceVersion, version_id)
            if not target_version or target_version.resource_id != resource.id:
                raise NotFoundException(f"Version #{version_id} introuvable pour cette ressource.")
        elif resource.versions:
            target_version = next((v for v in resource.versions if v.status in ("published", "active")), resource.versions[0])

        active_fields = target_version.schema_fields if target_version and target_version.schema_fields else (resource.schema_fields or [])

        # 1. Extraction des lignes du fichier
        incoming_rows = parse_data_file(file_bytes, filename, active_fields)

        # 2. Récupération des données existantes (filtrées par version si active)
        items_stmt = select(ResourceItem).where(ResourceItem.resource_id == resource.id)
        if target_version:
            items_stmt = items_stmt.where(ResourceItem.resource_version_id == target_version.id)
        items_res = await db.execute(items_stmt)
        existing_item_objs = list(items_res.scalars().all())
        existing_items = [{"id": it.id, "data": it.data} for it in existing_item_objs]

        # 3. Analyse stricte
        report = validate_and_reconcile_data_rows(
            schema_fields=active_fields,
            incoming_rows=incoming_rows,
            existing_items=existing_items,
            update_existing=update_existing,
        )

        # 4. Si exécution effective et aucune erreur
        if not dry_run and report["is_valid"]:
            # Contrôle strict de gouvernance de la version
            v_gov = (target_version.template_governance if target_version and target_version.template_governance else {}) or {}
            if report.get("to_insert") and (v_gov.get("allow_add_records") is False or v_gov.get("allow_import_data") is False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="L'ajout ou l'importation de nouvelles données dans cette version de ressource est désactivé par la politique de gouvernance."
                )
            if report.get("to_update") and (v_gov.get("allow_edit_records") is False):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="La modification des données existantes dans cette version de ressource est désactivée par la politique de gouvernance."
                )

            is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
            if not is_super_admin:
                gov = await self._resolve_resource_governance(db, resource, user)
                if report.get("to_insert") and not gov.get("can_add_data", True):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="L'ajout de nouvelles données dans cette ressource est interdit par la politique de gouvernance."
                    )
                if report.get("to_update") and not gov.get("can_update_data", True):
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="La modification des données existantes de cette ressource est interdite par la politique de gouvernance."
                    )

            # A. Mises à jour
            if report.get("to_update"):
                item_map = {it.id: it for it in existing_item_objs}
                for upd in report["to_update"]:
                    item_id = upd["item_id"]
                    if item_id in item_map:
                        item_map[item_id].data = upd["data"]

            # B. Insertions
            if report.get("to_insert"):
                auto_fields = [f.get("name") for f in active_fields if f.get("auto_increment")]
                auto_counters = {}
                for af in auto_fields:
                    auto_counters[af] = await self._compute_auto_increment(
                        db, resource.id, af, version_id=target_version.id if target_version else None
                    )

                new_objs = []
                for row_data in report["to_insert"]:
                    for af in auto_fields:
                        if row_data.get(af) is None or str(row_data.get(af)).strip() == "":
                            row_data[af] = auto_counters[af]
                            auto_counters[af] += 1

                    new_objs.append(
                        ResourceItem(
                            resource_id=resource.id,
                            resource_version_id=target_version.id if target_version else None,
                            organization_id=resource.organization_id,
                            data=row_data,
                            created_by_id=user.id,
                        )
                    )
                db.add_all(new_objs)

            # Mettre à jour les métriques
            metrics = dict(resource.metrics or {})
            total_after = len(existing_item_objs) + len(report.get("to_insert") or [])
            metrics["item_count"] = total_after
            metrics["last_import_at"] = datetime.now(timezone.utc).isoformat()
            resource.metrics = metrics

            if target_version:
                v_metrics = dict(target_version.metrics or {})
                v_metrics["item_count"] = total_after
                v_metrics["last_import_at"] = datetime.now(timezone.utc).isoformat()
                target_version.metrics = v_metrics
                from sqlalchemy.orm.attributes import flag_modified
                flag_modified(target_version, "metrics")

            await db.commit()
            report["imported_count"] = len(report.get("to_insert") or []) + len(report.get("to_update") or [])

        return report

    async def upload_resource_media(
        self,
        db: AsyncSession,
        resource_id: int,
        file_bytes: bytes,
        filename: str,
        media_type: str,
        user: User,
        item_key: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Téléverse un média pour une ressource dynamique dans l'arborescence structurée :
        organizations/{org_id}/tools/resources/{resource_id}/{media_type}/{uuid}_{filename}
        """
        resource = await self.get_resource_by_id(db, resource_id, user)
        # Vérifier permission de modification
        await self._resolve_user_org_and_permissions(
            db, user, action="create_edit", requested_org_id=resource.organization_id
        )

        # Construire le chemin hiérarchisé dans le répertoire de l'utilisateur / organisation
        target_org_id = resource.organization_id or getattr(user, "organization_id", None) or 1
        rel_path = storage_service.build_resource_media_path(
            org_id=target_org_id,
            resource_id=resource.id,
            media_type=media_type,
            filename=filename,
            item_key=item_key,
            user_id=user.id if user else None,
        )

        # Enregistrer de façon transparente (local ou S3)
        file_info = storage_service.save_file(
            file_bytes=file_bytes,
            relative_path=rel_path
        )
        return file_info

    async def export_resource_data(
        self,
        db: AsyncSession,
        resource_id: int,
        format: str = "xlsx",
        user: Optional[User] = None,
        version_id: Optional[int] = None,
    ) -> Tuple[bytes, str, str]:
        """
        Exporte les données d'une ressource au format spécifié (xlsx, csv, json) pour une version spécifique.
        Si la ressource contient des médias et que des fichiers sont effectivement référencés,
        génère une archive ZIP autonome contenant le fichier de données et le sous-dossier 'media/'
        avec les liens internes réécrits vers les médias extraits.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)
        target_version: Optional[ResourceVersion] = None
        if version_id:
            ver_res = await db.execute(
                select(ResourceVersion).where(
                    ResourceVersion.id == version_id,
                    ResourceVersion.resource_id == resource.id
                )
            )
            target_version = ver_res.scalars().first()
        elif resource.versions:
            target_version = resource.versions[0]

        gov = (target_version.template_governance if target_version and target_version.template_governance else {}) or {}
        if gov.get("allow_export") is False:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="L'exportation des données de cette version est désactivée par la politique de gouvernance."
            )

        fields = target_version.schema_fields if target_version and target_version.schema_fields else (resource.schema_fields or [])
        fmt = (format or "xlsx").lower().strip()

        # Récupération des enregistrements
        items_stmt = select(ResourceItem).where(ResourceItem.resource_id == resource.id)
        if version_id:
            items_stmt = items_stmt.where(ResourceItem.resource_version_id == version_id)
        items_stmt = items_stmt.order_by(ResourceItem.id.asc())

        items_res = await db.execute(items_stmt)
        items = list(items_res.scalars().all())

        # Trouver le champ clé pour nommer les médias de façon intelligible
        key_field = next(
            (f.get("name") for f in fields if f.get("unique") or f.get("auto_increment")),
            "id"
        )

        media_files_map: Dict[str, bytes] = {}
        processed_rows: List[Dict[str, Any]] = []

        for idx, it in enumerate(items):
            row_data = dict(it.data or {})
            raw_key_val = row_data.get(key_field)
            item_key_str = str(raw_key_val) if raw_key_val is not None else f"row_{idx + 1}"
            clean_item_key = storage_service.sanitize_filename(item_key_str)

            for f in fields:
                fname = f.get("name")
                ftype = f.get("type", "text")
                val = row_data.get(fname)

                # Si le champ est un média ou référence un fichier stocké
                if val and isinstance(val, str) and (is_media_field(ftype) or "/storage/" in val or "organizations/" in val):
                    file_bytes = storage_service.get_file_bytes(val)
                    if file_bytes:
                        clean_base = storage_service.sanitize_filename(
                            os.path.basename(storage_service.resolve_relative_path(val))
                        )
                        zip_rel_path = f"media/{clean_item_key}_{fname}_{clean_base}"
                        media_files_map[zip_rel_path] = file_bytes
                        # Réécrire le lien dans la ligne de données pour pointer vers le sous-dossier de l'archive
                        row_data[fname] = zip_rel_path

            processed_rows.append(row_data)

        # Générer le fichier de données tabulaire (xlsx, csv ou json)
        data_bytes = generate_resource_data_export(
            resource_name=resource.name,
            schema_fields=fields,
            rows=processed_rows,
            format=fmt
        )

        ext = "xlsx" if fmt == "xlsx" else "csv" if fmt == "csv" else "json"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

        # Si des médias sont présents, packager dans une archive ZIP autonome
        if media_files_map:
            data_filename = f"donnees_{resource.code}.{ext}"
            zip_bytes = bundle_export_zip(
                data_filename=data_filename,
                data_bytes=data_bytes,
                media_files=media_files_map
            )
            zip_filename = f"export_{resource.code}_v{resource.version}_{timestamp}.zip"
            return zip_bytes, zip_filename, "application/zip"
        else:
            # Pas de médias : renvoyer directement le fichier de données
            mime = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                if fmt == "xlsx"
                else "text/csv; charset=utf-8"
                if fmt == "csv"
                else "application/json"
            )
            data_filename = f"export_{resource.code}_v{resource.version}_{timestamp}.{ext}"
            return data_bytes, data_filename, mime

    async def export_resource_schema(
        self,
        db: AsyncSession,
        resource_id: int,
        format: str = "json",
        user: Optional[User] = None
    ) -> Tuple[bytes, str, str]:
        """
        Exporte la spécification de schéma d'une ressource au format JSON ou Excel.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)
        return generate_resource_schema_export(resource, format)

    async def provision_plugin_templates(
        self,
        db: AsyncSession,
        org_id: int,
        plugin: Plugin,
        version: Any,
        user: User
    ) -> List[Resource]:
        """
        Instancie souverainement et isole les ressources préconfigurées déclarées dans la version du plugin
        lors de son intégration dans l'espace de travail d'une organisation.
        Pour chaque ressource configurée :
        - Crée un clone souverain dédié scellé à l'organisation (organization_id) et au plugin (plugin_id).
        - Applique la gouvernance stricte de permissions configurée dans le plugin.
        - Si include_initial_data est actif, copie les données initiales du template.
        """
        pkg = version.package_data or {}
        raw_tools = pkg.get("tools", [])
        templates_config = []

        if isinstance(raw_tools, list):
            for t in raw_tools:
                if isinstance(t, dict):
                    tool_code = t.get("tool_code")
                    item_key = t.get("item_key", "")
                    if tool_code == "resources" or item_key.startswith("resources_"):
                        if t.get("config_target") == "tool_resource" or t.get("selected_resource_id") is not None or t.get("selected_resource_data") is not None:
                            templates_config.append(t)
        elif isinstance(raw_tools, dict):
            res_config = raw_tools.get("resources", {})
            if isinstance(res_config, list):
                templates_config.extend(res_config)

        # Root resources list
        if isinstance(pkg.get("resources"), list):
            for r in pkg.get("resources"):
                if isinstance(r, dict):
                    templates_config.append(r)

        if not templates_config:
            return []

        created_resources: List[Resource] = []

        for tpl_cfg in templates_config:
            parent_id = tpl_cfg.get("selected_resource_id") or (tpl_cfg.get("selected_resource_data") or {}).get("id") or tpl_cfg.get("id")
            if not parent_id:
                continue

            # Vérifier si déjà cloné pour cette organisation ET ce plugin spécifique
            existing_res = await db.execute(
                select(Resource).where(
                    Resource.organization_id == org_id,
                    Resource.plugin_id == plugin.id,
                    Resource.parent_template_id == parent_id
                )
            )
            existing = existing_res.scalars().first()
            if existing:
                created_resources.append(existing)
                continue

            # Récupérer le modèle parent (Super Admin)
            parent_res_stmt = await db.execute(
                select(Resource).options(selectinload(Resource.items)).where(Resource.id == parent_id)
            )
            parent_resource = parent_res_stmt.scalars().first()
            if not parent_resource:
                logger.warning(f"[RESOURCE_PROVISION] Modèle parent ID {parent_id} introuvable.")
                continue

            # Code technique unique souverain pour cette organisation et ce plugin
            clean_code = f"{parent_resource.code}_{plugin.code}_{org_id}"[:100]

            is_imm = bool(tpl_cfg.get("is_immutable", True))
            imm_fields = tpl_cfg.get("immutable_fields", ["schema_fields", "code"] if is_imm else [])

            # Gouvernance complète et verrouillage de permissions
            governance_settings = {
                **copy.deepcopy(tpl_cfg),
                "plugin_id": plugin.id,
                "plugin_code": plugin.code,
                "plugin_name": plugin.name,
                "allow_creation": bool(tpl_cfg.get("allow_creation", False)),
                "allow_resource_deletion": bool(tpl_cfg.get("allow_resource_deletion", False)),
                "allow_field_addition": bool(tpl_cfg.get("allow_field_addition", False)),
                "allow_schema_modification": bool(tpl_cfg.get("allow_schema_modification", False)),
                "allow_data_update": bool(tpl_cfg.get("allow_data_update", False)),
                "data_update_scope": tpl_cfg.get("data_update_scope", "tenant_local"),
                "allow_data_addition": bool(tpl_cfg.get("allow_data_addition", True)),
                "data_addition_scope": tpl_cfg.get("data_addition_scope", "isolated"),
                "data_sharing_scope": tpl_cfg.get("data_sharing_scope", "isolated"),
                "allow_data_deletion": bool(tpl_cfg.get("allow_data_deletion", False)),
                "allow_export": bool(tpl_cfg.get("allow_export", True)),
                "allow_import": bool(tpl_cfg.get("allow_import", False)),
                "allow_archive": bool(tpl_cfg.get("allow_archive", True)),
                "locked_rows_mode": tpl_cfg.get("locked_rows_mode", "all_initial"),
            }

            cloned = Resource(
                organization_id=org_id,
                plugin_id=plugin.id,
                plugin_code=plugin.code,
                scope="tenant_clone",
                is_shared=False,
                is_immutable=is_imm,
                immutable_fields=imm_fields,
                parent_template_id=parent_id,
                name=tpl_cfg.get("name") or parent_resource.name,
                code=clean_code,
                description=tpl_cfg.get("description") or parent_resource.description,
                category=parent_resource.category,
                icon=parent_resource.icon,
                status=ResourceStatus.ACTIVE.value,
                version=parent_resource.version,
                is_lts=parent_resource.is_lts,
                schema_history=copy.deepcopy(parent_resource.schema_history or []),
                schema_fields=copy.deepcopy(parent_resource.schema_fields or []),
                settings=governance_settings,
                metrics={"item_count": 0},
                created_by_id=user.id
            )
            db.add(cloned)
            await db.flush()

            # Copie des données initiales si configuré
            include_init = tpl_cfg.get("include_initial_data", True)
            if include_init and parent_resource.items:
                copied_count = 0
                for item in parent_resource.items:
                    new_item = ResourceItem(
                        resource_id=cloned.id,
                        organization_id=org_id,
                        data=copy.deepcopy(item.data),
                        created_by_id=user.id
                    )
                    db.add(new_item)
                    copied_count += 1
                cloned.metrics = {"item_count": copied_count}

            created_resources.append(cloned)

        if created_resources:
            await db.commit()
            logger.info(f"[RESOURCES_PROVISIONED] {len(created_resources)} ressources isolées instanciées pour l'organisation ID {org_id} (plugin {plugin.code})")

        return created_resources


resource_service = ResourceService()


