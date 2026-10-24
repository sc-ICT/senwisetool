from datetime import datetime, timezone, timedelta
from typing import Any
from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
import jwt

from app.core.config import settings
from app.core import security
from app.models.user.user import User, UserStatus
from app.models.auth.refresh_token import RefreshToken
from app.services.user.user_service import user_service

class AuthService:
    @staticmethod
    async def authenticate(
        db: AsyncSession,
        identifier: str,
        password: str,
        remember_me: bool = False,
        ip_address: str | None = None,
        device_info: str | None = None
    ) -> dict[str, Any]:
        """
        Authentifie un utilisateur avec règles de sécurité strictes :
        - Détection anti brute-force
        - Vérification e-mail vérifié + statut ACTIVE
        - Challenge MFA si activé
        - Révocation des sessions antérieures (Single Active Session Policy)
        """
        now = datetime.now(timezone.utc)
        
        # 1. Recherche de l'utilisateur par identifiant (email ou username)
        user = await user_service.get_by_identifier(db, identifier)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"field": "identifier", "message": "Identifiant ou mot de passe incorrect."}
            )

        # 2. Vérification du verrouillage du compte (Anti brute-force)
        if user.locked_until and user.locked_until > now:
            remaining_seconds = int((user.locked_until - now).total_seconds())
            remaining_minutes = max(1, remaining_seconds // 60)
            raise HTTPException(
                status_code=status.HTTP_423_LOCKED,
                detail={
                    "field": "identifier",
                    "code": "ACCOUNT_LOCKED",
                    "message": f"Compte temporairement verrouillé pour des raisons de sécurité. Veuillez réessayer dans {remaining_minutes} minute(s).",
                    "remaining_minutes": remaining_minutes
                }
            )

        # 3. Vérification du mot de passe
        if not security.verify_password(password, user.hashed_password):
            user.failed_login_attempts += 1
            if user.failed_login_attempts >= settings.MAX_FAILED_LOGIN_ATTEMPTS:
                user.locked_until = now + timedelta(minutes=settings.ACCOUNT_LOCKOUT_MINUTES)
                # Sécurité : révocation immédiate de toute session existante
                await db.execute(
                    update(RefreshToken)
                    .where(RefreshToken.user_id == user.id, RefreshToken.is_revoked == False)
                    .values(is_revoked=True)
                )
                await db.commit()
                raise HTTPException(
                    status_code=status.HTTP_423_LOCKED,
                    detail={
                        "field": "identifier",
                        "code": "ACCOUNT_LOCKED",
                        "message": f"Compte verrouillé pour {settings.ACCOUNT_LOCKOUT_MINUTES} minutes suite à {settings.MAX_FAILED_LOGIN_ATTEMPTS} tentatives infructueuses consécutives.",
                        "remaining_minutes": settings.ACCOUNT_LOCKOUT_MINUTES
                    }
                )
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"field": "password", "message": "Identifiant ou mot de passe incorrect."}
            )

        # 4. Mot de passe valide : Réinitialisation du compteur d'échecs
        user.failed_login_attempts = 0
        user.locked_until = None

        # 5. Règle impérative : Vérification de l'e-mail
        if not user.is_email_verified:
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "field": "identifier",
                    "code": "EMAIL_NOT_VERIFIED",
                    "message": "Votre adresse e-mail n'a pas encore été vérifiée. Veuillez confirmer votre e-mail avant de vous connecter."
                }
            )

        # 6. Règle impérative : Seul le statut ACTIVE donne accès à la plateforme
        if user.status != UserStatus.ACTIVE:
            status_val = user.status.value
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "field": "identifier",
                    "code": "ACCOUNT_NOT_ACTIVE",
                    "message": f"Votre compte est actuellement avec le statut '{status_val}'. L'accès au tableau de bord est refusé."
                }
            )

        # 7. Challenge MFA (si configuré pour l'utilisateur)
        if user.mfa_enabled and user.mfa_secret:
            mfa_token = security.create_mfa_token(user.id)
            await db.commit()
            return {
                "status": "mfa_required",
                "mfa_token": mfa_token,
                "user": user,
                "expires_in": settings.MFA_TOKEN_EXPIRE_MINUTES * 60
            }

        # 8. Single Active Session : Révocation de toutes les sessions actives antérieures
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.is_revoked == False)
            .values(is_revoked=True)
        )

        # 9. Émission de la nouvelle session (Tokens)
        access_token = security.create_access_token(
            subject=user.id,
            extra_claims={"email": user.email, "role": user.role.value, "status": user.status.value}
        )
        refresh_token, expires_at = security.create_refresh_token(subject=user.id, remember_me=remember_me)

        # Enregistrement du hash du refresh token en base
        db_refresh = RefreshToken(
            user_id=user.id,
            token_hash=security.hash_token(refresh_token),
            device_info=device_info,
            ip_address=ip_address,
            expires_at=expires_at,
            is_revoked=False
        )
        db.add(db_refresh)

        # Mise à jour de la dernière connexion
        user.last_login_at = now
        await db.commit()
        await db.refresh(user)

        return {
            "status": "success",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": user,
            "remember_me": remember_me
        }

    @staticmethod
    async def refresh_session(
        db: AsyncSession,
        refresh_token_str: str,
        ip_address: str | None = None,
        device_info: str | None = None
    ) -> dict[str, Any]:
        """
        Rafraîchit la session de manière silencieuse et transparente avec rotation de token.
        Si le token est révoqué ou falsifié, éjection immédiate.
        """
        now = datetime.now(timezone.utc)

        # Décodage du JWT Refresh
        try:
            payload = security.decode_token(refresh_token_str)
            if payload.get("type") != "refresh":
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Jeton de rafraîchissement invalide.")
            user_id = int(payload.get("sub"))
            remember_me = bool(payload.get("remember_me", False))
        except (jwt.PyJWTError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expirée ou invalide. Veuillez vous reconnecter."
            )

        # Recherche du token par son hash
        token_hash = security.hash_token(refresh_token_str)
        result = await db.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        db_token = result.scalars().first()

        # Si le token est absent ou révoqué -> Possible tentative de vol de session
        if not db_token or db_token.is_revoked:
            if db_token and db_token.is_revoked:
                # Alerte de sécurité : révocation globale par précaution
                await db.execute(
                    update(RefreshToken)
                    .where(RefreshToken.user_id == user_id, RefreshToken.is_revoked == False)
                    .values(is_revoked=True)
                )
                await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session révoquée. Veuillez vous reconnecter."
            )

        # Vérification expiration
        if db_token.expires_at <= now:
            db_token.is_revoked = True
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expirée. Veuillez vous reconnecter."
            )

        # Récupération et vérification de l'utilisateur
        user = await user_service.get_by_id(db, user_id)
        if not user or user.status != UserStatus.ACTIVE or not user.is_email_verified:
            db_token.is_revoked = True
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Le compte utilisateur n'est plus actif ou vérifié. Déconnexion immédiate."
            )

        # Rotation du Refresh Token : L'ancien est révoqué
        db_token.is_revoked = True

        # Émission des nouveaux jetons
        new_access_token = security.create_access_token(
            subject=user.id,
            extra_claims={"email": user.email, "role": user.role.value, "status": user.status.value}
        )
        new_refresh_token, new_expires_at = security.create_refresh_token(subject=user.id, remember_me=remember_me)

        # Enregistrement du nouveau refresh token
        new_db_refresh = RefreshToken(
            user_id=user.id,
            token_hash=security.hash_token(new_refresh_token),
            device_info=device_info or db_token.device_info,
            ip_address=ip_address or db_token.ip_address,
            expires_at=new_expires_at,
            is_revoked=False
        )
        db.add(new_db_refresh)
        await db.commit()

        return {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "user": user,
            "remember_me": remember_me
        }

    @staticmethod
    async def logout(db: AsyncSession, refresh_token_str: str | None) -> None:
        """Révoque la session active lors de la déconnexion."""
        if not refresh_token_str:
            return
        token_hash = security.hash_token(refresh_token_str)
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.token_hash == token_hash)
            .values(is_revoked=True)
        )
        await db.commit()

auth_service = AuthService()
