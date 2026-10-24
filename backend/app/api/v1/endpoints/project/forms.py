from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Query, status, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.api.deps import get_current_user
from app.models.user.user import User, UserRole
from app.schemas.project.form import (
    FormCreate,
    FormUpdate,
    FormGovernanceUpdate,
    FormResourceProjectionRequest,
    FormTreeImportRequest,
    FormResponse,
    FormDetailResponse,
)
from app.schemas.project.form_section import (
    FormSectionCreate,
    FormSectionUpdate,
    FormSectionResponse,
)
from app.schemas.project.form_question import (
    FormQuestionCreate,
    FormQuestionUpdate,
    FormQuestionResponse,
)
from app.schemas.project.form_submission import (
    FormSubmissionCreate,
    FormSubmissionResponse,
    FormSubmissionDetailResponse,
)
from app.services.project.form_service import FormService
from app.services.project.resource_binding import ResourceBindingService
from app.api.v1.endpoints.project.projects import resolve_org_id

router = APIRouter()


# ---------------------------------------------------------------------------
# FORMULAIRES DE PROJET
# ---------------------------------------------------------------------------

@router.get("/projects/{project_id}/forms", response_model=List[FormResponse], summary="Lister les formulaires d'un projet")
async def list_project_forms(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    forms = await FormService.get_forms(
        db=db,
        project_id=project_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )
    return forms


@router.post("/projects/{project_id}/forms", response_model=FormDetailResponse, status_code=status.HTTP_201_CREATED, summary="Créer un formulaire dans un projet")
async def create_project_form(
    project_id: int,
    payload: FormCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_org_id = await resolve_org_id(db, current_user)
    form = await FormService.create_form(
        db=db,
        project_id=project_id,
        data=payload,
        organization_id=target_org_id,
        user_id=current_user.id,
    )
    return form


@router.get("/forms/{form_id}", response_model=FormDetailResponse, summary="Obtenir un formulaire complet avec arborescence")
async def get_form_detail(
    form_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    form = await FormService.get_form_by_id(
        db=db,
        form_id=form_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )
    if not form:
        raise HTTPException(status_code=404, detail="Formulaire introuvable.")
    return form


@router.patch("/forms/{form_id}", response_model=FormResponse, summary="Modifier un formulaire")
async def update_form(
    form_id: int,
    payload: FormUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_org_id = await resolve_org_id(db, current_user)
    form = await FormService.update_form(
        db=db,
        form_id=form_id,
        data=payload,
        organization_id=target_org_id,
    )
    return form


@router.patch("/forms/{form_id}/governance", response_model=FormResponse, summary="Mettre à jour la gouvernance et droits organisation du formulaire")
async def update_form_governance(
    form_id: int,
    payload: FormGovernanceUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    form = await FormService.update_governance(
        db=db,
        form_id=form_id,
        data=payload,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )
    return form


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


@router.post("/forms/{form_id}/publish", response_model=FormResponse, summary="Publier un modèle de formulaire (le verrouille)")
async def publish_form(
    form_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await FormService.publish_form(
        db=db,
        form_id=form_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
    )


@router.post("/forms/{form_id}/new-version", response_model=FormDetailResponse, status_code=status.HTTP_201_CREATED, summary="Créer une nouvelle version (brouillon) d'un formulaire publié")
async def create_new_form_version(
    form_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    target_org_id = await resolve_org_id(db, current_user)
    return await FormService.create_new_form_version(
        db=db,
        form_id=form_id,
        organization_id=target_org_id,
        is_super_admin=is_super_admin,
        user_id=current_user.id,
    )



# ---------------------------------------------------------------------------
# SECTIONS & SOUS-SECTIONS (ARBORESCENCE RÉCURSIVE)
# ---------------------------------------------------------------------------

@router.post("/forms/{form_id}/sections", response_model=FormSectionResponse, status_code=status.HTTP_201_CREATED, summary="Ajouter une section ou sous-section")
async def add_section(
    form_id: int,
    payload: FormSectionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    sec = await FormService.add_section(db=db, form_id=form_id, data=payload, is_super_admin=is_super_admin)
    return sec


@router.patch("/forms/sections/{section_id}", response_model=FormSectionResponse, summary="Modifier une section")
@router.patch("/sections/{section_id}", response_model=FormSectionResponse, summary="Modifier une section (alias)")
async def update_section(
    section_id: int,
    payload: FormSectionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sec = await FormService.update_section(db=db, section_id=section_id, data=payload)
    return sec


@router.delete("/forms/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Supprimer une section")
@router.delete("/sections/{section_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Supprimer une section (alias)")
async def delete_section(
    section_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    await FormService.delete_section(db=db, section_id=section_id, is_super_admin=is_super_admin)
    return None


# ---------------------------------------------------------------------------
# QUESTIONS
# ---------------------------------------------------------------------------

@router.post("/forms/{form_id}/questions", response_model=FormQuestionResponse, status_code=status.HTTP_201_CREATED, summary="Ajouter un modèle de question à la racine du formulaire (hors section)")
async def add_form_question(
    form_id: int,
    payload: FormQuestionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    q = await FormService.add_question(
        db=db,
        section_id=None,
        form_id=form_id,
        data=payload,
        is_super_admin=is_super_admin,
    )
    return q


@router.post("/sections/{section_id}/questions", response_model=FormQuestionResponse, status_code=status.HTTP_201_CREATED, summary="Ajouter une question dans une section")
@router.post("/forms/sections/{section_id}/questions", response_model=FormQuestionResponse, status_code=status.HTTP_201_CREATED, summary="Ajouter une question dans une section (alias)")
async def add_question(
    section_id: int,
    payload: FormQuestionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    q = await FormService.add_question(db=db, section_id=section_id, data=payload, is_super_admin=is_super_admin)
    return q


@router.patch("/questions/{question_id}", response_model=FormQuestionResponse, summary="Modifier une question")
@router.patch("/forms/questions/{question_id}", response_model=FormQuestionResponse, summary="Modifier une question (alias)")
async def update_question(
    question_id: int,
    payload: FormQuestionUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    q = await FormService.update_question(db=db, question_id=question_id, data=payload, is_super_admin=is_super_admin)
    return q


@router.delete("/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Supprimer une question")
@router.delete("/forms/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Supprimer une question (alias)")
async def delete_question(
    question_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    is_super_admin = current_user.role == UserRole.SUPER_ADMIN
    await FormService.delete_question(db=db, question_id=question_id, is_super_admin=is_super_admin)
    return None


# ---------------------------------------------------------------------------
# PROJECTION DYNAMIQUE DE QUESTIONS DEPUIS LES RESSOURCES
# ---------------------------------------------------------------------------

@router.get("/questions/resource-options/{resource_id}", summary="Options dynamiques issues d'une ressource liée")
async def get_resource_options(
    resource_id: int,
    label_field: str = Query("name", description="Champ utilisé pour le libellé"),
    value_field: str = Query("id", description="Champ utilisé pour la valeur"),
    secondary_label_field: Optional[str] = Query(None, description="Champ secondaire optionnel"),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_org_id = await resolve_org_id(db, current_user)
    options = await ResourceBindingService.get_question_dynamic_options(
        db=db,
        resource_id=resource_id,
        resource_code=None,
        organization_id=target_org_id,
        label_field=label_field,
        value_field=value_field,
        secondary_label_field=secondary_label_field,
        limit=limit,
    )
    return options


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
        chapter_field=payload.chapter_field,
        section_field=payload.section_field,
        code_field=payload.code_field,
        title_field=payload.title_field,
        help_field=payload.help_field,
        type_field=payload.type_field,
        critical_field=payload.critical_field,
        target_group_field=payload.target_group_field,
        target_group_filter=payload.target_group_filter,
        default_question_type=payload.default_question_type,
        unique_id_field=payload.unique_id_field or "id",
        response_type_mode=payload.response_type_mode or "fixed",
        structure_mode=payload.structure_mode or "auto",
        block_resource_id=payload.block_resource_id,
        block_title_field=payload.block_title_field or "name",
        block_code_field=payload.block_code_field or "code",
        section_resource_id=payload.section_resource_id,
        section_title_field=payload.section_title_field or "name",
        section_code_field=payload.section_code_field or "code",
        section_block_link_field=payload.section_block_link_field,
        question_section_link_field=payload.question_section_link_field,
    )


@router.post("/forms/{form_id}/generate-from-resource", response_model=FormDetailResponse, summary="Générer et persister l'arborescence du formulaire depuis une ressource")
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
        resource_id=payload.resource_id,
        chapter_field=payload.chapter_field,
        section_field=payload.section_field,
        code_field=payload.code_field,
        title_field=payload.title_field,
        help_field=payload.help_field,
        type_field=payload.type_field,
        critical_field=payload.critical_field,
        target_group_field=payload.target_group_field,
        target_group_filter=payload.target_group_filter,
        default_question_type=payload.default_question_type,
        unique_id_field=payload.unique_id_field or "id",
        response_type_mode=payload.response_type_mode or "fixed",
        structure_mode=payload.structure_mode or "auto",
        block_resource_id=payload.block_resource_id,
        block_title_field=payload.block_title_field or "name",
        block_code_field=payload.block_code_field or "code",
        section_resource_id=payload.section_resource_id,
        section_title_field=payload.section_title_field or "name",
        section_code_field=payload.section_code_field or "code",
        section_block_link_field=payload.section_block_link_field,
        question_section_link_field=payload.question_section_link_field,
        sanctuarize_generated=payload.sanctuarize_generated,
        replace_existing=payload.replace_existing,
        target_parent_section_id=payload.target_parent_section_id,
        allow_tenant_select_requirements=payload.allow_tenant_select_requirements,
        is_super_admin=is_super_admin,
    )
    return form


@router.post("/forms/{form_id}/import-tree", response_model=FormDetailResponse, summary="Importer une arborescence récursive de sections et questions ($N$-aire)")
async def import_form_tree(
    form_id: int,
    payload: FormTreeImportRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Importe et insère directement une arborescence récursive de sections et questions
    construite dynamiquement depuis des ressources ou des relations inter-ressources.
    """
    updated_form = await FormService.import_tree(
        db=db,
        form_id=form_id,
        target_parent_section_id=payload.target_parent_section_id,
        replace_existing=payload.replace_existing,
        sections=payload.sections,
    )
    return updated_form


@router.post("/forms/{form_id}/project-from-resource/{resource_id}", summary="Projeter l'arborescence du formulaire depuis une ressource standard (legacy)")
async def project_from_resource(
    form_id: int,
    resource_id: int,
    group_by_field: str = Query("chapter", description="Champ ressource servant à créer les sections"),
    title_field: str = Query("criterion_title", description="Champ pour l'intitulé de la question"),
    code_field: str = Query("criterion_code", description="Champ pour le code de la question"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sections_data = await ResourceBindingService.project_form_from_resource(
        db=db,
        resource_id=resource_id,
        group_by_field=group_by_field,
        title_field=title_field,
        code_field=code_field,
    )

    created_sections = []
    for s_dict in sections_data:
        q_creates = [FormQuestionCreate(**q) for q in s_dict["questions"]]
        s_create = FormSectionCreate(
            title=s_dict["title"],
            code=s_dict["code"],
            order_index=s_dict["order_index"],
            questions=q_creates,
        )
        sec = await FormService._create_section_recursive(db, form_id, None, s_create)
        created_sections.append(sec.code)

    await db.commit()
    return {"status": "success", "imported_sections_count": len(created_sections), "sections": created_sections}



# ---------------------------------------------------------------------------
# SOUMISSIONS, AUDIT & SCORING
# ---------------------------------------------------------------------------

@router.post("/forms/{form_id}/submissions", response_model=FormSubmissionDetailResponse, status_code=status.HTTP_201_CREATED, summary="Soumettre des réponses et calculer le score de conformité")
async def submit_form(
    form_id: int,
    payload: FormSubmissionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_org_id = await resolve_org_id(db, current_user)
    submission = await FormService.submit_form(
        db=db,
        form_id=form_id,
        payload=payload,
        organization_id=target_org_id,
        user_id=current_user.id,
    )
    return await FormService.get_submission_by_id(db, submission.id)


@router.get("/forms/{form_id}/submissions", response_model=List[FormSubmissionResponse], summary="Lister les soumissions d'un formulaire")
async def list_form_submissions(
    form_id: int,
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    target_org_id = await resolve_org_id(db, current_user)
    submissions = await FormService.get_submissions(
        db=db,
        form_id=form_id,
        organization_id=target_org_id,
        limit=limit,
    )
    return submissions


@router.get("/forms/submissions/{submission_id}", response_model=FormSubmissionDetailResponse, summary="Obtenir les détails et réponses d'une soumission")
async def get_submission(
    submission_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    sub = await FormService.get_submission_by_id(db, submission_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Soumission introuvable.")
    return sub
