from typing import Optional, List
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import require_super_admin, get_current_user
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
    PluginVersionToolCreate,
    PluginVersionToolUpdate,
    PluginVersionToolResponse,
)
from app.services.plugin.plugin_service import plugin_service
from app.services.plugin.tool_registry import get_tool_catalog


router = APIRouter()


def _format_tool_obj(t) -> dict:
    if isinstance(t, dict):
        return t
    params = getattr(t, "parameters", {}) or {}
    return {
        "id": getattr(t, "id", None),
        "plugin_version_id": getattr(t, "plugin_version_id", None),
        "item_key": getattr(t, "item_key", ""),
        "tool_code": getattr(t, "tool_code", ""),
        "type": getattr(t, "type", "module"),
        "config_target": getattr(t, "config_target", "tool_resource"),
        "name": getattr(t, "name", ""),
        "description": getattr(t, "description", None),
        "selected_resource_id": getattr(t, "selected_resource_id", None),
        "selected_resource_code": getattr(t, "selected_resource_code", None),
        "selected_resource_name": getattr(t, "selected_resource_name", None),
        "selected_resource_status": getattr(t, "selected_resource_status", None),
        "distribution_mode": getattr(t, "distribution_mode", "tenant"),
        "permissions_mode": getattr(t, "permissions_mode", "restricted"),
        "parameters": params,
        "created_at": getattr(t, "created_at", None),
        "updated_at": getattr(t, "updated_at", None),
    }


def _format_version_obj(v) -> Optional[dict]:
    if not v:
        return None
    if isinstance(v, dict):
        return v
    raw_tools = []
    v_dict = getattr(v, "__dict__", {})
    if isinstance(v_dict, dict) and "tools" in v_dict:
        val = v_dict.get("tools")
        if isinstance(val, (list, tuple)):
            raw_tools = val
    if not raw_tools and isinstance(v_dict, dict) and "package_data" in v_dict:
        pkg = v_dict.get("package_data")
        if isinstance(pkg, dict) and "tools" in pkg and isinstance(pkg["tools"], (list, tuple)):
            raw_tools = pkg["tools"]

    tools_formatted = [_format_tool_obj(t) for t in raw_tools]

    return {
        "id": getattr(v, "id", None),
        "plugin_id": getattr(v, "plugin_id", None),
        "version_number": getattr(v, "version_number", ""),
        "changelog": getattr(v, "changelog", None),
        "package_data": getattr(v, "package_data", {}) or {},
        "usage_policy": getattr(v, "usage_policy", None),
        "status": getattr(v, "status", "draft"),
        "tools": tools_formatted,
        "published_by_id": getattr(v, "published_by_id", None),
        "published_at": getattr(v, "published_at", None),
        "deprecated_at": getattr(v, "deprecated_at", None),
        "deprecated_by_id": getattr(v, "deprecated_by_id", None),
        "deprecated_eol_date": getattr(v, "deprecated_eol_date", None),
        "deprecation_reason": getattr(v, "deprecation_reason", None),
        "is_blocked": getattr(v, "is_blocked", False),
        "created_at": getattr(v, "created_at", None),
        "updated_at": getattr(v, "updated_at", None),
    }


def _format_plugin_response(plug) -> dict:
    p_dict = getattr(plug, "__dict__", {})
    versions_list = p_dict.get("versions") if isinstance(p_dict, dict) and "versions" in p_dict else []
    installations_list = p_dict.get("installations") if isinstance(p_dict, dict) and "installations" in p_dict else []

    pub_versions = [v for v in versions_list if getattr(v, "status", "") == PluginVersionStatus.PUBLISHED.value]
    pub_versions.sort(key=lambda x: (getattr(x, "id", 0) or 0), reverse=True)
    latest_version = pub_versions[0] if pub_versions else (versions_list[0] if versions_list else None)
    oldest_published_version = pub_versions[-1] if pub_versions else None

    draft_version = next((v for v in versions_list if getattr(v, "status", "") == PluginVersionStatus.DRAFT.value), None)

    now = datetime.now(timezone.utc)
    ongoing_dep = None
    for v in versions_list:
        if getattr(v, "status", "") == PluginVersionStatus.DEPRECATED.value and not getattr(v, "is_blocked", False):
            eol_date = getattr(v, "deprecated_eol_date", None)
            if eol_date:
                eol = eol_date if eol_date.tzinfo else eol_date.replace(tzinfo=timezone.utc)
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
        "is_public": getattr(plug, "is_public", True),
        "allowed_organization_ids": getattr(plug, "allowed_organization_ids", []) or [],
        "created_at": plug.created_at,
        "updated_at": plug.updated_at,
        "versions": [_format_version_obj(v) for v in versions_list],
        "latest_version": _format_version_obj(latest_version),
        "has_draft_version": bool(draft_version),
        "draft_version": _format_version_obj(draft_version),
        "has_ongoing_deprecation": bool(ongoing_dep),
        "ongoing_deprecation_version": _format_version_obj(ongoing_dep),
        "oldest_published_version_id": oldest_published_version.id if oldest_published_version else None,
        "versions_count": len(versions_list),
        "installations_count": len([i for i in installations_list if getattr(i, "status", "") == "installed"]) if installations_list else 0,
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
    return _format_version_obj(version)


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
    return _format_version_obj(version)


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
    return _format_version_obj(version)


