from typing import List, Optional
from fastapi import APIRouter, Depends, status, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import get_current_user, require_super_admin
from app.models.user.user import User, UserRole
from app.schemas.organization.organization import (
    OrganizationResponse,
    OrganizationUpdateProfile,
    OrganizationReviewActionRequest,
    OrganizationMetricsResponse,
)
from app.services.organization.organization_service import organization_service

router = APIRouter()

def _to_org_response(org) -> OrganizationResponse:
    if isinstance(org, dict):
        return OrganizationResponse.model_validate(org)

    owner_name = None
    owner_email = None
    if getattr(org, "owner", None):
        owner_name = org.owner.full_name or org.owner.username
        owner_email = org.owner.email

    data = {
        "id": org.id,
        "name": org.name,
        "code": org.code,
        "logo_url": org.logo_url,
        "country": org.country,
        "region": org.region,
        "city": org.city,
        "address": org.address,
        "postal_code": org.postal_code,
        "type": org.type,
        "sector": org.sector,
        "status": org.status,
        "plan": org.plan,
        "owner_id": org.owner_id,
        "owner_name": owner_name,
        "owner_email": owner_email,
        "contact_email": org.contact_email,
        "phone": org.phone,
        "website": org.website,
        "legal_doc_url": org.legal_doc_url,
        "legal_doc_name": org.legal_doc_name,
        "custom_answers": org.custom_answers,
        "review_notes": org.review_notes,
        "reviewed_at": org.reviewed_at,
        "rejection_reason": org.rejection_reason,
        "members_count": org.members_count,
        "storage_used_gb": org.storage_used_gb,
        "storage_limit_gb": org.storage_limit_gb,
        "iot_nodes_count": org.iot_nodes_count,
        "created_at": org.created_at,
        "updated_at": org.updated_at,
        "activated_at": (org.reviewed_at or org.created_at) if org.status == "active" else None,
        "custom_questions": getattr(org, "custom_questions", None),
        "is_in_creation": False,
    }
    return OrganizationResponse.model_validate(data)

@router.get(
    "/metrics",
    response_model=OrganizationMetricsResponse,
    summary="Indicateurs et KPIs consolidés des organisations"
)
async def get_organization_metrics(
    current_user: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db)
):
    metrics = await organization_service.get_metrics(db=db)
    return OrganizationMetricsResponse(**metrics)

@router.get(
    "/",
    response_model=List[OrganizationResponse],
    summary="Lister les organisations avec filtrage et recherche"
)
async def list_organizations(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None, description="in_creation, pending, active, revision_requested, rejected, suspended, archived"),
    search: Optional[str] = Query(None, description="Nom, code, pays ou email"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if current_user.role == UserRole.SUPER_ADMIN:
        orgs = await organization_service.list_organizations(
            db=db,
            skip=skip,
            limit=limit,
            status_filter=status,
            search=search
        )
    else:
        # L'administrateur ou membre d'organisation ne consulte QUE son organisation rattachée
        own_org = await organization_service.get_user_organization(db=db, user=current_user)
        orgs = [own_org] if own_org else []

    return [_to_org_response(org) for org in orgs]

@router.get(
    "/{id}",
    response_model=OrganizationResponse,
    summary="Consulter le dossier détaillé d'une organisation (ou dossier en création)"
)
async def get_organization_detail(
    id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    org = await organization_service.get_by_id(db=db, org_id=id)
    if not org:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Organisation introuvable.")

    owner_id = org.get("owner_id") if isinstance(org, dict) else org.owner_id
    if current_user.role != UserRole.SUPER_ADMIN and owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès non autorisé à cette organisation.")

    return _to_org_response(org)



@router.post(
    "/{id}/review",
    response_model=OrganizationResponse,
    summary="Instruction et revue de conformité par l'équipe SenWiseTool (avec mini-éditeur WYSIWYG)"
)
async def review_organization(
    id: str,
    review_in: OrganizationReviewActionRequest,
    current_super_admin: User = Depends(require_super_admin),
    db: AsyncSession = Depends(get_db)
):
    org = await organization_service.review_organization(
        db=db,
        org_id=id,
        reviewer=current_super_admin,
        review_in=review_in
    )
    return _to_org_response(org)

@router.put(
    "/{id}/profile",
    response_model=OrganizationResponse,
    summary="Mise à jour du profil de l'organisation par son administrateur (une fois active)"
)
async def update_organization_profile(
    id: int,
    update_in: OrganizationUpdateProfile,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    org = await organization_service.update_profile(
        db=db,
        org_id=id,
        current_user=current_user,
        update_in=update_in
    )
    return _to_org_response(org)

