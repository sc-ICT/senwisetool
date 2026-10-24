from typing import Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import require_super_admin
from app.models.user.user import User
from app.models.plugin.plugin import PluginVersionStatus, PluginStatus
from app.schemas.plugin.plugin import (
    PluginCreate,
    PluginUpdate,
    PluginResponse,
    PluginListResponse,
    PluginVersionCreate,
    PluginVersionUpdate,
    PluginVersionResponse,
    PluginVersionPublish,
    PluginVersionDeprecate,
)
from app.services.plugin.plugin_service import plugin_service
from app.services.plugin.tool_registry import get_tool_catalog


router = APIRouter()


def _format_plugin_response(plug) -> dict:
    pub_versions = [v for v in plug.versions if v.status == PluginVersionStatus.PUBLISHED.value]
    pub_versions.sort(key=lambda x: (x.id or 0), reverse=True)
    latest_version = pub_versions[0] if pub_versions else (plug.versions[0] if plug.versions else None)
    oldest_published_version = pub_versions[-1] if pub_versions else None

    draft_version = next((v for v in plug.versions if v.status == PluginVersionStatus.DRAFT.value), None)

    now = datetime.now(timezone.utc)
    ongoing_dep = None
    for v in plug.versions:
        if v.status == PluginVersionStatus.DEPRECATED.value and not v.is_blocked:
            if v.deprecated_eol_date:
                eol = v.deprecated_eol_date if v.deprecated_eol_date.tzinfo else v.deprecated_eol_date.replace(tzinfo=timezone.utc)
                if now < eol:
                    ongoing_dep = v
                    break
            else:
                ongoing_dep = v
                break

    return {
        "id": plug.id,
        "code": plug.code,
        "name": plug.name,
        "category": plug.category,
        "description": plug.description,
        "short_description": plug.short_description,
        "icon_url": plug.icon_url,
        "banner_url": plug.banner_url,
        "author": plug.author,
        "status": plug.status,
        "tags": plug.tags or [],
        "created_at": plug.created_at,
        "updated_at": plug.updated_at,
        "versions": plug.versions,
        "latest_version": latest_version,
        "has_draft_version": bool(draft_version),
        "draft_version": draft_version,
        "has_ongoing_deprecation": bool(ongoing_dep),
        "ongoing_deprecation_version": ongoing_dep,
        "oldest_published_version_id": oldest_published_version.id if oldest_published_version else None,
        "versions_count": len(plug.versions),
        "installations_count": len([i for i in plug.installations if i.status == "installed"]) if plug.installations else 0,
    }



