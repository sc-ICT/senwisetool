from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import get_current_user
from app.models.user.user import User, UserRole
from app.schemas.project.project import (
    ProjectCreate,
    ProjectUpdate,
    ProjectResponse,
)
from app.services.project.project_service import ProjectService

router = APIRouter()


async def resolve_org_id(db: AsyncSession, current_user: User, org_id: Optional[int] = None) -> Optional[int]:
    """
    Résout de manière souveraine et sécurisée l'organisation de rattachement :
    1. Si Super Admin et org_id explicite fourni, cible cette organisation.
    2. Si l'attribut organization_id est présent sur l'objet (ex: mocks de tests unitaires).
    3. Résolution dynamique via organization_service (propriétaire, contact ou membre).
    4. None si Super Admin hors d'un tenant (modèle global / blueprint de studio).
    """
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    if is_super_admin and org_id is not None:
        return org_id

    # Fallback pour mocks ou objets avec organization_id direct
    direct_org_id = getattr(current_user, "organization_id", None)
    if direct_org_id is not None:
        return direct_org_id

    try:
        from app.services.organization.organization_service import organization_service
        org = await organization_service.get_user_organization(db, current_user)
        return org.id if org else None
    except Exception:
        return None


@router.get("", response_model=List[ProjectResponse], summary="Lister les projets ou modèles accessibles")
async def list_projects(
    category: Optional[str] = Query(None, description="Filtre par catégorie"),
    project_type: Optional[str] = Query(None, description="Filtre par type de projet"),
    status: Optional[str] = Query(None, description="Filtre par statut"),
    is_template: Optional[bool] = Query(None, description="Filtrer les modèles de projets (True) ou les projets opérationnels (False)"),
    search: Optional[str] = Query(None, description="Recherche par nom ou code"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    org_id: Optional[int] = Query(None, description="Organisation ciblée (Super Admin uniquement)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user, org_id)

    projects = await ProjectService.get_projects(
        db=db,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
        is_template=is_template,
        status_filter=status,
        category_filter=category,
        search=search,
        skip=skip,
        limit=limit,
    )
    return projects


@router.post("/{template_id}/instantiate", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED, summary="Instancier un projet opérationnel depuis un modèle certifié")
async def instantiate_project(
    template_id: int,
    name_override: Optional[str] = Query(None, description="Surcharge du nom du projet"),
    code_override: Optional[str] = Query(None, description="Surcharge du code technique"),
    org_id: Optional[int] = Query(None, description="Organisation ciblée (Super Admin uniquement)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Instancie pour l'organisation cliente un projet complet à partir du modèle de projet (blueprint)
    conçu par le Super Admin, en clonant fidèlement ses formulaires, sections sanctuarisées,
    questions critiques, formules et liaisons de ressources.
    """
    target_org_id = await resolve_org_id(db, current_user, org_id)
    if not target_org_id:
        raise HTTPException(status_code=400, detail="Une organisation doit être spécifiée pour instancier un projet opérationnel.")

    instantiated = await ProjectService.instantiate_project_from_template(
        db=db,
        template_id=template_id,
        organization_id=target_org_id,
        user_id=current_user.id,
        name_override=name_override,
        code_override=code_override,
    )
    return instantiated


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED, summary="Créer un nouveau projet ou modèle de projet")
async def create_project(
    payload: ProjectCreate,
    org_id: Optional[int] = Query(None, description="Organisation ciblée (Super Admin uniquement)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user, org_id)

    project = await ProjectService.create_project(
        db=db,
        data=payload,
        organization_id=target_org_id,
        user_id=current_user.id,
        is_super_admin=is_super_admin,
    )
    return project


@router.get("/{project_id}", response_model=ProjectResponse, summary="Obtenir les détails d'un projet")
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    project = await ProjectService.get_project_by_id(
        db=db,
        project_id=project_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )
    if not project:
        raise HTTPException(status_code=404, detail="Projet introuvable.")
    return project


@router.patch("/{project_id}", response_model=ProjectResponse, summary="Modifier un projet existant")
async def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    project = await ProjectService.update_project(
        db=db,
        project_id=project_id,
        data=payload,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Supprimer un projet")
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    await ProjectService.delete_project(
        db=db,
        project_id=project_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )
    return None


@router.post("/{project_id}/publish", response_model=ProjectResponse, summary="Publier un modèle de projet (vérifie que tous les formulaires sont publiés)")
async def publish_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await ProjectService.publish_project(
        db=db,
        project_id=project_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/{project_id}/new-version", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED, summary="Créer une nouvelle version (brouillon) d'un projet publié")
async def create_new_project_version(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await ProjectService.create_new_project_version(
        db=db,
        project_id=project_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
        user_id=current_user.id,
    )



@router.get("/{project_id}/export-blueprint", summary="Exporter un modèle de projet en package JSON complet (Blueprint)")
async def export_blueprint(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await ProjectService.export_project_blueprint(
        db=db,
        project_id=project_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/import-blueprint", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED, summary="Importer un modèle de projet depuis un package JSON")
async def import_blueprint(
    blueprint_data: Dict[str, Any],
    org_id: Optional[int] = Query(None, description="Organisation ciblée (Super Admin uniquement)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user, org_id)
    return await ProjectService.import_project_blueprint(
        db=db,
        blueprint_data=blueprint_data,
        organization_id=target_org_id,
        user_id=current_user.id,
        is_super_admin=is_super_admin,
    )


@router.get("/{project_id}/export-compliance", summary="Exporter le rapport d'audit et de conformité réglementaire")
async def export_compliance(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await ProjectService.export_compliance_report(
        db=db,
        project_id=project_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )
