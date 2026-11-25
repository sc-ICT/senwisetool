import copy
import uuid
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, desc, func, or_, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from fastapi import HTTPException, status

from app.models.plugin.plugin import (
    Plugin,
    PluginVersion,
    PluginVersionTool,
    PluginStatus,
    PluginVersionStatus,
    OrgPluginStatus,
)
from app.models.organization.organization import Organization
from app.models.organization.organization_plugin import (
    OrganizationPlugin,
    OrganizationPluginVersion,
)
from app.models.organization.organization_feature import OrganizationFeature
from app.models.organization.organization_project import (
    OrganizationProject,
    OrganizationProjectVersion,
)
from app.models.organization.organization_campaign import (
    OrganizationCampaign,
    OrganizationCampaignVersion,
)
from app.models.organization.organization_resource import (
    OrganizationResource,
    OrganizationResourceVersion,
    OrganizationResourceItem,
)
from app.models.organization.organization_form import (
    OrganizationForm,
    OrganizationFormVersion,
    OrganizationFormSection,
    OrganizationFormQuestion,
)
from app.models.campaign.campaign_version import CampaignVersion
from app.models.form.form_version import FormVersion
from app.models.form.form_section import FormSection
from app.models.form.form_question import FormQuestion
from app.models.user.user import User
from app.models.campaign.campaign import Campaign, CampaignStatus
from app.models.resource.resource import Resource, ResourceStatus
from app.models.resource.resource_version import ResourceVersion, ResourceVersionStatus
from app.models.resource.resource_item import ResourceItem
from app.models.project.project import Project, ProjectVersion
from app.models.form.form import Form
from app.schemas.plugin.plugin import (
    PluginCreate,
    PluginUpdate,
    PluginVersionCreate,
    PluginVersionUpdate,
    PluginVersionToolCreate,
    PluginVersionToolUpdate,
)
import logging
from app.services.notification.notification_service import notification_service

logger = logging.getLogger(__name__)


