from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import require_active_user
from app.models.user.user import User
from app.schemas.user.user import UserRead, UserUpdate, ChangePasswordRequest
from app.services.user.user_service import user_service

router = APIRouter()

@router.get(
    "/me",
    response_model=UserRead,
    summary="Profil utilisateur courant",
    description="Retourne le profil détaillé de l'utilisateur authentifié."
)
async def get_my_profile(
    current_user: User = Depends(require_active_user)
) -> Any:
    return UserRead.model_validate(current_user)

@router.put(
    "/me",
    response_model=UserRead,
    summary="Mise à jour du profil",
    description="Met à jour le nom complet ou l'adresse e-mail de l'utilisateur connecté."
)
async def update_my_profile(
    user_in: UserUpdate,
    current_user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db)
) -> Any:
    updated_user = await user_service.update_profile(db, current_user, user_in)
    return UserRead.model_validate(updated_user)

@router.post(
    "/me/change-password",
    summary="Changement de mot de passe",
    description="Modifie le mot de passe de l'utilisateur après vérification du mot de passe actuel."
)
async def change_my_password(
    password_data: ChangePasswordRequest,
    current_user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    await user_service.change_password(
        db,
        current_user,
        password_data.current_password,
        password_data.new_password
    )
    return {"status": "success", "message": "Mot de passe modifié avec succès."}
