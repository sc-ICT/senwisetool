from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from sqlalchemy.orm import selectinload

from app.db.database import get_db
from app.api.deps import get_current_user, get_optional_current_user
from app.models.user.user import User, UserRole
from app.models.organization.organization import Organization
from app.models.organization.member import OrganizationMember
from app.models.plugin.plugin import PluginVersionStatus, PluginStatus, OrgPluginStatus
from app.schemas.plugin.plugin import (
    MarketplaceCatalogResponse,
    MarketplacePluginItem,
    PluginInstallRequest,
    PluginUpgradeRequest,
    PluginUninstallRequest,
    OrganizationPluginResponse,
    PluginResponse,
    PluginVersionResponse,
)
from app.services.plugin.plugin_service import plugin_service
from app.services.organization.organization_service import organization_service

router = APIRouter()


async def _resolve_user_organization(
    db: AsyncSession,
    user: User,
    requested_org_id: Optional[int] = None
) -> Optional[Organization]:
    """
    Identifie l'organisation active du demandeur.
    Si Super Admin, peut cibler une organisation spécifique si requested_org_id est spécifié.
    """
    if user.role == UserRole.SUPER_ADMIN and requested_org_id:
        res = await db.execute(select(Organization).where(Organization.id == requested_org_id))
        return res.scalars().first()

    return await organization_service.get_user_organization(db, user)


async def _verify_org_admin_permission(
    db: AsyncSession,
    org: Organization,
    user: User
) -> None:
    """
    Vérifie formellement que l'utilisateur est soit Super Admin, soit Propriétaire ou Administrateur
    d'organisation qualifié pour installer, mettre à niveau ou désinstaller un plugin pour le compte du tenant.
    """
    if user.role == UserRole.SUPER_ADMIN:
        return

    # Propriétaire fondateur ou contact légal
    if org.owner_id == user.id or org.contact_email.lower() == user.email.lower():
        return

    # Collaborateur membre avec privilèges administrateur
    mem_res = await db.execute(
        select(OrganizationMember).where(
            OrganizationMember.organization_id == org.id,
            or_(
                OrganizationMember.user_id == user.id,
                func.lower(OrganizationMember.email) == user.email.lower()
            )
        )
    )
    member = mem_res.scalars().first()
    if member:
        if member.is_owner:
            return
        if member.role_code and any(k in member.role_code.upper() for k in ["ADM", "ADMIN", "OWNER", "DIRECT"]):
            return
        if member.roles_list and any("admin" in str(r).lower() for r in member.roles_list):
            return

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Action réservée aux administrateurs de l'organisation. Vous n'avez pas les droits nécessaires pour modifier les plugins de cette organisation."
    )


def _format_plugin_dict(plug) -> dict:
    pub_versions = [v for v in plug.versions if v.status == PluginVersionStatus.PUBLISHED.value]
    pub_versions.sort(key=lambda x: x.created_at, reverse=True)
    latest_version = pub_versions[0] if pub_versions else (plug.versions[0] if plug.versions else None)

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
        "versions": pub_versions,
        "latest_version": latest_version,
        "versions_count": len(pub_versions),
        "installations_count": len(plug.installations) if plug.installations else 0,
    }


def _format_installation_dict(inst, plug=None, installed_version=None, latest_version=None, has_update=False) -> dict:
    p = plug or inst.plugin
    iv = installed_version or inst.installed_version
    lv = latest_version or iv

    return {
        "id": inst.id,
        "organization_id": inst.organization_id,
        "plugin_id": inst.plugin_id,
        "installed_version_id": inst.installed_version_id,
        "status": inst.status,
        "policy_accepted_at": inst.policy_accepted_at,
        "policy_accepted_by_user_id": inst.policy_accepted_by_user_id,
        "policy_version": inst.policy_version,
        "local_settings": inst.local_settings or {},
        "installed_at": inst.installed_at,
        "updated_at": inst.updated_at,
        "plugin": _format_plugin_dict(p) if p else None,
        "installed_version": iv,
        "latest_version": lv,
        "has_update_available": has_update,
    }


