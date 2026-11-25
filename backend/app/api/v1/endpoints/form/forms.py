from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import get_current_user
from app.models.user.user import User, UserRole
from app.schemas.form.form import FormCreate, FormUpdate, FormResponse, FormResourceProjectionRequest, FormTreeImportRequest
from app.schemas.form.form_version import (
    FormVersionCreate,
    FormVersionCloneRequest,
    FormVersionUpdate,
    FormVersionResponse,
)
from app.schemas.form.form_section import FormSectionCreate, FormSectionUpdate, FormSectionResponse
from app.schemas.form.form_question import FormQuestionCreate, FormQuestionUpdate, FormQuestionResponse
from app.services.form.form_service import FormService
from app.services.form.resource_binding import ResourceBindingService

router = APIRouter()


async def resolve_org_id(db: AsyncSession, current_user: User, org_id: Optional[int] = None) -> Optional[int]:
    """
    Résout l'organisation de l'utilisateur :
    1. Si Super Admin avec org_id explicite.
    2. Si l'attribut organization_id est présent sur l'utilisateur.
    3. Résolution dynamique via organization_service.
    """
    if current_user.role == UserRole.SUPER_ADMIN:
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


# ---------------------------------------------------------------------------
# FORMULAIRES AUTONOMES (CRUD)
# ---------------------------------------------------------------------------

@router.get("/forms", response_model=List[FormResponse], summary="Lister tous les formulaires")
async def list_forms(
    visibility: Optional[str] = Query(None, description="Filtrer par visibilité (public / private)"),
    status: Optional[str] = Query(None, description="Filtrer par statut"),
    search: Optional[str] = Query(None, description="Recherche par nom, code ou description"),
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user, org_id)

    forms = await FormService.get_forms(
        db=db,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
        visibility=visibility,
        status=status,
        search=search,
    )
    return forms


