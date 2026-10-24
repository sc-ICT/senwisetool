import secrets
from datetime import datetime, timezone, timedelta
from typing import Any
from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
import jwt

from app.core.config import settings
from app.core import security
from app.models.user.user import UserStatus
from app.models.auth.refresh_token import RefreshToken
from app.models.auth.trusted_device import TrustedDevice
from app.services.user.user_service import user_service

class MfaService:
    @staticmethod
    async def verify_mfa_challenge(
        db: AsyncSession,
        mfa_token: str,
        code: str,
        remember_device: bool = False,
        ip_address: str | None = None,
        device_info: str | None = None
    ) -> dict[str, Any]:
        """
        Valide le code à 6 chiffres TOTP lors d'un challenge MFA.
        En cas de succès : émet la session unique et enregistre éventuellement l'appareil pour 30 jours.
        """
        now = datetime.now(timezone.utc)

        # Décodage et validation du mfa_token
        try:
            payload = security.decode_token(mfa_token)
            if payload.get("type") != "mfa":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Jeton MFA invalide.")
            user_id = int(payload.get("sub"))
        except (jwt.PyJWTError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"field": "code", "message": "Session de vérification MFA expirée. Veuillez vous reconnecter."}
            )

        user = await user_service.get_by_id(db, user_id)
        if not user or user.status != UserStatus.ACTIVE or not user.is_email_verified:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Compte non autorisé ou non vérifié."
            )

        if not user.mfa_secret or not security.verify_totp(user.mfa_secret, code):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"field": "code", "message": "Code de vérification invalide ou expiré."}
            )

        # Single Active Session : Révocation des sessions antérieures
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == user.id, RefreshToken.is_revoked == False)
            .values(is_revoked=True)
        )

        # Émission des tokens de session
        access_token = security.create_access_token(
            subject=user.id,
            extra_claims={"email": user.email, "role": user.role.value, "status": user.status.value}
        )
        refresh_token, expires_at = security.create_refresh_token(subject=user.id, remember_me=False)

        db_refresh = RefreshToken(
            user_id=user.id,
            token_hash=security.hash_token(refresh_token),
            device_info=device_info,
            ip_address=ip_address,
            expires_at=expires_at,
            is_revoked=False
        )
        db.add(db_refresh)

        # Mémorisation de l'appareil (30 jours)
        trusted_device_token: str | None = None
        if remember_device:
            trusted_device_token = secrets.token_urlsafe(32)
            db_device = TrustedDevice(
                user_id=user.id,
                device_token_hash=security.hash_token(trusted_device_token),
                device_name=device_info or "Navigateur Web",
                ip_address=ip_address,
                expires_at=now + timedelta(days=30)
            )
            db.add(db_device)

        user.last_login_at = now
        await db.commit()
        await db.refresh(user)

        return {
            "status": "success",
            "access_token": access_token,
            "refresh_token": refresh_token,
            "user": user,
            "trusted_device_token": trusted_device_token
        }

mfa_service = MfaService()
