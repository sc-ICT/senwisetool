from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, desc, func, or_, and_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.plugin.plugin import (
    Plugin,
    PluginVersion,
    OrganizationPlugin,
    PluginStatus,
    PluginVersionStatus,
    OrgPluginStatus,
)
from app.models.organization.organization import Organization
from app.models.user.user import User
from app.schemas.plugin.plugin import (
    PluginCreate,
    PluginUpdate,
    PluginVersionCreate,
    PluginVersionUpdate,
)
from app.services.notification.notification_service import notification_service


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
        )
        db.add(plugin)
        await db.flush()

        # Création automatique de la version initiale si spécifiée
        if data.initial_version_number and data.initial_usage_policy:
            initial_version = PluginVersion(
                plugin_id=plugin.id,
                version_number=data.initial_version_number.strip(),
                changelog=data.initial_changelog or "Version initiale de démarrage",
                package_data=data.initial_package_data or {},
                usage_policy=data.initial_usage_policy.strip(),
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
                selectinload(Plugin.versions),
                selectinload(Plugin.installations),
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
                selectinload(Plugin.versions),
                selectinload(Plugin.installations),
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
        Récupère une version spécifique d'un plugin par son ID.
        """
        stmt = select(PluginVersion).where(
            PluginVersion.id == version_id,
            PluginVersion.plugin_id == plugin_id
        )
        res = await db.execute(stmt)
        return res.scalars().first()

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
            selectinload(Plugin.versions),
            selectinload(Plugin.installations),
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

        if not data.usage_policy or len(data.usage_policy.strip()) < 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La politique d'utilisation obligatoire (EULA) doit être rédigée et détaillée pour chaque version."
            )

        version = PluginVersion(
            plugin_id=plugin_id,
            version_number=clean_version,
            changelog=data.changelog,
            package_data=data.package_data or {},
            usage_policy=data.usage_policy.strip(),
            status=PluginVersionStatus.DRAFT.value,
        )
        db.add(version)
        await db.commit()
        await db.refresh(version)
        return version

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
        await db.refresh(version)
        return version

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

        res_v = await db.execute(
            select(PluginVersion).where(
                PluginVersion.id == version_id,
                PluginVersion.plugin_id == plugin_id
            )
        )
        version = res_v.scalars().first()
        if not version:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce plugin.")

        if version.status == PluginVersionStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=400,
                detail="Cette version est déjà publiée et est désormais immuable."
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

        # 2. Identifier les organisations clientes utilisant une version précédente
        subscribers_res = await db.execute(
            select(OrganizationPlugin)
            .options(selectinload(OrganizationPlugin.organization))
            .where(
                OrganizationPlugin.plugin_id == plugin_id,
                OrganizationPlugin.status == OrgPluginStatus.INSTALLED.value,
                OrganizationPlugin.installed_version_id != version.id
            )
        )
        subscribers = subscribers_res.scalars().all()

        # 3. Dispatch des notifications in-app aux administrateurs (Règle 5)
        notified_owners = set()
        for inst in subscribers:
            if inst.organization and inst.organization.owner_id and inst.organization.owner_id not in notified_owners:
                notified_owners.add(inst.organization.owner_id)
                await notification_service.create_notification(
                    db=db,
                    user_id=inst.organization.owner_id,
                    title=f"Mise à jour disponible : {plugin.name} v{version.version_number}",
                    message=(
                        f"Une nouvelle version {version.version_number} du plugin '{plugin.name}' est disponible "
                        f"sur le Marketplace avec de nouveaux ajouts. Consultez le changelog et mettez à jour votre espace."
                    ),
                    link=f"/dashboard/marketplace/{plugin.code}",
                    type="info"
                )

        await db.commit()
        await db.refresh(version)
        return version

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

        res_v = await db.execute(
            select(PluginVersion).where(
                PluginVersion.id == version_id,
                PluginVersion.plugin_id == plugin_id
            )
        )
        version = res_v.scalars().first()
        if not version:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce plugin.")

        if version.status != PluginVersionStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=400,
                detail="Seule une version actuellement publiée peut être dépréciée."
            )

        now = datetime.now(timezone.utc)

        # RÈGLE 10 : Vérifier qu'aucun processus de dépréciation n'est déjà en cours pour ce plugin
        res_dep = await db.execute(
            select(PluginVersion).where(
                PluginVersion.plugin_id == plugin_id,
                PluginVersion.status == PluginVersionStatus.DEPRECATED.value,
                PluginVersion.is_blocked.is_(False)
            )
        )
        deprecated_versions = res_dep.scalars().all()
        for dep_ver in deprecated_versions:
            eol = dep_ver.deprecated_eol_date
            if eol:
                eol_aware = eol if eol.tzinfo else eol.replace(tzinfo=timezone.utc)
                if now < eol_aware:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Un processus de dépréciation est déjà en cours pour la version v{dep_ver.version_number} "
                            f"(fin de vie prévue le {eol_aware.strftime('%d/%m/%Y')}). Vous ne pouvez pas lancer un second "
                            f"processus de dépréciation tant que celui-ci n'est pas terminé et que la version n'est pas désactivée."
                        )
                    )

        # RÈGLE 10 : On ne peut déprécier que la version publiée la plus ancienne
        res_pub = await db.execute(
            select(PluginVersion)
            .where(
                PluginVersion.plugin_id == plugin_id,
                PluginVersion.status == PluginVersionStatus.PUBLISHED.value
            )
            .order_by(PluginVersion.created_at.asc(), PluginVersion.id.asc())
        )
        published_versions = list(res_pub.scalars().all())

        if len(published_versions) < 2:
            raise HTTPException(
                status_code=400,
                detail="Impossible de déprécier l'unique version publiée du plugin. Vous devez d'abord publier une version plus récente vers laquelle les organisations pourront migrer."
            )

        oldest_published = published_versions[0]
        if version.id != oldest_published.id:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Seule la version publiée la plus ancienne (v{oldest_published.version_number}) peut être dépréciée. "
                    f"Vous ne pouvez pas déprécier la version v{version.version_number} tant que les versions antérieures "
                    f"n'ont pas achevé leur cycle de dépréciation."
                )
            )

        eol_date = custom_eol_date or (now + timedelta(days=days_until_eol))

        version.status = PluginVersionStatus.DEPRECATED.value
        version.deprecated_at = now
        version.deprecated_by_id = user.id
        version.deprecated_eol_date = eol_date
        version.deprecation_reason = reason.strip()
        version.is_blocked = False

        # Règle 9 : Notifications in-app urgentes à tous les administrateurs abonnés
        subscribers_res = await db.execute(
            select(OrganizationPlugin)
            .options(selectinload(OrganizationPlugin.organization))
            .where(
                OrganizationPlugin.plugin_id == plugin_id,
                OrganizationPlugin.status == OrgPluginStatus.INSTALLED.value,
                OrganizationPlugin.installed_version_id == version.id
            )
        )
        subscribers = subscribers_res.scalars().all()

        for inst in subscribers:
            if inst.organization and inst.organization.owner_id:
                await notification_service.create_notification(
                    db=db,
                    user_id=inst.organization.owner_id,
                    title=f"⚠️ URGENT : Version v{version.version_number} dépréciée ({plugin.name})",
                    message=(
                        f"La version v{version.version_number} du plugin '{plugin.name}' utilisée par votre organisation a été déclarée dépréciée "
                        f"et sera DÉFINITIVEMENT BLOQUÉE le {eol_date.strftime('%d/%m/%Y')}. "
                        f"Motif : {reason.strip()}. Veuillez exporter vos données et mettre à niveau votre espace dès maintenant."
                    ),
                    link=f"/dashboard/marketplace/{plugin.code}",
                    type="warning"
                )

        await db.commit()
        await db.refresh(version)
        return version


    # ==========================================
    # MARKETPLACE & DISTRIBUTION MULTI-TENANT
    # ==========================================

    async def get_marketplace_catalog(
        self,
        db: AsyncSession,
        organization_id: Optional[int] = None,
        category: Optional[str] = None,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Fournit le catalogue public / organisationnel du Marketplace :
        - Seuls les plugins et versions publiées sont exposés.
        - Si un organization_id est fourni, calcule l'état d'installation et 'has_update_available'.
        """
        # 1. Requête des plugins publiés
        query = (
            select(Plugin)
            .options(
                selectinload(Plugin.versions),
                selectinload(Plugin.installations)
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
        org_installations: Dict[int, OrganizationPlugin] = {}
        if organization_id:
            inst_res = await db.execute(
                select(OrganizationPlugin)
                .options(
                    selectinload(OrganizationPlugin.installed_version),
                    selectinload(OrganizationPlugin.plugin)
                )
                .where(OrganizationPlugin.organization_id == organization_id)
            )
            for inst in inst_res.scalars().all():
                org_installations[inst.plugin_id] = inst

        items = []
        categories_set = set()

        for plug in all_plugins:
            categories_set.add(plug.category)
            # Versions publiées uniquement pour le store
            pub_versions = [v for v in plug.versions if v.status == PluginVersionStatus.PUBLISHED.value]
            if not pub_versions:
                continue

            # La dernière version publiée
            pub_versions.sort(key=lambda x: (x.id or 0), reverse=True)
            latest_version = pub_versions[0]

            inst = org_installations.get(plug.id)
            is_installed = bool(inst and inst.status == OrgPluginStatus.INSTALLED.value)
            has_update = bool(is_installed and inst.installed_version_id != latest_version.id)

            items.append({
                "plugin": plug,
                "latest_version": latest_version,
                "is_installed": is_installed,
                "installed_instance": inst,
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
        organization_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Détails complets d'un plugin pour affichage sur le store et signature de la politique d'utilisation.
        """
        plugin = await self.get_by_code(db, code)
        if not plugin or plugin.status != PluginStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Plugin introuvable ou non disponible sur le Marketplace."
            )

        pub_versions = [v for v in plugin.versions if v.status == PluginVersionStatus.PUBLISHED.value]
        if not pub_versions:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Aucune version publiée n'est disponible pour ce plugin."
            )

        pub_versions.sort(key=lambda x: (x.id or 0), reverse=True)
        latest_version = pub_versions[0]

        inst = None
        is_installed = False
        has_update = False

        if organization_id:
            inst_res = await db.execute(
                select(OrganizationPlugin)
                .options(selectinload(OrganizationPlugin.installed_version))
                .where(
                    OrganizationPlugin.organization_id == organization_id,
                    OrganizationPlugin.plugin_id == plugin.id
                )
            )
            inst = inst_res.scalars().first()
            is_installed = bool(inst and inst.status == OrgPluginStatus.INSTALLED.value)
            has_update = bool(is_installed and inst.installed_version_id != latest_version.id)

        return {
            "plugin": plugin,
            "versions": pub_versions,
            "latest_version": latest_version,
            "is_installed": is_installed,
            "installed_instance": inst,
            "has_update_available": has_update
        }

    # ==========================================
    # WORKSPACE TENANT: INSTALLATION & VERSIONING
    # ==========================================

    async def install_plugin_for_org(
        self,
        db: AsyncSession,
        org_id: int,
        plugin_id: int,
        policy_accepted: bool,
        user: User,
        version_id: Optional[int] = None,
        local_settings: Optional[Dict[str, Any]] = None
    ) -> OrganizationPlugin:
        """
        Installe un plugin dans l'organisation cliente.
        Règles d'or appliquées :
        - Règle 4 : Consentement explicite obligatoire à la politique d'utilisation.
        - Règle 3 : Installation unique par organisation.
        - Règle 2 : Pinned execution sur la version choisie (ou dernière publiée).
        """
        # Règle 4 : Vérification stricte du consentement
        if not policy_accepted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vous devez obligatoirement lire et accepter la politique d'utilisation pour intégrer ce plugin."
            )

        plugin = await self.get_by_id(db, plugin_id)
        if not plugin or plugin.status != PluginStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ce plugin n'est pas disponible sur le Marketplace."
            )

        # Déterminer la version à installer
        if version_id:
            res_v = await db.execute(
                select(PluginVersion).where(
                    PluginVersion.id == version_id,
                    PluginVersion.plugin_id == plugin_id,
                    PluginVersion.status == PluginVersionStatus.PUBLISHED.value
                )
            )
            target_version = res_v.scalars().first()
            if not target_version:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="La version demandée est introuvable ou n'est pas encore publiée."
                )
        else:
            pub_versions = [v for v in plugin.versions if v.status == PluginVersionStatus.PUBLISHED.value]
            if not pub_versions:
                raise HTTPException(status_code=400, detail="Aucune version publiée disponible pour ce plugin.")
            pub_versions.sort(key=lambda x: (x.id or 0), reverse=True)
            target_version = pub_versions[0]

        now = datetime.now(timezone.utc)
        eol_target = target_version.deprecated_eol_date
        if eol_target and eol_target.tzinfo is None:
            eol_target = eol_target.replace(tzinfo=timezone.utc)

        if target_version.is_blocked or (eol_target and now >= eol_target):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La version v{target_version.version_number} a atteint sa date de fin de vie (EOL) et est définitivement bloquée. Elle ne peut plus être intégrée."
            )

        # Règle 3 : Vérifier l'installation unique

        res_inst = await db.execute(
            select(OrganizationPlugin).where(
                OrganizationPlugin.organization_id == org_id,
                OrganizationPlugin.plugin_id == plugin_id
            )
        )
        existing_inst = res_inst.scalars().first()

        now = datetime.now(timezone.utc)

        if existing_inst:
            if existing_inst.status == OrgPluginStatus.INSTALLED.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ce plugin est déjà intégré et actif dans votre espace de travail. Une organisation ne peut intégrer un même plugin qu'une seule fois."
                )
            # Réactivation d'un plugin précédemment désinstallé
            existing_inst.status = OrgPluginStatus.INSTALLED.value
            existing_inst.installed_version_id = target_version.id
            existing_inst.policy_accepted_at = now
            existing_inst.policy_accepted_by_user_id = user.id
            existing_inst.policy_version = target_version.version_number
            existing_inst.local_settings = local_settings or existing_inst.local_settings or {}
            existing_inst.updated_at = now
            inst = existing_inst
        else:
            inst = OrganizationPlugin(
                organization_id=org_id,
                plugin_id=plugin_id,
                installed_version_id=target_version.id,
                status=OrgPluginStatus.INSTALLED.value,
                policy_accepted_at=now,
                policy_accepted_by_user_id=user.id,
                policy_version=target_version.version_number,
                local_settings=local_settings or {},
            )
            db.add(inst)

        # Notification in-app de confirmation
        await notification_service.create_notification(
            db=db,
            user_id=user.id,
            title=f"Plugin intégré avec succès : {plugin.name}",
            message=f"Le plugin '{plugin.name}' (version {target_version.version_number}) est désormais opérationnel dans votre espace de travail.",
            link=f"/dashboard/marketplace/{plugin.code}",
            type="success"
        )

        await db.commit()
        await db.refresh(inst)

        # Provisionnement automatique des outils préconfigurés (Mode C : Templates préconfigurés & immuables)
        from app.services.campaign.campaign_service import campaign_service
        try:
            await campaign_service.provision_plugin_templates(
                db=db,
                org_id=org_id,
                plugin=plugin,
                version=target_version,
                user=user
            )
        except Exception as e:
            logger.warning(f"[TOOL_PROVISION_WARNING] Erreur mineure lors du provisionnement des outils du plugin {plugin.code}: {e}")

        return inst

    async def upgrade_plugin_for_org(
        self,
        db: AsyncSession,
        org_id: int,
        plugin_id: int,
        target_version_id: int,
        policy_accepted: bool,
        user: User
    ) -> OrganizationPlugin:
        """
        Met à niveau la version installée d'un plugin pour une organisation.
        Règles d'or appliquées :
        - Règle 2 : Pinned execution préservée jusqu'à mise à niveau volontaire.
        - Règle 4 : Validation de la politique d'utilisation de la nouvelle version.
        - Règle 6 : Non destructif (garde les paramètres locaux et l'historique de données).
        """
        if not policy_accepted:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vous devez lire et accepter la politique d'utilisation de cette nouvelle version pour procéder à la mise à niveau."
            )

        # Vérifier l'installation existante
        res_inst = await db.execute(
            select(OrganizationPlugin).where(
                OrganizationPlugin.organization_id == org_id,
                OrganizationPlugin.plugin_id == plugin_id
            )
        )
        inst = res_inst.scalars().first()
        if not inst or inst.status != OrgPluginStatus.INSTALLED.value:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ce plugin n'est pas actuellement installé dans votre organisation."
            )

        if inst.installed_version_id == target_version_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Votre organisation utilise déjà cette version exacte du plugin."
            )

        # Vérifier que la version cible existe et est publiée
        res_v = await db.execute(
            select(PluginVersion).where(
                PluginVersion.id == target_version_id,
                PluginVersion.plugin_id == plugin_id,
                PluginVersion.status == PluginVersionStatus.PUBLISHED.value
            )
        )
        target_version = res_v.scalars().first()
        if not target_version:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="La version cible est introuvable ou n'est pas encore publiée."
            )

        now = datetime.now(timezone.utc)
        eol_target = target_version.deprecated_eol_date
        if eol_target and eol_target.tzinfo is None:
            eol_target = eol_target.replace(tzinfo=timezone.utc)

        if target_version.is_blocked or (eol_target and now >= eol_target):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La version v{target_version.version_number} a atteint sa date de fin de vie (EOL) et est définitivement bloquée. Vous devez migrer vers une version plus récente."
            )


        # Récupérer le nom du plugin pour la notification
        plugin = await self.get_by_id(db, plugin_id)

        now = datetime.now(timezone.utc)
        inst.installed_version_id = target_version.id
        inst.policy_accepted_at = now
        inst.policy_accepted_by_user_id = user.id
        inst.policy_version = target_version.version_number
        inst.updated_at = now

        # Notification in-app de succès de mise à jour
        await notification_service.create_notification(
            db=db,
            user_id=user.id,
            title=f"Mise à niveau réussie : {plugin.name if plugin else 'Plugin'}",
            message=f"Votre espace de travail a été mis à niveau avec succès vers la version {target_version.version_number}.",
            link=f"/dashboard/marketplace/{plugin.code if plugin else ''}",
            type="success"
        )

        await db.commit()
        await db.refresh(inst)
        return inst

    async def uninstall_plugin_for_org(
        self,
        db: AsyncSession,
        org_id: int,
        plugin_id: int,
        user: User,
        reason: Optional[str] = None
    ) -> OrganizationPlugin:
        """
        Désinstallation douce d'un plugin (Soft Uninstall - Règle 6).
        Désactive l'accès opérationnel tout en préservant l'intégrité des données d'audit.
        """
        res_inst = await db.execute(
            select(OrganizationPlugin).where(
                OrganizationPlugin.organization_id == org_id,
                OrganizationPlugin.plugin_id == plugin_id
            )
        )
        inst = res_inst.scalars().first()
        if not inst or inst.status == OrgPluginStatus.UNINSTALLED.value:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ce plugin n'est pas actif dans votre organisation."
            )

        plugin = await self.get_by_id(db, plugin_id)

        inst.status = OrgPluginStatus.UNINSTALLED.value
        inst.updated_at = datetime.now(timezone.utc)

        await notification_service.create_notification(
            db=db,
            user_id=user.id,
            title=f"Plugin retiré : {plugin.name if plugin else 'Plugin'}",
            message=f"Le plugin '{plugin.name if plugin else ''}' a été désinstallé de vos espaces de travail. Vous pourrez le réintégrer à tout moment.",
            link="/dashboard/marketplace",
            type="info"
        )

        await db.commit()
        await db.refresh(inst)
        return inst

    async def list_installed_plugins_for_org(
        self,
        db: AsyncSession,
        org_id: int
    ) -> List[Dict[str, Any]]:
        """
        Liste tous les plugins actuellement installés et actifs pour une organisation.
        """
        res = await db.execute(
            select(OrganizationPlugin)
            .options(
                selectinload(OrganizationPlugin.plugin).selectinload(Plugin.versions),
                selectinload(OrganizationPlugin.installed_version)
            )
            .where(
                OrganizationPlugin.organization_id == org_id,
                OrganizationPlugin.status == OrgPluginStatus.INSTALLED.value
            )
            .order_by(desc(OrganizationPlugin.installed_at))
        )
        installations = res.scalars().all()

        results = []
        for inst in installations:
            plug = inst.plugin
            pub_versions = [v for v in plug.versions if v.status == PluginVersionStatus.PUBLISHED.value]
            pub_versions.sort(key=lambda x: (x.id or 0), reverse=True)
            latest_version = pub_versions[0] if pub_versions else inst.installed_version

            has_update = bool(latest_version and inst.installed_version_id != latest_version.id)

            results.append({
                "installation": inst,
                "plugin": plug,
                "installed_version": inst.installed_version,
                "latest_version": latest_version,
                "has_update_available": has_update
            })

        return results


plugin_service = PluginService()
