from typing import Optional
from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import get_current_user
from app.models.user.user import User
from app.schemas.campaign.campaign import (
    CampaignCreate,
    CampaignUpdate,
    CampaignStatusUpdate,
    CampaignResponse,
    CampaignListResponse,
)
from app.services.campaign.campaign_service import campaign_service

router = APIRouter()


@router.get("", response_model=CampaignListResponse, summary="Lister les campagnes de l'organisation")
async def list_campaigns(
    status: Optional[str] = Query(None, description="Filtre par statut (draft, open, paused, closed, archived)"),
    campaign_type: Optional[str] = Query(None, description="Filtre par type de campagne"),
    plugin_code: Optional[str] = Query(None, description="Filtre par code plugin"),
    search: Optional[str] = Query(None, description="Recherche textuelle par nom ou code"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    org_id: Optional[int] = Query(None, description="Organisation ciblée (Super Admin uniquement)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne la liste paginée des campagnes de l'organisation de l'utilisateur connecté,
    ainsi que les compteurs d'états pour les filtres et tableaux de bord.
    """
    result = await campaign_service.list_campaigns(
        db=db,
        user=current_user,
        status_filter=status,
        campaign_type=campaign_type,
        plugin_code=plugin_code,
        search=search,
        skip=skip,
        limit=limit,
        org_id=org_id,
    )
    return result


@router.post("", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED, summary="Créer une nouvelle campagne")
async def create_campaign(
    payload: CampaignCreate,
    org_id: Optional[int] = Query(None, description="Organisation ciblée (Super Admin uniquement)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Crée une nouvelle campagne rattachée à l'organisation de l'utilisateur.
    Vérifie les permissions RBAC (Administrateurs et Gestionnaires autorisés).
    """
    campaign = await campaign_service.create_campaign(
        db=db,
        data=payload,
        user=current_user,
        org_id=org_id,
    )
    return campaign


@router.get("/{campaign_id}", response_model=CampaignResponse, summary="Obtenir les détails d'une campagne")
async def get_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne les détails complets d'une campagne avec vérification d'isolation tenant.
    """
    campaign = await campaign_service.get_campaign_by_id(
        db=db,
        campaign_id=campaign_id,
        user=current_user,
    )
    return campaign


@router.put("/{campaign_id}", response_model=CampaignResponse, summary="Mettre à jour une campagne")
async def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Met à jour une campagne active ou brouillon.
    Empêche toute modification si la campagne est clôturée ou archivée.
    """
    campaign = await campaign_service.update_campaign(
        db=db,
        campaign_id=campaign_id,
        data=payload,
        user=current_user,
    )
    return campaign


@router.post("/{campaign_id}/status", response_model=CampaignResponse, summary="Changer le statut d'une campagne")
async def change_campaign_status(
    campaign_id: int,
    payload: CampaignStatusUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Transitionne le statut d'une campagne (open, paused, closed, archived).
    Gère les notifications de lancement et le verrouillage d'audit à la clôture.
    """
    campaign = await campaign_service.change_campaign_status(
        db=db,
        campaign_id=campaign_id,
        data=payload,
        user=current_user,
    )
    return campaign


@router.delete("/{campaign_id}", summary="Supprimer une campagne brouillon")
async def delete_campaign(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Supprime définitivement une campagne UNIQUEMENT si elle est au statut 'draft'.
    Les campagnes ouvertes ou clôturées doivent être archivées pour préserver l'audit.
    """
    return await campaign_service.delete_campaign(
        db=db,
        campaign_id=campaign_id,
        user=current_user,
    )
