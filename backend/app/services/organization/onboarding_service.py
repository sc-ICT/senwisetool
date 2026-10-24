import hashlib
import secrets
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone, timedelta
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func

from app.core.config import settings
from app.services.email.email_service import email_service
from app.services.notification.notification_service import notification_service
from app.models.organization.organization import Organization, OrgStatusEnum
from app.models.organization.onboarding_draft import OnboardingDraft
from app.models.organization.invitation import OrganizationInvitation
from app.models.user.user import User, UserRole
from app.schemas.organization.onboarding import (
    OnboardingDraftSaveRequest,
    OnboardingSubmissionRequest,
    OrganizationStatusCheckResponse,
    AttestationVerificationResponse,
)

# Stockage sécurisé en mémoire des codes OTP de validation d'email officiel de contact
_contact_email_otps: Dict[str, Dict[str, Any]] = {}

class OnboardingService:
    async def save_draft(
        self,
        db: AsyncSession,
        draft_in: OnboardingDraftSaveRequest
    ) -> OnboardingDraft:
        email = draft_in.email.strip().lower()
        res = await db.execute(select(OnboardingDraft).where(OnboardingDraft.admin_email == email))
        draft = res.scalars().first()

        if draft:
            draft.current_step = draft_in.step
            draft.draft_data = draft_in.draft_data
        else:
            draft = OnboardingDraft(
                admin_email=email,
                current_step=draft_in.step,
                draft_data=draft_in.draft_data
            )
            db.add(draft)

        await db.commit()
        await db.refresh(draft)
        return draft

    async def get_draft(
        self,
        db: AsyncSession,
        email: str
    ) -> Optional[OnboardingDraft]:
        res = await db.execute(
            select(OnboardingDraft).where(OnboardingDraft.admin_email == email.strip().lower())
        )
        return res.scalars().first()

    async def check_email_availability(
        self,
        db: AsyncSession,
        email: str
    ) -> Dict[str, Any]:
        email_clean = email.strip().lower()
        if not email_clean or "@" not in email_clean:
            return {
                "email": email,
                "available": False,
                "message": "Adresse e-mail invalide.",
                "used_as": None
            }

        # 1. Vérification dans la table User
        res_user = await db.execute(
            select(User).where(func.lower(User.email) == email_clean)
        )
        if res_user.scalars().first():
            return {
                "email": email_clean,
                "available": False,
                "message": f"L'adresse e-mail '{email_clean}' est déjà utilisée par un compte utilisateur dans le système.",
                "used_as": "user"
            }

        # 2. Vérification dans la table Organization
        res_org = await db.execute(
            select(Organization).where(func.lower(Organization.contact_email) == email_clean)
        )
        if res_org.scalars().first():
            return {
                "email": email_clean,
                "available": False,
                "message": f"L'adresse e-mail '{email_clean}' est déjà enregistrée comme contact officiel d'une organisation dans le système.",
                "used_as": "organization"
            }

        # 3. Vérification dans la table OrganizationInvitation
        res_inv = await db.execute(
            select(OrganizationInvitation).where(func.lower(OrganizationInvitation.admin_email) == email_clean)
        )
        if res_inv.scalars().first():
            return {
                "email": email_clean,
                "available": False,
                "message": f"L'adresse e-mail '{email_clean}' fait déjà l'objet d'une invitation ou d'un dossier en cours dans le système.",
                "used_as": "invitation"
            }

        return {
            "email": email_clean,
            "available": True,
            "message": "Adresse e-mail disponible et unique dans tout le système.",
            "used_as": None
        }

    async def send_contact_email_otp(
        self,
        db: AsyncSession,
        contact_email: str
    ) -> Dict[str, Any]:
        email_clean = contact_email.strip().lower()

        # Règle d'unicité absolue : L'adresse email de contact ne doit appartenir ni à un utilisateur, ni à une organisation, ni à une invitation
        res_user = await db.execute(
            select(User).where(func.lower(User.email) == email_clean)
        )
        if res_user.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"L'adresse e-mail '{email_clean}' est déjà utilisée par un compte utilisateur dans le système. Aucun code OTP ne peut être émis."
            )

        res_org = await db.execute(
            select(Organization).where(func.lower(Organization.contact_email) == email_clean)
        )
        if res_org.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"L'adresse e-mail '{email_clean}' est déjà enregistrée comme contact officiel d'une organisation dans le système. Aucun code OTP ne peut être émis."
            )

        res_inv = await db.execute(
            select(OrganizationInvitation).where(func.lower(OrganizationInvitation.admin_email) == email_clean)
        )
        if res_inv.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"L'adresse e-mail '{email_clean}' fait déjà l'objet d'une invitation ou d'un dossier en cours dans le système. Aucun code OTP ne peut être émis."
            )

        # Génération d'un code OTP à 6 chiffres
        otp = f"{secrets.randbelow(900000) + 100000}"
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=15)

        _contact_email_otps[email_clean] = {
            "otp": otp,
            "expires_at": expires_at,
            "is_verified": False
        }

        # Expédition par e-mail
        await email_service.send_otp_verification_email(
            to_email=email_clean,
            otp_code=otp,
            expiration_minutes=15
        )

        return {
            "sent": True,
            "message": "Code OTP de vérification transmis avec succès à l'adresse e-mail officielle de contact.",
            "dev_otp": otp if settings.ENVIRONMENT == "development" else None
        }

    async def verify_contact_email_otp(
        self,
        db: AsyncSession,
        contact_email: str,
        otp: str
    ) -> Dict[str, Any]:
        email_clean = contact_email.strip().lower()
        entry = _contact_email_otps.get(email_clean)

        if not entry:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucun code OTP n'a été émis pour cette adresse e-mail ou le code a expiré."
            )

        now = datetime.now(timezone.utc)
        if entry["expires_at"] < now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Le code OTP a expiré. Veuillez demander un nouveau code."
            )

        if entry["otp"] != otp.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Code OTP incorrect. Veuillez réessayer."
            )

        entry["is_verified"] = True

        return {
            "valid": True,
            "message": "Adresse e-mail officielle de contact vérifiée avec succès."
        }

    async def submit_onboarding(
        self,
        db: AsyncSession,
        user: User,
        submission: OnboardingSubmissionRequest
    ) -> Dict[str, Any]:
        # Validation de conformité des données obligatoires
        if not submission.name or not submission.name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="La dénomination officielle de l'organisation est obligatoire."
            )
        if not submission.code or not submission.code.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Le matricule d'organisation unique est obligatoire."
            )
        if not submission.contact_email or not submission.contact_email.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="L'adresse e-mail officielle de contact est obligatoire."
            )
        if not submission.phone or not submission.phone.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Le numéro de téléphone officiel est obligatoire."
            )

        sub_code = submission.code.strip().upper()

        # 1. Règle stricte 1 compte = 1 organisation
        if user.role == UserRole.SUPER_ADMIN:
            # Le Super Admin peut tester ou créer des organisations sans être bloqué par son compte
            res_existing_org = await db.execute(
                select(Organization).where(Organization.code == sub_code)
            )
            existing_org = res_existing_org.scalars().first()
        else:
            res_existing_org = await db.execute(
                select(Organization).where(Organization.owner_id == user.id)
            )
            existing_org = res_existing_org.scalars().first()
            if not existing_org and user.email:
                res_email_org = await db.execute(
                    select(Organization).where(func.lower(Organization.contact_email) == user.email.lower())
                )
                existing_org = res_email_org.scalars().first()
        
        if existing_org:
            if user.role != UserRole.SUPER_ADMIN and sub_code != existing_org.code:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Vous possédez déjà une organisation ('{existing_org.code}'). La règle stricte 1 compte = 1 organisation s'applique."
                )

            # Règle de non-retransmission : un admin ayant déjà soumis sa demande ne peut plus soumettre à nouveau
            if user.role != UserRole.SUPER_ADMIN:
                if existing_org.status == OrgStatusEnum.PENDING.value:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Votre dossier de demande d'agrément a déjà été transmis et est actuellement en cours d'instruction par les administrateurs SenWiseTool. Vous ne pouvez pas soumettre à nouveau."
                    )
                if existing_org.status == OrgStatusEnum.ACTIVE.value:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Votre organisation est déjà agréée et active sur SenWiseTool. Vous ne pouvez pas soumettre une nouvelle demande."
                    )
                if existing_org.status in [OrgStatusEnum.SUSPENDED.value, OrgStatusEnum.REJECTED.value]:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Ce dossier d'organisation a fait l'objet d'une décision administrative définitive. Vous ne pouvez pas soumettre à nouveau."
                    )

            # Mise à jour fluide du dossier en cours d'instruction ou suite à révision
            existing_org.name = submission.name.strip()
            existing_org.code = sub_code
            existing_org.type = submission.type
            existing_org.legal_doc_name = submission.legal_doc_name
            existing_org.legal_doc_url = submission.legal_doc_url
            existing_org.country = submission.country
            existing_org.region = submission.region
            existing_org.city = submission.city
            existing_org.address = submission.address
            existing_org.postal_code = submission.postal_code
            existing_org.plan = submission.plan
            existing_org.sector = submission.sector
            existing_org.contact_email = submission.contact_email
            existing_org.phone = submission.phone
            existing_org.website = submission.website
            existing_org.logo_url = submission.logo_url
            existing_org.custom_answers = submission.custom_answers
            # Rebasculer en statut PENDING pour ré-examen
            existing_org.status = OrgStatusEnum.PENDING.value
            
            # Nettoyer le brouillon
            await db.execute(delete(OnboardingDraft).where(OnboardingDraft.admin_email == user.email.lower()))
            
            await db.commit()
            await db.refresh(existing_org)

            try:
                await notification_service.create_notification(
                    db=db,
                    title="Dossier d'onboarding mis à jour",
                    message=f"L'organisation '{existing_org.name}' ({existing_org.code}) a déposé son dossier complété pour instruction.",
                    link=f"/dashboard/organizations/{existing_org.id}",
                    type="submission",
                    target_role="superadmin"
                )
            except Exception as e:
                print(f"[NOTIFICATION WARNING] {e}")
            
            try:
                date_str = existing_org.updated_at.strftime("%d/%m/%Y à %H:%M UTC") if existing_org.updated_at else datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC")
                await email_service.send_onboarding_submission_receipt_email(
                    to_email=existing_org.contact_email,
                    org_name=existing_org.name,
                    org_code=existing_org.code,
                    submitted_at_str=date_str,
                    portal_url=f"{settings.FRONTEND_URL}/fr/onboarding/pending?code={existing_org.code}",
                    attestation_url=f"{settings.FRONTEND_URL}/fr/verify/attestation?code={existing_org.code}",
                )
            except Exception as e:
                print(f"[RECEIPT EMAIL WARNING] {e}")

            return {
                "organization_id": existing_org.id,
                "code": existing_org.code,
                "name": existing_org.name,
                "status": existing_org.status,
                "message": "Votre dossier a été enregistré et est en cours d'instruction par l'équipe SWT.",
                "waiting_url": "/onboarding/pending"
            }

        # 2. Vérifier l'unicité du code d'organisation
        res_code = await db.execute(select(Organization).where(Organization.code == sub_code))
        if res_code.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Le matricule d'organisation '{sub_code}' est déjà attribué. Veuillez en choisir un autre."
            )

        # 3. Création de la nouvelle organisation au statut PENDING
        assigned_owner_id = user.id
        if user.role == UserRole.SUPER_ADMIN and submission.contact_email:
            res_contact_user = await db.execute(
                select(User).where(func.lower(User.email) == submission.contact_email.strip().lower())
            )
            target_user = res_contact_user.scalars().first()
            if target_user:
                assigned_owner_id = target_user.id

        # Résolution des questions personnalisées (transmises directement ou issues de l'invitation / brouillon)
        custom_q = submission.custom_questions
        if not custom_q:
            res_inv = await db.execute(
                select(OrganizationInvitation).where(
                    func.lower(OrganizationInvitation.admin_email) == user.email.lower()
                ).order_by(OrganizationInvitation.created_at.desc())
            )
            inv = res_inv.scalars().first()
            if inv and inv.custom_questions:
                custom_q = inv.custom_questions
            else:
                res_draft = await db.execute(
                    select(OnboardingDraft).where(
                        func.lower(OnboardingDraft.admin_email) == user.email.lower()
                    )
                )
                draft = res_draft.scalars().first()
                if draft and draft.draft_data and draft.draft_data.get("customQuestions"):
                    custom_q = draft.draft_data.get("customQuestions")

        new_org = Organization(
            name=submission.name.strip(),
            code=sub_code,
            logo_url=submission.logo_url,
            country=submission.country,
            region=submission.region,
            city=submission.city,
            address=submission.address,
            postal_code=submission.postal_code,
            type=submission.type,
            sector=submission.sector,
            status=OrgStatusEnum.PENDING.value,
            plan=submission.plan,
            owner_id=assigned_owner_id,
            contact_email=submission.contact_email,
            phone=submission.phone,
            website=submission.website,
            legal_doc_url=submission.legal_doc_url,
            legal_doc_name=submission.legal_doc_name,
            custom_questions=custom_q,
            custom_answers=submission.custom_answers,
        )
        db.add(new_org)

        # Nettoyer le brouillon une fois soumis
        draft_emails_to_clean = [user.email.lower()]
        if submission.contact_email:
            draft_emails_to_clean.append(submission.contact_email.strip().lower())
        await db.execute(delete(OnboardingDraft).where(OnboardingDraft.admin_email.in_(draft_emails_to_clean)))

        await db.commit()
        await db.refresh(new_org)

        try:
            await notification_service.create_notification(
                db=db,
                title="Nouvelle demande d'onboarding",
                message=f"L'organisation '{new_org.name}' ({new_org.code}) a déposé son dossier pour instruction.",
                link=f"/dashboard/organizations/{new_org.id}",
                type="submission",
                target_role="superadmin"
            )
        except Exception as e:
            print(f"[NOTIFICATION WARNING] {e}")

        # Expédition immédiate de l'accusé de réception officiel avec lien vers l'attestation scellée
        try:
            date_str = new_org.created_at.strftime("%d/%m/%Y à %H:%M UTC") if new_org.created_at else datetime.now(timezone.utc).strftime("%d/%m/%Y à %H:%M UTC")
            await email_service.send_onboarding_submission_receipt_email(
                to_email=new_org.contact_email,
                org_name=new_org.name,
                org_code=new_org.code,
                submitted_at_str=date_str,
                portal_url=f"{settings.FRONTEND_URL}/fr/onboarding/pending?code={new_org.code}",
                attestation_url=f"{settings.FRONTEND_URL}/fr/verify/attestation?code={new_org.code}",
            )
        except Exception as e:
            print(f"[RECEIPT EMAIL WARNING] {e}")

        return {
            "organization_id": new_org.id,
            "code": new_org.code,
            "name": new_org.name,
            "status": new_org.status,
            "message": "Dossier d'organisation enregistré avec succès. En attente de validation réglementaire par le Super Admin.",
            "waiting_url": "/onboarding/pending"
        }

    async def get_status_for_user(
        self,
        db: AsyncSession,
        user: User,
        code: Optional[str] = None
    ) -> OrganizationStatusCheckResponse:
        org = None
        if code and code.strip():
            res = await db.execute(
                select(Organization).where(Organization.code == code.strip().upper())
            )
            org = res.scalars().first()
            if org:
                # Vérification stricte d'isolation des comptes :
                # Si l'utilisateur connecté n'est pas Super Admin et n'est pas le propriétaire du dossier demandé, interdire l'accès !
                if user.role != UserRole.SUPER_ADMIN and org.owner_id != user.id:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Accès non autorisé : Vous êtes connecté avec le compte '{user.email}', qui n'est pas le propriétaire du dossier de l'organisation '{org.name}' ({org.code}).",
                    )

        if not org:
            if user.role == UserRole.SUPER_ADMIN:
                res = await db.execute(
                    select(Organization).where(Organization.owner_id == user.id).order_by(Organization.created_at.desc())
                )
                org = res.scalars().first()
                if not org:
                    res_any = await db.execute(
                        select(Organization).order_by(Organization.created_at.desc())
                    )
                    org = res_any.scalars().first()
            else:
                from app.services.organization.organization_service import organization_service
                org = await organization_service.get_user_organization(db=db, user=user)

        if not org:
            return OrganizationStatusCheckResponse(
                organization_id=None,
                organization_name=None,
                organization_code=None,
                status="none",
                review_notes=None,
                rejection_reason=None,
                reviewed_at=None,
                reviewed_by_name=None,
                can_edit=True,
                can_access_dashboard=False,
            )

        reviewer_name = None
        if org.reviewed_by_id:
            res_rev = await db.execute(select(User).where(User.id == org.reviewed_by_id))
            reviewer = res_rev.scalars().first()
            if reviewer:
                reviewer_name = reviewer.full_name or reviewer.email

        can_edit = org.status == OrgStatusEnum.REVISION_REQUESTED.value
        can_access = org.status == OrgStatusEnum.ACTIVE.value

        custom_q = getattr(org, "custom_questions", None)
        if not custom_q and org.custom_answers:
            candidate_emails = []
            if org.contact_email:
                candidate_emails.append(org.contact_email.lower())
            if user and user.email:
                candidate_emails.append(user.email.lower())
            if candidate_emails:
                res_inv = await db.execute(
                    select(OrganizationInvitation).where(
                        func.lower(OrganizationInvitation.admin_email).in_(candidate_emails)
                    ).order_by(OrganizationInvitation.created_at.desc())
                )
                inv = res_inv.scalars().first()
                if inv and inv.custom_questions:
                    custom_q = inv.custom_questions

        return OrganizationStatusCheckResponse(
            organization_id=org.id,
            organization_name=org.name,
            organization_code=org.code,
            logo_url=org.logo_url,
            status=org.status,
            review_notes=org.review_notes,
            rejection_reason=org.rejection_reason,
            reviewed_at=org.reviewed_at,
            reviewed_by_name=reviewer_name,
            custom_questions=custom_q,
            can_edit=can_edit,
            can_access_dashboard=can_access,
            country=org.country,
            region=org.region,
            city=org.city,
            address=org.address,
            postal_code=org.postal_code,
            type=org.type,
            sector=org.sector,
            plan=org.plan,
            contact_email=org.contact_email,
            phone=org.phone,
            website=org.website,
            legal_doc_url=org.legal_doc_url,
            legal_doc_name=org.legal_doc_name,
            custom_answers=org.custom_answers,
            created_at=org.created_at,
        )

    async def verify_attestation(
        self,
        db: AsyncSession,
        code: str
    ) -> AttestationVerificationResponse:
        sub_code = code.strip().upper()
        res = await db.execute(select(Organization).where(Organization.code == sub_code))
        org = res.scalars().first()

        if not org:
            return AttestationVerificationResponse(
                authentic=False,
                status="not_found",
                message="Aucun dossier d'organisation homologué ne correspond à ce matricule d'enregistrement.",
            )

        # Masquer l'email pour la confidentialité : e.g. "b***i@necub.com"
        masked_email = None
        if org.contact_email and "@" in org.contact_email:
            parts = org.contact_email.split("@")
            user_part = parts[0]
            domain_part = parts[1]
            if len(user_part) > 2:
                masked_user = f"{user_part[0]}***{user_part[-1]}"
            else:
                masked_user = f"{user_part[0]}***"
            masked_email = f"{masked_user}@{domain_part}"

        # Hash scellé d'authenticité SHA-256
        raw_seal = f"SWT-SEAL:{org.id}:{org.code}:{org.name}:{org.created_at}"
        cert_seal = hashlib.sha256(raw_seal.encode("utf-8")).hexdigest().upper()

        date_str = org.created_at.strftime("%d/%m/%Y à %H:%M:%S UTC") if org.created_at else "Enregistré"

        status_label = {
            OrgStatusEnum.PENDING.value: "Dossier officiellement déposé — En cours d'instruction",
            OrgStatusEnum.REVISION_REQUESTED.value: "En attente de révision / compléments par l'organisation",
            OrgStatusEnum.ACTIVE.value: "Organisation agréée et active sur SenWiseTool",
            OrgStatusEnum.SUSPENDED.value: "Organisation temporairement suspendue",
            OrgStatusEnum.REJECTED.value: "Dossier clôturé",
        }.get(org.status, org.status)

        return AttestationVerificationResponse(
            authentic=True,
            status=org.status,
            message=status_label,
            organization_name=org.name,
            organization_code=org.code,
            logo_url=org.logo_url,
            contact_email_masked=masked_email,
            country=org.country,
            city=org.city,
            type=org.type,
            plan=org.plan,
            submitted_at=date_str,
            certificate_seal=cert_seal,
        )

onboarding_service = OnboardingService()