@router.post("/", response_model=PluginResponse, status_code=status.HTTP_201_CREATED)
async def create_plugin(
    data: PluginCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Création d'un nouveau plugin spécialisé (en mode brouillon ou prêt à publier).
    """
    plugin = await plugin_service.create_plugin(db=db, data=data, user=current_user)
    return _format_plugin_response(plugin)


@router.get("/", response_model=PluginListResponse)
async def list_plugins(
    category: Optional[str] = Query(None, description="Filtrer par catégorie"),
    status: Optional[str] = Query(None, description="Filtrer par statut: draft, published, deprecated, archived"),
    search: Optional[str] = Query(None, description="Recherche textuelle sur nom ou code"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Liste complète de tous les plugins de la plateforme avec métriques d'installations.
    """
    items, total = await plugin_service.list_plugins_admin(
        db=db,
        category=category,
        status_filter=status,
        search=search,
        skip=skip,
        limit=limit
    )
    formatted_items = [_format_plugin_response(p) for p in items]
    return {
        "items": formatted_items,
        "total": total
    }


@router.get("/tools/catalog")
async def get_plugin_tools_catalog(
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Retourne le catalogue des outils modulaires de la plateforme pouvant être intégrés dans un plugin.
    """
    return get_tool_catalog()


@router.get("/{plugin_id}", response_model=PluginResponse)
async def get_plugin_detail(
    plugin_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Récupération détaillée d'un plugin avec toutes ses versions (brouillons et publiées).
    """
    plugin = await plugin_service.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plugin introuvable.")
    return _format_plugin_response(plugin)


@router.put("/{plugin_id}", response_model=PluginResponse)
async def update_plugin(
    plugin_id: int,
    data: PluginUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Mise à jour des métadonnées globales d'un plugin.
    """
    plugin = await plugin_service.update_plugin(db=db, plugin_id=plugin_id, data=data, user=current_user)
    return _format_plugin_response(plugin)


@router.post("/{plugin_id}/versions", response_model=PluginVersionResponse, status_code=status.HTTP_201_CREATED)
async def create_plugin_version(
    plugin_id: int,
    data: PluginVersionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Création d'une nouvelle version brouillon (SemVer) avec paquet de données et politique d'utilisation.
    """
    version = await plugin_service.create_version(db=db, plugin_id=plugin_id, data=data, user=current_user)
    return version


@router.get("/{plugin_id}/versions/{version_id}", response_model=PluginVersionResponse)
async def get_plugin_version_detail(
    plugin_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Récupération détaillée d'une version spécifique d'un plugin avec son package et sa politique.
    """
    version = await plugin_service.get_version_by_id(db=db, plugin_id=plugin_id, version_id=version_id)
    if not version:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Version introuvable pour ce plugin.")
    return version


@router.put("/{plugin_id}/versions/{version_id}", response_model=PluginVersionResponse)
async def update_plugin_version(
    plugin_id: int,
    version_id: int,
    data: PluginVersionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Mise à jour d'une version brouillon : configuration du package (outils, paramètres), changelog, politique.
    Règle 1 : Formellement interdit si la version est déjà publiée (immuabilité garantie).
    """
    version = await plugin_service.update_version(
        db=db,
        plugin_id=plugin_id,
        version_id=version_id,
        data=data,
        user=current_user
    )
    return version


@router.post("/{plugin_id}/versions/{version_id}/publish", response_model=PluginVersionResponse)
async def publish_plugin_version(
    plugin_id: int,
    version_id: int,
    payload: Optional[PluginVersionPublish] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Publication officielle d'une version de plugin sur le Marketplace.
    - Scelle définitivement la version (Immutabilité - Règle 1).
    - Règle 8 : Détail obligatoire du contenu (v1) ou des nouveautés (versions suivantes).
    - Déclenche automatiquement des notifications in-app à toutes les organisations abonnées (Règle 5).
    """
    version = await plugin_service.publish_version(
        db=db,
        plugin_id=plugin_id,
        version_id=version_id,
        publisher_user=current_user,
        details_or_changelog=payload.details_or_changelog if payload else None,
        usage_policy=payload.usage_policy if payload else None,
    )
    return version


@router.delete("/{plugin_id}/versions/{version_id}")
async def delete_plugin_version(
    plugin_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Suppression d'une version de plugin.
    - Règle 7 : Autorisée UNIQUEMENT si la version est encore en mode brouillon (draft).
    - Formellement interdite si la version est déjà publiée (immutabilité garantie).
    """
    await plugin_service.delete_version(
        db=db,
        plugin_id=plugin_id,
        version_id=version_id,
        user=current_user
    )
    return {"success": True, "message": "Version brouillon supprimée avec succès."}


@router.post("/{plugin_id}/versions/{version_id}/deprecate", response_model=PluginVersionResponse)
async def deprecate_plugin_version(
    plugin_id: int,
    version_id: int,
    payload: PluginVersionDeprecate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Dépréciation planifiée d'une version publiée (Règle 9).
    - Fixe une date d'échéance EOL (End of Life) configurable (ex : 30 jours).
    - Expédie un signalement d'urgence et des notifications de rappel à toutes les organisations clientes
      pour leur demander d'exporter leurs données et de basculer vers une version supérieure.
    - Dès l'échéance atteinte, la version et ses éléments associés seront bloqués.
    """
    version = await plugin_service.deprecate_version(
        db=db,
        plugin_id=plugin_id,
        version_id=version_id,
        days_until_eol=payload.days_until_eol,
        reason=payload.reason,
        custom_eol_date=payload.custom_eol_date,
        user=current_user
    )
    return version