@router.post("/{plugin_id}/versions/{version_id}/publish", response_model=PluginVersionResponse)
async def publish_plugin_version(
    plugin_id: int,
    version_id: int,
    payload: Optional[PluginVersionPublish] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Publication officielle d'une version de plugin sur le SenWiseStore.
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
    return _format_version_obj(version)


@router.get("/{plugin_id}/versions/{version_id}/diff")
async def get_plugin_version_diff(
    plugin_id: int,
    version_id: int,
    base_version_id: Optional[int] = Query(None, description="ID de la version de référence pour la comparaison"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Connecté / Admin] Calcule et renvoie la comparaison automatique (diff)
    entre une version de plugin et une version de base/précédente.
    """
    plugin = await plugin_service.get_by_id(db, plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin introuvable.")

    current_version = await plugin_service.get_version_by_id(db, plugin_id, version_id)
    if not current_version:
        raise HTTPException(status_code=404, detail="Version cible introuvable.")

    base_version = None
    if base_version_id:
        base_version = await plugin_service.get_version_by_id(db, plugin_id, base_version_id)

    diff_markdown = await plugin_service.generate_version_diff(
        db=db,
        plugin=plugin,
        current_version=current_version,
        base_version=base_version,
    )

    return {
        "plugin_id": plugin_id,
        "version_id": version_id,
        "version_number": current_version.version_number,
        "base_version_id": base_version.id if base_version else None,
        "base_version_number": base_version.version_number if base_version else None,
        "diff_markdown": diff_markdown,
    }


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


# ==========================================
# GESTION DES OUTILS/MODULES DE VERSION (BDD)
# ==========================================

@router.get("/{plugin_id}/versions/{version_id}/tools", response_model=List[PluginVersionToolResponse])
async def list_plugin_version_tools(
    plugin_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Liste tous les outils et modules enregistrés en base pour une version.
    """
    return await plugin_service.list_version_tools(db=db, plugin_id=plugin_id, version_id=version_id)


@router.post("/{plugin_id}/versions/{version_id}/tools", response_model=PluginVersionToolResponse, status_code=status.HTTP_201_CREATED)
async def add_plugin_version_tool(
    plugin_id: int,
    version_id: int,
    data: PluginVersionToolCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Enregistre ou met à jour un module/fonctionnalité dans la table de la version.
    """
    return await plugin_service.add_or_update_version_tool(
        db=db,
        plugin_id=plugin_id,
        version_id=version_id,
        data=data,
        user=current_user
    )


@router.put("/{plugin_id}/versions/{version_id}/tools/{item_key}", response_model=PluginVersionToolResponse)
async def update_plugin_version_tool(
    plugin_id: int,
    version_id: int,
    item_key: str,
    data: PluginVersionToolUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Modifie un module/fonctionnalité existant dans la table de la version.
    """
    return await plugin_service.update_version_tool(
        db=db,
        plugin_id=plugin_id,
        version_id=version_id,
        item_key=item_key,
        data=data,
        user=current_user
    )


@router.delete("/{plugin_id}/versions/{version_id}/tools/{item_key}")
async def delete_plugin_version_tool(
    plugin_id: int,
    version_id: int,
    item_key: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Supprime un module/fonctionnalité de la table de la version.
    """
    await plugin_service.delete_version_tool(
        db=db,
        plugin_id=plugin_id,
        version_id=version_id,
        item_key=item_key,
        user=current_user
    )
    return {"success": True, "message": f"Composant '{item_key}' retiré avec succès de la version."}


@router.delete("/{plugin_id}")
async def delete_plugin(
    plugin_id: int,
    force: bool = Query(False, description="Forcer la suppression même si des organisations ont installé le plugin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_super_admin),
):
    """
    [Super Admin] Suppression définitive d'un plugin et de toutes ses versions.
    """
    await plugin_service.delete_plugin(
        db=db,
        plugin_id=plugin_id,
        user=current_user,
        force=force
    )
    return {"success": True, "message": "Plugin supprimé avec succès."}


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
    return _format_version_obj(version)

