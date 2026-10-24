import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func, delete

from app.core.config import settings
from app.core.security import get_password_hash, create_access_token, create_refresh_token, hash_token
from app.models.auth.refresh_token import RefreshToken
from app.models.organization.invitation import OrganizationInvitation
from app.models.organization.organization import Organization
from app.models.user.user import User, UserRole, UserStatus
from app.schemas.organization.invitation import (
    CustomQuestionSchema,
    OrganizationInvitationCreate,
    SetupFirstPasswordRequest,
)
from app.services.email.email_service import email_service

class InvitationService:
    async def create_invitation(
        self,
        db: AsyncSession,
        invitation_in: OrganizationInvitationCreate,
        creator_id: Optional[int] = None
    ) -> OrganizationInvitation:
        admin_email = invitation_in.admin_email.strip().lower()

        # Règle d'unicité globale : l'email ne doit être utilisé ni par un utilisateur, ni par une organisation
        result_user = await db.execute(select(User).where(func.lower(User.email) == admin_email))
        if result_user.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"L'adresse e-mail '{admin_email}' est déjà utilisée par un compte utilisateur dans le système. L'adresse e-mail doit être strictement unique."
            )

        result_org = await db.execute(select(Organization).where(func.lower(Organization.contact_email) == admin_email))
        if result_org.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"L'adresse e-mail '{admin_email}' est déjà enregistrée comme contact officiel d'une organisation. L'adresse e-mail doit être strictement unique."
            )


        # Générer un jeton cryptographiquement sécurisé
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=invitation_in.expiration_hours)

        # Vérifier si une invitation antérieure existe pour cet email
        result_inv = await db.execute(
            select(OrganizationInvitation).where(func.lower(OrganizationInvitation.admin_email) == admin_email)
        )
        existing_inv = result_inv.scalars().first()
        if existing_inv:
            if existing_inv.is_used:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"L'adresse e-mail '{admin_email}' a déjà été utilisée pour activer une organisation dans le système. L'adresse e-mail doit être strictement unique."
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Une invitation est déjà en cours pour l'adresse e-mail '{admin_email}'. Vous pouvez relancer l'invitation existante depuis la console des organisations."
                )

        questions_dict = [q.model_dump() for q in invitation_in.custom_questions] if invitation_in.custom_questions else None

        invitation = OrganizationInvitation(
            admin_email=admin_email,
            token=token,
            token_expires_at=expires_at,
            custom_questions=questions_dict,
            is_used=False,
            created_by_id=creator_id,
        )
        db.add(invitation)

        await db.commit()
        await db.refresh(invitation)

        # Envoi officiel du courriel d'invitation avec lien unique
        invite_url = f"{settings.FRONTEND_URL}/onboarding/activate?token={invitation.token}"
        custom_q_count = len(invitation_in.custom_questions) if invitation_in.custom_questions else 0
        await email_service.send_organization_invitation_email(
            to_email=invitation.admin_email,
            invite_url=invite_url,
            expiration_hours=invitation_in.expiration_hours,
            custom_questions_count=custom_q_count,
        )

        return invitation

    async def list_invitations(self, db: AsyncSession) -> List[OrganizationInvitation]:
        result = await db.execute(select(OrganizationInvitation).order_by(OrganizationInvitation.created_at.desc()))
        return list(result.scalars().all())

    async def verify_token_and_send_otp(
        self,
        db: AsyncSession,
        token: str,
        email: str,
        current_user: Optional[User] = None
    ) -> Dict[str, Any]:
        result = await db.execute(select(OrganizationInvitation).where(OrganizationInvitation.token == token))
        invitation = result.scalars().first()

        if not invitation:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Lien d'invitation invalide ou introuvable."
            )

        if current_user and current_user.role != UserRole.SUPER_ADMIN:
            if current_user.email.strip().lower() != invitation.admin_email.strip().lower():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Conflit de compte : Vous êtes actuellement connecté avec le compte '{current_user.email}'. Cette invitation d'organisation est exclusivement réservée à '{invitation.admin_email}'. Veuillez vous déconnecter avant de poursuivre."
                )

        if invitation.is_used:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Ce lien d'invitation a déjà été utilisé pour créer une organisation."
            )

        now = datetime.now(timezone.utc)
        if invitation.token_expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Ce lien d'invitation a expiré. Veuillez solliciter un nouveau lien auprès du Super Administrateur."
            )

        if invitation.admin_email.lower() != email.strip().lower():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="L'adresse email saisie ne correspond pas au destinataire de cette invitation."
            )

        email_clean = email.strip().lower()
        res_user = await db.execute(
            select(User).where(func.lower(User.email) == email_clean)
        )
        if res_user.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"L'adresse e-mail '{email_clean}' est déjà associée à un compte utilisateur dans le système. Aucun code OTP ne peut être envoyé."
            )

        res_org = await db.execute(
            select(Organization).where(func.lower(Organization.contact_email) == email_clean)
        )
        if res_org.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"L'adresse e-mail '{email_clean}' est déjà enregistrée comme contact officiel d'une organisation dans le système. Aucun code OTP ne peut être envoyé."
            )

        # Génération du code OTP à 6 chiffres
        otp = f"{secrets.randbelow(900000) + 100000}"
        otp_expiry = now + timedelta(minutes=15)
        
        invitation.otp_code = otp
        invitation.otp_expires_at = otp_expiry
        invitation.otp_verified = False
        await db.commit()

        # Envoi officiel du courriel contenant le code OTP
        await email_service.send_otp_verification_email(
            to_email=invitation.admin_email,
            otp_code=otp,
            expiration_minutes=15,
        )

        expires_delta = invitation.token_expires_at - now
        hours_remaining = max(0.1, round(expires_delta.total_seconds() / 3600, 1))

        return {
            "valid": True,
            "admin_email": invitation.admin_email,
            "otp_sent": True,
            "expires_in_hours": hours_remaining,
            "has_custom_questions": bool(invitation.custom_questions and len(invitation.custom_questions) > 0),
            "custom_questions": invitation.custom_questions,
            "message": "Code de vérification OTP transmis avec succès à votre adresse email.",
            # En développement, on retourne aussi l'OTP pour faciliter les tests automatisés
            "dev_otp": otp
        }

    async def verify_otp(
        self,
        db: AsyncSession,
        token: str,
        email: str,
        otp: str
    ) -> Dict[str, Any]:
        result = await db.execute(select(OrganizationInvitation).where(OrganizationInvitation.token == token))
        invitation = result.scalars().first()

        if not invitation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Jeton d'invitation invalide.")

        if invitation.admin_email.lower() != email.strip().lower():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Adresse email non concordante.")

        now = datetime.now(timezone.utc)
        if not invitation.otp_code or not invitation.otp_expires_at or invitation.otp_expires_at < now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Le code OTP a expiré. Veuillez demander un nouveau code."
            )

        if invitation.otp_code.strip() != otp.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Code OTP incorrect. Veuillez réessayer."
            )

        invitation.otp_verified = True
        await db.commit()

        # Ticket de session sécurisé pour l'étape de création de mot de passe
        session_ticket = secrets.token_hex(24)

        return {
            "valid": True,
            "session_ticket": session_ticket,
            "message": "Identité vérifiée avec succès. Vous pouvez maintenant définir votre mot de passe d'administration."
        }

    async def setup_first_password(
        self,
        db: AsyncSession,
        req: SetupFirstPasswordRequest
    ) -> Dict[str, Any]:
        result = await db.execute(select(OrganizationInvitation).where(OrganizationInvitation.token == req.token))
        invitation = result.scalars().first()

        if not invitation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Jeton d'invitation invalide.")

        if invitation.is_used:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail="Ce lien d'invitation a déjà été utilisé pour créer et activer un compte."
            )

        if not invitation.otp_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="L'adresse email doit être validée par OTP avant de définir le mot de passe."
            )

        email = req.email.strip().lower()
        if invitation.admin_email.lower() != email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Adresse email incorrecte.")

        # Règle stricte 1 compte = 1 organisation : vérifier si un compte utilisateur existe
        res_user = await db.execute(select(User).where(User.email == email))
        user = res_user.scalars().first()

        if user:
            # Vérifier s'il possède déjà une organisation
            res_org = await db.execute(select(Organization).where(Organization.owner_id == user.id))
            if res_org.scalars().first():
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Vous possédez déjà une organisation liée à ce compte. Règle 1 compte = 1 organisation."
                )
            user.hashed_password = get_password_hash(req.password)
            user.role = UserRole.ADMIN_ORG
            user.status = UserStatus.ACTIVE
            user.is_email_verified = True
            user.full_name = req.full_name.strip()
        else:
            username = email.split("@")[0] + "_" + secrets.token_hex(3)
            user = User(
                email=email,
                username=username,
                hashed_password=get_password_hash(req.password),
                full_name=req.full_name.strip(),
                role=UserRole.ADMIN_ORG,
                status=UserStatus.ACTIVE,
                is_email_verified=True,
            )
            db.add(user)

        # Consommer l'invitation
        invitation.is_used = True
        invitation.used_at = datetime.now(timezone.utc)

        # Flush pour obtenir l'ID de l'utilisateur s'il vient d'être inséré
        await db.flush()

        # Règle de sécurité : Révocation des sessions antérieures (Single Active Session)
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.is_revoked == False)
            .values(is_revoked=True)
        )

        # Générer la session complète (Access Token + Refresh Token sécurisé)
        access_token = create_access_token(
            subject=user.id,
            extra_claims={"email": user.email, "role": user.role.value, "status": user.status.value, "org_onboarding": True}
        )
        refresh_token, expires_at = create_refresh_token(subject=user.id, remember_me=False)

        # Enregistrement du hash du refresh token en base
        db_refresh = RefreshToken(
            user_id=user.id,
            token_hash=hash_token(refresh_token),
            expires_at=expires_at,
            is_revoked=False
        )
        db.add(db_refresh)

        # Mettre à jour la date de dernière connexion
        user.last_login_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(user)

        return {
            "success": True,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "user_id": user.id,
            "email": user.email,
            "redirect_url": "/onboarding",
            "custom_questions": invitation.custom_questions,
        }

    async def resend_invitation(self, db: AsyncSession, invitation_id: int) -> OrganizationInvitation:
        invitation = await db.get(OrganizationInvitation, invitation_id)
        if not invitation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation introuvable.")

        # Prolonger la validité et régénérer le jeton
        invitation.token = secrets.token_urlsafe(32)
        invitation.token_expires_at = datetime.now(timezone.utc) + timedelta(hours=48)
        invitation.otp_code = None
        invitation.otp_expires_at = None
        invitation.otp_verified = False
        await db.commit()
        await db.refresh(invitation)

        # Envoi de l'email
        invite_url = f"{settings.FRONTEND_URL}/onboarding/activate?token={invitation.token}"
        custom_q_count = len(invitation.custom_questions) if invitation.custom_questions else 0
        await email_service.send_organization_invitation_email(
            to_email=invitation.admin_email,
            invite_url=invite_url,
            expiration_hours=48,
            custom_questions_count=custom_q_count,
        )
        return invitation

    async def delete_invitation(self, db: AsyncSession, invitation_id: int) -> bool:
        invitation = await db.get(OrganizationInvitation, invitation_id)
        if not invitation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invitation introuvable.")

        from app.models.organization.onboarding_draft import OnboardingDraft
        # Supprimer le brouillon associé si existant
        await db.execute(delete(OnboardingDraft).where(OnboardingDraft.admin_email == invitation.admin_email.lower()))
        await db.delete(invitation)
        await db.commit()
        return True

invitation_service = InvitationService()