@router.get("/catalog", response_model=MarketplaceCatalogResponse)
async def get_marketplace_catalog(
    category: Optional[str] = Query(None, description="Filtrer par catégorie"),
    search: Optional[str] = Query(None, description="Recherche textuelle"),
    org_id: Optional[int] = Query(None, description="Contexte organisationnel spécifique"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    """
    [Public / Connecté] Catalogue complet des plugins officiels publiés.
    Si l'utilisateur est connecté et rattaché à une organisation, chaque item intègre
    son statut d'installation et signale si une mise à jour est disponible.
    """
    resolved_org = None
    if current_user:
        resolved_org = await _resolve_user_organization(db, current_user, org_id)

    catalog_data = await plugin_service.get_marketplace_catalog(
        db=db,
        organization_id=resolved_org.id if resolved_org else None,
        category=category,
        search=search
    )

    formatted_items = []
    for item in catalog_data["items"]:
        plug_dict = _format_plugin_dict(item["plugin"])
        inst_dict = None
        if item["installed_instance"]:
            inst_dict = _format_installation_dict(
                inst=item["installed_instance"],
                plug=item["plugin"],
                installed_version=item["installed_instance"].installed_version,
                latest_version=item["latest_version"],
                has_update=item["has_update_available"]
            )

        formatted_items.append({
            "plugin": plug_dict,
            "latest_version": item["latest_version"],
            "is_installed": item["is_installed"],
            "installed_instance": inst_dict,
            "has_update_available": item["has_update_available"]
        })

    return {
        "items": formatted_items,
        "categories": catalog_data["categories"],
        "total": len(formatted_items)
    }


@router.get("/plugins/{code}", response_model=Dict[str, Any])
async def get_marketplace_plugin_detail(
    code: str,
    org_id: Optional[int] = Query(None, description="Contexte organisationnel spécifique"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    """
    [Public / Connecté] Fiche descriptive complète d'un plugin, incluant la politique d'utilisation (EULA)
    de chaque version pour lecture et accord préalable.
    """
    resolved_org = None
    if current_user:
        resolved_org = await _resolve_user_organization(db, current_user, org_id)

    detail_data = await plugin_service.get_marketplace_plugin_detail(
        db=db,
        code=code,
        organization_id=resolved_org.id if resolved_org else None
    )

    plug_dict = _format_plugin_dict(detail_data["plugin"])
    inst_dict = None
    if detail_data["installed_instance"]:
        inst_dict = _format_installation_dict(
            inst=detail_data["installed_instance"],
            plug=detail_data["plugin"],
            installed_version=detail_data["installed_instance"].installed_version,
            latest_version=detail_data["latest_version"],
            has_update=detail_data["has_update_available"]
        )

    return {
        "plugin": plug_dict,
        "versions": detail_data["versions"],
        "latest_version": detail_data["latest_version"],
        "is_installed": detail_data["is_installed"],
        "installed_instance": inst_dict,
        "has_update_available": detail_data["has_update_available"]
    }


@router.get("/installed", response_model=List[OrganizationPluginResponse])
async def get_organization_installed_plugins(
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Organisation] Récupère la liste souveraine des plugins installés et actifs pour l'organisation de l'utilisateur.
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune organisation active n'est associée à votre compte."
        )

    raw_items = await plugin_service.list_installed_plugins_for_org(db=db, org_id=org.id)
    results = []
    for item in raw_items:
        formatted = _format_installation_dict(
            inst=item["installation"],
            plug=item["plugin"],
            installed_version=item["installed_version"],
            latest_version=item["latest_version"],
            has_update=item["has_update_available"]
        )
        results.append(formatted)

    return results


@router.post("/install", response_model=OrganizationPluginResponse, status_code=status.HTTP_201_CREATED)
async def install_plugin(
    payload: PluginInstallRequest,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Admin Organisation] Intègre un plugin dans l'espace de travail de l'organisation.
    - Exige formellement l'acceptation de la politique d'utilisation (Règle 4).
    - Enregistre l'horodatage et la signature de l'admin.
    - Garantit l'unicité stricte de l'installation (Règle 3).
    - Fixe la version installée (Pinned execution - Règle 2).
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune organisation active n'est associée à votre compte."
        )

    await _verify_org_admin_permission(db, org, current_user)

    inst = await plugin_service.install_plugin_for_org(
        db=db,
        org_id=org.id,
        plugin_id=payload.plugin_id,
        version_id=payload.version_id,
        policy_accepted=payload.policy_accepted,
        local_settings=payload.local_settings,
        user=current_user
    )

    plug = await plugin_service.get_by_id(db, payload.plugin_id)
    return _format_installation_dict(inst=inst, plug=plug, installed_version=inst.installed_version)


@router.post("/upgrade", response_model=OrganizationPluginResponse)
async def upgrade_plugin(
    payload: PluginUpgradeRequest,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Admin Organisation] Met à niveau le plugin vers une nouvelle version publiée.
    - Exige l'acceptation de la politique d'utilisation de la nouvelle version.
    - Exécution non destructive (Règle 6).
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune organisation active n'est associée à votre compte."
        )

    await _verify_org_admin_permission(db, org, current_user)

    inst = await plugin_service.upgrade_plugin_for_org(
        db=db,
        org_id=org.id,
        plugin_id=payload.plugin_id,
        target_version_id=payload.target_version_id,
        policy_accepted=payload.policy_accepted,
        user=current_user
    )

    plug = await plugin_service.get_by_id(db, payload.plugin_id)
    return _format_installation_dict(inst=inst, plug=plug, installed_version=inst.installed_version)


@router.post("/uninstall", response_model=OrganizationPluginResponse)
async def uninstall_plugin(
    payload: PluginUninstallRequest,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    [Admin Organisation] Désinstalle un plugin de l'espace de travail.
    - Désactivation sécurisée préservant les données de collecte et de télémétrie existantes (Règle 6).
    """
    org = await _resolve_user_organization(db, current_user, org_id)
    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Aucune organisation active n'est associée à votre compte."
        )

    await _verify_org_admin_permission(db, org, current_user)

    inst = await plugin_service.uninstall_plugin_for_org(
        db=db,
        org_id=org.id,
        plugin_id=payload.plugin_id,
        user=current_user,
        reason=payload.reason
    )

    plug = await plugin_service.get_by_id(db, payload.plugin_id)
    return _format_installation_dict(inst=inst, plug=plug, installed_version=inst.installed_version)
