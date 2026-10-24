from typing import List, Optional
from fastapi import APIRouter, Depends, status, Query, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.db.database import get_db
from app.api.deps import get_current_user
from app.models.user.user import User, UserRole
from app.models.organization.organization import Organization
from app.models.organization.member import OrganizationMember
from app.schemas.organization.member import (
    MemberInviteRequest,
    MemberUpdateRequest,
    MemberResponse,
    MemberInvitationVerifyResponse,
    MemberInvitationAcceptRequest,
    MemberInvitationAcceptResponse,
)
from app.core.security import set_auth_cookies
from app.services.organization.organization_service import organization_service
from app.services.organization.member_service import member_service

router = APIRouter()

@router.get(
    "/members/invitations/verify",
    response_model=MemberInvitationVerifyResponse,
    summary="Vérifier la validité d'un jeton d'invitation de membre"
)
async def verify_member_invitation_endpoint(
    token: str = Query(..., description="Jeton sécurisé d'invitation"),
    db: AsyncSession = Depends(get_db)
):
    result = await member_service.verify_member_invitation(db=db, token=token)
    return MemberInvitationVerifyResponse(**result)

@router.post(
    "/members/invitations/accept",
    response_model=MemberInvitationAcceptResponse,
    summary="Accepter l'invitation, définir le mot de passe et activer le compte"
)
async def accept_member_invitation_endpoint(
    payload: MemberInvitationAcceptRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db)
):
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")

    result = await member_service.accept_member_invitation(
        db=db,
        token=payload.token,
        password=payload.password,
        full_name=payload.full_name,
        ip_address=client_ip,
        device_info=user_agent
    )

    set_auth_cookies(
        response=response,
        access_token=result["access_token"],
        refresh_token=result["refresh_token"],
        remember_me=True
    )

    return MemberInvitationAcceptResponse(
        status="success",
        message=result["message"],
        redirect_url=result.get("redirect_url", "/dashboard/my-organization")
    )


async def resolve_org_id(org_id_or_me: str, current_user: User, db: AsyncSession) -> int:
    """Résout l'ID d'organisation ('me' ou entier) et valide les droits d'accès."""
    if org_id_or_me == "me":
        # 1. Vérifier si l'utilisateur est propriétaire d'une organisation
        org = await organization_service.get_by_owner_id(db=db, owner_id=current_user.id)
        if org:
            return org.id

        # 2. Vérifier s'il est membre d'une organisation
        mem_res = await db.execute(
            select(OrganizationMember).where(
                (OrganizationMember.user_id == current_user.id) |
                (OrganizationMember.email == current_user.email)
            )
        )
        mem = mem_res.scalars().first()
        if mem:
            return mem.organization_id

        # 3. Si Super Admin sans organisation propre, prendre la première organisation active
        if current_user.role == UserRole.SUPER_ADMIN:
            first_org_res = await db.execute(select(Organization).limit(1))
            first_org = first_org_res.scalars().first()
            if first_org:
                return first_org.id

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aucune organisation active associée à ce compte."
        )

    try:
        target_id = int(org_id_or_me)
    except ValueError:
        raise HTTPException(status_code=400, detail="Identifiant d'organisation invalide.")

    # Contrôle de sécurité RBAC
    if current_user.role != UserRole.SUPER_ADMIN:
        own_org = await organization_service.get_by_owner_id(db=db, owner_id=current_user.id)
        if not own_org or own_org.id != target_id:
            # Vérifier si membre
            mem_res = await db.execute(
                select(OrganizationMember).where(
                    OrganizationMember.organization_id == target_id,
                    (OrganizationMember.user_id == current_user.id) | (OrganizationMember.email == current_user.email)
                )
            )
            if not mem_res.scalars().first():
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès interdit aux membres de cette organisation."
                )

    return target_id

def _to_member_response(m: OrganizationMember) -> MemberResponse:
    roles = m.roles_list if m.roles_list else [m.role_code]
    projects = m.accessible_projects if m.accessible_projects else ["Tous les projets"]
    return MemberResponse(
        id=m.id,
        organization_id=m.organization_id,
        user_id=m.user_id,
        email=m.email,
        name=m.name or m.email.split("@")[0],
        role_code=m.role_code,
        is_owner=getattr(m, "is_owner", False),
        roles=roles,
        accessible_projects=projects,
        status=m.status,
        mfa_enabled=m.mfa_enabled,
        last_login_at=m.last_login_at,
        last_device=m.last_device,
        invited_at=m.invited_at,
        joined_at=m.joined_at,
        created_at=m.created_at
    )

@router.get(
    "/{org_id}/members",
    response_model=List[MemberResponse],
    summary="Lister les membres d'une organisation (ORG-03)"
)
async def list_organization_members(
    org_id: str,
    status_filter: Optional[str] = Query(None, alias="status", description="Tous, Actifs, Suspendus, Invités"),
    search: Optional[str] = Query(None, description="Recherche par nom, email ou rôle"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    resolved_id = await resolve_org_id(org_id, current_user, db)
    members = await member_service.list_members(
        db=db,
        org_id=resolved_id,
        status_filter=status_filter,
        search=search,
        skip=skip,
        limit=limit
    )
    return [_to_member_response(m) for m in members]

@router.post(
    "/{org_id}/members/invite",
    response_model=MemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Inviter un nouveau membre avec contrainte d'organisation unique"
)
async def invite_organization_member(
    org_id: str,
    payload: MemberInviteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    resolved_id = await resolve_org_id(org_id, current_user, db)
    new_member = await member_service.invite_member(
        db=db,
        org_id=resolved_id,
        data=payload
    )
    return _to_member_response(new_member)

@router.post(
    "/{org_id}/members/{member_id}/resend-invitation",
    response_model=MemberResponse,
    summary="Renvoyer l'e-mail d'invitation à un membre non encore activé"
)
async def resend_organization_member_invitation(
    org_id: str,
    member_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    resolved_id = await resolve_org_id(org_id, current_user, db)
    updated = await member_service.resend_member_invitation(
        db=db,
        org_id=resolved_id,
        member_id=member_id
    )
    return _to_member_response(updated)

@router.put(
    "/{org_id}/members/{member_id}",
    response_model=MemberResponse,
    summary="Mettre à jour les informations, rôles ou statut d'un membre (ORG-06)"
)
async def update_organization_member(
    org_id: str,
    member_id: int,
    payload: MemberUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    resolved_id = await resolve_org_id(org_id, current_user, db)
    updated = await member_service.update_member(
        db=db,
        org_id=resolved_id,
        member_id=member_id,
        data=payload
    )
    return _to_member_response(updated)

@router.delete(
    "/{org_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retirer un membre de l'organisation"
)
async def delete_organization_member(
    org_id: str,
    member_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    resolved_id = await resolve_org_id(org_id, current_user, db)
    await member_service.delete_member(
        db=db,
        org_id=resolved_id,
        member_id=member_id
    )
    return None
