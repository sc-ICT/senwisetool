import logging
from typing import Optional, List, Dict, Any, Union
from datetime import datetime, timezone
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, func
from sqlalchemy.orm import selectinload

from app.models.organization.organization import Organization, OrgStatusEnum
from app.models.organization.invitation import OrganizationInvitation
from app.models.organization.onboarding_draft import OnboardingDraft
from app.models.user.user import User, UserRole
from app.schemas.organization.organization import (
    OrganizationUpdateProfile,
    OrganizationReviewActionRequest,
)
from app.services.email.email_service import email_service
from app.services.notification.notification_service import notification_service
from app.core.config import settings

logger = logging.getLogger(__name__)

class OrganizationService:
    async def list_organizations(
        self,
        db: AsyncSession,
        skip: int = 0,
        limit: int = 50,
        status_filter: Optional[str] = None,
        search: Optional[str] = None,
        include_in_creation: bool = True
    ) -> List[Any]:
        orgs: List[Any] = []

        # 1. Requête sur les organisations existantes (sauf si filtre explicite sur 'in_creation')
        if status_filter != "in_creation":
            query = select(Organization).options(selectinload(Organization.owner)).order_by(Organization.created_at.desc())

            if status_filter and status_filter != "all":
                query = query.where(Organization.status == status_filter)

            if search and search.strip():
                term = f"%{search.strip()}%"
                query = query.where(
                    or_(
                        Organization.name.ilike(term),
                        Organization.code.ilike(term),
                        Organization.country.ilike(term),
                        Organization.contact_email.ilike(term)
                    )
                )

            res = await db.execute(query)
            orgs = list(res.scalars().all())

        # 2. Organisations en cours de création (invitations / brouillons non encore finalisés)
        in_creation_items: List[Dict[str, Any]] = []
        if include_in_creation and (not status_filter or status_filter in ["all", "in_creation"]):
            res_all_orgs = await db.execute(
                select(Organization).options(selectinload(Organization.owner))
            )
            all_orgs = res_all_orgs.scalars().all()
            existing_emails = set()
            for o in all_orgs:
                if o.contact_email:
                    existing_emails.add(o.contact_email.lower())
                if o.owner and o.owner.email:
                    existing_emails.add(o.owner.email.lower())

            res_invs = await db.execute(
                select(OrganizationInvitation).order_by(OrganizationInvitation.created_at.desc())
            )
            invitations = res_invs.scalars().all()

            res_drafts = await db.execute(select(OnboardingDraft))
            drafts_by_email = {d.admin_email.lower(): d for d in res_drafts.scalars().all()}

            res_users = await db.execute(select(User))
            users_by_email = {u.email.lower(): u for u in res_users.scalars().all()}

            for inv in invitations:
                email = inv.admin_email.lower()
                if email in existing_emails:
                    continue  # Déjà convertie en organisation officielle

                draft = drafts_by_email.get(email)
                draft_data = draft.draft_data if (draft and draft.draft_data) else {}
                user = users_by_email.get(email)

                # Nom lisible de l'organisation
                raw_name = draft_data.get("name")
                if not raw_name:
                    domain_part = email.split("@")[1].split(".")[0].replace("-", " ").title()
                    raw_name = f"Organisation {domain_part}"

                code = draft_data.get("code") or f"INV-{inv.id:04d}"
                country = draft_data.get("country") or "—"
                org_type = draft_data.get("type") or draft_data.get("orgType") or "En cours de création"
                sector = draft_data.get("sector")
                plan = draft_data.get("plan") or "pro"
                contact_email = draft_data.get("contact_email") or draft_data.get("contactEmail") or inv.admin_email

                owner_name = (
                    user.full_name
                    if user and user.full_name
                    else ("Compte activé" if inv.is_used else "Invitation émise")
                )
                owner_email = email
                owner_id = user.id if user else None

                # Filtrage recherche sur les éléments en cours de création
                if search and search.strip():
                    term = search.strip().lower()
                    matches = (
                        term in raw_name.lower()
                        or term in code.lower()
                        or term in country.lower()
                        or term in contact_email.lower()
                        or (owner_name and term in owner_name.lower())
                    )
                    if not matches:
                        continue

                item = {
                    "id": f"inv-{inv.id}",
                    "name": raw_name,
                    "code": code,
                    "logo_url": draft_data.get("logo_url") or draft_data.get("logoUrl"),
                    "country": country,
                    "region": draft_data.get("region"),
                    "city": draft_data.get("city"),
                    "address": draft_data.get("address"),
                    "postal_code": draft_data.get("postal_code") or draft_data.get("postalCode"),
                    "type": org_type,
                    "sector": sector,
                    "status": "in_creation",
                    "plan": plan,
                    "owner_id": owner_id,
                    "owner_name": owner_name,
                    "owner_email": owner_email,
                    "contact_email": contact_email,
                    "phone": draft_data.get("phone"),
                    "website": draft_data.get("website"),
                    "legal_doc_url": draft_data.get("legal_doc_url") or draft_data.get("legalDocUrl"),
                    "legal_doc_name": draft_data.get("legal_doc_name") or draft_data.get("legalDocName"),
                    "custom_answers": draft_data.get("custom_answers") or draft_data.get("customAnswers"),
                    "review_notes": None,
                    "reviewed_at": None,
                    "rejection_reason": None,
                    "members_count": 1,
                    "storage_used_gb": 0.0,
                    "storage_limit_gb": 50.0,
                    "iot_nodes_count": 0,
                    "created_at": inv.created_at,
                    "updated_at": draft.updated_at if draft else inv.created_at,
                    "is_in_creation": True,
                    "creation_step": draft.current_step if draft else (1 if inv.is_used else 0),
                    "invitation_id": inv.id,
                    "invitation_token": inv.token,
                    "invitation_expires_at": inv.token_expires_at,
                    "invitation_used": inv.is_used,
                    "activated_at": (inv.used_at or inv.created_at) if inv.is_used else None,
                    "custom_questions": inv.custom_questions or draft_data.get("customQuestions"),
                    "draft_data": draft_data,
                }
                in_creation_items.append(item)

        # 3. Fusion et ordonnancement
        if status_filter == "in_creation":
            merged = in_creation_items
        else:
            merged = list(orgs) + in_creation_items
            def _get_created_at(it):
                if isinstance(it, dict):
                    return it.get("created_at") or datetime.min.replace(tzinfo=timezone.utc)
                return getattr(it, "created_at", None) or datetime.min.replace(tzinfo=timezone.utc)
            merged.sort(key=_get_created_at, reverse=True)

        return merged[skip:skip + limit]

    async def get_by_id(self, db: AsyncSession, org_id: Union[int, str]) -> Optional[Any]:
        str_id = str(org_id).strip()

        # 1. Vérification si ID d'invitation (préfixe inv- ou chiffre)
        is_inv_prefix = str_id.startswith("inv-")
        inv_numeric_id = None
        if is_inv_prefix:
            try:
                inv_numeric_id = int(str_id.replace("inv-", ""))
            except ValueError:
                inv_numeric_id = None
        elif str_id.isdigit():
            # Tenter d'abord la table Organization
            res = await db.execute(
                select(Organization)
                .options(selectinload(Organization.owner))
                .where(Organization.id == int(str_id))
            )
            org = res.scalars().first()
            if org:
                # Si custom_questions est absent mais que l'organisation a des réponses ou provient d'une invitation
                if not org.custom_questions:
                    candidate_emails = []
                    if org.contact_email:
                        candidate_emails.append(org.contact_email.lower())
                    if org.owner and org.owner.email:
                        candidate_emails.append(org.owner.email.lower())
                    if candidate_emails:
                        res_inv = await db.execute(
                            select(OrganizationInvitation).where(
                                func.lower(OrganizationInvitation.admin_email).in_(candidate_emails)
                            ).order_by(OrganizationInvitation.created_at.desc())
                        )
                        inv = res_inv.scalars().first()
                        if inv and inv.custom_questions:
                            org.custom_questions = inv.custom_questions
                return org
            inv_numeric_id = int(str_id)

        # 2. Chercher dans les invitations / dossiers en cours de création
        if inv_numeric_id is not None:
            res_inv = await db.execute(
                select(OrganizationInvitation).where(OrganizationInvitation.id == inv_numeric_id)
            )
            inv = res_inv.scalars().first()
            if inv:
                email = inv.admin_email.lower()
                res_draft = await db.execute(
                    select(OnboardingDraft).where(OnboardingDraft.admin_email == email)
                )
                draft = res_draft.scalars().first()
                draft_data = draft.draft_data if (draft and draft.draft_data) else {}

                res_user = await db.execute(
                    select(User).where(func.lower(User.email) == email)
                )
                user = res_user.scalars().first()

                raw_name = draft_data.get("name")
                if not raw_name:
                    domain_part = email.split("@")[1].split(".")[0].replace("-", " ").title()
                    raw_name = f"Organisation {domain_part}"

                code = draft_data.get("code") or f"INV-{inv.id:04d}"
                country = draft_data.get("country") or "—"
                org_type = draft_data.get("type") or draft_data.get("orgType") or "En cours de création"
                owner_name = (
                    user.full_name
                    if user and user.full_name
                    else ("Compte activé" if inv.is_used else "Invitation émise")
                )

                return {
                    "id": f"inv-{inv.id}",
                    "name": raw_name,
                    "code": code,
                    "logo_url": draft_data.get("logo_url") or draft_data.get("logoUrl"),
                    "country": country,
                    "region": draft_data.get("region"),
                    "city": draft_data.get("city"),
                    "address": draft_data.get("address"),
                    "postal_code": draft_data.get("postal_code") or draft_data.get("postalCode"),
                    "type": org_type,
                    "sector": draft_data.get("sector"),
                    "status": "in_creation",
                    "plan": draft_data.get("plan") or "pro",
                    "owner_id": user.id if user else None,
                    "owner_name": owner_name,
                    "owner_email": email,
                    "contact_email": draft_data.get("contact_email") or draft_data.get("contactEmail") or inv.admin_email,
                    "phone": draft_data.get("phone"),
                    "website": draft_data.get("website"),
                    "legal_doc_url": draft_data.get("legal_doc_url") or draft_data.get("legalDocUrl"),
                    "legal_doc_name": draft_data.get("legal_doc_name") or draft_data.get("legalDocName"),
                    "custom_answers": draft_data.get("custom_answers") or draft_data.get("customAnswers"),
                    "review_notes": None,
                    "reviewed_at": None,
                    "rejection_reason": None,
                    "members_count": 1,
                    "storage_used_gb": 0.0,
                    "storage_limit_gb": 50.0,
                    "iot_nodes_count": 0,
                    "created_at": inv.created_at,
                    "updated_at": draft.updated_at if draft else inv.created_at,
                    "is_in_creation": True,
                    "creation_step": draft.current_step if draft else (1 if inv.is_used else 0),
                    "invitation_id": inv.id,
                    "invitation_token": inv.token,
                    "invitation_expires_at": inv.token_expires_at,
                    "invitation_used": inv.is_used,
                    "activated_at": (inv.used_at or inv.created_at) if inv.is_used else None,
                    "custom_questions": inv.custom_questions or draft_data.get("customQuestions"),
                    "draft_data": draft_data,
                }

        return None

    async def get_metrics(self, db: AsyncSession) -> Dict[str, int]:
        res_total = await db.execute(select(func.count(Organization.id)))
        total_orgs = res_total.scalar_one() or 0

        res_active = await db.execute(
            select(func.count(Organization.id)).where(Organization.status == OrgStatusEnum.ACTIVE.value)
        )
        active = res_active.scalar_one() or 0

        res_pending = await db.execute(
            select(func.count(Organization.id)).where(Organization.status == OrgStatusEnum.PENDING.value)
        )
        pending = res_pending.scalar_one() or 0

        res_revision = await db.execute(
            select(func.count(Organization.id)).where(Organization.status == OrgStatusEnum.REVISION_REQUESTED.value)
        )
        revision = res_revision.scalar_one() or 0

        res_suspended = await db.execute(
            select(func.count(Organization.id)).where(Organization.status == OrgStatusEnum.SUSPENDED.value)
        )
        suspended = res_suspended.scalar_one() or 0

        res_rejected = await db.execute(
            select(func.count(Organization.id)).where(Organization.status == OrgStatusEnum.REJECTED.value)
        )
        rejected = res_rejected.scalar_one() or 0

        # Décompte des invitations en cours de création
        res_all_orgs = await db.execute(select(Organization).options(selectinload(Organization.owner)))
        existing_emails = set()
        for o in res_all_orgs.scalars().all():
            if o.contact_email:
                existing_emails.add(o.contact_email.lower())
            if o.owner and o.owner.email:
                existing_emails.add(o.owner.email.lower())

        res_invs = await db.execute(select(OrganizationInvitation))
        all_invs = res_invs.scalars().all()
        in_creation = sum(1 for inv in all_invs if inv.admin_email.lower() not in existing_emails)

        total = total_orgs + in_creation

        return {
            "total": total,
            "active": active,
            "pending": pending,
            "revision_requested": revision,
            "suspended": suspended,
            "rejected": rejected,
            "in_creation": in_creation,
        }


    async def get_by_owner_id(self, db: AsyncSession, owner_id: int) -> Optional[Organization]:
        res = await db.execute(
            select(Organization)
            .options(selectinload(Organization.owner))
            .where(Organization.owner_id == owner_id)
        )
        return res.scalars().first()

    async def get_user_organization(self, db: AsyncSession, user: User) -> Optional[Organization]:
        """
        Résout de façon souveraine l'organisation à laquelle appartient l'utilisateur :
        1. En tant que Propriétaire Fondateur (Organization.owner_id == user.id ou contact_email == user.email)
        2. En tant que Membre / Collaborateur rattaché (OrganizationMember.user_id ou email)
        """
        if not user:
            return None

        # 1. Vérifier si l'utilisateur est propriétaire ou email de contact de l'organisation
        res = await db.execute(
            select(Organization)
            .options(selectinload(Organization.owner))
            .where(
                or_(
                    Organization.owner_id == user.id,
                    func.lower(Organization.contact_email) == user.email.lower()
                )
            )
        )
        org = res.scalars().first()
        if org:
            return org

        # 2. Vérifier si l'utilisateur est membre d'une organisation
        from app.models.organization.member import OrganizationMember
        mem_res = await db.execute(
            select(OrganizationMember)
            .options(selectinload(OrganizationMember.organization))
            .where(
                or_(
                    OrganizationMember.user_id == user.id,
                    func.lower(OrganizationMember.email) == user.email.lower()
                )
            )
        )
        mem = mem_res.scalars().first()
        if mem:
            if mem.organization:
                return mem.organization
            if mem.organization_id:
                org_res = await db.execute(
                    select(Organization)
                    .options(selectinload(Organization.owner))
                    .where(Organization.id == mem.organization_id)
                )
                return org_res.scalars().first()

        return None

    async def review_organization(
        self,
        db: AsyncSession,
        org_id: Union[int, str],
        reviewer: User,
        review_in: OrganizationReviewActionRequest
    ) -> Any:
        org = await self.get_by_id(db, org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Organisation ID {org_id} introuvable."
            )

        valid_actions = [
            OrgStatusEnum.ACTIVE.value,
            OrgStatusEnum.REVISION_REQUESTED.value,
            OrgStatusEnum.REJECTED.value,
            OrgStatusEnum.SUSPENDED.value,
        ]
        if review_in.action not in valid_actions:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action de revue invalide. Choix possibles : {', '.join(valid_actions)}"
            )

        # Si rejet, exiger un motif dans les notes ou le champ rejection_reason
        if review_in.action == OrgStatusEnum.REJECTED.value and not (review_in.notes.strip() or review_in.rejection_reason):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Un motif détaillé de rejet est obligatoire pour clore défavorablement l'instruction."
            )

        # Cas 1 : Organisation en cours de création (Invitation / Brouillon)
        if isinstance(org, dict):
            inv_numeric_id = org.get("invitation_id")
            if not inv_numeric_id and str(org_id).startswith("inv-"):
                try:
                    inv_numeric_id = int(str(org_id).replace("inv-", ""))
                except ValueError:
                    inv_numeric_id = None

            if inv_numeric_id:
                res_inv = await db.execute(
                    select(OrganizationInvitation).where(OrganizationInvitation.id == inv_numeric_id)
                )
                inv = res_inv.scalars().first()
                if inv:
                    if review_in.custom_questions is not None:
                        inv.custom_questions = review_in.custom_questions

                    # Mettre à jour le brouillon d'onboarding associé
                    res_draft = await db.execute(
                        select(OnboardingDraft).where(OnboardingDraft.admin_email == inv.admin_email.lower())
                    )
                    draft = res_draft.scalars().first()
                    if draft:
                        draft_data = draft.draft_data or {}
                        if review_in.custom_questions is not None:
                            draft_data["customQuestions"] = review_in.custom_questions
                        draft_data["reviewNotes"] = review_in.notes
                        draft.draft_data = draft_data

                    await db.commit()

                    # Notification par e-mail
                    if review_in.action == OrgStatusEnum.REVISION_REQUESTED.value:
                        try:
                            portal_url = f"{settings.FRONTEND_URL}/fr/onboarding/review?token={inv.token}"
                            await email_service.send_revision_requested_email(
                                to_email=inv.admin_email,
                                org_name=org.get("name", "Votre organisation"),
                                review_notes=review_in.notes or "",
                                custom_questions=review_in.custom_questions,
                                portal_url=portal_url
                            )
                        except Exception as e:
                            print(f"[EMAIL WARNING] Impossible d'envoyer l'e-mail de révision : {e}")
                    elif review_in.action == OrgStatusEnum.ACTIVE.value:
                        try:
                            draft_data = draft.draft_data if draft else {}
                            org_name = org.get("name") or draft_data.get("name") or "Votre organisation"
                            org_code = org.get("code") or draft_data.get("code") or "ORG"
                            admin_email = inv.admin_email.strip().lower()
                            org_contact_email = (draft_data.get("contact_email") or draft_data.get("contactEmail") or admin_email).strip().lower()
                            portal_url = f"{settings.FRONTEND_URL}/dashboard"
                            attestation_url = f"{settings.FRONTEND_URL}/fr/verify/attestation?code={org_code}"

                            # 1. Courriel à l'administrateur
                            await email_service.send_organization_activated_email(
                                to_email=admin_email,
                                org_name=org_name,
                                org_code=org_code,
                                portal_url=portal_url
                            )
                            # 2. Courriel officiel à l'organisation
                            await email_service.send_organization_official_activation_email(
                                to_email=org_contact_email,
                                org_name=org_name,
                                org_code=org_code,
                                admin_email=admin_email,
                                attestation_url=attestation_url,
                                portal_url=portal_url
                            )
                        except Exception as e:
                            print(f"[EMAIL WARNING] Impossible d'envoyer les e-mails d'activation : {e}")

                    return await self.get_by_id(db, org_id)

        # Cas 2 : Organisation standard existante
        org.status = review_in.action
        org.review_notes = review_in.notes
        org.reviewed_by_id = reviewer.id
        org.reviewed_at = datetime.now(timezone.utc)

        if review_in.custom_questions is not None:
            org.custom_questions = review_in.custom_questions

        if review_in.rejection_reason:
            org.rejection_reason = review_in.rejection_reason
        elif review_in.action == OrgStatusEnum.REJECTED.value:
            org.rejection_reason = review_in.notes

        await db.commit()
        await db.refresh(org)

        # Notification par email transmise à l'administrateur et à l'organisation
        owner_user = None
        if org.owner_id:
            res_owner = await db.execute(select(User).where(User.id == org.owner_id))
            owner_user = res_owner.scalars().first()

        admin_email = owner_user.email.strip().lower() if (owner_user and owner_user.email) else None
        org_contact_email = org.contact_email.strip().lower() if (org.contact_email and org.contact_email.strip()) else None

        try:
            if review_in.action == OrgStatusEnum.ACTIVE.value:
                portal_url = f"{settings.FRONTEND_URL}/dashboard"
                attestation_url = f"{settings.FRONTEND_URL}/fr/verify/attestation?code={org.code}"

                # 1. Envoi au compte administrateur
                if admin_email:
                    logger.info(f"[EMAIL DISPATCH] Envoi notification d'activation à l'administrateur {admin_email} pour '{org.name}'")
                    await email_service.send_organization_activated_email(
                        to_email=admin_email,
                        org_name=org.name,
                        org_code=org.code,
                        portal_url=portal_url
                    )

                # 2. Envoi officiel d'homologation à l'organisation
                target_org_email = org_contact_email or admin_email
                if target_org_email:
                    logger.info(f"[EMAIL DISPATCH] Envoi courriel officiel d'homologation à l'organisation {target_org_email} pour '{org.name}'")
                    await email_service.send_organization_official_activation_email(
                        to_email=target_org_email,
                        org_name=org.name,
                        org_code=org.code,
                        admin_email=admin_email,
                        attestation_url=attestation_url,
                        portal_url=portal_url
                    )
            elif review_in.action == OrgStatusEnum.REVISION_REQUESTED.value:
                recipients = set(filter(None, [admin_email, org_contact_email]))
                res_inv = await db.execute(
                    select(OrganizationInvitation).where(
                        func.lower(OrganizationInvitation.admin_email).in_([r.lower() for r in recipients])
                    ).order_by(OrganizationInvitation.created_at.desc())
                )
                inv = res_inv.scalars().first()
                token_param = f"&token={inv.token}" if inv and inv.token else ""
                portal_url = f"{settings.FRONTEND_URL}/fr/onboarding/review?org_id={org.id}{token_param}"

                for recipient in recipients:
                    logger.info(f"[EMAIL DISPATCH] Envoi notification de révision à {recipient} pour '{org.name}'")
                    await email_service.send_revision_requested_email(
                        to_email=recipient,
                        org_name=org.name,
                        review_notes=review_in.notes or "",
                        custom_questions=review_in.custom_questions,
                        portal_url=portal_url
                    )
            elif review_in.action == OrgStatusEnum.SUSPENDED.value:
                recipients = set(filter(None, [admin_email, org_contact_email]))
                for recipient in recipients:
                    await email_service.send_organization_suspended_email(
                        to_email=recipient,
                        org_name=org.name,
                        reason=review_in.notes or "Suspension temporaire par l'administrateur de conformité."
                    )
            elif review_in.action == OrgStatusEnum.REJECTED.value:
                recipients = set(filter(None, [admin_email, org_contact_email]))
                reason = review_in.rejection_reason or review_in.notes or "Dossier non conforme aux exigences d'agrément."
                for recipient in recipients:
                    await email_service.send_organization_rejected_email(
                        to_email=recipient,
                        org_name=org.name,
                        reason=reason
                    )
        except Exception as e:
            logger.error(f"[EMAIL ERROR] Échec lors de l'envoi de notification ({review_in.action}) pour {org.name}: {e}", exc_info=True)

        # Notification dans l'application pour le propriétaire de l'organisation
        if org.owner_id:
            try:
                notif_config = {
                    OrgStatusEnum.REVISION_REQUESTED.value: {
                        "title": "Complément d'information requis",
                        "message": f"Le Super Administrateur a sollicité des révisions ou compléments d'informations pour '{org.name}'.",
                        "link": f"/onboarding/review?org_id={org.id}",
                        "type": "review"
                    },
                    OrgStatusEnum.ACTIVE.value: {
                        "title": "Agrément accordé & Organisation activée !",
                        "message": f"Félicitations, l'organisation '{org.name}' est désormais active et opérationnelle sur SenWiseTool.",
                        "link": "/dashboard",
                        "type": "success"
                    },
                    OrgStatusEnum.SUSPENDED.value: {
                        "title": "Organisation temporairement suspendue",
                        "message": f"L'organisation '{org.name}' a été suspendue temporairement par la direction de conformité.",
                        "link": "/dashboard",
                        "type": "warning"
                    },
                    OrgStatusEnum.REJECTED.value: {
                        "title": "Décision d'agrément : Rejet du dossier",
                        "message": f"Le dossier d'onboarding de '{org.name}' n'a pas été agréé.",
                        "link": "/onboarding/pending",
                        "type": "warning"
                    }
                }
                cfg = notif_config.get(review_in.action)
                if cfg:
                    await notification_service.create_notification(
                        db=db,
                        title=cfg["title"],
                        message=cfg["message"],
                        link=cfg["link"],
                        type=cfg["type"],
                        user_id=org.owner_id
                    )
            except Exception as e:
                print(f"[NOTIFICATION WARNING] Échec de la notification in-app : {e}")

        print(f"[AUDIT & NOTIFICATION] Statut de l'organisation {org.code} basculé vers '{org.status}' par {reviewer.email}")
        return org

    async def update_profile(
        self,
        db: AsyncSession,
        org_id: int,
        current_user: User,
        update_in: OrganizationUpdateProfile
    ) -> Organization:
        org = await self.get_by_id(db, org_id)
        if not org:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Organisation introuvable."
            )

        # Vérifier que l'utilisateur est le propriétaire légitime (ou super admin)
        if current_user.role != UserRole.SUPER_ADMIN and org.owner_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous n'êtes pas autorisé à modifier cette organisation."
            )

        # Vérifier que l'organisation est active
        if org.status != OrgStatusEnum.ACTIVE.value and current_user.role != UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Les modifications du profil sont verrouillées tant que l'organisation n'est pas active."
            )

        update_data = update_in.model_dump(exclude_unset=True)
        for field, value in update_data.items():
            if hasattr(org, field) and value is not None:
                setattr(org, field, value)

        await db.commit()
        await db.refresh(org)
        return org

organization_service = OrganizationService()
