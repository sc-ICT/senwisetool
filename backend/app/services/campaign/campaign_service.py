import copy
import logging
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.models.campaign.campaign import Campaign, CampaignStatus
from app.models.campaign.campaign_version import CampaignVersion, CampaignVersionStatus
from app.models.project.project import Project
from app.models.form.form import Form
from app.models.organization.organization import Organization
from app.models.organization.member import OrganizationMember
from app.models.organization.role import OrganizationRole
from app.models.user.user import User, UserRole
from app.schemas.campaign.campaign import (
    CampaignCreate,
    CampaignUpdate,
    CampaignStatusUpdate,
    CampaignVersionCreate,
    CampaignVersionUpdate,
    CampaignVersionCloneRequest,
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

        if is_super_admin:
            if requested_org_id:
                org_res = await db.execute(select(Organization).where(Organization.id == requested_org_id))
                return org_res.scalars().first(), True
            return None, True

        org = await organization_service.get_user_organization(db, user)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous devez être rattaché à une organisation active pour accéder aux campagnes."
            )

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
        visibility_filter: Optional[str] = None,
        plugin_code: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
        org_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Liste les campagnes disponibles :
        - Pour le Super Admin : toutes les campagnes maîtresses de la plateforme.
        - Pour une organisation cliente : campagnes publiques (ou autorisées).
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        if not is_super_admin:
            await self._resolve_user_org_and_permissions(db, user, action="view", requested_org_id=org_id)

        query = select(Campaign).options(selectinload(Campaign.versions)).order_by(desc(Campaign.created_at))
        count_stmt = select(func.count(Campaign.id))
        open_stmt = select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.OPEN.value)
        draft_stmt = select(func.count(Campaign.id)).where(Campaign.status == CampaignStatus.DRAFT.value)
        closed_stmt = select(func.count(Campaign.id)).where(
            Campaign.status.in_([CampaignStatus.CLOSED.value, CampaignStatus.ARCHIVED.value])
        )

        if not is_super_admin:
            query = query.where(Campaign.visibility == "public")
            count_stmt = count_stmt.where(Campaign.visibility == "public")
            open_stmt = open_stmt.where(Campaign.visibility == "public")
            draft_stmt = draft_stmt.where(Campaign.visibility == "public")
            closed_stmt = closed_stmt.where(Campaign.visibility == "public")

        if status_filter and status_filter != "all":
            query = query.where(Campaign.status == status_filter)

        if visibility_filter and visibility_filter != "all":
            query = query.where(Campaign.visibility == visibility_filter)

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
        Récupère une campagne par son identifiant.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        res = await db.execute(
            select(Campaign)
            .options(selectinload(Campaign.versions))
            .where(Campaign.id == campaign_id)
        )
        campaign = res.scalars().first()
        if not campaign:
            raise HTTPException(status_code=404, detail="Campagne introuvable.")

        if not is_super_admin and campaign.visibility != "public":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Accès restreint à cette campagne."
            )

        return campaign

    async def create_campaign(
        self,
        db: AsyncSession,
        data: CampaignCreate,
        user: User,
        org_id: Optional[int] = None
    ) -> Campaign:
        """
        Crée une nouvelle campagne maîtresse avec sa version initiale 1.0.0.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        if not is_super_admin:
            await self._resolve_user_org_and_permissions(db, user, action="create_edit", requested_org_id=org_id)

        clean_code = data.code.strip().lower()

        # Vérifier l'unicité stricte et globale du code identifiant de la campagne
        code_check = await db.execute(select(Campaign).where(func.lower(Campaign.code) == clean_code))
        if code_check.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Une campagne avec le code technique '{clean_code}' existe déjà. Le code identifiant doit être strictement unique."
            )

        if data.start_date and data.end_date:
            s = data.start_date if data.start_date.tzinfo else data.start_date.replace(tzinfo=timezone.utc)
            e = data.end_date if data.end_date.tzinfo else data.end_date.replace(tzinfo=timezone.utc)
            if e < s:
                raise HTTPException(
                    status_code=400,
                    detail="La date de fin de la campagne doit être postérieure à la date de début."
                )

        initial_version_number = (getattr(data, "initial_version", None) or "1.0.0").strip()

        campaign = Campaign(
            name=data.name.strip(),
            code=clean_code,
            description=data.description,
            visibility=getattr(data, "visibility", "public") or "public",
            status=CampaignStatus.DRAFT.value,
            version=initial_version_number,
            settings=data.settings or {},
            created_by_id=user.id
        )

        db.add(campaign)
        await db.flush()

        # Création immédiate de la version 1.0.0
        initial_version = CampaignVersion(
            campaign_id=campaign.id,
            version_number=initial_version_number,
            status=CampaignVersionStatus.DRAFT.value,
            description=data.description,
            start_date=data.start_date,
            end_date=data.end_date,
            settings=data.settings or {},
            template_governance={"visibility": getattr(data, "visibility", "public") or "public"},
            changelog="Version initiale créée.",
            metrics={
                "progress_percentage": 0,
                "total_submissions": 0,
                "validated_submissions": 0
            },
            created_by_id=user.id
        )
        db.add(initial_version)
        await db.commit()
        await db.refresh(campaign)

        logger.info(f"[CAMPAIGN_CREATED] Campagne '{campaign.name}' (ID: {campaign.id}) créée par {user.email}")
        return campaign

    async def update_campaign(
        self,
        db: AsyncSession,
        campaign_id: int,
        data: CampaignUpdate,
        user: User
    ) -> Campaign:
        """
        Met à jour les informations globales d'une campagne.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        if not is_super_admin:
            await self._resolve_user_org_and_permissions(db, user, action="create_edit")

        campaign = await self.get_campaign_by_id(db, campaign_id, user)

        if campaign.status in [CampaignStatus.ARCHIVED.value, CampaignStatus.CLOSED.value]:
            raise HTTPException(
                status_code=400,
                detail="Une campagne clôturée ou archivée est scellée et ne peut plus être modifiée."
            )

        if data.code is not None:
            clean_c = data.code.strip().lower()
            if clean_c != campaign.code:
                chk_c = await db.execute(
                    select(Campaign).where(
                        func.lower(Campaign.code) == clean_c,
                        Campaign.id != campaign.id
                    )
                )
                if chk_c.scalars().first():
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Une campagne avec le code technique '{clean_c}' existe déjà. Le code doit être strictement unique."
                    )
                campaign.code = clean_c

        if data.name is not None:
            campaign.name = data.name.strip()
        if data.description is not None:
            campaign.description = data.description
        if data.visibility is not None:
            campaign.visibility = data.visibility
        if getattr(data, "version", None) is not None:
            campaign.version = data.version
        if data.settings is not None:
            campaign.settings = data.settings

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
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        if not is_super_admin:
            await self._resolve_user_org_and_permissions(db, user, action="create_edit")

        campaign = await self.get_campaign_by_id(db, campaign_id, user)

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
        elif new_status == CampaignStatus.ARCHIVED.value:
            campaign.status = CampaignStatus.ARCHIVED.value
            if not campaign.closed_at:
                campaign.closed_at = now
                campaign.closed_by_id = user.id
        elif new_status == CampaignStatus.OPEN.value:
            campaign.status = CampaignStatus.OPEN.value
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
        Supprime définitivement une campagne brouillon.
        """
        is_super_admin = user.role == UserRole.SUPER_ADMIN or str(user.role).upper() == "SUPER_ADMIN"
        if not is_super_admin:
            await self._resolve_user_org_and_permissions(db, user, action="delete")

        campaign = await self.get_campaign_by_id(db, campaign_id, user)

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

    # =========================================================================
    # GESTION DES VERSIONS DE LA CAMPAGNE (VERSIONING PATTERN)
    # =========================================================================

    @classmethod
    def _populate_latest_version_attributes(cls, campaign: Campaign) -> None:
        """
        Synchronise les données miroir de la campagne avec la version active ou la plus récente.
        """
        if not campaign.versions:
            return
        active_ver = next(
            (v for v in campaign.versions if v.status in [CampaignVersionStatus.OPEN.value, CampaignVersionStatus.ACTIVE.value]),
            campaign.versions[0]
        )
        campaign.version = active_ver.version_number
        campaign.status = active_ver.status
        if active_ver.description is not None:
            campaign.description = active_ver.description

    async def list_campaign_versions(
        self,
        db: AsyncSession,
        campaign_id: int,
        user: User
    ) -> List[CampaignVersion]:
        """
        Retourne l'historique complet et ordonné des versions d'une campagne.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)
        res = await db.execute(
            select(CampaignVersion)
            .where(CampaignVersion.campaign_id == campaign.id)
            .order_by(desc(CampaignVersion.id))
        )
        return list(res.scalars().all())

    async def get_campaign_version(
        self,
        db: AsyncSession,
        campaign_id: int,
        version_id: int,
        user: User
    ) -> CampaignVersion:
        """
        Récupère une version spécifique d'une campagne.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)
        res = await db.execute(
            select(CampaignVersion).where(
                CampaignVersion.campaign_id == campaign.id,
                CampaignVersion.id == version_id
            )
        )
        ver = res.scalars().first()
        if not ver:
            raise HTTPException(status_code=404, detail="Version de campagne introuvable.")
        return ver

    async def create_campaign_version(
        self,
        db: AsyncSession,
        campaign_id: int,
        data: CampaignVersionCreate,
        user: User
    ) -> Campaign:
        """
        Crée une nouvelle version incrémentale (brouillon) pour une campagne existante.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)

        new_v_num = data.version_number.strip()

        # Vérifier si la version existe déjà
        chk_ver = await db.execute(
            select(CampaignVersion).where(
                CampaignVersion.campaign_id == campaign.id,
                CampaignVersion.version_number == new_v_num
            )
        )
        if chk_ver.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La version '{new_v_num}' existe déjà pour cette campagne."
            )

        new_version = CampaignVersion(
            campaign_id=campaign.id,
            version_number=new_v_num,
            status=CampaignVersionStatus.DRAFT.value,
            description=data.description if data.description is not None else campaign.description,
            objectives=data.objectives,
            targets=data.targets or {},
            start_date=data.start_date,
            end_date=data.end_date,
            budget=data.budget,
            currency=data.currency or "XOF",
            target_forms=data.target_forms or [],
            target_projects=data.target_projects or [],
            settings=data.settings or {},
            template_governance=data.template_governance or {},
            changelog=data.changelog or f"Version {new_v_num} créée.",
            metrics={"progress_percentage": 0, "total_submissions": 0, "validated_submissions": 0},
            created_by_id=user.id
        )
        db.add(new_version)
        await db.flush()
        await db.refresh(campaign)
        self._populate_latest_version_attributes(campaign)
        await db.commit()
        return await self.get_campaign_by_id(db, campaign.id, user)

    async def update_campaign_version(
        self,
        db: AsyncSession,
        campaign_id: int,
        version_id: int,
        data: CampaignVersionUpdate,
        user: User
    ) -> Campaign:
        """
        Met à jour les paramètres d'une version spécifique d'une campagne.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)
        ver = await self.get_campaign_version(db, campaign_id, version_id, user)

        if ver.status in [CampaignVersionStatus.CLOSED.value, CampaignVersionStatus.ARCHIVED.value]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cette version de campagne est clôturée/archivée et ne peut plus être modifiée."
            )

        # Règle d'immuabilité : une version publiée/scellée ne peut plus voir ses paramètres de gouvernance modifiés
        if data.template_governance is not None and ver.status in [CampaignVersionStatus.OPEN.value, CampaignVersionStatus.ACTIVE.value]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de modifier les paramètres d'une version déjà publiée ou scellée."
            )

        # Règle de gouvernance : interdiction de modifier les données de la version si allow_edit_version == False
        gov = ver.template_governance or {}
        allow_edit = gov.get("allow_edit_version", gov.get("allow_edit", True))
        if allow_edit is False and data.template_governance is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La modification des données de la version « v{ver.version_number} » a été désactivée dans ses paramètres de gouvernance."
            )

        if data.description is not None:
            ver.description = data.description
        if data.objectives is not None:
            ver.objectives = data.objectives
        if data.targets is not None:
            ver.targets = data.targets
            flag_modified(ver, "targets")
        if data.start_date is not None:
            ver.start_date = data.start_date
        if data.end_date is not None:
            ver.end_date = data.end_date
        if data.budget is not None:
            ver.budget = data.budget
        if data.currency is not None:
            ver.currency = data.currency
        if data.target_forms is not None:
            ver.target_forms = data.target_forms
            flag_modified(ver, "target_forms")
        if data.target_projects is not None:
            ver.target_projects = data.target_projects
            flag_modified(ver, "target_projects")
            await self._cascade_project_forms_to_campaign_version(db, ver)
        if data.settings is not None:
            ver.settings = data.settings
            flag_modified(ver, "settings")
        if data.template_governance is not None:
            current_gov = dict(ver.template_governance or {})
            current_gov.update(data.template_governance)
            ver.template_governance = current_gov
            flag_modified(ver, "template_governance")
            if "visibility" in data.template_governance and data.template_governance["visibility"]:
                campaign.visibility = str(data.template_governance["visibility"])
        if data.changelog is not None:
            ver.changelog = data.changelog
        if data.status is not None:
            ver.status = data.status

        # Synchroniser les miroirs de campagne avec la version courante
        self._populate_latest_version_attributes(campaign)
        await db.commit()
        return await self.get_campaign_by_id(db, campaign_id, user)

    async def publish_campaign_version(
        self,
        db: AsyncSession,
        campaign_id: int,
        version_id: int,
        user: User
    ) -> Campaign:
        """
        Publie et active la version sélectionnée pour une campagne.
        Archive les versions actives ou ouvertes précédentes.
        Met à jour le statut et la version courante de la campagne.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)
        ver = await self.get_campaign_version(db, campaign_id, version_id, user)

        # Vérifier la gouvernance de la version
        gov = ver.template_governance or {}
        allow_publish = gov.get("allow_publish_version", gov.get("allow_publish", True))
        if allow_publish is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La publication de la version « v{ver.version_number} » a été désactivée dans ses paramètres de gouvernance."
            )

        # Si déjà active/ouverte et déjà la version active de la campagne, retourner directement
        if ver.status in [CampaignVersionStatus.ACTIVE.value, CampaignVersionStatus.OPEN.value] and campaign.version == ver.version_number:
            return campaign

        # Activer et ouvrir la version ciblée (les versions publiées antérieures restent publiées)
        ver.status = CampaignVersionStatus.OPEN.value
        ver.updated_at = datetime.now(timezone.utc)

        # Mettre à jour la campagne globale
        campaign.status = CampaignStatus.OPEN.value
        campaign.version = ver.version_number
        if ver.description is not None:
            campaign.description = ver.description

        self._populate_latest_version_attributes(campaign)
        await db.commit()
        return await self.get_campaign_by_id(db, campaign_id, user)

    async def set_active_campaign_version(
        self,
        db: AsyncSession,
        campaign_id: int,
        version_id: int,
        user: User
    ) -> Campaign:
        """
        Définit une version spécifique publiée ou archivée comme étant la version active courante de la campagne.
        Archive automatiquement les autres versions actives/ouvertes.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)
        ver = await self.get_campaign_version(db, campaign_id, version_id, user)

        if ver.status == CampaignVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Une version en brouillon ne peut pas être directement activée. Veuillez d'abord publier la version « v{ver.version_number} »."
            )

        # Mettre la version ciblée en statut OPEN (active)
        ver.status = CampaignVersionStatus.OPEN.value
        ver.updated_at = datetime.now(timezone.utc)

        # Archiver les autres versions précédemment actives ou ouvertes de cette campagne
        other_versions_res = await db.execute(
            select(CampaignVersion).where(
                CampaignVersion.campaign_id == campaign.id,
                CampaignVersion.id != ver.id
            )
        )
        for other_v in other_versions_res.scalars().all():
            if other_v.status in [CampaignVersionStatus.OPEN.value, CampaignVersionStatus.ACTIVE.value]:
                other_v.status = CampaignVersionStatus.ARCHIVED.value
                other_v.updated_at = datetime.now(timezone.utc)

        campaign.version = ver.version_number
        campaign.status = CampaignStatus.OPEN.value
        if ver.description is not None:
            campaign.description = ver.description

        self._populate_latest_version_attributes(campaign)
        await db.commit()
        return await self.get_campaign_by_id(db, campaign_id, user)

    async def clone_campaign_version(
        self,
        db: AsyncSession,
        campaign_id: int,
        req: CampaignVersionCloneRequest,
        user: User
    ) -> Campaign:
        """
        Clone une version existante pour créer une nouvelle version de campagne incrémentale.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)

        # Vérifier si un brouillon existe déjà
        draft_chk = await db.execute(
            select(CampaignVersion).where(
                CampaignVersion.campaign_id == campaign.id,
                CampaignVersion.status == CampaignVersionStatus.DRAFT.value
            )
        )
        existing_draft = draft_chk.scalars().first()
        if existing_draft:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Une version en brouillon existe déjà (« v{existing_draft.version_number} »). Veuillez la publier ou la supprimer avant de cloner une nouvelle version."
            )

        new_v_num = req.new_version_number.strip()
        chk_ver = await db.execute(
            select(CampaignVersion).where(
                CampaignVersion.campaign_id == campaign.id,
                CampaignVersion.version_number == new_v_num
            )
        )
        if chk_ver.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La version '{new_v_num}' existe déjà pour cette campagne."
            )

        # Résoudre la version source
        source_ver: Optional[CampaignVersion] = None
        if req.source_version_id:
            src_res = await db.execute(
                select(CampaignVersion).where(
                    CampaignVersion.campaign_id == campaign.id,
                    CampaignVersion.id == req.source_version_id
                )
            )
            source_ver = src_res.scalars().first()
        elif campaign.versions:
            source_ver = campaign.versions[0]

        if source_ver:
            src_gov = source_ver.template_governance or {}
            allow_clone = src_gov.get("allow_clone_version", src_gov.get("allow_clone", True))
            if allow_clone is False:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Le clonage de la version « v{source_ver.version_number} » a été désactivé dans ses paramètres de gouvernance."
                )

        should_copy_scope = bool(getattr(req, "copy_scope", False))

        new_version = CampaignVersion(
            campaign_id=campaign.id,
            version_number=new_v_num,
            status=CampaignVersionStatus.DRAFT.value,
            description=source_ver.description if source_ver else campaign.description,
            objectives=source_ver.objectives if source_ver else None,
            targets=copy.deepcopy(source_ver.targets) if source_ver and source_ver.targets else {},
            start_date=source_ver.start_date if source_ver else None,
            end_date=source_ver.end_date if source_ver else None,
            budget=source_ver.budget if source_ver else None,
            currency=source_ver.currency if source_ver else "XOF",
            target_forms=copy.deepcopy(source_ver.target_forms) if (should_copy_scope and source_ver and source_ver.target_forms) else [],
            target_projects=copy.deepcopy(source_ver.target_projects) if (should_copy_scope and source_ver and source_ver.target_projects) else [],
            settings=copy.deepcopy(source_ver.settings) if source_ver and source_ver.settings else {},
            template_governance=copy.deepcopy(source_ver.template_governance) if source_ver and source_ver.template_governance else {"visibility": campaign.visibility},
            changelog=req.changelog or f"Cloné depuis v{source_ver.version_number if source_ver else 'initial'}.",
            metrics={"progress_percentage": 0, "total_submissions": 0, "validated_submissions": 0},
            created_by_id=user.id
        )

        db.add(new_version)
        await db.flush()
        await db.refresh(campaign)
        self._populate_latest_version_attributes(campaign)
        await db.commit()
        return await self.get_campaign_by_id(db, campaign.id, user)

    async def delete_campaign_version(
        self,
        db: AsyncSession,
        campaign_id: int,
        version_id: int,
        user: User
    ) -> Dict[str, Any]:
        """
        Supprime une version spécifique de la campagne.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)
        ver = await self.get_campaign_version(db, campaign_id, version_id, user)

        del_gov = ver.template_governance or {}
        allow_delete = del_gov.get("allow_delete_version", True)
        if allow_delete is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La suppression de la version « v{ver.version_number} » a été désactivée dans ses paramètres de gouvernance."
            )

        if ver.status not in [CampaignVersionStatus.DRAFT.value, CampaignVersionStatus.ARCHIVED.value]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de supprimer une version active ou ouverte. Vous devez d'abord la clôturer ou l'archiver."
            )

        # Vérifier le nombre total de versions
        count_res = await db.execute(
            select(func.count(CampaignVersion.id)).where(CampaignVersion.campaign_id == campaign.id)
        )
        total_versions = count_res.scalar() or 0

        if total_versions <= 1:
            await db.delete(campaign)
            await db.commit()
            return {"message": "Dernière version supprimée : La campagne a été entièrement supprimée.", "deleted_campaign": True}

        deleted_version_number = ver.version_number
        await db.delete(ver)
        await db.flush()

        # Recharger les versions restantes pour réajuster le statut courant de la campagne
        remaining_res = await db.execute(
            select(CampaignVersion).where(CampaignVersion.campaign_id == campaign.id).order_by(desc(CampaignVersion.id))
        )
        campaign.versions = list(remaining_res.scalars().all())
        self._populate_latest_version_attributes(campaign)
        await db.commit()

        return {
            "message": f"La version « v{deleted_version_number} » a été supprimée avec succès.",
            "deleted_version_number": deleted_version_number,
            "deleted_campaign": False
        }

    # =========================================================================
    # ASSOCIATIONS PROJETS & FORMULAIRES À UNE VERSION DE CAMPAGNE (CASCADE)
    # =========================================================================

    async def _cascade_project_forms_to_campaign_version(
        self,
        db: AsyncSession,
        ver: CampaignVersion,
    ) -> None:
        """
        Règle d'or de cascade :
        Lorsqu'une version spécifique de projet est associée à une version de campagne,
        seuls les formulaires associés à cette version précise de projet sont intégrés.
        """
        raw_projects = ver.target_projects or []
        project_ids = []
        project_target_ver_map = {}
        for p in raw_projects:
            if isinstance(p, dict):
                p_id = p.get("id") or p.get("project_id")
                p_ver_id = p.get("project_version_id")
                p_ver_num = p.get("version_number")
            elif isinstance(p, (int, str)) and str(p).isdigit():
                p_id = int(p)
                p_ver_id = None
                p_ver_num = None
            else:
                p_id = None
                p_ver_id = None
                p_ver_num = None
            if p_id:
                p_id = int(p_id)
                project_ids.append(p_id)
                project_target_ver_map[p_id] = (p_ver_id, p_ver_num)

        if not project_ids:
            return

        current_forms = list(ver.target_forms or [])
        existing_form_ids = set()
        for f in current_forms:
            if isinstance(f, dict):
                fid = f.get("id") or f.get("form_id")
            elif isinstance(f, (int, str)) and str(f).isdigit():
                fid = int(f)
            else:
                fid = None
            if fid:
                existing_form_ids.add(fid)

        stmt = select(Project).options(selectinload(Project.versions)).where(Project.id.in_(project_ids))
        res = await db.execute(stmt)
        projects = res.scalars().all()

        forms_to_add_ids = set()
        project_form_map = {}
        project_form_version_map = {}
        for proj in projects:
            p_form_ids = set()
            (target_pv_id, target_pv_num) = project_target_ver_map.get(proj.id, (None, None))
            target_pv = None
            if target_pv_id and proj.versions:
                target_pv = next((v for v in proj.versions if v.id == target_pv_id), None)
            elif target_pv_num and proj.versions:
                target_pv = next((v for v in proj.versions if v.version_number == target_pv_num), None)
            if not target_pv and proj.versions:
                target_pv = next((v for v in proj.versions if v.status in ("published", "active")), proj.versions[0])

            # Extraction des formulaires UNIQUEMENT depuis la version ciblée du projet
            if target_pv:
                p_ver_settings = getattr(target_pv, "settings", None) or {}
                for fid in p_ver_settings.get("attached_form_ids", []):
                    if str(fid).isdigit():
                        p_form_ids.add(int(fid))
                for af in p_ver_settings.get("attached_form_versions", []):
                    if isinstance(af, dict) and af.get("form_id") and str(af["form_id"]).isdigit():
                        fid = int(af["form_id"])
                        p_form_ids.add(fid)
                        if af.get("version_number"):
                            project_form_version_map[fid] = {
                                "version_number": af.get("version_number"),
                                "form_version_id": af.get("form_version_id"),
                                "source_project_version_id": target_pv.id,
                            }
            else:
                # Rétrocompatibilité si pas de version spécifique
                for fid in (getattr(proj, "settings", None) or {}).get("attached_form_ids", []):
                    if str(fid).isdigit():
                        p_form_ids.add(int(fid))

            for fid in p_form_ids:
                forms_to_add_ids.add(fid)
                project_form_map[fid] = (proj, target_pv)

        # Mettre à jour les formulaires déjà présents si version_number manquait ou a changé
        for cf in current_forms:
            if isinstance(cf, dict):
                cf_id = cf.get("id") or cf.get("form_id")
                if cf_id in project_form_version_map:
                    cf["version_number"] = project_form_version_map[cf_id]["version_number"]
                    if project_form_version_map[cf_id].get("form_version_id"):
                        cf["form_version_id"] = project_form_version_map[cf_id]["form_version_id"]
                    if project_form_version_map[cf_id].get("source_project_version_id"):
                        cf["source_project_version_id"] = project_form_version_map[cf_id]["source_project_version_id"]

        if forms_to_add_ids:
            f_stmt = select(Form).options(selectinload(Form.versions)).where(Form.id.in_(list(forms_to_add_ids)))
            f_res = await db.execute(f_stmt)
            forms_found = f_res.scalars().all()

            for f_obj in forms_found:
                # RÈGLE MÉTIER : On n'associe pas un formulaire en brouillon, uniquement les publiés
                is_pub = (
                    f_obj.status in ("published", "active")
                    or any(v.status in ("published", "active", "archived") for v in (f_obj.versions or []))
                )
                if not is_pub:
                    continue

                proj_tuple = project_form_map.get(f_obj.id)
                parent_proj = proj_tuple[0] if proj_tuple else None
                target_pv = proj_tuple[1] if proj_tuple else None

                ver_info = project_form_version_map.get(f_obj.id) or {}
                ver_num = ver_info.get("version_number")
                target_fv_id = ver_info.get("form_version_id")

                if not ver_num and f_obj.versions:
                    pub_vers = [v for v in f_obj.versions if v.status in ("published", "active", "archived")]
                    if pub_vers:
                        ver_num = pub_vers[0].version_number
                        target_fv_id = pub_vers[0].id

                # Si déjà présent dans current_forms, on ne rajoute pas de doublon
                if f_obj.id not in existing_form_ids:
                    current_forms.append({
                        "id": f_obj.id,
                        "form_id": f_obj.id,
                        "name": f_obj.name,
                        "code": f_obj.code,
                        "version_number": ver_num or f_obj.version or "1.0.0",
                        "form_version_id": target_fv_id,
                        "source_project_id": parent_proj.id if parent_proj else None,
                        "source_project_version_id": target_pv.id if target_pv else None,
                        "source_project_name": parent_proj.name if parent_proj else None,
                        "attached_at": datetime.now(timezone.utc).isoformat(),
                    })
                    existing_form_ids.add(f_obj.id)

            ver.target_forms = current_forms
            flag_modified(ver, "target_forms")

    async def attach_project_to_campaign_version(
        self,
        db: AsyncSession,
        campaign_id: int,
        version_id: int,
        project_id: int,
        user: User,
        project_version_id: Optional[int] = None,
    ) -> Campaign:
        """
        Associe une version spécifique d'un projet à une version de campagne.
        RÈGLES D'OR DU SYSTÈME :
        1. Seules les versions de campagne en brouillon ('draft') peuvent être configurées.
        2. Seules les versions de projet publiées ('published' / 'active') peuvent être associées.
        3. RÈGLE D'EXCLUSIVITÉ STRICTE PAR CAMPAGNE :
           Un projet et ses versions ne peuvent être associés qu'aux versions d'une seule et même campagne.
           Si ce projet (ou l'une de ses versions) a déjà été lié à une AUTRE campagne, l'association est rejetée.
        4. Cascade ciblée : seuls les formulaires associés à la version choisie du projet sont intégrés.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)
        ver = await self.get_campaign_version(db, campaign_id, version_id, user)

        # RÈGLE MÉTIER 1 : Interdiction de modifier une version scellée/publiée
        if ver.status != CampaignVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Modification non autorisée : La version « v{ver.version_number} » de la campagne est déjà {ver.status}. Seules les versions en brouillon peuvent être configurées. Une fois publiée, le périmètre d'une version est scellé."
            )

        proj_stmt = select(Project).options(selectinload(Project.versions)).where(Project.id == project_id)
        project = (await db.execute(proj_stmt)).scalars().first()
        if not project:
            raise HTTPException(status_code=404, detail=f"Projet #{project_id} introuvable.")

        # RÈGLE MÉTIER 3 : EXCLUSIVITÉ STRICTE PAR CAMPAGNE
        # Vérifier si ce projet (ou l'une de ses versions) est déjà rattaché à une autre campagne
        other_camp_stmt = (
            select(CampaignVersion, Campaign)
            .join(Campaign, CampaignVersion.campaign_id == Campaign.id)
            .where(CampaignVersion.campaign_id != campaign_id)
        )
        other_res = await db.execute(other_camp_stmt)
        for other_ver, other_c in other_res.all():
            for tp in (other_ver.target_projects or []):
                tp_id = tp.get("project_id") or tp.get("id") if isinstance(tp, dict) else tp
                if tp_id == project_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Association impossible : Le projet « {project.name} » est déjà lié à la version v{other_ver.version_number} de la campagne « {other_c.name} » (#{other_c.code}). Un projet et ses versions ne peuvent être associés qu'aux versions d'une seule et même campagne."
                    )

        # Résoudre la version exacte de projet ciblée
        target_pv = None
        if project_version_id and project.versions:
            target_pv = next((v for v in project.versions if v.id == project_version_id), None)
            if not target_pv:
                raise HTTPException(status_code=404, detail=f"Version de projet #{project_version_id} introuvable.")
        elif project.versions:
            target_pv = next((v for v in project.versions if v.status in ("published", "active")), None)
            if not target_pv:
                target_pv = project.versions[0]

        # RÈGLE MÉTIER 2 : Exclusion stricte des versions en brouillon
        if not target_pv or target_pv.status not in ("published", "active"):
            v_status_str = target_pv.status if target_pv else "draft"
            v_num_str = target_pv.version_number if target_pv else "1.0.0"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action non autorisée : La version v{v_num_str} du projet « {project.name} » est en statut « {v_status_str} ». Seules les versions publiées peuvent être associées à une version de campagne."
            )

        proj_entry = {
            "id": project.id,
            "project_id": project.id,
            "name": project.name,
            "code": project.code,
            "project_type": project.project_type,
            "project_version_id": target_pv.id,
            "version_number": target_pv.version_number,
            "version_status": target_pv.status,
            "attached_at": datetime.now(timezone.utc).isoformat(),
        }

        current_projects = list(ver.target_projects or [])
        found_idx = None
        for idx, p in enumerate(current_projects):
            pid = p.get("project_id") or p.get("id") if isinstance(p, dict) else p
            if pid == project.id:
                found_idx = idx
                break

        if found_idx is not None:
            # Mettre à jour la version du projet déjà associé
            current_projects[found_idx] = proj_entry
        else:
            current_projects.append(proj_entry)

        ver.target_projects = current_projects
        flag_modified(ver, "target_projects")

        # Cascade automatique des formulaires associés à CETTE version spécifique du projet
        await self._cascade_project_forms_to_campaign_version(db, ver)

        await db.commit()
        await db.refresh(ver)
        await db.refresh(campaign)
        self._populate_latest_version_attributes(campaign)
        return campaign

    async def detach_project_from_campaign_version(
        self,
        db: AsyncSession,
        campaign_id: int,
        version_id: int,
        project_id: int,
        user: User,
    ) -> Campaign:
        """
        Dissocie un projet d'une version de campagne et retire les formulaires hérités de ce projet.
        RÈGLE MÉTIER ABSOLUE :
        Interdiction formelle de dissocier un projet d'une campagne publiée ou ouverte ('published', 'active', 'open').
        Seules les versions en brouillon ('draft') d'une campagne non encore ouverte aux opérations peuvent être modifiées.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)
        ver = await self.get_campaign_version(db, campaign_id, version_id, user)

        # RÈGLE MÉTIER STRICTE : Seules les versions en brouillon ('draft') peuvent être modifiées
        if ver.status != CampaignVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action interdite : La version « v{ver.version_number} » de la campagne « {campaign.name} » est déjà scellée ({ver.status}). Impossible de dissocier un projet d'une version scellée afin de préserver l'intégrité et la traçabilité des collectes de données. Créez une nouvelle version en brouillon pour ajuster le périmètre."
            )

        current_projects = list(ver.target_projects or [])
        filtered_projects = [
            p for p in current_projects
            if not ((p.get("id") == project_id or p.get("project_id") == project_id) if isinstance(p, dict) else p == project_id)
        ]
        ver.target_projects = filtered_projects
        flag_modified(ver, "target_projects")

        # Retirer les formulaires qui étaient sourcés uniquement par ce projet
        current_forms = list(ver.target_forms or [])
        filtered_forms = [
            f for f in current_forms
            if not (isinstance(f, dict) and f.get("source_project_id") == project_id)
        ]
        ver.target_forms = filtered_forms
        flag_modified(ver, "target_forms")

        await db.commit()
        await db.refresh(ver)
        await db.refresh(campaign)
        self._populate_latest_version_attributes(campaign)
        return campaign

    async def attach_form_to_campaign_version(
        self,
        db: AsyncSession,
        campaign_id: int,
        version_id: int,
        form_id: int,
        user: User,
        form_version_id: Optional[int] = None,
    ) -> Campaign:
        """
        Associe une version spécifique d'un formulaire autonome à une version de campagne.
        RÈGLES D'OR DU SYSTÈME :
        1. Seules les versions de campagne en brouillon ('draft') peuvent être configurées.
        2. Seules les versions de formulaire publiées ('published' / 'active' / 'archived') peuvent être associées.
        3. RÈGLE D'EXCLUSIVITÉ STRICTE PAR CAMPAGNE :
           Un formulaire et ses versions ne peuvent être associés qu'aux versions d'une seule et même campagne.
           Si ce formulaire (ou l'une de ses versions) a déjà été lié à une AUTRE campagne, l'association est rejetée.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)
        ver = await self.get_campaign_version(db, campaign_id, version_id, user)

        # RÈGLE MÉTIER 1 : Interdiction de modifier une version scellée/publiée
        if ver.status != CampaignVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Modification non autorisée : La version « v{ver.version_number} » de la campagne est déjà {ver.status}. Seules les versions en brouillon peuvent être configurées. Une fois publiée, le périmètre d'une version est scellé."
            )

        form_stmt = select(Form).options(selectinload(Form.versions)).where(Form.id == form_id)
        form_obj = (await db.execute(form_stmt)).scalars().first()
        if not form_obj:
            raise HTTPException(status_code=404, detail=f"Formulaire #{form_id} introuvable.")

        # RÈGLE MÉTIER 3 : EXCLUSIVITÉ STRICTE PAR CAMPAGNE
        # Vérifier si ce formulaire (ou l'une de ses versions) est déjà rattaché à une autre campagne
        other_camp_stmt = (
            select(CampaignVersion, Campaign)
            .join(Campaign, CampaignVersion.campaign_id == Campaign.id)
            .where(CampaignVersion.campaign_id != campaign_id)
        )
        other_res = await db.execute(other_camp_stmt)
        for other_ver, other_c in other_res.all():
            for tf in (other_ver.target_forms or []):
                tf_id = tf.get("form_id") or tf.get("id") if isinstance(tf, dict) else tf
                if tf_id == form_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Association impossible : Le formulaire « {form_obj.name} » est déjà lié à la version v{other_ver.version_number} de la campagne « {other_c.name} » (#{other_c.code}). Un formulaire et ses versions ne peuvent être associés qu'aux versions d'une seule et même campagne."
                    )

        # Résoudre la version exacte de formulaire ciblée
        target_v = None
        if form_version_id and form_obj.versions:
            target_v = next((v for v in form_obj.versions if v.id == form_version_id), None)
            if not target_v:
                raise HTTPException(status_code=404, detail=f"Version de formulaire #{form_version_id} introuvable.")
        elif form_obj.versions:
            target_v = next((v for v in form_obj.versions if v.status in ("published", "active", "archived")), None)
            if not target_v:
                target_v = form_obj.versions[0]

        # RÈGLE MÉTIER 2 : Exclusion stricte des versions en brouillon
        if not target_v or target_v.status not in ("published", "active", "archived"):
            v_status_str = target_v.status if target_v else "draft"
            v_num_str = target_v.version_number if target_v else "1.0.0"
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action non autorisée : La version v{v_num_str} du formulaire « {form_obj.name} » est en statut « {v_status_str} ». Seules les versions publiées peuvent être associées à une version de campagne."
            )

        ver_num = target_v.version_number
        ver_id = target_v.id
        ver_status = target_v.status

        form_entry = {
            "id": form_obj.id,
            "form_id": form_obj.id,
            "name": form_obj.name,
            "code": form_obj.code,
            "version_number": ver_num,
            "form_version_id": ver_id,
            "version_status": ver_status,
            "attached_at": datetime.now(timezone.utc).isoformat(),
        }

        current_forms = list(ver.target_forms or [])
        found_idx = None
        for idx, f in enumerate(current_forms):
            fid = f.get("form_id") or f.get("id") if isinstance(f, dict) else f
            if fid == form_obj.id:
                found_idx = idx
                break

        if found_idx is not None:
            # Conserver la référence projet source si existante
            existing_f = current_forms[found_idx]
            if isinstance(existing_f, dict) and existing_f.get("source_project_id"):
                form_entry["source_project_id"] = existing_f["source_project_id"]
                form_entry["source_project_name"] = existing_f.get("source_project_name")
                form_entry["source_project_version_id"] = existing_f.get("source_project_version_id")
            current_forms[found_idx] = form_entry
        else:
            current_forms.append(form_entry)

        ver.target_forms = current_forms
        flag_modified(ver, "target_forms")

        await db.commit()
        await db.refresh(ver)
        await db.refresh(campaign)
        self._populate_latest_version_attributes(campaign)
        return campaign

    async def detach_form_from_campaign_version(
        self,
        db: AsyncSession,
        campaign_id: int,
        version_id: int,
        form_id: int,
        user: User,
    ) -> Campaign:
        """
        Dissocie un formulaire autonome d'une version de campagne.
        RÈGLE MÉTIER ABSOLUE :
        Interdiction formelle de dissocier un formulaire d'une campagne publiée ou ouverte ('published', 'active', 'open').
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)
        ver = await self.get_campaign_version(db, campaign_id, version_id, user)

        # RÈGLE MÉTIER STRICTE : Seules les versions en brouillon ('draft') peuvent être modifiées
        if ver.status != CampaignVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action interdite : La version « v{ver.version_number} » de la campagne « {campaign.name} » est déjà scellée ({ver.status}). Impossible de dissocier un formulaire d'une version scellée afin de préserver l'intégrité et la traçabilité des collectes de données. Créez une nouvelle version en brouillon pour ajuster le périmètre."
            )

        current_forms = list(ver.target_forms or [])
        filtered_forms = [
            f for f in current_forms
            if not ((f.get("id") == form_id or f.get("form_id") == form_id) if isinstance(f, dict) else f == form_id)
        ]
        ver.target_forms = filtered_forms
        flag_modified(ver, "target_forms")

        await db.commit()
        await db.refresh(ver)
        await db.refresh(campaign)
        self._populate_latest_version_attributes(campaign)
        return campaign

    async def get_campaigns_for_project(
        self,
        db: AsyncSession,
        project_id: int,
        user: User,
    ) -> List[Dict[str, Any]]:
        """
        Récupère toutes les campagnes et versions associées à un projet.
        """
        stmt = (
            select(Campaign)
            .options(selectinload(Campaign.versions))
            .order_by(desc(Campaign.created_at))
        )
        res = await db.execute(stmt)
        campaigns = res.scalars().all()

        results = []
        for camp in campaigns:
            matching_versions = []
            for v in (camp.versions or []):
                for p in (v.target_projects or []):
                    pid = p.get("id") or p.get("project_id") if isinstance(p, dict) else p
                    if pid == project_id:
                        matching_versions.append({
                            "id": v.id,
                            "version_number": v.version_number,
                            "status": v.status,
                            "start_date": v.start_date.isoformat() if v.start_date else None,
                            "end_date": v.end_date.isoformat() if v.end_date else None,
                        })
                        break

            if matching_versions:
                results.append({
                    "id": camp.id,
                    "name": camp.name,
                    "code": camp.code,
                    "status": camp.status,
                    "version": camp.version,
                    "matching_versions": matching_versions,
                })

        return results

    async def get_campaigns_for_form(
        self,
        db: AsyncSession,
        form_id: int,
        user: User,
    ) -> List[Dict[str, Any]]:
        """
        Récupère toutes les campagnes et versions associées à un formulaire.
        """
        stmt = (
            select(Campaign)
            .options(selectinload(Campaign.versions))
            .order_by(desc(Campaign.created_at))
        )
        res = await db.execute(stmt)
        campaigns = res.scalars().all()

        results = []
        for camp in campaigns:
            matching_versions = []
            for v in (camp.versions or []):
                for f in (v.target_forms or []):
                    fid = f.get("id") or f.get("form_id") if isinstance(f, dict) else f
                    if fid == form_id:
                        matching_versions.append({
                            "id": v.id,
                            "version_number": v.version_number,
                            "status": v.status,
                            "start_date": v.start_date.isoformat() if v.start_date else None,
                            "end_date": v.end_date.isoformat() if v.end_date else None,
                            "source_project_id": f.get("source_project_id") if isinstance(f, dict) else None,
                            "source_project_name": f.get("source_project_name") if isinstance(f, dict) else None,
                        })
                        break

            if matching_versions:
                results.append({
                    "id": camp.id,
                    "name": camp.name,
                    "code": camp.code,
                    "status": camp.status,
                    "version": camp.version,
                    "matching_versions": matching_versions,
                })

        return results

    async def reset_campaign_version_scope(
        self,
        db: AsyncSession,
        campaign_id: int,
        version_id: int,
        user: User,
    ) -> Campaign:
        """
        Réinitialise complètement le périmètre opérationnel (retire tous les projets et formulaires)
        d'une version de campagne spécifique.
        Règle d'or : Seules les versions en brouillon ('draft') peuvent voir leur périmètre réinitialisé.
        """
        campaign = await self.get_campaign_by_id(db, campaign_id, user)
        ver = await self.get_campaign_version(db, campaign_id, version_id, user)

        if ver.status != CampaignVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action interdite : Seules les versions en brouillon peuvent voir leur périmètre réinitialisé (Statut actuel : {ver.status})."
            )

        ver.target_projects = []
        ver.target_forms = []
        flag_modified(ver, "target_projects")
        flag_modified(ver, "target_forms")

        await db.commit()
        await db.refresh(ver)
        await db.refresh(campaign)
        self._populate_latest_version_attributes(campaign)
        return campaign


campaign_service = CampaignService()

