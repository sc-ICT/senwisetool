from fastapi import Depends, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
import jwt

from app.db.database import get_db
from app.core import security
from app.models.user.user import User, UserStatus
from app.services.user.user_service import user_service

async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User:
    # Priorité 1 : Header Authorization 'Bearer <token>' (si spécifié explicitement)
    token: str | None = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    
    # Priorité 2 : Cookie HttpOnly 'access_token' (navigation web sécurisée)
    if not token:
        token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Non authentifié. Aucun jeton d'accès valide fourni."
        )

    try:
        payload = security.decode_token(token)
        if payload.get("type") != "access":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Type de jeton invalide.")
        user_id = int(payload.get("sub"))
    except (jwt.PyJWTError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton d'accès expiré ou invalide. Veuillez vous reconnecter."
        )

    user = await user_service.get_by_id(db, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable."
        )

    # Règle impérative : compte actif et e-mail vérifié
    if not user.is_email_verified or user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Le compte n'est pas actif ou l'adresse e-mail n'est pas vérifiée."
        )

    return user

async def require_active_user(
    current_user: User = Depends(get_current_user)
) -> User:
    """Garantit formellement que l'utilisateur a le statut ACTIVE."""
    if current_user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Accès refusé. Statut utilisateur : {current_user.status.value}"
        )
    return current_user

async def require_super_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    """Garantit formellement que l'utilisateur a le rôle SUPER_ADMIN."""
    from app.models.user.user import UserRole
    if current_user.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Action réservée aux super-administrateurs de la plateforme."
        )
    return current_user

async def get_optional_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> User | None:
    """Retourne l'utilisateur connecté s'il existe, ou None sans lever d'exception."""
    try:
        return await get_current_user(request, db)
    except HTTPException:
        return None

