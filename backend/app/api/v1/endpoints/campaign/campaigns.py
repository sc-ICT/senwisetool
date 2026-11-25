from typing import Optional, List, Dict, Any
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
    CampaignVersionResponse,
    CampaignVersionCreate,
    CampaignVersionUpdate,
    CampaignVersionCloneRequest,
)
from app.services.campaign.campaign_service import campaign_service

router = APIRouter()


@router.get("", response_model=CampaignListResponse, summary="Lister les campagnes de l'organisation")
async def list_campaigns(
    status: Optional[str] = Query(None, description="Filtre par statut (draft, open, paused, closed, archived)"),
    visibility: Optional[str] = Query(None, description="Filtre par visibilité (public, private)"),
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
        visibility_filter=visibility,
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


# =============================================================================
# ROUTES DE GESTION DES VERSIONS DE LA CAMPAGNE
# =============================================================================

@router.get("/{campaign_id}/versions", response_model=List[CampaignVersionResponse], summary="Lister l'historique des versions d'une campagne")
async def list_campaign_versions(
    campaign_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne la liste complète ordonnée de toutes les versions (brouillons, actives, archivées) de la campagne.
    """
    return await campaign_service.list_campaign_versions(
        db=db,
        campaign_id=campaign_id,
        user=current_user,
    )


@router.post("/{campaign_id}/versions", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED, summary="Créer une nouvelle version pour une campagne")
async def create_campaign_version(
    campaign_id: int,
    payload: CampaignVersionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Crée une nouvelle version incrémentale en brouillon pour la campagne.
    """
    return await campaign_service.create_campaign_version(
        db=db,
        campaign_id=campaign_id,
        data=payload,
        user=current_user,
    )


@router.post("/{campaign_id}/versions/clone", response_model=CampaignResponse, status_code=status.HTTP_201_CREATED, summary="Cloner une version existante pour créer une nouvelle version")
async def clone_campaign_version(
    campaign_id: int,
    payload: CampaignVersionCloneRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Clone les directives et configurations d'une version pour générer une nouvelle version incrémentale en brouillon.
    """
    return await campaign_service.clone_campaign_version(
        db=db,
        campaign_id=campaign_id,
        req=payload,
        user=current_user,
    )


@router.get("/{campaign_id}/versions/{version_id}", response_model=CampaignVersionResponse, summary="Obtenir les détails d'une version spécifique")
async def get_campaign_version(
    campaign_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Retourne les directives, cibles, timeline et métriques d'une version spécifique de la campagne.
    """
    return await campaign_service.get_campaign_version(
        db=db,
        campaign_id=campaign_id,
        version_id=version_id,
        user=current_user,
    )


@router.put("/{campaign_id}/versions/{version_id}", response_model=CampaignResponse, summary="Mettre à jour une version spécifique")
async def update_campaign_version(
    campaign_id: int,
    version_id: int,
    payload: CampaignVersionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Met à jour la description, les cibles, les dates ou le budget d'une version de campagne non scellée.
    """
    return await campaign_service.update_campaign_version(
        db=db,
        campaign_id=campaign_id,
        version_id=version_id,
        data=payload,
        user=current_user,
    )


@router.post("/{campaign_id}/versions/{version_id}/publish", response_model=CampaignResponse, summary="Publier et activer une version de campagne")
async def publish_campaign_version(
    campaign_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Publie et active la version sélectionnée pour la campagne.
    """
    return await campaign_service.publish_campaign_version(
        db=db,
        campaign_id=campaign_id,
        version_id=version_id,
        user=current_user,
    )


@router.post("/{campaign_id}/versions/{version_id}/activate", response_model=CampaignResponse, summary="Définir une version publiée comme version active")
async def set_active_campaign_version(
    campaign_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Bascule la version active courante de la campagne vers une version déjà publiée.
    """
    return await campaign_service.set_active_campaign_version(
        db=db,
        campaign_id=campaign_id,
        version_id=version_id,
        user=current_user,
    )


@router.delete("/{campaign_id}/versions/{version_id}", summary="Supprimer une version de campagne")
async def delete_campaign_version(
    campaign_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Supprime une version spécifique en brouillon ou archivée. Si c'est l'unique version, supprime la campagne.
    """
    return await campaign_service.delete_campaign_version(
        db=db,
        campaign_id=campaign_id,
        version_id=version_id,
        user=current_user,
    )


# =============================================================================
# ASSOCIATIONS DE PROJETS ET FORMULAIRES À UNE VERSION DE CAMPAGNE (CASCADE)
# =============================================================================

@router.post(
    "/{campaign_id}/versions/{version_id}/projects/attach/{project_id}",
    response_model=CampaignResponse,
    summary="Associer un projet et tous ses formulaires à une version de campagne",
)
async def attach_project_to_campaign_version(
    campaign_id: int,
    version_id: int,
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Associe un projet dans sa globalité à une version de campagne.
    Règle : Toutes ses versions sont liées, et tous ses formulaires associés sont automatiquement intégrés dans la campagne.
    """
    return await campaign_service.attach_project_to_campaign_version(
        db=db,
        campaign_id=campaign_id,
        version_id=version_id,
        project_id=project_id,
        user=current_user,
    )


@router.delete(
    "/{campaign_id}/versions/{version_id}/projects/detach/{project_id}",
    response_model=CampaignResponse,
    summary="Dissocier un projet d'une version de campagne",
)
async def detach_project_from_campaign_version(
    campaign_id: int,
    version_id: int,
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await campaign_service.detach_project_from_campaign_version(
        db=db,
        campaign_id=campaign_id,
        version_id=version_id,
        project_id=project_id,
        user=current_user,
    )


@router.post(
    "/{campaign_id}/versions/{version_id}/forms/attach/{form_id}",
    response_model=CampaignResponse,
    summary="Associer un formulaire autonome à une version de campagne",
)
async def attach_form_to_campaign_version(
    campaign_id: int,
    version_id: int,
    form_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Associe un formulaire dans sa globalité à une version de campagne.
    """
    return await campaign_service.attach_form_to_campaign_version(
        db=db,
        campaign_id=campaign_id,
        version_id=version_id,
        form_id=form_id,
        user=current_user,
    )


@router.delete(
    "/{campaign_id}/versions/{version_id}/forms/detach/{form_id}",
    response_model=CampaignResponse,
    summary="Dissocier un formulaire d'une version de campagne",
)
async def detach_form_from_campaign_version(
    campaign_id: int,
    version_id: int,
    form_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await campaign_service.detach_form_from_campaign_version(
        db=db,
        campaign_id=campaign_id,
        version_id=version_id,
        form_id=form_id,
        user=current_user,
    )


@router.post(
    "/{campaign_id}/versions/{version_id}/scope/reset",
    response_model=CampaignResponse,
    summary="Réinitialiser complètement le périmètre opérationnel d'une version de campagne (0 projet, 0 formulaire)",
)
async def reset_campaign_version_scope(
    campaign_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Supprime tous les projets et formulaires pré-rattachés à une version en brouillon
    afin de permettre une reconfiguration intégrale de zéro.
    """
    return await campaign_service.reset_campaign_version_scope(
        db=db,
        campaign_id=campaign_id,
        version_id=version_id,
        user=current_user,
    )



@router.get("/by-project/{project_id}", summary="Lister les campagnes associées à un projet")
async def get_campaigns_by_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await campaign_service.get_campaigns_for_project(
        db=db,
        project_id=project_id,
        user=current_user,
    )


@router.get("/by-form/{form_id}", summary="Lister les campagnes associées à un formulaire")
async def get_campaigns_by_form(
    form_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await campaign_service.get_campaigns_for_form(
        db=db,
        form_id=form_id,
        user=current_user,
    )
