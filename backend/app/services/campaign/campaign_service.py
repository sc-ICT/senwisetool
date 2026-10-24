import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc
from sqlalchemy.orm import selectinload

from app.models.campaign.campaign import Campaign, CampaignStatus, CampaignType
from app.models.organization.organization import Organization
from app.models.organization.member import OrganizationMember
from app.models.organization.role import OrganizationRole
from app.models.plugin.plugin import Plugin, OrganizationPlugin, OrgPluginStatus
from app.models.user.user import User, UserRole
from app.schemas.campaign.campaign import (
    CampaignCreate,
    CampaignUpdate,
    CampaignStatusUpdate,
)
from app.services.organization.organization_service import organization_service
from app.services.notification.notification_service import notification_service

logger = logging.getLogger(__name__)


class CampaignService:

    async def _resolve_user_org_and_permissions(
        self,
        db: AsyncSession,
        user: User,
        action: str,  # "view", "create_edit", "delete"
        requested_org_id: Optional[int] = None
    ) -> Tuple[Optional[Organization], bool]:
        """
        Résout souverainement l'organisation rattachée et vérifie les permissions RBAC pour l'action demandée.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"

        # Super Admin peut opérer sur une organisation spécifique ou globalement
        if is_super_admin and requested_org_id:
            org_res = await db.execute(select(Organization).where(Organization.id == requested_org_id))
            org = org_res.scalars().first()
            if not org:
                raise HTTPException(status_code=404, detail="Organisation introuvable.")
            return org, True

        org = await organization_service.get_user_organization(db, user)
        if not org and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous devez être rattaché à une organisation active pour accéder aux campagnes."
            )

        if is_super_admin:
            return org, True

        is_owner = org.owner_id == user.id or (org.contact_email and org.contact_email.lower() == user.email.lower())
        is_org_admin = user.role == UserRole.ADMIN_ORG or str(user.role).upper() == "ADMIN_ORG"

        if is_owner or is_org_admin:
            return org, True

        # Contrôle des permissions des collaborateurs via OrganizationMember
        mem_res = await db.execute(
            select(OrganizationMember)
            .options(selectinload(OrganizationMember.role))
            .where(
                OrganizationMember.organization_id == org.id,
                or_(
                    OrganizationMember.user_id == user.id,
                    func.lower(OrganizationMember.email) == user.email.lower()
                )
            )
        )
        member = mem_res.scalars().first()
        if not member:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous n'êtes pas enregistré comme membre actif de cette organisation."
            )

        # Vérifier dans le rôle du membre
        has_permission = False
        r = member.role
        if not r and member.role_code:
            r_res = await db.execute(
                select(OrganizationRole).where(
                    OrganizationRole.organization_id == org.id,
                    OrganizationRole.code == member.role_code
                )
            )
            r = r_res.scalars().first()

        if r:
            perms = r.permissions or {}
            c_perms = perms.get("campaigns", {})
            val = c_perms.get(action, "inactive")
            if action == "view" and val in ["active", "partial"]:
                has_permission = True
            elif action == "create_edit" and val in ["active", "partial"]:
                has_permission = True
            elif action == "delete" and val == "active":
                has_permission = True

        if not has_permission:
            action_labels = {
                "view": "consulter les campagnes",
                "create_edit": "créer ou modifier des campagnes",
                "delete": "supprimer des campagnes"
            }
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permissions insuffisantes pour {action_labels.get(action, action)}."
            )

        return org, True

    async def list_campaigns(
        self,
        db: AsyncSession,
        user: User,
        status_filter: Optional[str] = None,
        campaign_type: Optional[str] = None,
        plugin_code: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
        org_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Liste les campagnes accessibles :
        - Les campagnes propres à l'organisation (tenant-owned ou preconfigured)
        - ET les campagnes partagées (is_shared == True) des plugins actifs installés dans l'organisation.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        org, _ = await self._resolve_user_org_and_permissions(db, user, action="view", requested_org_id=org_id)
        target_org_id = org.id if org else None

        query = select(Campaign).order_by(desc(Campaign.created_at))
        count_stmt = select(func.count(Campaign.id))
        open_stmt = select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.OPEN.value)
        draft_stmt = select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.DRAFT.value)
        closed_stmt = select(func.count(Campaign.id)).where(
            Campaign.status.in_([CampaignStatus.CLOSED.value, CampaignStatus.ARCHIVED.value])
        )

        if target_org_id:
            # Récupérer les plugins installés par cette organisation
            inst_res = await db.execute(
                select(OrganizationPlugin.plugin_id).where(
                    OrganizationPlugin.organization_id == target_org_id,
                    OrganizationPlugin.status == OrgPluginStatus.INSTALLED.value
                )
            )
            installed_plugin_ids = list(inst_res.scalars().all())

            # Condition d'accès : Campagne détenue par l'organisation OU Campagne partagée liée à un plugin installé
            if installed_plugin_ids:
                org_condition = or_(
                    Campaign.organization_id == target_org_id,
                    and_(Campaign.is_shared == True, Campaign.plugin_id.in_(installed_plugin_ids))
                )
            else:
                org_condition = (Campaign.organization_id == target_org_id)

            query = query.where(org_condition)
            count_stmt = count_stmt.where(org_condition)
            open_stmt = open_stmt.where(org_condition)
            draft_stmt = draft_stmt.where(org_condition)
            closed_stmt = closed_stmt.where(org_condition)

        if status_filter and status_filter != "all":
            query = query.where(Campaign.status == status_filter)

        if campaign_type and campaign_type != "all":
            query = query.where(Campaign.campaign_type == campaign_type)

        if plugin_code and plugin_code != "all":
            query = query.where(Campaign.plugin_code == plugin_code)

        if search and search.strip():
            term = f"%{search.strip().lower()}%"
            query = query.where(
                or_(
                    func.lower(Campaign.name).like(term),
                    func.lower(Campaign.code).like(term),
                    func.lower(Campaign.description).like(term)
                )
            )

        total_res = await db.execute(count_stmt)
        total = total_res.scalar() or 0

        open_res = await db.execute(open_stmt)
        open_count = open_res.scalar() or 0

        draft_res = await db.execute(draft_stmt)
        draft_count = draft_res.scalar() or 0

        closed_res = await db.execute(closed_stmt)
        closed_count = closed_res.scalar() or 0

        # Pagination
        query = query.offset(skip).limit(limit)
        items_res = await db.execute(query)
        items = list(items_res.scalars().all())

        return {
            "items": items,
            "total": total,
            "open_count": open_count,
            "draft_count": draft_count,
            "closed_count": closed_count
        }

    async def get_campaign_by_id(
        self,
        db: AsyncSession,
        campaign_id: int,
        user: User
    ) -> Campaign:
        """
        Récupère une campagne par son identifiant avec vérification d'isolation tenant ou de partage de plugin.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        org, _ = await self._resolve_user_org_and_permissions(db, user, action="view")

        res = await db.execute(select(Campaign).where(Campaign.id == campaign_id))
        campaign = res.scalars().first()
        if not campaign:
            raise HTTPException(status_code=404, detail="Campagne introuvable.")

        if is_super_admin:
            return campaign

        # Si la campagne appartient à l'organisation de l'utilisateur
        if org and campaign.organization_id == org.id:
            return campaign

        # Si la campagne est partagée par un plugin installé dans l'organisation de l'utilisateur
        if campaign.is_shared and campaign.plugin_id and org:
            inst_res = await db.execute(
                select(OrganizationPlugin).where(
                    OrganizationPlugin.organization_id == org.id,
                    OrganizationPlugin.plugin_id == campaign.plugin_id,
                    OrganizationPlugin.status == OrgPluginStatus.INSTALLED.value
                )
            )
            if inst_res.scalars().first():
                return campaign

        raise HTTPException(status_code=403, detail="Accès interdit à cette campagne.")

    async def create_campaign(
        self,
        db: AsyncSession,
        data: CampaignCreate,
        user: User,
        org_id: Optional[int] = None
    ) -> Campaign:
        """
        Crée une nouvelle campagne :
        - Soit propre à l'organisation (tenant / preconfigured).
        - Soit partagée au niveau du plugin (is_shared == True, réservé au Super Admin).
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"

        is_shared_request = bool(data.is_shared or data.scope == "plugin_shared")

        if is_shared_request and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Seul le Super Administrateur de la plateforme peut créer une campagne partagée au niveau d'un plugin."
            )

        org = None
        if not is_shared_request or org_id:
            org, _ = await self._resolve_user_org_and_permissions(
                db, user, action="create_edit", requested_org_id=org_id
            )
            if not org and not is_super_admin:
                raise HTTPException(status_code=400, detail="Impossible de créer une campagne sans organisation rattachée.")

        target_org_id = org.id if org else None
        clean_code = data.code.strip().lower()

        # Vérifier l'unicité du code
        if target_org_id:
            res_check = await db.execute(
                select(Campaign).where(
                    Campaign.organization_id == target_org_id,
                    Campaign.code == clean_code
                )
            )
            if res_check.scalars().first():
                raise HTTPException(
                    status_code=400,
                    detail=f"Une campagne avec le code '{clean_code}' existe déjà dans votre organisation."
                )
        elif is_shared_request and data.plugin_id:
            res_check = await db.execute(
                select(Campaign).where(
                    Campaign.is_shared == True,
                    Campaign.plugin_id == data.plugin_id,
                    Campaign.code == clean_code
                )
            )
            if res_check.scalars().first():
                raise HTTPException(
                    status_code=400,
                    detail=f"Une campagne partagée avec le code '{clean_code}' existe déjà pour ce plugin."
                )

        if data.start_date and data.end_date:
            s = data.start_date if data.start_date.tzinfo else data.start_date.replace(tzinfo=timezone.utc)
            e = data.end_date if data.end_date.tzinfo else data.end_date.replace(tzinfo=timezone.utc)
            if e < s:
                raise HTTPException(
                    status_code=400,
                    detail="La date de fin de la campagne doit être postérieure à la date de début."
                )

        plugin_id = data.plugin_id
        plugin_code = data.plugin_code
        if plugin_id and not plugin_code:
            p_res = await db.execute(select(Plugin).where(Plugin.id == plugin_id))
            plug = p_res.scalars().first()
            if plug:
                plugin_code = plug.code

        final_scope = "plugin_shared" if is_shared_request else (data.scope or "tenant")
        final_is_shared = is_shared_request

        campaign = Campaign(
            organization_id=target_org_id,
            plugin_id=plugin_id,
            plugin_code=plugin_code,
            scope=final_scope,
            is_shared=final_is_shared,
            is_immutable=bool(data.is_immutable),
            immutable_fields=data.immutable_fields or [],
            parent_template_id=data.parent_template_id,
            name=data.name.strip(),
            code=clean_code,
            campaign_type=data.campaign_type,
            status=CampaignStatus.DRAFT.value,
            description=data.description,
            objectives=data.objectives,
            targets=data.targets or {},
            start_date=data.start_date,
            end_date=data.end_date,
            budget=data.budget,
            currency=data.currency,
            target_forms=data.target_forms or [],
            target_projects=data.target_projects or [],
            settings=data.settings or {},
            metrics={
                "progress_percentage": 0,
                "total_submissions": 0,
                "validated_submissions": 0
            },
            created_by_id=user.id
        )

        db.add(campaign)
        await db.commit()
        await db.refresh(campaign)

        logger.info(f"[CAMPAIGN_CREATED] Campagne '{campaign.name}' (ID: {campaign.id}, Scope: {final_scope}, Shared: {final_is_shared}) créée par {user.email}")
        return campaign

    async def update_campaign(
        self,
        db: AsyncSession,
        campaign_id: int,
        data: CampaignUpdate,
        user: User
    ) -> Campaign:
        """
        Met à jour les paramètres d'une campagne.
        Gouvernance stricte :
        - Si la campagne est partagée (is_shared == True), seul le Super Admin peut la modifier.
        - Si la campagne est immuable (is_immutable == True), les champs protégés sont bloqués.
        - Si la campagne est clôturée ou archivée, elle est scellée pour audit.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        campaign = await self.get_campaign_by_id(db, campaign_id, user)

        # 1. Règle de partage : Seul Super Admin modifie une campagne partagée
        if campaign.is_shared and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cette campagne est partagée au niveau du plugin et gérée exclusivement par le Super Administrateur de la plateforme."
            )

        if not campaign.is_shared:
            await self._resolve_user_org_and_permissions(
                db, user, action="create_edit", requested_org_id=campaign.organization_id
            )

        # 2. Règle de scellement audit : Clôturée ou archivée
        if campaign.status in [CampaignStatus.ARCHIVED.value, CampaignStatus.CLOSED.value]:
            raise HTTPException(
                status_code=400,
                detail="Une campagne clôturée ou archivée est scellée et ne peut plus être modifiée."
            )

        # 3. Règle d'immuabilité : Vérifier les champs verrouillés
        if campaign.is_immutable and not is_super_admin:
            locked = campaign.immutable_fields or ["campaign_type", "target_forms", "code"]
            if "*" in locked:
                raise HTTPException(
                    status_code=400,
                    detail="Cette campagne est intégralement verrouillée (immuable) selon les spécifications strictes du plugin."
                )
            if "name" in locked and data.name is not None and data.name.strip() != campaign.name:
                raise HTTPException(status_code=400, detail="Le nom de cette campagne est immuable et verrouillé par le plugin.")
            if "description" in locked and data.description is not None:
                raise HTTPException(status_code=400, detail="La description certifiée de cette campagne est immuable.")
            if "objectives" in locked and data.objectives is not None:
                raise HTTPException(status_code=400, detail="Les objectifs réglementaires de cette campagne sont immuables.")
            if "target_forms" in locked and data.target_forms is not None:
                raise HTTPException(status_code=400, detail="Les formulaires obligatoires rattachés à cette campagne sont immuables.")

        # Application des modifications permises
        if data.name is not None:
            campaign.name = data.name.strip()
        if data.description is not None:
            campaign.description = data.description
        if data.objectives is not None:
            campaign.objectives = data.objectives
        if data.targets is not None:
            campaign.targets = data.targets
        if data.start_date is not None:
            campaign.start_date = data.start_date
        if data.end_date is not None:
            campaign.end_date = data.end_date
        if data.budget is not None:
            campaign.budget = data.budget
        if data.currency is not None:
            campaign.currency = data.currency
        if data.target_forms is not None:
            campaign.target_forms = data.target_forms
        if data.target_projects is not None:
            campaign.target_projects = data.target_projects
        if data.settings is not None:
            campaign.settings = data.settings

        if is_super_admin:
            if data.is_shared is not None:
                campaign.is_shared = data.is_shared
            if data.is_immutable is not None:
                campaign.is_immutable = data.is_immutable
            if data.immutable_fields is not None:
                campaign.immutable_fields = data.immutable_fields
            if data.scope is not None:
                campaign.scope = data.scope

        # Chronologie des dates
        if campaign.start_date and campaign.end_date:
            s = campaign.start_date if campaign.start_date.tzinfo else campaign.start_date.replace(tzinfo=timezone.utc)
            e = campaign.end_date if campaign.end_date.tzinfo else campaign.end_date.replace(tzinfo=timezone.utc)
            if e < s:
                raise HTTPException(
                    status_code=400,
                    detail="La date de fin de la campagne doit être postérieure à la date de début."
                )

        await db.commit()
        await db.refresh(campaign)
        return campaign

    async def change_campaign_status(
        self,
        db: AsyncSession,
        campaign_id: int,
        data: CampaignStatusUpdate,
        user: User
    ) -> Campaign:
        """
        Gère les transitions de statut (open, paused, closed, archived).
        Pour une campagne partagée, seul le Super Admin peut changer le statut.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        campaign = await self.get_campaign_by_id(db, campaign_id, user)

        if campaign.is_shared and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Seul le Super Administrateur peut changer le statut opérationnel d'une campagne partagée au niveau du plugin."
            )

        if not campaign.is_shared:
            await self._resolve_user_org_and_permissions(
                db, user, action="create_edit", requested_org_id=campaign.organization_id
            )

        new_status = data.status.strip().lower()
        valid_statuses = [s.value for s in CampaignStatus]
        if new_status not in valid_statuses:
            raise HTTPException(
                status_code=400,
                detail=f"Statut '{new_status}' invalide. Valeurs acceptées: {', '.join(valid_statuses)}."
            )

        if campaign.status == CampaignStatus.ARCHIVED.value:
            raise HTTPException(
                status_code=400,
                detail="Une campagne archivée est définitivement scellée."
            )

        now = datetime.now(timezone.utc)

        if new_status == CampaignStatus.CLOSED.value:
            campaign.status = CampaignStatus.CLOSED.value
            campaign.closed_at = now
            campaign.closed_by_id = user.id
            if campaign.metrics:
                campaign.metrics["progress_percentage"] = 100
        elif new_status == CampaignStatus.ARCHIVED.value:
            campaign.status = CampaignStatus.ARCHIVED.value
            if not campaign.closed_at:
                campaign.closed_at = now
                campaign.closed_by_id = user.id
        elif new_status == CampaignStatus.OPEN.value:
            campaign.status = CampaignStatus.OPEN.value
            if not campaign.start_date:
                campaign.start_date = now

            # Notification si organisation rattachée
            if campaign.organization_id:
                org_res = await db.execute(select(Organization).where(Organization.id == campaign.organization_id))
                org = org_res.scalars().first()
                if org and org.owner_id:
                    await notification_service.create_notification(
                        db=db,
                        user_id=org.owner_id,
                        title=f"Campagne ouverte : {campaign.name}",
                        message=f"La campagne '{campaign.name}' est désormais ouverte et active pour la collecte de données.",
                        link=f"/dashboard/campaigns/{campaign.id}",
                        type="info"
                    )
        else:
            campaign.status = new_status

        await db.commit()
        await db.refresh(campaign)
        return campaign

    async def delete_campaign(
        self,
        db: AsyncSession,
        campaign_id: int,
        user: User
    ) -> Dict[str, Any]:
        """
        Supprime une campagne brouillon.
        Seul le Super Admin peut supprimer une campagne partagée brouillon.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        campaign = await self.get_campaign_by_id(db, campaign_id, user)

        if campaign.is_shared and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Seul le Super Administrateur peut supprimer une campagne partagée."
            )

        if not campaign.is_shared:
            await self._resolve_user_org_and_permissions(
                db, user, action="delete", requested_org_id=campaign.organization_id
            )

        if campaign.status != CampaignStatus.DRAFT.value:
            raise HTTPException(
                status_code=400,
                detail="Seule une campagne au statut 'brouillon' peut être supprimée. Utilisez l'archivage pour les campagnes ouvertes ou clôturées afin de préserver l'historique d'audit."
            )

        name = campaign.name
        await db.delete(campaign)
        await db.commit()

        logger.info(f"[CAMPAIGN_DELETED] Campagne '{name}' (ID: {campaign_id}) supprimée par {user.email}")
        return {
            "message": f"La campagne '{name}' a été supprimée avec succès.",
            "campaign_id": campaign_id
        }

    async def provision_plugin_templates(
        self,
        db: AsyncSession,
        org_id: int,
        plugin: Plugin,
        version: Any,
        user: User
    ) -> List[Campaign]:
        """
        Instancie automatiquement les campagnes préconfigurées déclarées dans la version du plugin
        lors de son intégration dans l'espace de travail d'une organisation.
        Supporte les configurations immuables (locked) et personnalisables (customizable).
        """
        pkg = version.package_data or {}
        tools = pkg.get("tools", {})
        camp_config = tools.get("campaigns", {})
        templates = camp_config.get("templates", [])
        if not templates:
            return []

        created_campaigns = []
        for tpl in templates:
            tpl_code = tpl.get("code", "").strip().lower()
            if not tpl_code:
                continue

            # Vérifier si déjà instancié pour cette organisation
            res = await db.execute(
                select(Campaign).where(
                    Campaign.organization_id == org_id,
                    Campaign.code == tpl_code
                )
            )
            if res.scalars().first():
                continue

            is_imm = bool(tpl.get("is_immutable", False))
            imm_fields = tpl.get("immutable_fields", ["campaign_type", "target_forms", "code"] if is_imm else [])

            new_camp = Campaign(
                organization_id=org_id,
                plugin_id=plugin.id,
                plugin_code=plugin.code,
                scope="preconfigured",
                is_shared=False,
                is_immutable=is_imm,
                immutable_fields=imm_fields,
                parent_template_id=tpl.get("id"),
                name=tpl.get("name", "Campagne Prédéfinie"),
                code=tpl_code,
                campaign_type=tpl.get("campaign_type", "harvest"),
                status=CampaignStatus.DRAFT.value,
                description=tpl.get("description"),
                objectives=tpl.get("objectives"),
                targets=tpl.get("targets", {}),
                budget=tpl.get("budget"),
                currency=tpl.get("currency", "XOF"),
                target_forms=tpl.get("target_forms", []),
                target_projects=tpl.get("target_projects", []),
                settings=tpl.get("settings", {}),
                metrics={"progress_percentage": 0, "total_submissions": 0, "validated_submissions": 0},
                created_by_id=user.id
            )
            db.add(new_camp)
            created_campaigns.append(new_camp)

        if created_campaigns:
            await db.commit()
            logger.info(f"[CAMPAIGNS_PROVISIONED] {len(created_campaigns)} campagnes préconfigurées instanciées pour l'organisation ID {org_id}")

        return created_campaigns


campaign_service = CampaignService()