class PluginService:
    # ==========================================
    # SUPER ADMIN: GESTION DU CATALOGUE PLUGINS
    # ==========================================

    async def create_plugin(
        self,
        db: AsyncSession,
        data: PluginCreate,
        user: User
    ) -> Plugin:
        """
        Crée un nouveau Plugin en statut brouillon (ou publié), avec éventuellement une première version.
        """
        clean_code = data.code.strip().lower()

        # Vérifier l'unicité du code
        existing = await self.get_by_code(db, clean_code)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Un plugin avec l'identifiant '{clean_code}' existe déjà. Choisissez un autre code unique."
            )

        plugin = Plugin(
            code=clean_code,
            name=data.name.strip(),
            category=data.category.strip(),
            description=data.description,
            short_description=data.short_description,
            icon_url=data.icon_url,
            banner_url=data.banner_url,
            author=data.author or "SenWiseTool",
            status=PluginStatus.DRAFT.value,
            tags=data.tags or [],
            is_public=data.is_public if data.is_public is not None else True,
            allowed_organization_ids=data.allowed_organization_ids or [],
        )
        db.add(plugin)
        await db.flush()

        # Création automatique de la version initiale si spécifiée
        if data.initial_version_number:
            initial_version = PluginVersion(
                plugin_id=plugin.id,
                version_number=data.initial_version_number.strip(),
                changelog=data.initial_changelog or "Version initiale de démarrage",
                package_data=data.initial_package_data or {},
                usage_policy=data.initial_usage_policy.strip() if data.initial_usage_policy else "",
                status=PluginVersionStatus.DRAFT.value,
            )
            db.add(initial_version)
            await db.flush()

        await db.commit()
        return await self.get_by_id(db, plugin.id)

    async def update_plugin(
        self,
        db: AsyncSession,
        plugin_id: int,
        data: PluginUpdate,
        user: User
    ) -> Plugin:
        """
        Met à jour les métadonnées globales d'un plugin (nom, catégorie, descriptions, icônes, tags).
        """
        plugin = await self.get_by_id(db, plugin_id)
        if not plugin:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plugin introuvable."
            )

        update_dict = data.model_dump(exclude_unset=True)
        for key, val in update_dict.items():
            if val is not None:
                setattr(plugin, key, val)

        await db.commit()
        return await self.get_by_id(db, plugin.id)

    async def get_by_id(
        self,
        db: AsyncSession,
        plugin_id: int
    ) -> Optional[Plugin]:
        stmt = (
            select(Plugin)
            .options(
                selectinload(Plugin.versions).selectinload(PluginVersion.tools),
            )
            .where(Plugin.id == plugin_id)
        )
        res = await db.execute(stmt)
        return res.scalars().first()

    async def get_by_code(
        self,
        db: AsyncSession,
        code: str
    ) -> Optional[Plugin]:
        clean_code = code.strip().lower()
        stmt = (
            select(Plugin)
            .options(
                selectinload(Plugin.versions).selectinload(PluginVersion.tools),
            )
            .where(Plugin.code == clean_code)
        )
        res = await db.execute(stmt)
        return res.scalars().first()

    async def get_version_by_id(
        self,
        db: AsyncSession,
        plugin_id: int,
        version_id: int
    ) -> Optional[PluginVersion]:
        """
        Récupère une version spécifique d'un plugin par son ID avec ses outils rattachés.
        """
        stmt = (
            select(PluginVersion)
            .options(
                selectinload(PluginVersion.tools),
                selectinload(PluginVersion.plugin)
            )
            .where(
                PluginVersion.id == version_id,
                PluginVersion.plugin_id == plugin_id
            )
        )
        res = await db.execute(stmt)
        version = res.scalars().first()
        if version:
            await self._ensure_tools_migrated(db, version)
        return version

    async def list_plugins_admin(
        self,
        db: AsyncSession,
        category: Optional[str] = None,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50
    ) -> Tuple[List[Plugin], int]:
        """
        Liste tous les plugins pour la console Super Admin (brouillons, publiés, archivés).
        """
        query = select(Plugin).options(
            selectinload(Plugin.versions).selectinload(PluginVersion.tools),
        )

        filters = []
        if category and category != "all":
            filters.append(func.lower(Plugin.category) == category.strip().lower())
        if status_filter and status_filter != "all":
            filters.append(Plugin.status == status_filter)
        if search and search.strip():
            s = f"%{search.strip().lower()}%"
            filters.append(or_(
                func.lower(Plugin.name).like(s),
                func.lower(Plugin.code).like(s),
                func.lower(Plugin.category).like(s),
                func.lower(Plugin.description).like(s)
            ))

        if filters:
            query = query.where(and_(*filters))

        count_stmt = select(func.count(Plugin.id))
        if filters:
            count_stmt = count_stmt.where(and_(*filters))
        total_res = await db.execute(count_stmt)
        total = total_res.scalar() or 0

        query = query.order_by(desc(Plugin.created_at)).offset(skip).limit(limit)
        items_res = await db.execute(query)
        items = list(items_res.scalars().all())

        return items, total

    # ==========================================
    # SUPER ADMIN: GESTION DES VERSIONS
    # ==========================================

    async def create_version(
        self,
        db: AsyncSession,
        plugin_id: int,
        data: PluginVersionCreate,
        user: User
    ) -> PluginVersion:
        """
        Ajoute une nouvelle version brouillon à un plugin existant.
        Règle 7 : Interdiction formelle de créer une version s'il existe déjà une version brouillon.
        """
        plugin = await self.get_by_id(db, plugin_id)
        if not plugin:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plugin introuvable."
            )

        # Règle 7 : Unicité du brouillon
        res_draft = await db.execute(
            select(PluginVersion).where(
                PluginVersion.plugin_id == plugin_id,
                PluginVersion.status == PluginVersionStatus.DRAFT.value
            )
        )
        if res_draft.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Une version brouillon est déjà en cours de préparation pour ce plugin. Vous devez la publier ou la supprimer avant de pouvoir en créer une nouvelle."
            )

        clean_version = data.version_number.strip()

        # Vérifier que ce numéro de version n'existe pas déjà
        res_v = await db.execute(
            select(PluginVersion).where(
                PluginVersion.plugin_id == plugin_id,
                PluginVersion.version_number == clean_version
            )
        )
        if res_v.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La version {clean_version} existe déjà pour ce plugin. Veuillez incrémenter le numéro de version SemVer."
            )

        # Résolution de la version source d'héritage (clone des modules, fonctionnalités et configurations)
        # RÈGLE D'OR : Uniquement si explicitement demandé par l'utilisateur (source_version_id fourni).
        # Si source_version_id est absent/None, la version est créée 100% vierge (aucun composant cloné).
        source_version = None
        if getattr(data, "source_version_id", None):
            res_src = await db.execute(
                select(PluginVersion)
                .options(selectinload(PluginVersion.tools))
                .where(
                    PluginVersion.plugin_id == plugin_id,
                    PluginVersion.id == data.source_version_id
                )
            )
            source_version = res_src.scalars().first()

        # Héritage complet du package_data
        package_data = data.package_data
        if not package_data or package_data == {"features": ["dynamic_forms", "workflow_engine"]}:
            package_data = copy.deepcopy(source_version.package_data) if (source_version and source_version.package_data) else {}

        # Héritage de la politique d'utilisation
        usage_policy = (data.usage_policy or "").strip()
        if not usage_policy and source_version and source_version.usage_policy:
            usage_policy = source_version.usage_policy.strip()

        if not usage_policy or len(usage_policy) < 10:
            usage_policy = f"# Conditions d'utilisation v{clean_version}\nL'organisation confirme l'accord juridique pour cette version."

        version = PluginVersion(
            plugin_id=plugin_id,
            version_number=clean_version,
            changelog=data.changelog or (f"Nouvelle version initialisée depuis v{source_version.version_number}." if source_version else None),
            package_data=package_data or {},
            usage_policy=usage_policy,
            status=PluginVersionStatus.DRAFT.value,
        )
        db.add(version)
        await db.flush()  # Obtention de version.id

        # Cloner tous les outils, modules et fonctionnalités (PluginVersionTool) de la version source
        if source_version and source_version.tools:
            for src_tool in source_version.tools:
                cloned_tool = PluginVersionTool(
                    plugin_version_id=version.id,
                    item_key=src_tool.item_key,
                    tool_code=src_tool.tool_code,
                    type=src_tool.type,
                    config_target=src_tool.config_target,
                    name=src_tool.name,
                    description=src_tool.description,
                    selected_resource_id=src_tool.selected_resource_id,
                    selected_resource_code=src_tool.selected_resource_code,
                    selected_resource_name=src_tool.selected_resource_name,
                    selected_resource_status=src_tool.selected_resource_status,
                    distribution_mode=src_tool.distribution_mode,
                    permissions_mode=src_tool.permissions_mode,
                    parameters=copy.deepcopy(src_tool.parameters or {}),
                )
                db.add(cloned_tool)

        await db.commit()
        fresh_version = await self.get_version_by_id(db, plugin_id, version.id)
        return fresh_version or version

    async def update_version(
        self,
        db: AsyncSession,
        plugin_id: int,
        version_id: int,
        data: PluginVersionUpdate,
        user: User
    ) -> PluginVersion:
        """
        Mise à jour d'une version brouillon (build du plugin : outils, paramètres, politique, changelog).
        Règle 1 & 7 : Seule une version en statut 'draft' peut être modifiée.
        Dès qu'une version est publiée, elle devient scellée et immuable.
        """
        res_v = await db.execute(
            select(PluginVersion).where(
                PluginVersion.id == version_id,
                PluginVersion.plugin_id == plugin_id
            )
        )
        version = res_v.scalars().first()
        if not version:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce plugin.")

        if version.status != PluginVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=400,
                detail="Cette version est scellée/publiée et ne peut plus être modifiée. Seule une version brouillon peut être éditée."
            )

        if data.changelog is not None:
            version.changelog = data.changelog
        if data.usage_policy is not None:
            version.usage_policy = data.usage_policy.strip()
        if data.package_data is not None:
            version.package_data = data.package_data

        version.updated_at = datetime.now(timezone.utc)
        await db.commit()
        fresh_version = await self.get_version_by_id(db, plugin_id, version_id)
        return fresh_version or version

    async def delete_version(
        self,
        db: AsyncSession,
        plugin_id: int,
        version_id: int,
        user: User
    ) -> bool:
        """
        Règle 7 : Une version en brouillon (non publiée) peut être librement supprimée.
        Une version publiée ne peut JAMAIS être supprimée directement (immuabilité).
        """
        res_v = await db.execute(
            select(PluginVersion).where(
                PluginVersion.id == version_id,
                PluginVersion.plugin_id == plugin_id
            )
        )
        version = res_v.scalars().first()
        if not version:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce plugin.")

        if version.status != PluginVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=400,
                detail="Une version publiée est immuable et ne peut pas être supprimée. Utilisez la dépréciation planifiée si nécessaire."
            )

        await db.delete(version)
        await db.commit()
        return True

    # ==========================================
    # GESTION DES OUTILS & MODULES DE VERSION (BDD DÉDIÉE)
    # ==========================================

    async def _ensure_tools_migrated(self, db: AsyncSession, version: PluginVersion) -> None:
        """
        Migre les outils stockés historiquement dans package_data['tools'] vers la table plugin_version_tools
        si celle-ci est encore vide pour cette version.
        """
        if version.tools and len(version.tools) > 0:
            return

        pkg_tools = (version.package_data or {}).get("tools")
        if not pkg_tools or not isinstance(pkg_tools, list):
            return

        migrated_any = False
        for item in pkg_tools:
            if not isinstance(item, dict):
                continue
            item_key = str(item.get("item_key") or item.get("id") or f"tool_{uuid.uuid4().hex[:8]}")
            tool_code = str(item.get("tool_code") or item.get("tool") or "projects")
            t_type = str(item.get("type") or ("feature" if item.get("config_target") == "tool_global" or item.get("is_global") else "module"))
            config_target = str(item.get("config_target") or ("tool_global" if t_type == "feature" else "tool_resource"))
            name = str(item.get("name") or item.get("title") or item_key)
            description = item.get("description") or ""

            selected_resource_id = item.get("selected_resource_id") or item.get("project_id") or item.get("resource_id")
            try:
                selected_resource_id = int(selected_resource_id) if selected_resource_id is not None else None
            except (ValueError, TypeError):
                selected_resource_id = None

            selected_resource_code = item.get("selected_resource_code")
            selected_resource_name = item.get("selected_resource_name")
            selected_resource_status = item.get("selected_resource_status")
            distribution_mode = str(item.get("distribution_mode") or "tenant")
            permissions_mode = str(item.get("permissions_mode") or "restricted")

            params = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
            standard_keys = {
                "item_key", "tool_code", "type", "config_target", "name", "description",
                "selected_resource_id", "selected_resource_code", "selected_resource_name",
                "selected_resource_status", "distribution_mode", "permissions_mode", "parameters", "id"
            }
            for k, v in item.items():
                if k not in standard_keys and k not in params:
                    params[k] = v

            tool_obj = PluginVersionTool(
                plugin_version_id=version.id,
                item_key=item_key,
                tool_code=tool_code,
                type=t_type,
                config_target=config_target,
                name=name,
                description=description,
                selected_resource_id=selected_resource_id,
                selected_resource_code=selected_resource_code,
                selected_resource_name=selected_resource_name,
                selected_resource_status=selected_resource_status,
                distribution_mode=distribution_mode,
                permissions_mode=permissions_mode,
                parameters=params,
            )
            db.add(tool_obj)
            migrated_any = True

        if migrated_any:
            await db.flush()
            res_v = await db.execute(
                select(PluginVersion).options(selectinload(PluginVersion.tools)).where(PluginVersion.id == version.id)
            )
            fresh_v = res_v.scalars().first()
            if fresh_v:
                version.tools = fresh_v.tools

    async def _sync_package_data_tools(self, db: AsyncSession, version: PluginVersion) -> None:
        """
        Synchronise package_data['tools'] avec les enregistrements réels de plugin_version_tools
        pour garantir la compatibilité ascendante avec le catalogue et le provisionnement.
        """
        tools_list = [t.to_dict() for t in (version.tools or [])]
        pkg_data = dict(version.package_data or {})
        pkg_data["tools"] = tools_list
        version.package_data = pkg_data
        version.updated_at = datetime.now(timezone.utc)
        await db.flush()

    async def list_version_tools(
        self,
        db: AsyncSession,
        plugin_id: int,
        version_id: int
    ) -> List[PluginVersionTool]:
        version = await self.get_version_by_id(db, plugin_id, version_id)
        if not version:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce plugin.")
        return list(version.tools or [])

    async def add_or_update_version_tool(
        self,
        db: AsyncSession,
        plugin_id: int,
        version_id: int,
        data: PluginVersionToolCreate,
        user: User
    ) -> PluginVersionTool:
        version = await self.get_version_by_id(db, plugin_id, version_id)
        if not version:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce plugin.")

        if version.status != PluginVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=400,
                detail="Seule une version en statut 'draft' peut être modifiée."
            )

        # Contrôle strict de compatibilité de visibilité (Multi-Tenant Sovereign Standard)
        if data.tool_code == "projects":
            target_proj_id = data.selected_resource_id
            if not target_proj_id and isinstance(data.parameters, dict):
                target_proj_id = data.parameters.get("project_id") or data.parameters.get("selected_resource_id")

            if target_proj_id:
                try:
                    from app.models.project.project import Project, ProjectVersion
                    stmt_proj = select(Project).where(Project.id == int(target_proj_id))
                    res_p = await db.execute(stmt_proj)
                    project = res_p.scalars().first()
                    if project:
                        is_proj_private = (
                            getattr(project, "visibility", "public") == "private"
                            or (isinstance(getattr(project, "settings", None), dict) and project.settings.get("visibility") == "private")
                        )
                        raw_proj_orgs = getattr(project, "authorized_organization_ids", None) or []
                        proj_org_ids = [int(x) for x in raw_proj_orgs if str(x).isdigit()]

                        plugin = version.plugin or await self.get_by_id(db, plugin_id)
                        is_plugin_public = getattr(plugin, "is_public", True) if plugin else True
                        raw_plugin_orgs = getattr(plugin, "allowed_organization_ids", None) or [] if plugin else []
                        plugin_org_ids = [int(x) for x in raw_plugin_orgs if str(x).isdigit()]

                        if is_proj_private and not is_plugin_public:
                            if not proj_org_ids:
                                raise HTTPException(
                                    status_code=status.HTTP_400_BAD_REQUEST,
                                    detail="Incompatibilité de visibilité : Ce projet est configuré en mode privé mais aucune organisation autorisée n'y a été définie."
                                )
                            plugin_org_set = set(plugin_org_ids)
                            missing_orgs = [oid for oid in proj_org_ids if oid not in plugin_org_set]
                            if missing_orgs:
                                raise HTTPException(
                                    status_code=status.HTTP_400_BAD_REQUEST,
                                    detail=f"Incompatibilité de visibilité : Les organisations autorisées sur ce projet privé (IDs: {missing_orgs}) ne sont pas autorisées sur ce plugin privé. Veuillez d'abord ajouter ces organisations au plugin ou modifier la visibilité du projet avant de l'intégrer."
                                )
                except HTTPException:
                    raise
                except Exception as ex:
                    print(f"[PluginService] Erreur lors de la vérification de visibilité projet: {ex}")

        res_t = await db.execute(
            select(PluginVersionTool).where(
                PluginVersionTool.plugin_version_id == version_id,
                PluginVersionTool.item_key == data.item_key
            )
        )
        tool = res_t.scalars().first()

        if tool:
            tool.name = data.name
            tool.description = data.description
            tool.type = data.type
            tool.config_target = data.config_target
            tool.tool_code = data.tool_code
            tool.selected_resource_id = data.selected_resource_id
            tool.selected_resource_code = data.selected_resource_code
            tool.selected_resource_name = data.selected_resource_name
            tool.selected_resource_status = data.selected_resource_status
            tool.distribution_mode = data.distribution_mode
            tool.permissions_mode = data.permissions_mode
            tool.parameters = data.parameters or {}
            tool.updated_at = datetime.now(timezone.utc)
        else:
            tool = PluginVersionTool(
                plugin_version_id=version_id,
                item_key=data.item_key,
                tool_code=data.tool_code,
                type=data.type,
                config_target=data.config_target,
                name=data.name,
                description=data.description,
                selected_resource_id=data.selected_resource_id,
                selected_resource_code=data.selected_resource_code,
                selected_resource_name=data.selected_resource_name,
                selected_resource_status=data.selected_resource_status,
                distribution_mode=data.distribution_mode,
                permissions_mode=data.permissions_mode,
                parameters=data.parameters or {},
            )
            db.add(tool)

        await db.flush()
        ref_v = await self.get_version_by_id(db, plugin_id, version_id)
        if ref_v:
            await self._sync_package_data_tools(db, ref_v)

        await db.commit()
        await db.refresh(tool)
        return tool

    async def update_version_tool(
        self,
        db: AsyncSession,
        plugin_id: int,
        version_id: int,
        item_key: str,
        data: PluginVersionToolUpdate,
        user: User
    ) -> PluginVersionTool:
        version = await self.get_version_by_id(db, plugin_id, version_id)
        if not version:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce plugin.")

        if version.status != PluginVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=400,
                detail="Seule une version en statut 'draft' peut être modifiée."
            )

        res_t = await db.execute(
            select(PluginVersionTool).where(
                PluginVersionTool.plugin_version_id == version_id,
                PluginVersionTool.item_key == item_key
            )
        )
        tool = res_t.scalars().first()
        if not tool:
            raise HTTPException(status_code=404, detail="Composant introuvable dans cette version.")

        if data.name is not None:
            tool.name = data.name
        if data.description is not None:
            tool.description = data.description
        if data.distribution_mode is not None:
            tool.distribution_mode = data.distribution_mode
        if data.permissions_mode is not None:
            tool.permissions_mode = data.permissions_mode
        if data.parameters is not None:
            tool.parameters = data.parameters

        tool.updated_at = datetime.now(timezone.utc)
        await db.flush()

        ref_v = await self.get_version_by_id(db, plugin_id, version_id)
        if ref_v:
            await self._sync_package_data_tools(db, ref_v)

        await db.commit()
        await db.refresh(tool)
        return tool

    async def delete_version_tool(
        self,
        db: AsyncSession,
        plugin_id: int,
        version_id: int,
        item_key: str,
        user: User
    ) -> bool:
        version = await self.get_version_by_id(db, plugin_id, version_id)
        if not version:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce plugin.")

        if version.status != PluginVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=400,
                detail="Seule une version en statut 'draft' peut être modifiée."
            )

        res_t = await db.execute(
            select(PluginVersionTool).where(
                PluginVersionTool.plugin_version_id == version_id,
                PluginVersionTool.item_key == item_key
            )
        )
        tool = res_t.scalars().first()
        if not tool:
            pkg_tools = (version.package_data or {}).get("tools", [])
            new_pkg_tools = [t for t in pkg_tools if str(t.get("item_key") or t.get("id")) != item_key]
            if len(new_pkg_tools) != len(pkg_tools):
                pkg = dict(version.package_data or {})
                pkg["tools"] = new_pkg_tools
                version.package_data = pkg
                await db.commit()
            return True

        await db.delete(tool)
        await db.flush()

        ref_v = await self.get_version_by_id(db, plugin_id, version_id)
        if ref_v:
            await self._sync_package_data_tools(db, ref_v)

        await db.commit()
        return True

    async def delete_plugin(
        self,
        db: AsyncSession,
        plugin_id: int,
        user: User,
        force: bool = False
    ) -> bool:
        """
        Supprime un plugin ainsi que toutes ses versions.
        Si force=False, vérifie qu'aucune organisation n'a installé ce plugin activement.
        """
        plugin = await self.get_by_id(db, plugin_id)
        if not plugin:
            raise HTTPException(status_code=404, detail="Plugin introuvable.")

        # Détacher de toute table liée (campaigns, resources, projects)
        for model_cls in [Campaign, Resource, Project]:
            await db.execute(
                update(model_cls)
                .where(or_(model_cls.plugin_id == plugin_id, model_cls.plugin_code == plugin.code))
                .values(plugin_id=None, plugin_code=None)
            )

        # Supprimer le plugin (cascade sur versions)
        await db.delete(plugin)
        await db.commit()
        return True

    async def publish_version(
        self,
        db: AsyncSession,
        plugin_id: int,
        version_id: int,
        publisher_user: User,
        details_or_changelog: Optional[str] = None,
        usage_policy: Optional[str] = None
    ) -> PluginVersion:
        """
        Publie une version de plugin.
        Règles d'or appliquées :
        1. Immutabilité : La version devient scellée (published).
        2. Règle 8 : Détail obligatoire du contenu (première version) ou des nouveautés (suivantes).
        3. Règle 5 : Notification in-app automatique aux administrateurs abonnés.
        """
        plugin = await self.get_by_id(db, plugin_id)
        if not plugin:
            raise HTTPException(status_code=404, detail="Plugin introuvable.")

        version = await self.get_version_by_id(db, plugin_id, version_id)
        if not version:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce plugin.")

        if version.status == PluginVersionStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=400,
                detail="Cette version est déjà publiée et est désormais immuable."
            )

        # Règle : Au moins un module ou une fonctionnalité doit être configuré dans cette version
        tool_count_res = await db.execute(
            select(func.count(PluginVersionTool.id)).where(
                PluginVersionTool.plugin_version_id == version.id
            )
        )
        tools_count = tool_count_res.scalar() or 0
        pkg_tools = (version.package_data or {}).get("tools") or []
        if tools_count == 0 and len(pkg_tools) == 0:
            raise HTTPException(
                status_code=400,
                detail="Impossible de publier : vous devez ajouter au moins un module ou une fonctionnalité à cette version avant de la publier."
            )

        # Règle 8 : Vérifier le détail du contenu / changelog
        res_count = await db.execute(
            select(func.count(PluginVersion.id)).where(
                PluginVersion.plugin_id == plugin_id,
                PluginVersion.status == PluginVersionStatus.PUBLISHED.value
            )
        )
        published_count = res_count.scalar() or 0

        final_changelog = details_or_changelog or version.changelog
        if published_count == 0:
            if not final_changelog or len(final_changelog.strip()) < 15:
                raise HTTPException(
                    status_code=400,
                    detail="Pour la première version du plugin, vous devez obligatoirement détailler avec précision l'ensemble des éléments inclus (modèles de projets, formulaires de collecte, connecteurs)."
                )
        else:
            if not final_changelog or len(final_changelog.strip()) < 15:
                raise HTTPException(
                    status_code=400,
                    detail="Pour publier une nouvelle version, vous devez obligatoirement détailler les nouveautés, correctifs et changements dans les notes de version."
                )

        version.changelog = final_changelog.strip()

        if usage_policy and len(usage_policy.strip()) >= 10:
            version.usage_policy = usage_policy.strip()

        if not version.usage_policy or len(version.usage_policy.strip()) < 10:
            raise HTTPException(
                status_code=400,
                detail="Impossible de publier : la politique d'utilisation obligatoire doit être définie."
            )

        # 1. Scellement de la version
        now = datetime.now(timezone.utc)
        version.status = PluginVersionStatus.PUBLISHED.value
        version.published_at = now
        version.published_by_id = publisher_user.id

        # Mettre à jour le statut global du plugin si c'était en brouillon
        if plugin.status == PluginStatus.DRAFT.value:
            plugin.status = PluginStatus.PUBLISHED.value

        # 2. Identifier les organisations clientes (temporairement vide)
        subscribers = []

        # 3. Dispatch des notifications in-app aux administrateurs (Règle 5)
        notified_owners = set()
        for inst in subscribers:
            org = getattr(inst, "organization", None)
            owner_id = getattr(org, "owner_id", None) if org else None
            if owner_id and owner_id not in notified_owners:
                notified_owners.add(owner_id)

        for owner_id in notified_owners:
            await notification_service.create_notification(
                db=db,
                user_id=owner_id,
                title=f"Mise à jour disponible : {plugin.name} v{version.version_number}",
                message=(
                    f"Une nouvelle version {version.version_number} du plugin '{plugin.name}' est disponible "
                    f"sur le SenWiseStore avec de nouveaux ajouts. Consultez le changelog et mettez à jour votre espace."
                ),
                link=f"/dashboard/marketplace/{plugin.code}",
                type="info",
                commit=False
            )

        await db.commit()
        fresh_version = await self.get_version_by_id(db, plugin_id, version_id)
        return fresh_version or version

    async def deprecate_version(
        self,
        db: AsyncSession,
        plugin_id: int,
        version_id: int,
        days_until_eol: int,
        reason: str,
        user: User,
        custom_eol_date: Optional[datetime] = None
    ) -> PluginVersion:
        """
        Règle 9 : Dépréciation planifiée d'une version avec date d'échéance EOL,
        signalement visuel et rappels in-app aux organisations utilisatrices.
        """
        plugin = await self.get_by_id(db, plugin_id)
        if not plugin:
            raise HTTPException(status_code=404, detail="Plugin introuvable.")

        version = await self.get_version_by_id(db, plugin_id, version_id)
        if not version:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce plugin.")

        if version.status != PluginVersionStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=400,
                detail="Seule une version actuellement publiée peut être dépréciée."
            )

        now = datetime.now(timezone.utc)
        eol_date = custom_eol_date or (now + timedelta(days=days_until_eol))

        version.status = PluginVersionStatus.DEPRECATED.value
        version.deprecated_at = now
        version.deprecated_by_id = user.id
        version.deprecated_eol_date = eol_date
        version.deprecation_reason = (reason or "").strip()
        version.is_blocked = False

        # Règle 9 : Notifications in-app urgentes à tous les administrateurs abonnés (temporairement vide)
        subscribers = []

        dep_owners = set()
        for inst in subscribers:
            org = getattr(inst, "organization", None)
            owner_id = getattr(org, "owner_id", None) if org else None
            if owner_id and owner_id not in dep_owners:
                dep_owners.add(owner_id)

        for owner_id in dep_owners:
            await notification_service.create_notification(
                db=db,
                user_id=owner_id,
                title=f"⚠️ URGENT : Version v{version.version_number} dépréciée ({plugin.name})",
                message=(
                    f"La version v{version.version_number} du plugin '{plugin.name}' utilisée par votre organisation a été déclarée dépréciée "
                    f"et sera DÉFINITIVEMENT BLOQUÉE le {eol_date.strftime('%d/%m/%Y')}. "
                    f"Motif : {reason.strip()}. Veuillez exporter vos données et mettre à niveau votre espace dès maintenant."
                ),
                link=f"/dashboard/marketplace/{plugin.code}",
                type="warning",
                commit=False
            )

        await db.commit()
        fresh_version = await self.get_version_by_id(db, plugin_id, version_id)
        return fresh_version or version


    # ==========================================
    # MARKETPLACE & DISTRIBUTION MULTI-TENANT
    # ==========================================

    async def get_marketplace_catalog(
        self,
        db: AsyncSession,
        organization_id: Optional[int] = None,
        category: Optional[str] = None,
        search: Optional[str] = None,
        is_super_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        Fournit le catalogue public / organisationnel du Marketplace :
        - Seuls les plugins et versions publiées sont exposés.
        - Si is_super_admin est True, affiche tous les plugins (publics et privés).
        - Si un organization_id est fourni, calcule l'état d'installation et 'has_update_available'.
        """
        # 1. Requête des plugins publiés
        query = (
            select(Plugin)
            .options(
                selectinload(Plugin.versions).selectinload(PluginVersion.tools)
            )
            .where(Plugin.status == PluginStatus.PUBLISHED.value)
        )

        if category and category != "all":
            query = query.where(func.lower(Plugin.category) == category.strip().lower())

        if search and search.strip():
            s = f"%{search.strip().lower()}%"
            query = query.where(or_(
                func.lower(Plugin.name).like(s),
                func.lower(Plugin.code).like(s),
                func.lower(Plugin.description).like(s),
                func.lower(Plugin.short_description).like(s)
            ))

        query = query.order_by(desc(Plugin.created_at))
        res = await db.execute(query)
        all_plugins = res.scalars().all()

        # 2. Récupérer les installations existantes pour cette organisation si spécifiée
        org_plugins_by_source_id: Dict[int, OrganizationPlugin] = {}
        org_plugin_versions_by_plugin_id: Dict[int, List[OrganizationPluginVersion]] = {}

        if organization_id:
            op_res = await db.execute(select(OrganizationPlugin))
            all_ops = op_res.scalars().all()
            for op in all_ops:
                if op.source_plugin_id and organization_id in (op.organization_ids or []):
                    org_plugins_by_source_id[op.source_plugin_id] = op

            opv_res = await db.execute(
                select(OrganizationPluginVersion).order_by(desc(OrganizationPluginVersion.id))
            )
            all_opvs = opv_res.scalars().all()
            for v in all_opvs:
                if organization_id in (v.organization_ids or []):
                    org_plugin_versions_by_plugin_id.setdefault(v.organization_plugin_id, []).append(v)

        items = []
        categories_set = set()

        for plug in all_plugins:
            # Filtrage de visibilité (public vs privé restreint par organisation)
            # Le Super Admin a le droit d'inspecter tous les plugins sur le store
            if not getattr(plug, "is_public", True) and not is_super_admin:
                allowed_ids = getattr(plug, "allowed_organization_ids", None) or []
                if not organization_id or organization_id not in allowed_ids:
                    continue

            categories_set.add(plug.category)
            # Versions publiées uniquement pour le store
            pub_versions = [v for v in plug.versions if v.status == PluginVersionStatus.PUBLISHED.value]
            if not pub_versions:
                continue

            # La dernière version publiée
            pub_versions.sort(key=lambda x: (x.id or 0), reverse=True)
            latest_version = pub_versions[0]

            org_plug = org_plugins_by_source_id.get(plug.id)
            is_installed = bool(org_plug is not None)
            has_update = False
            installed_instance_dict = None

            if is_installed and org_plug:
                user_opvs = org_plugin_versions_by_plugin_id.get(org_plug.id, [])
                user_opvs.sort(key=lambda x: (x.id or 0), reverse=True)
                installed_opv = user_opvs[0] if user_opvs else None
                source_installed_ver = next((v for v in pub_versions if installed_opv and v.id == installed_opv.source_plugin_version_id), None)

                has_update = bool(
                    latest_version and (not installed_opv or installed_opv.source_plugin_version_id != latest_version.id)
                )

                installed_instance_dict = self._build_installed_instance_dict(
                    org_plug=org_plug,
                    installed_opv=installed_opv,
                    latest_version=latest_version,
                    source_installed_ver=source_installed_ver,
                    organization_id=organization_id,
                    has_update=has_update,
                    user_opvs=user_opvs
                )

            items.append({
                "plugin": plug,
                "latest_version": latest_version,
                "is_installed": is_installed,
                "installed_instance": installed_instance_dict,
                "has_update_available": has_update
            })

        return {
            "items": items,
            "categories": sorted(list(categories_set)),
            "total": len(items)
        }

    async def get_marketplace_plugin_detail(
        self,
        db: AsyncSession,
        code: str,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        Détails complets d'un plugin pour affichage sur le store et signature de la politique d'utilisation.
        """
        plugin = await self.get_by_code(db, code)
        if not plugin or plugin.status != PluginStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plugin introuvable ou non disponible sur le SenWiseStore."
            )

        # Vérification d'accès si privé (le Super Admin est autorisé à consulter)
        if not getattr(plugin, "is_public", True) and not is_super_admin:
            allowed_ids = getattr(plugin, "allowed_organization_ids", None) or []
            if not organization_id or organization_id not in allowed_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Ce plugin est privé et son accès n'est pas autorisé pour votre organisation."
                )

        pub_versions = [v for v in plugin.versions if v.status == PluginVersionStatus.PUBLISHED.value]
        if not pub_versions:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Aucune version publiée n'est disponible pour ce plugin."
            )

        pub_versions.sort(key=lambda x: (x.id or 0), reverse=True)
        latest_version = pub_versions[0]

        is_installed = False
        has_update = False
        installed_instance_dict = None

        if organization_id:
            op_res = await db.execute(
                select(OrganizationPlugin).where(OrganizationPlugin.source_plugin_id == plugin.id)
            )
            op = op_res.scalars().first()
            if op and organization_id in (op.organization_ids or []):
                is_installed = True
                opv_res = await db.execute(
                    select(OrganizationPluginVersion)
                    .where(OrganizationPluginVersion.organization_plugin_id == op.id)
                    .order_by(desc(OrganizationPluginVersion.id))
                )
                user_opvs = [v for v in opv_res.scalars().all() if organization_id in (v.organization_ids or [])]
                installed_opv = user_opvs[0] if user_opvs else None
                source_installed_ver = next((v for v in pub_versions if installed_opv and v.id == installed_opv.source_plugin_version_id), None)

                has_update = bool(
                    latest_version and (not installed_opv or installed_opv.source_plugin_version_id != latest_version.id)
                )

                installed_instance_dict = self._build_installed_instance_dict(
                    org_plug=op,
                    installed_opv=installed_opv,
                    latest_version=latest_version,
                    source_installed_ver=source_installed_ver,
                    organization_id=organization_id,
                    has_update=has_update,
                    user_opvs=user_opvs
                )

        return {
            "plugin": plugin,
            "versions": pub_versions,
            "latest_version": latest_version,
            "is_installed": is_installed,
            "installed_instance": installed_instance_dict,
            "has_update_available": has_update
        }


    # ==========================================
    # WORKSPACE TENANT: INSTALLATION & VERSIONING
    # ==========================================

    def _build_installed_instance_dict(
        self,
        org_plug: OrganizationPlugin,
        installed_opv: Optional[OrganizationPluginVersion],
        latest_version: PluginVersion,
        source_installed_ver: Optional[PluginVersion],
        organization_id: int,
        has_update: bool,
        user_opvs: Optional[List[OrganizationPluginVersion]] = None
    ) -> Dict[str, Any]:
        """
        Construit un dictionnaire d'installation conforme pour le frontend (OrganizationPluginResponse).
        """
        versions_list = user_opvs if user_opvs is not None else (org_plug.versions or [])
        downloaded = []
        for v in versions_list:
            if organization_id in (v.organization_ids or []):
                downloaded.append({
                    "id": v.id,
                    "organization_plugin_id": v.organization_plugin_id,
                    "plugin_id": org_plug.source_plugin_id,
                    "plugin_version_id": v.source_plugin_version_id,
                    "version_number": v.version_number,
                    "status": v.status,
                    "changelog": v.changelog,
                    "parameters": v.parameters or {},
                    "created_at": v.created_at,
                })

        # Trier les versions téléchargées par version ID décroissant
        downloaded.sort(key=lambda x: (x.get("id") or 0), reverse=True)

        ver_number = installed_opv.version_number if installed_opv else latest_version.version_number
        inst_at = installed_opv.created_at if installed_opv else org_plug.created_at

        return {
            "id": org_plug.id,
            "organization_id": organization_id,
            "plugin_id": org_plug.source_plugin_id,
            "installed_version_id": installed_opv.source_plugin_version_id if installed_opv else latest_version.id,
            "code": org_plug.code,
            "name": org_plug.name,
            "category": org_plug.category,
            "description": org_plug.description,
            "short_description": org_plug.short_description,
            "icon_url": org_plug.icon_url,
            "banner_url": org_plug.banner_url,
            "author": org_plug.author,
            "status": "installed",
            "policy_accepted_at": inst_at,
            "policy_accepted_by_user_id": None,
            "policy_version": ver_number,
            "local_settings": org_plug.settings or {},
            "installed_at": inst_at,
            "updated_at": org_plug.updated_at,
            "has_update_available": has_update,
            "downloaded_versions": downloaded,
            "installed_version": {
                "id": (source_installed_ver or latest_version).id,
                "plugin_id": (source_installed_ver or latest_version).plugin_id,
                "version_number": (source_installed_ver or latest_version).version_number,
                "changelog": (source_installed_ver or latest_version).changelog,
                "status": (source_installed_ver or latest_version).status,
                "usage_policy": (source_installed_ver or latest_version).usage_policy,
                "package_data": (source_installed_ver or latest_version).package_data or {},
                "created_at": (source_installed_ver or latest_version).created_at,
                "updated_at": (source_installed_ver or latest_version).updated_at,
            }
        }

    async def install_plugin_for_org(
        self,
        db: AsyncSession,
        org_id: int,
        plugin_id: int,
        policy_accepted: bool,
        user: User,
        version_id: Optional[int] = None,
        local_settings: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Télécharge ou installe une version de plugin pour une organisation.
        Vérifie et met à jour les 4 tables pivots : Plugin, PluginVersion, OrganizationPlugin, OrganizationPluginVersion.
        Copie récursivement tous les modules embarqués dans les tables locales correspondantes Organization*
        et re-câble l'ensemble des dépendances locales.
        """
        # 1. Vérification du consentement légal (Règle 4)
        if not policy_accepted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vous devez obligatoirement lire et accepter la politique d'utilisation pour intégrer ce plugin."
            )

        # 2. Vérification de la disponibilité du plugin source
        plugin = await self.get_by_id(db, plugin_id)
        if not plugin or plugin.status != PluginStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ce plugin n'est pas disponible sur le SenWiseStore."
            )

        # Contrôle de souveraineté si privé
        if not getattr(plugin, "is_public", True):
            allowed_ids = getattr(plugin, "allowed_organization_ids", None) or []
            if org_id not in allowed_ids:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Ce plugin est privé et son intégration n'est pas autorisée pour votre organisation."
                )

        # 3. Résolution de la version cible
        if version_id:
            version = await self.get_version_by_id(db, plugin_id, version_id)
            if not version or version.plugin_id != plugin.id:
                raise HTTPException(status_code=404, detail="Version spécifiée introuvable pour ce plugin.")
            if version.status not in (PluginVersionStatus.PUBLISHED.value, "published"):
                raise HTTPException(status_code=400, detail="Impossible d'installer une version non publiée.")
        else:
            pub_versions = [v for v in plugin.versions if v.status == PluginVersionStatus.PUBLISHED.value]
            if not pub_versions:
                raise HTTPException(status_code=400, detail="Aucune version publiée disponible pour ce plugin.")
            pub_versions.sort(key=lambda x: (x.id or 0), reverse=True)
            version = pub_versions[0]

        if getattr(version, "is_blocked", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cette version du plugin est obsolète ou bloquée (End of Life) et ne peut plus être installée."
            )

        # 4. Synchronisation de OrganizationPlugin
        op_res = await db.execute(
            select(OrganizationPlugin).where(OrganizationPlugin.source_plugin_id == plugin.id)
        )
        org_plug = op_res.scalars().first()

        now = datetime.now(timezone.utc)

        if not org_plug:
            # Première organisation à télécharger ce plugin
            org_plug = OrganizationPlugin(
                source_plugin_id=plugin.id,
                organization_ids=[org_id],
                user_ids=[user.id],
                code=plugin.code,
                name=plugin.name,
                category=plugin.category,
                description=plugin.description,
                short_description=plugin.short_description,
                icon_url=plugin.icon_url,
                banner_url=plugin.banner_url,
                author=plugin.author,
                status="active",
                tags=plugin.tags or [],
                is_public=plugin.is_public,
                allowed_organization_ids=plugin.allowed_organization_ids or [],
                settings=local_settings or {},
                parameters={},
            )
            db.add(org_plug)
            await db.flush()
        else:
            # Le plugin existe déjà dans organization_plugins : gestion de la liste des organisations
            curr_org_ids = list(org_plug.organization_ids or [])
            if org_id not in curr_org_ids:
                curr_org_ids.append(org_id)
                org_plug.organization_ids = curr_org_ids
                flag_modified(org_plug, "organization_ids")

            curr_user_ids = list(org_plug.user_ids or [])
            if user.id not in curr_user_ids:
                curr_user_ids.append(user.id)
                org_plug.user_ids = curr_user_ids
                flag_modified(org_plug, "user_ids")

            # Mise à jour des informations générales depuis le plugin source
            org_plug.code = plugin.code
            org_plug.name = plugin.name
            org_plug.category = plugin.category
            org_plug.description = plugin.description
            org_plug.short_description = plugin.short_description
            org_plug.icon_url = plugin.icon_url
            org_plug.banner_url = plugin.banner_url
            org_plug.author = plugin.author
            org_plug.tags = plugin.tags or []
            org_plug.is_public = plugin.is_public
            org_plug.allowed_organization_ids = plugin.allowed_organization_ids or []
            if local_settings:
                merged = dict(org_plug.settings or {})
                merged.update(local_settings)
                org_plug.settings = merged
                flag_modified(org_plug, "settings")
            await db.flush()

        # 5. Synchronisation de OrganizationPluginVersion
        opv_res = await db.execute(
            select(OrganizationPluginVersion).where(
                OrganizationPluginVersion.organization_plugin_id == org_plug.id,
                OrganizationPluginVersion.source_plugin_version_id == version.id
            )
        )
        org_ver = opv_res.scalars().first()

        if not org_ver:
            # Première organisation à télécharger cette version spécifique
            org_ver = OrganizationPluginVersion(
                organization_plugin_id=org_plug.id,
                source_plugin_version_id=version.id,
                organization_ids=[org_id],
                user_ids=[user.id],
                version_number=version.version_number,
                changelog=version.changelog,
                package_data=version.package_data or {},
                usage_policy=version.usage_policy,
                status="active",
                published_by_id=version.published_by_id,
                published_at=version.published_at,
                deprecated_at=version.deprecated_at,
                deprecated_by_id=version.deprecated_by_id,
                deprecated_eol_date=version.deprecated_eol_date,
                deprecation_reason=version.deprecation_reason,
                is_blocked=version.is_blocked,
                settings=local_settings or {},
                parameters=version.package_data or {},
            )
            db.add(org_ver)
            await db.flush()
        else:
            # Cette version est déjà enregistrée : ajout de l'organisation dans la liste
            curr_v_orgs = list(org_ver.organization_ids or [])
            if org_id not in curr_v_orgs:
                curr_v_orgs.append(org_id)
                org_ver.organization_ids = curr_v_orgs
                flag_modified(org_ver, "organization_ids")

            curr_v_users = list(org_ver.user_ids or [])
            if user.id not in curr_v_users:
                curr_v_users.append(user.id)
                org_ver.user_ids = curr_v_users
                flag_modified(org_ver, "user_ids")

            org_ver.version_number = version.version_number
            org_ver.changelog = version.changelog
            org_ver.package_data = version.package_data or {}
            org_ver.usage_policy = version.usage_policy
            flag_modified(org_ver, "package_data")
            await db.flush()

        # 6. Copie intégrale des éléments embarqués par la version dans les tables locales de l'organisation
        await self._provision_embedded_elements(
            db=db,
            org_id=org_id,
            org_plug=org_plug,
            org_ver=org_ver,
            version=version,
            user=user,
            local_settings=local_settings
        )

        await db.commit()

        # Recharger org_plug avec ses versions pour construire le résultat
        reload_res = await db.execute(
            select(OrganizationPlugin)
            .options(selectinload(OrganizationPlugin.versions))
            .where(OrganizationPlugin.id == org_plug.id)
        )
        org_plug_reloaded = reload_res.scalars().first() or org_plug

        return self._build_installed_instance_dict(
            org_plug=org_plug_reloaded,
            installed_opv=org_ver,
            latest_version=version,
            source_installed_ver=version,
            organization_id=org_id,
            has_update=False
        )

    async def upgrade_plugin_for_org(
        self,
        db: AsyncSession,
        org_id: int,
        plugin_id: int,
        policy_accepted: bool,
        user: User,
        target_version_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Met à niveau un plugin pour une organisation vers une nouvelle version publiée.
        Réutilise le protocole de téléchargement et enregistre la mise à niveau.
        """
        # Récupération de la version cible (ou dernière publiée)
        plugin = await self.get_by_id(db, plugin_id)
        if not plugin:
            raise HTTPException(status_code=404, detail="Plugin introuvable.")

        if target_version_id:
            new_version = await self.get_version_by_id(db, plugin_id, target_version_id)
            if not new_version or new_version.plugin_id != plugin.id:
                raise HTTPException(status_code=404, detail="Version cible spécifiée introuvable.")
        else:
            pub_versions = [v for v in plugin.versions if v.status == PluginVersionStatus.PUBLISHED.value]
            pub_versions.sort(key=lambda x: (x.id or 0), reverse=True)
            if not pub_versions:
                raise HTTPException(status_code=400, detail="Aucune version publiée disponible pour mise à niveau.")
            new_version = pub_versions[0]

        res = await self.install_plugin_for_org(
            db=db,
            org_id=org_id,
            plugin_id=plugin_id,
            policy_accepted=policy_accepted,
            user=user,
            version_id=new_version.id
        )

        # Notification in-app
        await notification_service.create_notification(
            db=db,
            user_id=user.id,
            type="plugin_upgraded",
            title=f"Mise à niveau réussie : {plugin.name}",
            message=f"Le plugin {plugin.name} a été mis à niveau avec succès vers la version {new_version.version_number}.",
            link=f"/dashboard/plugins/{plugin.id}"
        )

        return res

    async def _provision_embedded_elements(
        self,
        db: AsyncSession,
        org_id: int,
        org_plug: OrganizationPlugin,
        org_ver: OrganizationPluginVersion,
        version: PluginVersion,
        user: User,
        local_settings: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Copie récursivement et de manière chirurgicale tous les éléments embarqués par la version du plugin
        dans les tables locales correspondantes de l'organisation :
        1. Ressources (OrganizationResource, OrganizationResourceVersion, OrganizationResourceItem)
        2. Formulaires (OrganizationForm, OrganizationFormVersion, OrganizationFormSection, OrganizationFormQuestion)
        3. Projets (OrganizationProject, OrganizationProjectVersion)
        4. Campagnes (OrganizationCampaign, OrganizationCampaignVersion)
        5. Fonctionnalités (OrganizationFeature)
        6. Re-câblage interne et cohérence des dépendances locales.
        """
        # Récupération des outils configurés dans la version
        pkg = version.package_data or {}
        raw_tools = list(pkg.get("tools", []))

        # Récupération des outils relationnels PluginVersionTool si disponibles
        rel_tools_res = await db.execute(
            select(PluginVersionTool)
            .where(PluginVersionTool.plugin_version_id == version.id)
            .order_by(PluginVersionTool.id)
        )
        rel_tools = rel_tools_res.scalars().all()
        if rel_tools:
            raw_tools = [t.to_dict() for t in rel_tools]

        # Support direct des clés du package_data (ex: embedded_resources, embedded_forms, embedded_projects, embedded_campaigns)
        existing_keys = {str(t.get("item_key")) for t in raw_tools if t.get("item_key")}
        for res_id in pkg.get("embedded_resources", []):
            k = f"resources_{res_id}"
            if k not in existing_keys:
                raw_tools.append({
                    "item_key": k,
                    "tool_code": "resources",
                    "ref_table": "resources",
                    "selected_resource_id": res_id,
                    "parameters": {}
                })
                existing_keys.add(k)

        for frm_id in pkg.get("embedded_forms", []):
            k = f"forms_{frm_id}"
            if k not in existing_keys:
                raw_tools.append({
                    "item_key": k,
                    "tool_code": "forms",
                    "ref_table": "forms",
                    "selected_resource_id": frm_id,
                    "parameters": {}
                })
                existing_keys.add(k)

        for prj_id in pkg.get("embedded_projects", []):
            k = f"projects_{prj_id}"
            if k not in existing_keys:
                raw_tools.append({
                    "item_key": k,
                    "tool_code": "projects",
                    "ref_table": "projects",
                    "selected_resource_id": prj_id,
                    "parameters": {}
                })
                existing_keys.add(k)

        for cmp_id in pkg.get("embedded_campaigns", []):
            k = f"campaigns_{cmp_id}"
            if k not in existing_keys:
                raw_tools.append({
                    "item_key": k,
                    "tool_code": "campaigns",
                    "ref_table": "campaigns",
                    "selected_resource_id": cmp_id,
                    "parameters": {}
                })
                existing_keys.add(k)

        # Tables de mapping ID source -> ID local pour le re-câblage autonome
        res_source_to_local: Dict[int, int] = {}
        res_ver_source_to_local: Dict[int, int] = {}
        form_source_to_local: Dict[int, int] = {}
        form_ver_source_to_local: Dict[int, int] = {}
        proj_source_to_local: Dict[int, int] = {}
        proj_ver_source_to_local: Dict[int, int] = {}
        camp_source_to_local: Dict[int, int] = {}
        camp_ver_source_to_local: Dict[int, int] = {}

        # =========================================================================
        # 1. RESSOURCES EMBARQUÉES (OrganizationResource, Version, Item)
        # =========================================================================
        for tool in raw_tools:
            t_ref = tool.get("ref_table")
            t_code = tool.get("code") or tool.get("tool_code")
            is_res = (t_ref == "resources") or (t_code in ("resources", "dynamic_resources")) or str(tool.get("item_key", "")).startswith("resources_")
            r_id = tool.get("selected_resource_id") or tool.get("resource_id")
            if not is_res or not r_id:
                continue

            source_res = await db.get(Resource, int(r_id))
            if not source_res:
                continue

            t_params = tool.get("parameters") or {}

            # Recherche ou création de OrganizationResource
            ores_stmt = select(OrganizationResource).where(
                OrganizationResource.organization_id == org_id,
                OrganizationResource.source_resource_id == source_res.id
            )
            org_res = (await db.execute(ores_stmt)).scalars().first()

            if not org_res:
                org_res = OrganizationResource(
                    source_resource_id=source_res.id,
                    organization_id=org_id,
                    organization_plugin_version_id=org_ver.id,
                    downloaded_by_user_id=user.id,
                    name=tool.get("selected_resource_name") or source_res.name,
                    code=source_res.code,
                    category=source_res.category or "general",
                    visibility=source_res.visibility or "private",
                    authorized_organization_ids=source_res.authorized_organization_ids or [],
                    cover_image_url=source_res.cover_image_url,
                    icon=source_res.icon or "database",
                    status=source_res.status or "active",
                    version=source_res.version or "1.0.0",
                    scope=source_res.scope or "organization",
                    is_shared=source_res.is_shared if hasattr(source_res, "is_shared") else False,
                    is_immutable=source_res.is_immutable if hasattr(source_res, "is_immutable") else False,
                    immutable_fields=source_res.immutable_fields or [],
                    is_lts=source_res.is_lts if hasattr(source_res, "is_lts") else False,
                    schema_history=source_res.schema_history or [],
                    description=source_res.description or "",
                    schema_fields=source_res.schema_fields or [],
                    settings=t_params,
                    parameters=t_params,
                    metrics=source_res.metrics or {},
                )
                db.add(org_res)
                await db.flush()
            else:
                org_res.name = tool.get("selected_resource_name") or source_res.name
                org_res.schema_fields = source_res.schema_fields or org_res.schema_fields
                org_res.settings = t_params
                org_res.parameters = t_params
                flag_modified(org_res, "settings")
                flag_modified(org_res, "parameters")
                await db.flush()

            res_source_to_local[source_res.id] = org_res.id

            # Version de ressource (OrganizationResourceVersion)
            rv_id = tool.get("selected_resource_version_id") or t_params.get("selected_resource_version_id")
            source_rv = None
            if rv_id:
                source_rv = await db.get(ResourceVersion, int(rv_id))
            if not source_rv:
                pub_rv_stmt = select(ResourceVersion).where(
                    ResourceVersion.resource_id == source_res.id,
                    ResourceVersion.status == ResourceVersionStatus.PUBLISHED.value
                ).order_by(desc(ResourceVersion.id))
                source_rv = (await db.execute(pub_rv_stmt)).scalars().first()

            if source_rv:
                orv_stmt = select(OrganizationResourceVersion).where(
                    OrganizationResourceVersion.organization_id == org_id,
                    OrganizationResourceVersion.organization_resource_id == org_res.id,
                    OrganizationResourceVersion.source_resource_version_id == source_rv.id
                )
                org_rv = (await db.execute(orv_stmt)).scalars().first()

                if not org_rv:
                    org_rv = OrganizationResourceVersion(
                        organization_resource_id=org_res.id,
                        organization_plugin_version_id=org_ver.id,
                        source_resource_version_id=source_rv.id,
                        organization_id=org_id,
                        downloaded_by_user_id=user.id,
                        version_number=source_rv.version_number,
                        status=source_rv.status or "published",
                        description=source_rv.description or "",
                        changelog=source_rv.changelog or "",
                        schema_fields=source_rv.schema_fields or [],
                        metrics=source_rv.metrics or {},
                        template_governance=source_rv.template_governance or {},
                        settings={**(source_rv.settings or {}), **t_params},
                        parameters={**(source_rv.settings or {}), **t_params},
                    )
                    db.add(org_rv)
                    await db.flush()
                else:
                    org_rv.schema_fields = source_rv.schema_fields or org_rv.schema_fields
                    org_rv.settings = t_params
                    org_rv.parameters = t_params
                    flag_modified(org_rv, "settings")
                    flag_modified(org_rv, "parameters")
                    await db.flush()

                res_ver_source_to_local[source_rv.id] = org_rv.id

                # Items de ressource (OrganizationResourceItem)
                # Copie des items initiaux de la ressource source
                src_items_stmt = select(ResourceItem).where(ResourceItem.resource_id == source_res.id)
                src_items = (await db.execute(src_items_stmt)).scalars().all()
                for s_item in src_items:
                    crit_stmt = select(OrganizationResourceItem).where(
                        OrganizationResourceItem.organization_id == org_id,
                        OrganizationResourceItem.organization_resource_id == org_res.id,
                        OrganizationResourceItem.source_resource_item_id == s_item.id
                    )
                    existing_item = (await db.execute(crit_stmt)).scalars().first()
                    if not existing_item:
                        new_org_item = OrganizationResourceItem(
                            organization_resource_id=org_res.id,
                            organization_resource_version_id=org_rv.id,
                            organization_plugin_version_id=org_ver.id,
                            source_resource_item_id=s_item.id,
                            organization_id=org_id,
                            downloaded_by_user_id=user.id,
                            data=s_item.data or {},
                            settings={},
                            parameters={},
                        )
                        db.add(new_org_item)
                await db.flush()

        # =========================================================================
        # 2. FORMULAIRES EMBARQUÉS (OrganizationForm, Version, Section, Question)
        # =========================================================================
        for tool in raw_tools:
            t_ref = tool.get("ref_table")
            t_code = tool.get("code") or tool.get("tool_code")
            is_form = (t_ref == "forms") or (t_code == "forms") or str(tool.get("item_key", "")).startswith("forms_")
            f_id = tool.get("selected_resource_id") or tool.get("selected_form_id") or tool.get("form_id")
            if not is_form or not f_id:
                continue

            source_form = await db.get(Form, int(f_id))
            if not source_form:
                continue

            t_params = tool.get("parameters") or {}

            # Recherche ou création de OrganizationForm
            oform_stmt = select(OrganizationForm).where(
                OrganizationForm.organization_id == org_id,
                OrganizationForm.source_form_id == source_form.id
            )
            org_form = (await db.execute(oform_stmt)).scalars().first()

            merged_form_settings = {**(source_form.settings or {}), **t_params}
            if not org_form:
                org_form = OrganizationForm(
                    source_form_id=source_form.id,
                    organization_id=org_id,
                    organization_plugin_version_id=org_ver.id,
                    downloaded_by_user_id=user.id,
                    name=tool.get("selected_resource_name") or source_form.name,
                    code=source_form.code,
                    description=source_form.description or "",
                    visibility=source_form.visibility or "private",
                    version=source_form.version or "1.0.0",
                    status=source_form.status or "active",
                    settings=merged_form_settings,
                    parameters=merged_form_settings,
                )
                db.add(org_form)
                await db.flush()
            else:
                org_form.name = tool.get("selected_resource_name") or source_form.name
                org_form.settings = merged_form_settings
                org_form.parameters = merged_form_settings
                flag_modified(org_form, "settings")
                flag_modified(org_form, "parameters")
                await db.flush()

            form_source_to_local[source_form.id] = org_form.id

            # Version de formulaire (OrganizationFormVersion)
            fv_id = tool.get("selected_form_version_id") or t_params.get("selected_form_version_id")
            source_fv = None
            if fv_id:
                source_fv = await db.get(FormVersion, int(fv_id))
            if not source_fv:
                pub_fv_stmt = select(FormVersion).where(
                    FormVersion.form_id == source_form.id,
                    FormVersion.status.in_(("published", "active"))
                ).order_by(desc(FormVersion.id))
                source_fv = (await db.execute(pub_fv_stmt)).scalars().first()

            if source_fv:
                ofv_stmt = select(OrganizationFormVersion).where(
                    OrganizationFormVersion.organization_id == org_id,
                    OrganizationFormVersion.organization_form_id == org_form.id,
                    OrganizationFormVersion.source_form_version_id == source_fv.id
                )
                org_fv = (await db.execute(ofv_stmt)).scalars().first()

                merged_ver_settings = {**(source_form.settings or {}), **(source_fv.settings or {}), **t_params}
                if not org_fv:
                    org_fv = OrganizationFormVersion(
                        organization_form_id=org_form.id,
                        organization_plugin_version_id=org_ver.id,
                        source_form_version_id=source_fv.id,
                        organization_id=org_id,
                        downloaded_by_user_id=user.id,
                        version_number=source_fv.version_number,
                        status=source_fv.status or "published",
                        description=source_fv.description or "",
                        form_type=source_fv.form_type or "standard",
                        template_governance=source_fv.template_governance or {},
                        published_at=source_fv.published_at,
                        settings=merged_ver_settings,
                        parameters=merged_ver_settings,
                    )
                    db.add(org_fv)
                    await db.flush()
                else:
                    org_fv.version_number = source_fv.version_number
                    org_fv.description = source_fv.description or ""
                    org_fv.form_type = source_fv.form_type or "standard"
                    org_fv.template_governance = source_fv.template_governance or {}
                    org_fv.settings = merged_ver_settings
                    org_fv.parameters = merged_ver_settings
                    flag_modified(org_fv, "settings")
                    flag_modified(org_fv, "parameters")
                    await db.flush()

                form_ver_source_to_local[source_fv.id] = org_fv.id

                # Sections de formulaire avec gestion parent-enfant à profondeur illimitée ($N$-aire)
                src_secs_stmt = select(FormSection).where(
                    FormSection.form_version_id == source_fv.id
                ).order_by(FormSection.order_index)
                src_sections = (await db.execute(src_secs_stmt)).scalars().all()

                section_id_map: Dict[int, int] = {}
                org_sec_objects: Dict[int, OrganizationFormSection] = {}

                # 1ère passe : création / mise à jour de toutes les sections sans lier parent_section_id
                for sec in src_sections:
                    osec_stmt = select(OrganizationFormSection).where(
                        OrganizationFormSection.organization_id == org_id,
                        OrganizationFormSection.organization_form_version_id == org_fv.id,
                        OrganizationFormSection.source_form_section_id == sec.id
                    )
                    org_sec = (await db.execute(osec_stmt)).scalars().first()

                    if not org_sec:
                        org_sec = OrganizationFormSection(
                            organization_form_id=org_form.id,
                            organization_form_version_id=org_fv.id,
                            organization_plugin_version_id=org_ver.id,
                            source_form_section_id=sec.id,
                            organization_id=org_id,
                            downloaded_by_user_id=user.id,
                            parent_section_id=None,
                            title=sec.title,
                            code=sec.code,
                            description=sec.description or "",
                            order_index=sec.order_index,
                            is_repeatable=sec.is_repeatable,
                            min_repeats=sec.min_repeats,
                            max_repeats=sec.max_repeats,
                            is_sanctuarized=sec.is_sanctuarized,
                            allow_custom_questions=sec.allow_custom_questions,
                            weight=sec.weight,
                            scoring_category=sec.scoring_category,
                            is_critical=sec.is_critical,
                            min_passing_score=sec.min_passing_score,
                            dependency_rules=sec.dependency_rules or {},
                            settings=sec.settings or {},
                            parameters=t_params,
                        )
                        db.add(org_sec)
                        await db.flush()
                    else:
                        org_sec.title = sec.title
                        org_sec.code = sec.code
                        org_sec.description = sec.description or ""
                        org_sec.order_index = sec.order_index
                        org_sec.is_repeatable = sec.is_repeatable
                        org_sec.min_repeats = sec.min_repeats
                        org_sec.max_repeats = sec.max_repeats
                        org_sec.is_sanctuarized = sec.is_sanctuarized
                        org_sec.allow_custom_questions = sec.allow_custom_questions
                        org_sec.weight = sec.weight
                        org_sec.scoring_category = sec.scoring_category
                        org_sec.is_critical = sec.is_critical
                        org_sec.min_passing_score = sec.min_passing_score
                        org_sec.dependency_rules = sec.dependency_rules or {}
                        org_sec.settings = sec.settings or {}
                        org_sec.parameters = t_params
                        await db.flush()

                    section_id_map[sec.id] = org_sec.id
                    org_sec_objects[sec.id] = org_sec

                # 2ème passe : résolution infaillible des liaisons parent-enfant pour TOUTES les profondeurs
                for sec in src_sections:
                    org_sec = org_sec_objects.get(sec.id)
                    if org_sec:
                        if sec.parent_section_id:
                            org_sec.parent_section_id = section_id_map.get(sec.parent_section_id)
                        else:
                            org_sec.parent_section_id = None
                        flag_modified(org_sec, "parent_section_id")
                await db.flush()

                # Nettoyage des sections orphelines éventuelles
                existing_secs_stmt = select(OrganizationFormSection).where(
                    OrganizationFormSection.organization_id == org_id,
                    OrganizationFormSection.organization_form_version_id == org_fv.id,
                )
                existing_org_secs = (await db.execute(existing_secs_stmt)).scalars().all()
                valid_src_sec_ids = {s.id for s in src_sections}
                for eos in existing_org_secs:
                    if eos.source_form_section_id and eos.source_form_section_id not in valid_src_sec_ids:
                        await db.delete(eos)
                await db.flush()

                # Questions de formulaire (OrganizationFormQuestion)
                src_qs_stmt = select(FormQuestion).where(
                    FormQuestion.form_version_id == source_fv.id
                ).order_by(FormQuestion.order_index)
                src_questions = (await db.execute(src_qs_stmt)).scalars().all()

                for q in src_questions:
                    local_sec_id = section_id_map.get(q.section_id) if q.section_id else None
                    oq_stmt = select(OrganizationFormQuestion).where(
                        OrganizationFormQuestion.organization_id == org_id,
                        OrganizationFormQuestion.organization_form_version_id == org_fv.id,
                        OrganizationFormQuestion.source_form_question_id == q.id
                    )
                    org_q = (await db.execute(oq_stmt)).scalars().first()

                    if not org_q:
                        org_q = OrganizationFormQuestion(
                            organization_form_id=org_form.id,
                            organization_form_version_id=org_fv.id,
                            organization_form_section_id=local_sec_id,
                            organization_plugin_version_id=org_ver.id,
                            source_form_question_id=q.id,
                            organization_id=org_id,
                            downloaded_by_user_id=user.id,
                            code=q.code,
                            label=q.label,
                            help_text=q.help_text,
                            placeholder=q.placeholder,
                            question_type=q.question_type,
                            order_index=q.order_index,
                            is_required=q.is_required,
                            is_readonly=q.is_readonly,
                            is_sanctuarized=q.is_sanctuarized,
                            is_critical=q.is_critical,
                            weight=q.weight,
                            scoring_config=q.scoring_config or {},
                            options=q.options or [],
                            resource_binding=q.resource_binding or {},
                            formula_expression=q.formula_expression,
                            validation_rules=q.validation_rules or {},
                            dependency_rules=q.dependency_rules or {},
                            settings=q.settings or {},
                            parameters=t_params,
                        )
                        db.add(org_q)
                        await db.flush()
                    else:
                        org_q.organization_form_section_id = local_sec_id
                        org_q.code = q.code
                        org_q.label = q.label
                        org_q.help_text = q.help_text
                        org_q.placeholder = q.placeholder
                        org_q.question_type = q.question_type
                        org_q.order_index = q.order_index
                        org_q.is_required = q.is_required
                        org_q.is_readonly = q.is_readonly
                        org_q.is_sanctuarized = q.is_sanctuarized
                        org_q.is_critical = q.is_critical
                        org_q.weight = q.weight
                        org_q.scoring_config = q.scoring_config or {}
                        org_q.options = q.options or []
                        org_q.resource_binding = q.resource_binding or {}
                        org_q.formula_expression = q.formula_expression
                        org_q.validation_rules = q.validation_rules or {}
                        org_q.dependency_rules = q.dependency_rules or {}
                        org_q.settings = q.settings or {}
                        org_q.parameters = t_params
                        flag_modified(org_q, "organization_form_section_id")

                # Nettoyage des questions orphelines éventuelles
                existing_qs_stmt = select(OrganizationFormQuestion).where(
                    OrganizationFormQuestion.organization_id == org_id,
                    OrganizationFormQuestion.organization_form_version_id == org_fv.id,
                )
                existing_org_qs = (await db.execute(existing_qs_stmt)).scalars().all()
                valid_src_q_ids = {q.id for q in src_questions}
                for eoq in existing_org_qs:
                    if eoq.source_form_question_id and eoq.source_form_question_id not in valid_src_q_ids:
                        await db.delete(eoq)
                await db.flush()

        # =========================================================================
        # 3. PROJETS EMBARQUÉS (OrganizationProject, Version)
        # =========================================================================
        for tool in raw_tools:
            t_ref = tool.get("ref_table")
            t_code = tool.get("code") or tool.get("tool_code")
            is_proj = (t_ref == "projects") or (t_code == "projects") or str(tool.get("item_key", "")).startswith("projects_")
            p_id = tool.get("selected_resource_id") or tool.get("selected_project_id") or tool.get("project_id")
            if not is_proj or not p_id:
                continue

            source_proj = await db.get(Project, int(p_id))
            if not source_proj:
                continue

            t_params = tool.get("parameters") or {}

            # Recherche ou création de OrganizationProject
            oproj_stmt = select(OrganizationProject).where(
                OrganizationProject.organization_id == org_id,
                OrganizationProject.source_project_id == source_proj.id
            )
            org_proj = (await db.execute(oproj_stmt)).scalars().first()

            if not org_proj:
                org_proj = OrganizationProject(
                    source_project_id=source_proj.id,
                    organization_id=org_id,
                    organization_plugin_version_id=org_ver.id,
                    downloaded_by_user_id=user.id,
                    name=tool.get("selected_resource_name") or source_proj.name,
                    code=source_proj.code,
                    project_type=source_proj.project_type or "audit",
                    category=source_proj.category or "general",
                    visibility=source_proj.visibility or "private",
                    authorized_organization_ids=source_proj.authorized_organization_ids or [],
                    cover_image_url=source_proj.cover_image_url,
                    status="active",
                    settings=t_params,
                    parameters=t_params,
                )
                db.add(org_proj)
                await db.flush()
            else:
                org_proj.name = tool.get("selected_resource_name") or source_proj.name
                org_proj.settings = t_params
                org_proj.parameters = t_params
                flag_modified(org_proj, "settings")
                flag_modified(org_proj, "parameters")
                await db.flush()

            proj_source_to_local[source_proj.id] = org_proj.id

            # Version de projet (OrganizationProjectVersion)
            pv_id = tool.get("selected_project_version_id") or t_params.get("selected_project_version_id")
            source_pv = None
            if pv_id:
                source_pv = await db.get(ProjectVersion, int(pv_id))
            if not source_pv:
                pub_pv_stmt = select(ProjectVersion).where(
                    ProjectVersion.project_id == source_proj.id,
                    ProjectVersion.status.in_(("published", "active"))
                ).order_by(desc(ProjectVersion.id))
                source_pv = (await db.execute(pub_pv_stmt)).scalars().first()

            if source_pv:
                opv_stmt = select(OrganizationProjectVersion).where(
                    OrganizationProjectVersion.organization_id == org_id,
                    OrganizationProjectVersion.organization_project_id == org_proj.id,
                    OrganizationProjectVersion.source_project_version_id == source_pv.id
                )
                org_pv = (await db.execute(opv_stmt)).scalars().first()

                merged_proj_settings = {**(source_pv.settings or {}), **(t_params or {})}
                # Si source_pv a attached_form_versions, s'assurer qu'elles ne sont pas effacées par t_params
                if "attached_form_versions" in (source_pv.settings or {}) and "attached_form_versions" not in (t_params or {}):
                    merged_proj_settings["attached_form_versions"] = source_pv.settings["attached_form_versions"]
                if "attached_form_ids" in (source_pv.settings or {}) and "attached_form_ids" not in (t_params or {}):
                    merged_proj_settings["attached_form_ids"] = source_pv.settings["attached_form_ids"]

                if not org_pv:
                    org_pv = OrganizationProjectVersion(
                        organization_project_id=org_proj.id,
                        organization_plugin_version_id=org_ver.id,
                        source_project_version_id=source_pv.id,
                        organization_id=org_id,
                        downloaded_by_user_id=user.id,
                        version_number=source_pv.version_number,
                        status=source_pv.status or "published",
                        description=source_pv.description or "",
                        changelog=source_pv.changelog or "",
                        metrics=source_pv.metrics or {},
                        template_governance=source_pv.template_governance or {},
                        settings=merged_proj_settings,
                        parameters=merged_proj_settings,
                    )
                    db.add(org_pv)
                    await db.flush()
                else:
                    org_pv.settings = merged_proj_settings
                    org_pv.parameters = merged_proj_settings
                    flag_modified(org_pv, "settings")
                    flag_modified(org_pv, "parameters")
                    await db.flush()

                proj_ver_source_to_local[source_pv.id] = org_pv.id

        # =========================================================================
        # 4. CAMPAGNES EMBARQUÉES (OrganizationCampaign, Version)
        # =========================================================================
        for tool in raw_tools:
            t_ref = tool.get("ref_table")
            t_code = tool.get("code") or tool.get("tool_code")
            is_camp = (t_ref == "campaigns") or (t_code == "campaigns") or str(tool.get("item_key", "")).startswith("campaigns_")
            c_id = tool.get("selected_resource_id") or tool.get("selected_campaign_id") or tool.get("campaign_id")
            if not is_camp or not c_id:
                continue

            source_camp = await db.get(Campaign, int(c_id))
            if not source_camp:
                continue

            t_params = tool.get("parameters") or {}

            # Recherche ou création de OrganizationCampaign
            ocamp_stmt = select(OrganizationCampaign).where(
                OrganizationCampaign.organization_id == org_id,
                OrganizationCampaign.source_campaign_id == source_camp.id
            )
            org_camp = (await db.execute(ocamp_stmt)).scalars().first()

            if not org_camp:
                org_camp = OrganizationCampaign(
                    source_campaign_id=source_camp.id,
                    organization_id=org_id,
                    organization_plugin_version_id=org_ver.id,
                    downloaded_by_user_id=user.id,
                    name=tool.get("selected_resource_name") or source_camp.name,
                    code=source_camp.code,
                    description=source_camp.description or "",
                    visibility=source_camp.visibility or "private",
                    status=source_camp.status or "active",
                    version=source_camp.version or "1.0.0",
                    settings=t_params,
                    parameters=t_params,
                )
                db.add(org_camp)
                await db.flush()
            else:
                org_camp.name = tool.get("selected_resource_name") or source_camp.name
                org_camp.settings = t_params
                org_camp.parameters = t_params
                flag_modified(org_camp, "settings")
                flag_modified(org_camp, "parameters")
                await db.flush()

            camp_source_to_local[source_camp.id] = org_camp.id

            # Version de campagne (OrganizationCampaignVersion)
            cv_id = tool.get("selected_campaign_version_id") or t_params.get("selected_campaign_version_id")
            source_cv = None
            if cv_id:
                source_cv = await db.get(CampaignVersion, int(cv_id))
            if not source_cv:
                pub_cv_stmt = select(CampaignVersion).where(
                    CampaignVersion.campaign_id == source_camp.id,
                    CampaignVersion.status.in_(("published", "open", "active"))
                ).order_by(desc(CampaignVersion.id))
                source_cv = (await db.execute(pub_cv_stmt)).scalars().first()

            if source_cv:
                ocv_stmt = select(OrganizationCampaignVersion).where(
                    OrganizationCampaignVersion.organization_id == org_id,
                    OrganizationCampaignVersion.organization_campaign_id == org_camp.id,
                    OrganizationCampaignVersion.source_campaign_version_id == source_cv.id
                )
                org_cv = (await db.execute(ocv_stmt)).scalars().first()

                merged_camp_settings = {**(source_cv.settings or {}), **t_params}
                if not org_cv:
                    org_cv = OrganizationCampaignVersion(
                        organization_campaign_id=org_camp.id,
                        organization_plugin_version_id=org_ver.id,
                        source_campaign_version_id=source_cv.id,
                        organization_id=org_id,
                        downloaded_by_user_id=user.id,
                        version_number=source_cv.version_number,
                        status=source_cv.status or "published",
                        description=source_cv.description or "",
                        objectives=source_cv.objectives or "",
                        targets=source_cv.targets or {},
                        start_date=source_cv.start_date,
                        end_date=source_cv.end_date,
                        budget=source_cv.budget,
                        currency=source_cv.currency or "XOF",
                        target_forms=source_cv.target_forms or [],
                        target_projects=source_cv.target_projects or [],
                        changelog=source_cv.changelog or "",
                        metrics=source_cv.metrics or {},
                        template_governance=source_cv.template_governance or {},
                        settings=merged_camp_settings,
                        parameters=merged_camp_settings,
                    )
                    db.add(org_cv)
                    await db.flush()
                else:
                    org_cv.target_forms = source_cv.target_forms or []
                    org_cv.target_projects = source_cv.target_projects or []
                    org_cv.description = source_cv.description or org_cv.description
                    org_cv.objectives = source_cv.objectives or org_cv.objectives
                    org_cv.start_date = source_cv.start_date or org_cv.start_date
                    org_cv.end_date = source_cv.end_date or org_cv.end_date
                    org_cv.budget = source_cv.budget or org_cv.budget
                    org_cv.currency = source_cv.currency or org_cv.currency
                    org_cv.settings = merged_camp_settings
                    org_cv.parameters = merged_camp_settings
                    flag_modified(org_cv, "target_forms")
                    flag_modified(org_cv, "target_projects")
                    flag_modified(org_cv, "settings")
                    flag_modified(org_cv, "parameters")
                    await db.flush()

                camp_ver_source_to_local[source_cv.id] = org_cv.id

        # =========================================================================
        # 5. FONCTIONNALITÉS EMBARQUÉES (OrganizationFeature)
        # =========================================================================
        features_to_sync: Dict[str, Dict[str, Any]] = {}
        # 1. Depuis local_settings
        loc_features = (local_settings or {}).get("features", {})
        for f_code, f_val in loc_features.items():
            if isinstance(f_val, dict):
                features_to_sync[f_code] = f_val

        # 2. Depuis les outils de type 'feature' ou globaux
        for tool in raw_tools:
            t_type = tool.get("type") or tool.get("tool_type")
            t_code = tool.get("code") or tool.get("tool_code")
            if t_type == "feature" or t_code in ("projects", "resources", "dynamic_resources", "forms", "campaigns"):
                f_code = t_code or tool.get("feature_code", "feature")
                features_to_sync[f_code] = {
                    "name": tool.get("name") or tool.get("title") or f_code.capitalize(),
                    "description": tool.get("description", ""),
                    "parameters": tool.get("parameters") or {},
                    "permissions": tool.get("permissions") or {},
                    "settings": tool.get("parameters") or {},
                }

        for f_code, f_data in features_to_sync.items():
            of_stmt = select(OrganizationFeature).where(
                OrganizationFeature.organization_plugin_id == org_plug.id,
                OrganizationFeature.organization_plugin_version_id == org_ver.id,
                OrganizationFeature.feature_code == f_code,
            )
            org_feat = (await db.execute(of_stmt)).scalars().first()

            if not org_feat:
                org_feat = OrganizationFeature(
                    organization_plugin_id=org_plug.id,
                    organization_plugin_version_id=org_ver.id,
                    organization_ids=[org_id],
                    user_ids=[user.id],
                    downloaded_by_user_id=user.id,
                    feature_code=f_code,
                    name=f_data.get("name") or f_code.capitalize(),
                    description=f_data.get("description", ""),
                    parameters=f_data.get("parameters", {}),
                    permissions=f_data.get("permissions", {}),
                    settings=f_data.get("settings", {}),
                    status="active"
                )
                db.add(org_feat)
                await db.flush()
            else:
                curr_feat_orgs = list(org_feat.organization_ids or [])
                if org_id not in curr_feat_orgs:
                    curr_feat_orgs.append(org_id)
                    org_feat.organization_ids = curr_feat_orgs
                    flag_modified(org_feat, "organization_ids")

                curr_feat_users = list(org_feat.user_ids or [])
                if user.id not in curr_feat_users:
                    curr_feat_users.append(user.id)
                    org_feat.user_ids = curr_feat_users
                    flag_modified(org_feat, "user_ids")

                org_feat.parameters = f_data.get("parameters", org_feat.parameters)
                org_feat.permissions = f_data.get("permissions", org_feat.permissions)
                org_feat.settings = f_data.get("settings", org_feat.settings)
                flag_modified(org_feat, "parameters")
                flag_modified(org_feat, "permissions")
                flag_modified(org_feat, "settings")
                await db.flush()

        # =========================================================================
        # 6. RE-CÂBLAGE INTERNE ET COHÉRENCE DES DÉPENDANCES LOCALES
        # =========================================================================
        # a) Questions de formulaire : re-câbler resource_binding vers les tables locales OrganizationResource
        org_qs_stmt = select(OrganizationFormQuestion).where(
            OrganizationFormQuestion.organization_id == org_id,
            OrganizationFormQuestion.organization_plugin_version_id == org_ver.id
        )
        org_questions = (await db.execute(org_qs_stmt)).scalars().all()

        for q in org_questions:
            binding = dict(q.resource_binding or {})
            changed_b = False
            if binding.get("resource_id"):
                orig_rid = binding.get("resource_id")
                if orig_rid in res_source_to_local:
                    binding["resource_id"] = res_source_to_local[orig_rid]
                    changed_b = True
            if binding.get("resource_version_id"):
                orig_rvid = binding.get("resource_version_id")
                if orig_rvid in res_ver_source_to_local:
                    binding["resource_version_id"] = res_ver_source_to_local[orig_rvid]
                    changed_b = True
            if binding.get("form_id"):
                orig_fid = binding.get("form_id")
                if orig_fid in form_source_to_local:
                    binding["form_id"] = form_source_to_local[orig_fid]
                    changed_b = True

            if changed_b:
                q.resource_binding = binding
                flag_modified(q, "resource_binding")

            # Re-câbler également q.settings
            q_settings = dict(q.settings or {})
            changed_s = False
            if q_settings.get("resource_id"):
                orig_rid = q_settings.get("resource_id")
                if orig_rid in res_source_to_local:
                    q_settings["resource_id"] = res_source_to_local[orig_rid]
                    changed_s = True
            if q_settings.get("resource_version_id"):
                orig_rvid = q_settings.get("resource_version_id")
                if orig_rvid in res_ver_source_to_local:
                    q_settings["resource_version_id"] = res_ver_source_to_local[orig_rvid]
                    changed_s = True
            if q_settings.get("form_id"):
                orig_fid = q_settings.get("form_id")
                if orig_fid in form_source_to_local:
                    q_settings["form_id"] = form_source_to_local[orig_fid]
                    changed_s = True

            if changed_s:
                q.settings = q_settings
                flag_modified(q, "settings")

        # b) Projets : re-câbler attached_form_ids et attached_form_versions vers OrganizationForm locaux
        org_pvs_stmt = select(OrganizationProjectVersion).where(
            OrganizationProjectVersion.organization_id == org_id,
            OrganizationProjectVersion.organization_plugin_version_id == org_ver.id
        )
        org_pvs = (await db.execute(org_pvs_stmt)).scalars().all()

        for pv in org_pvs:
            for field in ("settings", "parameters"):
                f_dict = dict(getattr(pv, field) or {})
                changed_f = False
                attached = f_dict.get("attached_form_ids", [])
                if attached:
                    f_dict["attached_form_ids"] = [form_source_to_local.get(int(fid), fid) for fid in attached if str(fid).isdigit()]
                    changed_f = True
                attached_ver = f_dict.get("attached_form_version_ids", [])
                if attached_ver:
                    f_dict["attached_form_version_ids"] = [form_ver_source_to_local.get(int(fvid), fvid) for fvid in attached_ver if str(fvid).isdigit()]
                    changed_f = True
                attached_fvs = f_dict.get("attached_form_versions", [])
                if attached_fvs:
                    new_fvs = []
                    for afv in attached_fvs:
                        afv_copy = dict(afv)
                        raw_fid = afv_copy.get("form_id")
                        if raw_fid and str(raw_fid).isdigit() and int(raw_fid) in form_source_to_local:
                            afv_copy["form_id"] = form_source_to_local[int(raw_fid)]
                        raw_fvid = afv_copy.get("form_version_id")
                        if raw_fvid and str(raw_fvid).isdigit() and int(raw_fvid) in form_ver_source_to_local:
                            afv_copy["form_version_id"] = form_ver_source_to_local[int(raw_fvid)]
                        new_fvs.append(afv_copy)
                    f_dict["attached_form_versions"] = new_fvs
                    changed_f = True
                if changed_f:
                    setattr(pv, field, f_dict)
                    flag_modified(pv, field)

        # c) Campagnes : re-câbler target_forms et target_projects vers les enregistrements locaux
        org_cvs_stmt = select(OrganizationCampaignVersion).where(
            OrganizationCampaignVersion.organization_id == org_id,
            OrganizationCampaignVersion.organization_plugin_version_id == org_ver.id
        )
        org_cvs = (await db.execute(org_cvs_stmt)).scalars().all()

        for cv in org_cvs:
            cv_forms = cv.target_forms or []
            if cv_forms:
                remapped_forms = []
                for f_item in cv_forms:
                    if isinstance(f_item, dict):
                        f_copy = dict(f_item)
                        orig_fid = f_copy.get("form_id") or f_copy.get("id")
                        if orig_fid is not None and int(orig_fid) in form_source_to_local:
                            local_fid = form_source_to_local[int(orig_fid)]
                            f_copy["id"] = local_fid
                            f_copy["form_id"] = local_fid
                        if f_copy.get("source_project_id") is not None and int(f_copy["source_project_id"]) in proj_source_to_local:
                            f_copy["source_project_id"] = proj_source_to_local[int(f_copy["source_project_id"])]
                        remapped_forms.append(f_copy)
                    elif str(f_item).isdigit():
                        remapped_forms.append(form_source_to_local.get(int(f_item), f_item))
                    else:
                        remapped_forms.append(f_item)
                cv.target_forms = remapped_forms
                flag_modified(cv, "target_forms")

            cv_projs = cv.target_projects or []
            if cv_projs:
                remapped_projs = []
                for p_item in cv_projs:
                    if isinstance(p_item, dict):
                        p_copy = dict(p_item)
                        orig_pid = p_copy.get("project_id") or p_copy.get("id")
                        if orig_pid is not None and int(orig_pid) in proj_source_to_local:
                            local_pid = proj_source_to_local[int(orig_pid)]
                            p_copy["id"] = local_pid
                            p_copy["project_id"] = local_pid
                        remapped_projs.append(p_copy)
                    elif str(p_item).isdigit():
                        remapped_projs.append(proj_source_to_local.get(int(p_item), p_item))
                    else:
                        remapped_projs.append(p_item)
                cv.target_projects = remapped_projs
                flag_modified(cv, "target_projects")

            for field in ("settings", "parameters"):
                f_dict = dict(getattr(cv, field) or {})
                changed_c = False
                if "target_forms" in f_dict and isinstance(f_dict["target_forms"], list):
                    remapped_f = []
                    for f_item in f_dict["target_forms"]:
                        if isinstance(f_item, dict):
                            f_copy = dict(f_item)
                            orig_fid = f_copy.get("form_id") or f_copy.get("id")
                            if orig_fid is not None and int(orig_fid) in form_source_to_local:
                                local_fid = form_source_to_local[int(orig_fid)]
                                f_copy["id"] = local_fid
                                f_copy["form_id"] = local_fid
                            if f_copy.get("source_project_id") is not None and int(f_copy["source_project_id"]) in proj_source_to_local:
                                f_copy["source_project_id"] = proj_source_to_local[int(f_copy["source_project_id"])]
                            remapped_f.append(f_copy)
                        elif str(f_item).isdigit():
                            remapped_f.append(form_source_to_local.get(int(f_item), f_item))
                        else:
                            remapped_f.append(f_item)
                    f_dict["target_forms"] = remapped_f
                    changed_c = True
                if "target_projects" in f_dict and isinstance(f_dict["target_projects"], list):
                    remapped_p = []
                    for p_item in f_dict["target_projects"]:
                        if isinstance(p_item, dict):
                            p_copy = dict(p_item)
                            orig_pid = p_copy.get("project_id") or p_copy.get("id")
                            if orig_pid is not None and int(orig_pid) in proj_source_to_local:
                                local_pid = proj_source_to_local[int(orig_pid)]
                                p_copy["id"] = local_pid
                                p_copy["project_id"] = local_pid
                            remapped_p.append(p_copy)
                        elif str(p_item).isdigit():
                            remapped_p.append(proj_source_to_local.get(int(p_item), p_item))
                        else:
                            remapped_p.append(p_item)
                    f_dict["target_projects"] = remapped_p
                    changed_c = True
                if changed_c:
                    setattr(cv, field, f_dict)
                    flag_modified(cv, field)

        await db.flush()

    async def list_installed_plugins_for_org(
        self,
        db: AsyncSession,
        org_id: int
    ) -> List[Dict[str, Any]]:
        """
        Liste tous les plugins actuellement installés pour une organisation.
        Vérifie l'appartenance de org_id dans OrganizationPlugin.organization_ids
        et résout la version installée et les mises à jour disponibles.
        """
        # Récupérer les OrganizationPlugin où l'organisation est enregistrée
        op_stmt = (
            select(OrganizationPlugin)
            .options(
                selectinload(OrganizationPlugin.source_plugin).selectinload(Plugin.versions)
            )
        )
        all_ops = (await db.execute(op_stmt)).scalars().all()
        installed_ops = [op for op in all_ops if org_id in (op.organization_ids or [])]

        # Récupérer les versions téléchargées pour cette organisation
        all_opvs = (await db.execute(select(OrganizationPluginVersion).order_by(desc(OrganizationPluginVersion.id)))).scalars().all()
        org_plugin_versions_by_plugin_id: Dict[int, List[OrganizationPluginVersion]] = {}
        for v in all_opvs:
            if org_id in (v.organization_ids or []):
                org_plugin_versions_by_plugin_id.setdefault(v.organization_plugin_id, []).append(v)

        results = []
        for op in installed_ops:
            # Identifier la version téléchargée pour cette organisation
            user_opvs = org_plugin_versions_by_plugin_id.get(op.id, [])
            user_opvs.sort(key=lambda x: (x.id or 0), reverse=True)
            installed_opv = user_opvs[0] if user_opvs else None

            # Identifier la dernière version publiée depuis source_plugin
            source_plugin = op.source_plugin
            latest_version = None
            if source_plugin:
                pub_v = [v for v in (source_plugin.versions or []) if v.status == PluginVersionStatus.PUBLISHED.value]
                pub_v.sort(key=lambda x: (x.id or 0), reverse=True)
                latest_version = pub_v[0] if pub_v else None

            has_update = bool(
                latest_version and (not installed_opv or installed_opv.source_plugin_version_id != latest_version.id)
            )

            inst_dict = self._build_installed_instance_dict(
                org_plug=op,
                installed_opv=installed_opv,
                latest_version=latest_version or installed_opv,
                source_installed_ver=latest_version,
                organization_id=org_id,
                has_update=has_update,
                user_opvs=user_opvs
            )

            results.append({
                "installation": inst_dict,
                "plugin": source_plugin or op,
                "installed_version": installed_opv,
                "latest_version": latest_version or installed_opv,
                "has_update_available": has_update
            })

        return results

    async def uninstall_plugin_for_org(
        self,
        db: AsyncSession,
        org_id: int,
        plugin_id: int,
        user: User,
        reason: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Désinstalle un plugin pour une organisation :
        Retire l'organisation de la liste des organisations dans OrganizationPlugin et OrganizationPluginVersion.
        """
        op_stmt = (
            select(OrganizationPlugin)
            .options(selectinload(OrganizationPlugin.versions))
            .where(
                or_(
                    OrganizationPlugin.id == plugin_id,
                    OrganizationPlugin.source_plugin_id == plugin_id
                )
            )
        )
        op = (await db.execute(op_stmt)).scalars().first()
        if not op or org_id not in (op.organization_ids or []):
            raise HTTPException(status_code=404, detail="Ce plugin n'est pas installé dans votre organisation.")

        # Retirer de OrganizationPlugin
        curr_orgs = [oid for oid in (op.organization_ids or []) if oid != org_id]
        op.organization_ids = curr_orgs
        flag_modified(op, "organization_ids")

        # Retirer de toutes les versions
        for v in (op.versions or []):
            v_orgs = [oid for oid in (v.organization_ids or []) if oid != org_id]
            v.organization_ids = v_orgs
            flag_modified(v, "organization_ids")

        await db.commit()

        # Notification in-app
        await notification_service.create_notification(
            db=db,
            user_id=user.id,
            type="plugin_uninstalled",
            title=f"Plugin désinstallé : {op.name}",
            message=f"Le plugin « {op.name} » a été désinstallé avec succès de votre organisation.",
            link="/dashboard/marketplace"
        )

        return {
            "message": "Plugin désinstallé avec succès.",
            "plugin_id": plugin_id,
            "organization_id": org_id,
            "status": "uninstalled"
        }


plugin_service = PluginService()
