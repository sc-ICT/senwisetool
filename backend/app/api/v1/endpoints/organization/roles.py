from typing import List, Dict, Any
from fastapi import APIRouter, Depends, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import get_current_user
from app.models.user.user import User
from app.schemas.organization.role import (
    RoleCreateRequest,
    RoleUpdateRequest,
    RoleResponse,
)
from app.services.organization.member_service import member_service
from app.api.v1.endpoints.organization.members import resolve_org_id

router = APIRouter()

@router.get(
    "/{org_id}/roles",
    response_model=List[RoleResponse],
    summary="Lister les rôles locaux de l'organisation (ORG-04)"
)
async def list_organization_roles(
    org_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    resolved_id = await resolve_org_id(org_id, current_user, db)
    roles = await member_service.list_roles(db=db, org_id=resolved_id)
    return [RoleResponse.model_validate(r) for r in roles]

@router.post(
    "/{org_id}/roles",
    response_model=RoleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un rôle personnalisé avec sa grille de permissions (ORG-04)"
)
async def create_organization_role(
    org_id: str,
    payload: RoleCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    resolved_id = await resolve_org_id(org_id, current_user, db)
    created = await member_service.create_role(
        db=db,
        org_id=resolved_id,
        data=payload
    )
    return RoleResponse(
        id=created.id,
        organization_id=created.organization_id,
        name=created.name,
        code=created.code,
        description=created.description,
        members_count=0,
        status=created.status,
        is_system=created.is_system,
        permissions=created.permissions or {},
        created_at=created.created_at
    )

@router.put(
    "/{org_id}/roles/{role_id}",
    response_model=RoleResponse,
    summary="Mettre à jour un rôle local"
)
async def update_organization_role(
    org_id: str,
    role_id: int,
    payload: RoleUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    resolved_id = await resolve_org_id(org_id, current_user, db)
    updated = await member_service.update_role(
        db=db,
        org_id=resolved_id,
        role_id=role_id,
        data=payload
    )
    return RoleResponse(
        id=updated.id,
        organization_id=updated.organization_id,
        name=updated.name,
        code=updated.code,
        description=updated.description,
        members_count=0,
        status=updated.status,
        is_system=updated.is_system,
        permissions=updated.permissions or {},
        created_at=updated.created_at
    )

@router.delete(
    "/{org_id}/roles/{role_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer un rôle personnalisé"
)
async def delete_organization_role(
    org_id: str,
    role_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    resolved_id = await resolve_org_id(org_id, current_user, db)
    await member_service.delete_role(
        db=db,
        org_id=resolved_id,
        role_id=role_id
    )
    return None

@router.get(
    "/{org_id}/permissions/matrix",
    summary="Obtenir la matrice comparative rôles / permissions (ORG-05)"
)
async def get_permissions_matrix(
    org_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    resolved_id = await resolve_org_id(org_id, current_user, db)
    matrix = await member_service.get_permissions_matrix(db=db, org_id=resolved_id)
    return matrix
