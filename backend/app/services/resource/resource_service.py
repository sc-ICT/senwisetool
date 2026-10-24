import os
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc, String
from sqlalchemy.orm import selectinload

from app.models.resource.resource import Resource, ResourceStatus
from app.models.resource.resource_item import ResourceItem
from app.models.organization.organization import Organization
from app.models.organization.member import OrganizationMember
from app.models.organization.role import OrganizationRole
from app.models.plugin.plugin import Plugin, OrganizationPlugin, OrgPluginStatus
from app.models.user.user import User, UserRole
from app.schemas.resource.resource import (
    ResourceCreate,
    ResourceUpdate,
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

        query = select(Resource).order_by(desc(Resource.created_at))
        count_stmt = select(func.count(Resource.id))
        active_stmt = select(func.count(Resource.id)).where(Resource.status == ResourceStatus.ACTIVE.value)
        draft_stmt = select(func.count(Resource.id)).where(Resource.status == ResourceStatus.DRAFT.value)
        archived_stmt = select(func.count(Resource.id)).where(Resource.status == ResourceStatus.ARCHIVED.value)

        if is_super_admin and not target_org_id:
            # Super Admin visualisant les ressources de la plateforme
            pass
        elif target_org_id:
            # Récupérer les plugins installés par cette organisation
            inst_res = await db.execute(
                select(OrganizationPlugin.plugin_id).where(
                    OrganizationPlugin.organization_id == target_org_id,
                    OrganizationPlugin.status == OrgPluginStatus.INSTALLED.value
                )
            )
            installed_plugin_ids = list(inst_res.scalars().all())

            if installed_plugin_ids:
                org_condition = or_(
                    Resource.organization_id == target_org_id,
                    and_(Resource.is_shared == True, Resource.plugin_id.in_(installed_plugin_ids))
                )
            else:
                org_condition = (Resource.organization_id == target_org_id)

            query = query.where(org_condition)
            count_stmt = count_stmt.where(org_condition)
            active_stmt = active_stmt.where(org_condition)
            draft_stmt = draft_stmt.where(org_condition)
            archived_stmt = archived_stmt.where(org_condition)

        if status_filter and status_filter != "all":
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
            .options(selectinload(Resource.items))
            .where(Resource.id == resource_id)
        )
        resource = res.scalars().first()
        if not resource:
            raise HTTPException(status_code=404, detail="Ressource introuvable.")

        if user is None or is_super_admin:
            return resource

        org, _ = await self._resolve_user_org_and_permissions(db, user, action="view")
        if resource.organization_id and resource.organization_id != org.id and not resource.is_shared:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès non autorisé à cette ressource."
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

        # Règle d'or : La ressource est TOUJOURS initialisée en DRAFT
        # Une ressource ne peut être activée qu'après validation de son schéma.
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
            description=data.description.strip() if data.description else None,
            category=data.category or "general",
            icon=data.icon or "Database",
            status=ResourceStatus.DRAFT.value,
            version="1.0.0",
            schema_history=[],
            schema_fields=fields_data,
            settings=data.settings or {},
            metrics={"item_count": 0},
            created_by_id=user.id,
        )

        db.add(new_resource)
        await db.commit()
        await db.refresh(new_resource)
        return new_resource

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

        # Contrôle d'immuabilité si ce n'est pas un Super Admin
        if resource.is_immutable and not is_super_admin:
            immutable_keys = resource.immutable_fields or []
            if "schema" in immutable_keys and data.schema_fields is not None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="La structure et le schéma de cette ressource sont scellés et immuables."
                )
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

            if resource.status == ResourceStatus.ACTIVE.value:
                # Réconciliation et vérification stricte d'intégrité avec les données existantes
                items_res = await db.execute(
                    select(ResourceItem).where(ResourceItem.resource_id == resource.id)
                )
                items_objs = list(items_res.scalars().all())
                existing_items = [{"id": it.id, "data": it.data} for it in items_objs]

                report = reconcile_schema_coherence(
                    existing_fields=resource.schema_fields or [],
                    incoming_fields=incoming_raw,
                    existing_items=existing_items,
                    current_version=resource.version or "1.0.0",
                    is_active=True,
                )

                if not report["is_coherent"]:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail={
                            "message": "Incohérence du schéma détectée avec les données existantes.",
                            "errors": report["errors"],
                            "warnings": report["warnings"],
                        }
                    )

                # Archivage de la version antérieure
                if report["added_fields"] or report["updated_fields"]:
                    snapshot = {
                        "version": resource.version,
                        "archived_at": datetime.now(timezone.utc).isoformat(),
                        "schema_fields": resource.schema_fields,
                    }
                    resource.schema_history = (resource.schema_history or []) + [snapshot]
                    resource.version = report["new_version"]

                resource.schema_fields = report["resulting_schema"]

                # Remplissage des valeurs par défaut pour les nouveaux champs
                for added_f in report["added_fields"]:
                    fname = added_f["name"]
                    dval = added_f.get("default_value")
                    if dval is not None and dval != "":
                        for item_obj in items_objs:
                            cur_data = dict(item_obj.data or {})
                            if fname not in cur_data or cur_data[fname] is None:
                                cur_data[fname] = dval
                                item_obj.data = cur_data
            else:
                # En mode DRAFT : modification libre du schéma
                resource.schema_fields = incoming_raw

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
    ) -> Dict[str, Any]:
        """
        Analyse et réconcilie un lot de champs entrants (ex: depuis Excel ou JSON)
        avec le schéma existant d'une ressource. Vérifie l'intégrité absolue avec les données existantes.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)
        items_res = await db.execute(
            select(ResourceItem).where(ResourceItem.resource_id == resource.id)
        )
        items_objs = list(items_res.scalars().all())
        existing_items = [{"id": it.id, "data": it.data} for it in items_objs]

        report = reconcile_schema_coherence(
            existing_fields=resource.schema_fields or [],
            incoming_fields=incoming_fields,
            existing_items=existing_items,
            current_version=resource.version or "1.0.0",
            is_active=(resource.status == ResourceStatus.ACTIVE.value),
        )

        if apply_changes and report["is_coherent"]:
            is_active = (resource.status == ResourceStatus.ACTIVE.value)
            has_changes = bool(report["added_fields"] or report["updated_fields"])

            if is_active and has_changes:
                # Archivage de la version précédente
                snapshot = {
                    "version": resource.version,
                    "archived_at": datetime.now(timezone.utc).isoformat(),
                    "schema_fields": resource.schema_fields,
                }
                resource.schema_history = (resource.schema_history or []) + [snapshot]
                resource.version = report["new_version"]

            resource.schema_fields = report["resulting_schema"]

            # Remplissage automatique des valeurs par défaut pour les champs obligatoires ajoutés
            for added_f in report["added_fields"]:
                fname = added_f["name"]
                dval = added_f.get("default_value")
                if dval is not None and dval != "":
                    for item_obj in items_objs:
                        cur_data = dict(item_obj.data or {})
                        if fname not in cur_data or cur_data[fname] is None:
                            cur_data[fname] = dval
                            item_obj.data = cur_data

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

        await db.delete(resource)
        await db.commit()
        return {"deleted": True, "archived": False, "message": "Ressource supprimée avec succès."}

    # =========================================================================
    # GESTION DES DONNÉES / ITEMS DE LA RESSOURCE
    # =========================================================================

    async def list_items(
        self,
        db: AsyncSession,
        resource_id: int,
        user: User,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> Dict[str, Any]:
        """
        Liste les enregistrements de données dynamiques d'une ressource.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)

        query = select(ResourceItem).where(ResourceItem.resource_id == resource.id).order_by(desc(ResourceItem.created_at))
        count_stmt = select(func.count(ResourceItem.id)).where(ResourceItem.resource_id == resource.id)

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
    ) -> None:
        """
        Valide l'intégrité, les champs obligatoires et l'unicité des données d'un enregistrement.
        """
        schema_fields = resource.schema_fields or []
        for field in schema_fields:
            fname = field.get("name")
            val = data.get(fname)

            # 1. Validation de l'obligation
            if field.get("required") and (val is None or val == ""):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                    detail=f"Le champ obligatoire '{field.get('label') or fname}' est manquant."
                )

            # 2. Validation de l'unicité pour garantir l'authenticité
            if field.get("unique") and val is not None and val != "":
                unique_stmt = select(ResourceItem.id).where(
                    ResourceItem.resource_id == resource.id,
                    func.json_extract_path_text(ResourceItem.data, fname) == str(val)
                )
                if exclude_item_id:
                    unique_stmt = unique_stmt.where(ResourceItem.id != exclude_item_id)
                exists_res = await db.execute(unique_stmt)
                if exists_res.scalars().first():
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"La valeur '{val}' pour le champ '{field.get('label') or fname}' existe déjà. Ce champ exige une valeur unique pour assurer l'authenticité des données."
                    )

    async def _compute_auto_increment(
        self, db: AsyncSession, resource_id: int, field_name: str
    ) -> int:
        """Calcule la valeur suivante pour un champ auto-incrémenté."""
        items_res = await db.execute(
            select(ResourceItem.data).where(ResourceItem.resource_id == resource_id)
        )
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
    ) -> ResourceItem:
        """
        Insère un nouvel enregistrement de données dynamiques avec auto-incrémentation
        et validation d'unicité et de schéma.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)

        item_data = dict(payload.data)
        for field in resource.schema_fields or []:
            if field.get("auto_increment"):
                fname = field.get("name")
                if item_data.get(fname) is None or str(item_data.get(fname)).strip() == "":
                    item_data[fname] = await self._compute_auto_increment(db, resource.id, fname)

        # Validation de l'intégrité et de l'authenticité
        await self._validate_item_data(db, resource, item_data)

        new_item = ResourceItem(
            resource_id=resource.id,
            organization_id=resource.organization_id,
            data=item_data,
            created_by_id=user.id,
        )

        db.add(new_item)

        # Mettre à jour le compteur d'enregistrements
        current_metrics = dict(resource.metrics or {})
        current_metrics["item_count"] = (current_metrics.get("item_count") or 0) + 1
        resource.metrics = current_metrics

        await db.commit()
        await db.refresh(new_item)
        return new_item

    async def bulk_import_items(
        self,
        db: AsyncSession,
        resource_id: int,
        payload: ResourceBulkImportPayload,
        user: User,
    ) -> Dict[str, Any]:
        """
        Import en masse d'enregistrements pour pré-charger la ressource.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)

        auto_fields = [f.get("name") for f in (resource.schema_fields or []) if f.get("auto_increment")]
        auto_counters = {}
        for af in auto_fields:
            auto_counters[af] = await self._compute_auto_increment(db, resource.id, af)

        new_items = []
        for row in payload.items:
            row_dict = dict(row)
            for af in auto_fields:
                if row_dict.get(af) is None or str(row_dict.get(af)).strip() == "":
                    row_dict[af] = auto_counters[af]
                    auto_counters[af] += 1

            item = ResourceItem(
                resource_id=resource.id,
                organization_id=resource.organization_id,
                data=row_dict,
                created_by_id=user.id,
            )
            new_items.append(item)
            db.add(item)

        current_metrics = dict(resource.metrics or {})
        current_metrics["item_count"] = (current_metrics.get("item_count") or 0) + len(new_items)
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
    ) -> ResourceItem:
        """
        Met à jour un enregistrement spécifique avec validation d'unicité et préservation des auto-incréments.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)

        res = await db.execute(
            select(ResourceItem).where(
                ResourceItem.id == item_id,
                ResourceItem.resource_id == resource_id
            )
        )
        item = res.scalars().first()
        if not item:
            raise HTTPException(status_code=404, detail="Enregistrement introuvable.")

        # Fusionner et valider les données
        updated_data = dict(item.data or {})
        updated_data.update(payload.data)

        # Préserver les valeurs des champs auto-incrémentés existants
        for field in resource.schema_fields or []:
            if field.get("auto_increment"):
                fname = field.get("name")
                if fname in (item.data or {}):
                    updated_data[fname] = item.data[fname]

        await self._validate_item_data(db, resource, updated_data, exclude_item_id=item_id)
        item.data = updated_data

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

        res = await db.execute(
            select(ResourceItem).where(
                ResourceItem.id == item_id,
                ResourceItem.resource_id == resource_id
            )
        )
        item = res.scalars().first()
        if not item:
            raise HTTPException(status_code=404, detail="Enregistrement introuvable.")

        await db.delete(item)

        current_metrics = dict(resource.metrics or {})
        current_metrics["item_count"] = max(0, (current_metrics.get("item_count") or 1) - 1)
        resource.metrics = current_metrics

        await db.commit()
        return {"deleted": True, "item_id": item_id}

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
    ) -> List[Dict[str, Any]]:
        """
        Retourne une liste simplifiée des options d'une ressource pour les champs sélecteurs de relations.
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

        query = select(ResourceItem).where(ResourceItem.resource_id == target_res.id).order_by(desc(ResourceItem.created_at))
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
                "data": data
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
    ) -> bytes:
        """Génère le gabarit Excel spécifique aux données de cette ressource."""
        if user:
            resource = await self.get_resource_by_id(db, resource_id, user)
        else:
            resource = await db.get(Resource, resource_id)
            if not resource:
                raise NotFoundException(f"Ressource #{resource_id} non trouvée.")
        return generate_resource_data_template(resource.name, resource.schema_fields or [])

    async def validate_and_import_data_file(
        self,
        db: AsyncSession,
        resource_id: int,
        file_bytes: bytes,
        filename: str,
        update_existing: bool,
        dry_run: bool,
        user: User,
    ) -> Dict[str, Any]:
        """
        Analyse et valide de manière stricte l'intégralité d'un fichier de données.
        Si dry_run=False et tout est valide, insère ou met à jour les données.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)

        # 1. Extraction des lignes du fichier
        incoming_rows = parse_data_file(file_bytes, filename, resource.schema_fields or [])

        # 2. Récupération des données existantes
        items_res = await db.execute(
            select(ResourceItem).where(ResourceItem.resource_id == resource.id)
        )
        existing_item_objs = list(items_res.scalars().all())
        existing_items = [{"id": it.id, "data": it.data} for it in existing_item_objs]

        # 3. Analyse stricte
        report = validate_and_reconcile_data_rows(
            schema_fields=resource.schema_fields or [],
            incoming_rows=incoming_rows,
            existing_items=existing_items,
            update_existing=update_existing,
        )

        # 4. Si exécution effective et aucune erreur
        if not dry_run and report["is_valid"]:
            # A. Mises à jour
            if report.get("to_update"):
                item_map = {it.id: it for it in existing_item_objs}
                for upd in report["to_update"]:
                    item_id = upd["item_id"]
                    if item_id in item_map:
                        item_map[item_id].data = upd["data"]

            # B. Insertions
            if report.get("to_insert"):
                auto_fields = [f.get("name") for f in (resource.schema_fields or []) if f.get("auto_increment")]
                auto_counters = {}
                for af in auto_fields:
                    auto_counters[af] = await self._compute_auto_increment(db, resource.id, af)

                new_objs = []
                for row_data in report["to_insert"]:
                    for af in auto_fields:
                        if row_data.get(af) is None or str(row_data.get(af)).strip() == "":
                            row_data[af] = auto_counters[af]
                            auto_counters[af] += 1

                    new_objs.append(
                        ResourceItem(
                            resource_id=resource.id,
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

        # Construire le chemin hiérarchisé
        rel_path = storage_service.build_resource_media_path(
            org_id=resource.organization_id,
            resource_id=resource.id,
            media_type=media_type,
            filename=filename,
            item_key=item_key
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
        user: Optional[User] = None
    ) -> Tuple[bytes, str, str]:
        """
        Exporte les données d'une ressource au format spécifié (xlsx, csv, json).
        Si la ressource contient des médias et que des fichiers sont effectivement référencés,
        génère une archive ZIP autonome contenant le fichier de données et le sous-dossier 'media/'
        avec les liens internes réécrits vers les médias extraits.
        """
        resource = await self.get_resource_by_id(db, resource_id, user)
        fields = resource.schema_fields or []
        fmt = (format or "xlsx").lower().strip()

        # Récupération des enregistrements
        items_res = await db.execute(
            select(ResourceItem)
            .where(ResourceItem.resource_id == resource.id)
            .order_by(ResourceItem.id.asc())
        )
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


resource_service = ResourceService()

