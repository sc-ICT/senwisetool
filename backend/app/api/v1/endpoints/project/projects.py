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
    ProjectVersionCloneRequest,
    ProjectVersionUpdate,
)
from app.services.project.project_service import ProjectService

router = APIRouter()


async def resolve_org_id(db: AsyncSession, current_user: User, org_id: Optional[int] = None) -> Optional[int]:
    """
    Résout de manière souveraine l'organisation de l'utilisateur :
    1. Si Super Admin avec org_id explicite.
    2. Si l'attribut organization_id est sur l'utilisateur.
    3. Résolution dynamique via organization_service.
    """
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    if is_super_admin:
        return org_id

    direct_org_id = getattr(current_user, "organization_id", None)
    if direct_org_id is not None:
        return direct_org_id

    try:
        from app.services.organization.organization_service import organization_service
        org = await organization_service.get_user_organization(db, current_user)
        return org.id if org else None
    except Exception:
        return None


@router.get("", response_model=List[ProjectResponse], summary="Lister les projets disponibles")
async def list_projects(
    project_type: Optional[str] = Query(None, description="Filtre par type de projet"),
    status: Optional[str] = Query(None, description="Filtre par statut"),
    search: Optional[str] = Query(None, description="Recherche par nom, code ou description"),
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
        status_filter=status,
        project_type=project_type,
        search=search,
        skip=skip,
        limit=limit,
    )
    return projects


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED, summary="Créer un nouveau projet")
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


@router.post("/{project_id}/versions/clone", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED, summary="Cloner une version existante pour créer une nouvelle version")
async def clone_project_version(
    project_id: int,
    payload: ProjectVersionCloneRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    await ProjectService.clone_project_version(
        db=db,
        project_id=project_id,
        req=payload,
        organization_id=target_org_id,
        user_id=current_user.id,
        is_super_admin=is_super_admin,
    )
    # Renvoyer le projet avec ses versions actualisées
    return await ProjectService.get_project_by_id(
        db=db,
        project_id=project_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/{project_id}/versions/{version_id}/publish", response_model=ProjectResponse, summary="Publier une version spécifique d'un projet")
async def publish_project_version(
    project_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    await ProjectService.publish_project_version(
        db=db,
        project_id=project_id,
        version_id=version_id,
        organization_id=target_org_id,
        user_id=current_user.id,
        is_super_admin=is_super_admin,
    )
    return await ProjectService.get_project_by_id(
        db=db,
        project_id=project_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.put("/{project_id}/versions/{version_id}", response_model=ProjectResponse, summary="Mettre à jour les paramètres d'une version spécifique d'un projet")
async def update_project_version(
    project_id: int,
    version_id: int,
    payload: ProjectVersionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await ProjectService.update_project_version(
        db=db,
        project_id=project_id,
        version_id=version_id,
        data=payload,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/{project_id}/publish", response_model=ProjectResponse, summary="Publier la version la plus récente d'un projet")
async def publish_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    project = await ProjectService.get_project_by_id(db, project_id, target_org_id, is_super_admin)
    if not project:
        raise HTTPException(status_code=404, detail="Projet introuvable.")

    if not project.versions:
        raise HTTPException(status_code=400, detail="Ce projet ne possède aucune version à publier.")

    latest_version = project.versions[0]
    await ProjectService.publish_project_version(
        db=db,
        project_id=project_id,
        version_id=latest_version.id,
        organization_id=target_org_id,
        user_id=current_user.id,
        is_super_admin=is_super_admin,
    )
    return await ProjectService.get_project_by_id(
        db=db,
        project_id=project_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.delete("/{project_id}/versions/{version_id}", summary="Supprimer une version d'un projet")
async def delete_project_version(
    project_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    result = await ProjectService.delete_project_version(
        db=db,
        project_id=project_id,
        version_id=version_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )
    return result


@router.post("/import-blueprint", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED, summary="Importer un projet depuis un package JSON Blueprint")
async def import_blueprint(
    blueprint_data: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    org_id: Optional[int] = Query(None, description="Organisation ciblée (Super Admin uniquement)"),
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


@router.get("/{project_id}/export-blueprint", summary="Exporter un projet en package JSON")
async def export_blueprint(
    project_id: int,
    version: Optional[str] = Query(None, description="Numéro de version spécifique à exporter (ex: '1.0.0') ou 'all' pour toutes les versions"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await ProjectService.export_project_blueprint(
        db=db,
        project_id=project_id,
        organization_id=target_org_id,
        version_number=version,
        is_super_admin=is_super_admin,
    )


@router.get("/{project_id}/export-compliance", summary="Exporter le rapport d'audit et de conformité du projet")
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


# =============================================================================
# ASSOCIATIONS DE FORMULAIRES AU PROJET (PAR VERSION, STRICTEMENT PUBLIÉS)
# =============================================================================

@router.get("/{project_id}/forms", summary="Lister les formulaires associés à un projet ou à sa version")
async def list_project_forms(
    project_id: int,
    project_version_id: Optional[int] = Query(None, description="Filtrer par version de projet"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await ProjectService.get_project_forms(
        db=db,
        project_id=project_id,
        project_version_id=project_version_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/{project_id}/forms/attach/{form_id}", summary="Associer une version de formulaire publiée à une version de projet")
async def attach_form_to_project(
    project_id: int,
    form_id: int,
    form_version_id: Optional[int] = Query(None, description="ID de la version de formulaire publiée (interdit en draft)"),
    project_version_id: Optional[int] = Query(None, description="ID de la version du projet cible"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await ProjectService.attach_form_to_project(
        db=db,
        project_id=project_id,
        form_id=form_id,
        form_version_id=form_version_id,
        project_version_id=project_version_id,
        user=current_user,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.delete("/{project_id}/forms/detach/{form_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Dissocier un formulaire d'un projet")
async def detach_form_from_project(
    project_id: int,
    form_id: int,
    project_version_id: Optional[int] = Query(None, description="ID de la version du projet"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    await ProjectService.detach_form_from_project(
        db=db,
        project_id=project_id,
        form_id=form_id,
        project_version_id=project_version_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )
    return None
