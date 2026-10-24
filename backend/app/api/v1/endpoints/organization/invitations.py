from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import require_super_admin
from app.models.user.user import User
from app.schemas.organization.invitation import (
    OrganizationInvitationCreate,
    OrganizationInvitationResponse,
)
from app.services.organization.invitation_service import invitation_service
from app.core.config import settings

router = APIRouter()

@router.post(
    "/",
    response_model=OrganizationInvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="L'équipe SenWiseTool invite l'administrateur de l'organisation avec délai d'expiration et questionnaire optionnel"
)
async def invite_organization_admin(
    invitation_in: OrganizationInvitationCreate,
    current_super_admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db)
):
    invitation = await invitation_service.create_invitation(
        db=db,
        invitation_in=invitation_in,
        creator_id=current_super_admin.id
    )
    invite_url = f"{settings.FRONTEND_URL}/onboarding/activate?token={invitation.token}"
    
    return OrganizationInvitationResponse(
        id=invitation.id,
        admin_email=invitation.admin_email,
        token=invitation.token,
        token_expires_at=invitation.token_expires_at,
        is_used=invitation.is_used,
        custom_questions=invitation.custom_questions,
        invite_url=invite_url,
        created_at=invitation.created_at,
    )

@router.get(
    "/",
    response_model=List[OrganizationInvitationResponse],
    summary="Lister toutes les invitations émises par l'équipe SenWiseTool"
)

async def list_invitations(
    current_super_admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db)
):
    invitations = await invitation_service.list_invitations(db)
    return [
        OrganizationInvitationResponse(
            id=inv.id,
            admin_email=inv.admin_email,
            token=inv.token,
            token_expires_at=inv.token_expires_at,
            is_used=inv.is_used,
            custom_questions=inv.custom_questions,
            invite_url=f"{settings.FRONTEND_URL}/onboarding/activate?token={inv.token}",
            created_at=inv.created_at,
        )
        for inv in invitations
    ]

@router.post(
    "/{id}/resend",
    response_model=OrganizationInvitationResponse,
    summary="Renvoyer l'email d'invitation officiel et prolonger sa validité de 48 heures"
)
async def resend_invitation(
    id: int,
    current_super_admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db)
):
    invitation = await invitation_service.resend_invitation(db=db, invitation_id=id)
    return OrganizationInvitationResponse(
        id=invitation.id,
        admin_email=invitation.admin_email,
        token=invitation.token,
        token_expires_at=invitation.token_expires_at,
        is_used=invitation.is_used,
        custom_questions=invitation.custom_questions,
        invite_url=f"{settings.FRONTEND_URL}/onboarding/activate?token={invitation.token}",
        created_at=invitation.created_at,
    )

@router.delete(
    "/{id}",
    status_code=status.HTTP_200_OK,
    summary="Annuler et révoquer une invitation d'organisation non finalisée"
)
async def delete_invitation(
    id: int,
    current_super_admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db)
):
    await invitation_service.delete_invitation(db=db, invitation_id=id)
    return {"success": True, "message": "Invitation révoquée avec succès."}