@router.post("/forms", response_model=FormResponse, status_code=status.HTTP_201_CREATED, summary="Créer un formulaire épuré")
async def create_form(
    payload: FormCreate,
    org_id: Optional[int] = Query(None, description="ID organisation pour Super Admin"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_org_id = await resolve_org_id(db, current_user, org_id)
    form = await FormService.create_form(
        db=db,
        form_in=payload,
        user=current_user,
        organization_id=target_org_id,
    )
    return form


@router.get("/forms/{form_id}", response_model=FormResponse, summary="Obtenir un formulaire et ses versions")
async def get_form(
    form_id: int,
    version_id: Optional[int] = Query(None, description="ID de version cible pour charger ses sections et questions spécifiques"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await FormService.get_form_by_id(
        db=db,
        form_id=form_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
        version_id=version_id,
    )


@router.patch("/forms/{form_id}", response_model=FormResponse, summary="Mettre à jour partiellement un formulaire")
@router.put("/forms/{form_id}", response_model=FormResponse, summary="Mettre à jour un formulaire")
async def update_form(
    form_id: int,
    payload: FormUpdate,
    version_id: Optional[int] = Query(None, description="ID de version cible pour appliquer les modifications"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await FormService.update_form(
        db=db,
        form_id=form_id,
        form_in=payload,
        user=current_user,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
        version_id=version_id,
    )


@router.delete("/forms/{form_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Supprimer un formulaire")
async def delete_form(
    form_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    await FormService.delete_form(
        db=db,
        form_id=form_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )
    return None


# ---------------------------------------------------------------------------
# GESTION DES VERSIONS DE FORMULAIRES (FormVersion)
# ---------------------------------------------------------------------------

@router.get("/forms/{form_id}/versions", response_model=List[FormVersionResponse], summary="Lister les versions d'un formulaire")
async def list_form_versions(
    form_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await FormService.get_form_versions(
        db=db,
        form_id=form_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.get("/forms/{form_id}/versions/{version_id}", response_model=FormVersionResponse, summary="Détails d'une version de formulaire")
async def get_form_version(
    form_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await FormService.get_form_version_by_id(
        db=db,
        form_id=form_id,
        version_id=version_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/forms/{form_id}/versions", response_model=FormResponse, status_code=status.HTTP_201_CREATED, summary="Créer une nouvelle version incrémentale en brouillon")
async def create_form_version(
    form_id: int,
    payload: FormVersionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await FormService.create_form_version(
        db=db,
        form_id=form_id,
        req=payload,
        user=current_user,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/forms/{form_id}/versions/clone", response_model=FormResponse, status_code=status.HTTP_201_CREATED, summary="Cloner une version existante de formulaire")
async def clone_form_version(
    form_id: int,
    payload: FormVersionCloneRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await FormService.clone_form_version(
        db=db,
        form_id=form_id,
        req=payload,
        user=current_user,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/forms/{form_id}/publish", response_model=FormResponse, summary="Publier et activer la version active courante du formulaire")
async def publish_form(
    form_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    form = await FormService.get_form_by_id(db, form_id, target_org_id, is_super_admin)
    ver = form.active_version
    if not ver:
        raise HTTPException(status_code=400, detail="Aucune version trouvée pour ce formulaire.")
    return await FormService.publish_form_version(
        db=db,
        form_id=form_id,
        version_id=ver.id,
        user=current_user,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/forms/{form_id}/new-version", response_model=FormResponse, summary="Créer une nouvelle version en clonant une version publiée")
async def create_new_version_shortcut(
    form_id: int,
    payload: Optional[Dict[str, Any]] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    form = await FormService.get_form_by_id(db, form_id, target_org_id, is_super_admin)

    # Récupérer la version source publiée
    src_v_id = (payload or {}).get("source_version_id")
    ver = None
    if src_v_id:
        ver = next((v for v in (form.versions or []) if v.id == int(src_v_id)), None)

    if not ver:
        # Trouver la version active si publiée, sinon la plus récente publiée
        published_candidates = [
            v for v in (form.versions or [])
            if v.status in (FormVersionStatus.PUBLISHED.value, "published", FormVersionStatus.ACTIVE.value, "active")
        ]
        if not published_candidates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de créer une nouvelle version : aucune version publiée n'est disponible pour servir de base."
            )
        ver = next((v for v in published_candidates if v.version_number == form.version), published_candidates[0])

    new_version_number = (payload or {}).get("version") or (payload or {}).get("new_version_number")
    if not new_version_number:
        parts = ver.version_number.split(".")
        if len(parts) == 3 and parts[1].isdigit():
            new_version_number = f"{parts[0]}.{int(parts[1]) + 1}.0"
        else:
            new_version_number = f"{ver.version_number}.1"

    desc = (payload or {}).get("description") or f"Nouvelle version dérivée de v{ver.version_number}"

    clone_req = FormVersionCloneRequest(
        source_version_id=ver.id,
        new_version_number=new_version_number,
        description=desc,
    )
    return await FormService.clone_form_version(
        db=db,
        form_id=form_id,
        req=clone_req,
        user=current_user,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/forms/{form_id}/versions/{version_id}/publish", response_model=FormResponse, summary="Publier et activer une version de formulaire")
async def publish_form_version(
    form_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await FormService.publish_form_version(
        db=db,
        form_id=form_id,
        version_id=version_id,
        user=current_user,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/forms/{form_id}/versions/{version_id}/set-active", response_model=FormResponse, summary="Définir une version publiée ou archivée comme la version active courante")
async def set_active_form_version(
    form_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await FormService.set_active_form_version(
        db=db,
        form_id=form_id,
        version_id=version_id,
        user=current_user,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.delete("/forms/{form_id}/versions/{version_id}", response_model=FormResponse, summary="Supprimer une version de formulaire")
async def delete_form_version(
    form_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await FormService.delete_form_version(
        db=db,
        form_id=form_id,
        version_id=version_id,
        user=current_user,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


# ---------------------------------------------------------------------------
# SECTIONS & QUESTIONS
# ---------------------------------------------------------------------------

@router.post("/forms/{form_id}/sections", response_model=FormSectionResponse, status_code=status.HTTP_201_CREATED, summary="Ajouter une section")
async def create_section(
    form_id: int,
    payload: FormSectionCreate,
    version_id: Optional[int] = Query(None, description="ID de version cible"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await FormService.create_section(
        db=db,
        form_id=form_id,
        section_in=payload,
        version_id=version_id,
    )


@router.patch("/forms/sections/{section_id}", response_model=FormSectionResponse, summary="Mettre à jour une section")
@router.patch("/sections/{section_id}", response_model=FormSectionResponse, include_in_schema=False)
async def update_section(
    section_id: int,
    payload: FormSectionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await FormService.update_section(
        db=db,
        section_id=section_id,
        section_in=payload,
    )


@router.delete("/forms/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Supprimer une section")
@router.delete("/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
async def delete_section(
    section_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await FormService.delete_section(
        db=db,
        section_id=section_id,
    )


@router.post("/forms/{form_id}/questions", response_model=FormQuestionResponse, status_code=status.HTTP_201_CREATED, summary="Ajouter une question racine")
@router.post("/forms/questions", response_model=FormQuestionResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_question(
    payload: FormQuestionCreate,
    form_id: Optional[int] = None,
    version_id: Optional[int] = Query(None, description="ID de version cible"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_form_id = form_id or payload.settings.get("form_id") if payload.settings else None
    if not target_form_id and payload.section_id:
        sec_chk = (await db.execute(select(FormSection).where(FormSection.id == payload.section_id))).scalars().first()
        if sec_chk:
            target_form_id = sec_chk.form_id
    if not target_form_id:
        target_form_id = form_id or 0
    return await FormService.create_question(
        db=db,
        form_id=target_form_id,
        question_in=payload,
        version_id=version_id,
    )


@router.post("/forms/sections/{section_id}/questions", response_model=FormQuestionResponse, status_code=status.HTTP_201_CREATED, summary="Ajouter une question dans une section")
@router.post("/sections/{section_id}/questions", response_model=FormQuestionResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
@router.post("/forms/{form_id}/sections/{section_id}/questions", response_model=FormQuestionResponse, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def add_question_to_section(
    section_id: int,
    payload: FormQuestionCreate,
    form_id: Optional[int] = None,
    version_id: Optional[int] = Query(None, description="ID de version cible"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await FormService.add_question_to_section(
        db=db,
        section_id=section_id,
        question_in=payload,
        version_id=version_id,
    )


@router.patch("/forms/questions/{question_id}", response_model=FormQuestionResponse, summary="Mettre à jour une question")
@router.patch("/questions/{question_id}", response_model=FormQuestionResponse, include_in_schema=False)
@router.put("/forms/questions/{question_id}", response_model=FormQuestionResponse, include_in_schema=False)
@router.put("/questions/{question_id}", response_model=FormQuestionResponse, include_in_schema=False)
async def update_question(
    question_id: int,
    payload: FormQuestionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await FormService.update_question(
        db=db,
        question_id=question_id,
        question_in=payload,
    )


@router.delete("/forms/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Supprimer une question")
@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT, include_in_schema=False)
async def delete_question(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await FormService.delete_question(
        db=db,
        question_id=question_id,
    )


# ---------------------------------------------------------------------------
# STRUCTURATION DYNAMIQUE DEPUIS DES RESSOURCES
# ---------------------------------------------------------------------------

@router.post("/forms/{form_id}/resource-preview", summary="Prévisualiser l'arborescence projetée depuis une ressource")
async def preview_resource_projection(
    form_id: int,
    payload: FormResourceProjectionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await ResourceBindingService.preview_form_projection(
        db=db,
        resource_id=payload.resource_id,
        resource_version_id=payload.resource_version_id,
        chapter_field=payload.chapter_field or "",
        section_field=payload.section_field,
        code_field=payload.code_field or "code",
        title_field=payload.title_field or "name",
        help_field=payload.help_field,
        type_field=payload.type_field,
        critical_field=payload.critical_field,
        target_group_field=payload.target_group_field,
        target_group_filter=payload.target_group_filter or "all",
        default_question_type=payload.default_question_type or "audit_compliance",
        is_required_default=payload.is_required_default,
        unique_id_field=payload.unique_id_field or "id",
        response_type_mode=payload.response_type_mode or "fixed",
        structure_mode=payload.structure_mode or "auto",
        block_resource_id=payload.block_resource_id,
        block_resource_version_id=payload.block_resource_version_id,
        block_title_field=payload.block_title_field or "name",
        block_code_field=payload.block_code_field or "code",
        block_description_field=payload.block_description_field,
        section_resource_id=payload.section_resource_id,
        section_resource_version_id=payload.section_resource_version_id,
        section_title_field=payload.section_title_field or "name",
        section_code_field=payload.section_code_field or "code",
        section_description_field=payload.section_description_field,
        section_block_link_field=payload.section_block_link_field,
        question_section_link_field=payload.question_section_link_field,
        selected_item_ids=payload.selected_item_ids,
    )


@router.post("/forms/{form_id}/generate-from-resource", response_model=FormResponse, summary="Générer et persister l'arborescence du formulaire depuis une ressource")
async def generate_from_resource(
    form_id: int,
    payload: FormResourceProjectionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    form = await ResourceBindingService.generate_and_persist_form_from_resource(
        db=db,
        form_id=form_id,
        form_version_id=payload.form_version_id,
        resource_id=payload.resource_id,
        resource_version_id=payload.resource_version_id,
        chapter_field=payload.chapter_field or "",
        section_field=payload.section_field,
        code_field=payload.code_field or "code",
        title_field=payload.title_field or "name",
        help_field=payload.help_field,
        type_field=payload.type_field,
        critical_field=payload.critical_field,
        target_group_field=payload.target_group_field,
        target_group_filter=payload.target_group_filter or "all",
        default_question_type=payload.default_question_type or "audit_compliance",
        is_required_default=payload.is_required_default,
        unique_id_field=payload.unique_id_field or "id",
        response_type_mode=payload.response_type_mode or "fixed",
        structure_mode=payload.structure_mode or "auto",
        block_resource_id=payload.block_resource_id,
        block_resource_version_id=payload.block_resource_version_id,
        block_title_field=payload.block_title_field or "name",
        block_code_field=payload.block_code_field or "code",
        block_description_field=payload.block_description_field,
        section_resource_id=payload.section_resource_id,
        section_resource_version_id=payload.section_resource_version_id,
        section_title_field=payload.section_title_field or "name",
        section_code_field=payload.section_code_field or "code",
        section_description_field=payload.section_description_field,
        section_block_link_field=payload.section_block_link_field,
        question_section_link_field=payload.question_section_link_field,
        sanctuarize_generated=payload.sanctuarize_generated,
        replace_existing=payload.replace_existing,
        target_parent_section_id=payload.target_parent_section_id,
        update_existing_root_section_id=payload.update_existing_root_section_id,
        allow_tenant_select_requirements=payload.allow_tenant_select_requirements,
        allow_tenant_add_unselected_questions=payload.allow_tenant_add_unselected_questions,
        allow_tenant_deselect_questions=payload.allow_tenant_deselect_questions,
        allow_tenant_add_questions=payload.allow_tenant_add_questions,
        allow_tenant_add_sections=payload.allow_tenant_add_sections,
        allow_tenant_delete_sections=payload.allow_tenant_delete_sections,
        allow_tenant_edit_structure=payload.allow_tenant_edit_structure,
        allow_tenant_delete_structure=payload.allow_tenant_delete_structure,
        selected_item_ids=payload.selected_item_ids,
        default_justification_config=payload.default_justification_config,
        is_super_admin=is_super_admin,
    )
    return form

