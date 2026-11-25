"""
Form Service for SenWiseTool.
Manages autonomous, multi-versioned Forms completely independent of projects.
Supports:
- Form CRUD (name, code, visibility, version, description)
- Form Versions lifecycle (draft, publish, activate, clone, delete)
- Form Sections and Questions per version
- Form Submissions and Scoring
"""

import copy
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from sqlalchemy import and_, delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

from app.models.form.form import Form, FormStatus, FormVisibility
from app.models.form.form_question import FormQuestion
from app.models.form.form_section import FormSection
from app.models.form.form_version import FormVersion, FormVersionStatus
from app.models.resource import Resource
from app.models.resource.resource import ResourceStatus
from app.models.resource.resource_version import ResourceVersion, ResourceVersionStatus
from app.models.user.user import User
from app.schemas.form.form import FormCreate, FormUpdate
from app.schemas.form.form_question import FormQuestionCreate, FormQuestionUpdate
from app.schemas.form.form_section import FormSectionCreate, FormSectionUpdate
from app.schemas.form.form_version import FormVersionCloneRequest, FormVersionCreate, FormVersionUpdate
from app.services.form.compliance_scorer import ComplianceScorer
from app.services.form.formula_engine import FormulaEngine

logger = logging.getLogger("app.services.form.form_service")


class FormService:

    @classmethod
    async def get_forms(
        cls,
        db: AsyncSession,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
        visibility: Optional[str] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[Form]:
        """
        Récupère la liste des formulaires accessibles pour une organisation ou super admin.
        """
        stmt = (
            select(Form)
            .options(
                selectinload(Form.versions).selectinload(FormVersion.sections).selectinload(FormSection.subsections),
                selectinload(Form.versions).selectinload(FormVersion.sections).selectinload(FormSection.questions),
                selectinload(Form.versions).selectinload(FormVersion.questions),
            )
            .order_by(desc(Form.created_at))
        )

        if not is_super_admin:
            if organization_id is not None:
                stmt = stmt.where(
                    or_(
                        Form.organization_id == organization_id,
                        Form.visibility == FormVisibility.PUBLIC.value,
                    )
                )
            else:
                stmt = stmt.where(Form.visibility == FormVisibility.PUBLIC.value)

        if visibility:
            stmt = stmt.where(Form.visibility == visibility)

        if status:
            stmt = stmt.where(Form.status == status)

        if search:
            search_pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Form.name.ilike(search_pattern),
                    Form.code.ilike(search_pattern),
                    Form.description.ilike(search_pattern),
                )
            )

        res = await db.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_form_by_id(
        cls,
        db: AsyncSession,
        form_id: int,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
        version_id: Optional[int] = None,
    ) -> Form:
        """
        Récupère un formulaire par son ID avec ses versions et arborescences.
        Les sections et questions retournées correspondent exactement à la version spécifiée
        (ou par défaut à la version active courante du formulaire).
        """
        stmt = (
            select(Form)
            .execution_options(populate_existing=True)
            .options(
                selectinload(Form.versions).selectinload(FormVersion.sections).selectinload(FormSection.subsections),
                selectinload(Form.versions).selectinload(FormVersion.sections).selectinload(FormSection.questions),
                selectinload(Form.versions).selectinload(FormVersion.questions),
            )
            .where(Form.id == form_id)
        )

        res = await db.execute(stmt)
        form = res.scalars().first()
        if not form:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Formulaire introuvable."
            )

        # Contrôle d'accès isolation multi-tenant
        if not is_super_admin and form.visibility == FormVisibility.PRIVATE.value:
            if organization_id is not None and form.organization_id != organization_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès refusé à ce formulaire privé."
                )

        # Résolution souveraine de la version cible :
        # - Si version_id est spécifié, cibler exactement cette version
        # - Si version_id est None, cibler en priorité absolue le brouillon en cours de conception ('draft') s'il existe,
        #   sinon la version active / publiée du formulaire.
        targeted_ver = None
        if version_id is not None:
            targeted_ver = next((v for v in form.versions if v.id == version_id), None)
            if not targeted_ver:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Version {version_id} introuvable pour ce formulaire."
                )
        else:
            # Chercher le brouillon actif en priorité
            targeted_ver = next((v for v in form.versions if v.status == FormVersionStatus.DRAFT.value), None)
            if not targeted_ver:
                targeted_ver = next((v for v in form.versions if v.version_number == form.version), None)
            if not targeted_ver and form.versions:
                targeted_ver = form.versions[0]

        if targeted_ver:
            form.version = targeted_ver.version_number
            form.status = targeted_ver.status
            form.sections = targeted_ver.sections
            form.questions = targeted_ver.questions
            form.settings = targeted_ver.settings

        return form

    @classmethod
    async def create_form(
        cls,
        db: AsyncSession,
        form_in: FormCreate,
        user: User,
        organization_id: Optional[int] = None,
    ) -> Form:
        """
        Création épurée d'un nouveau Formulaire :
        Uniquement Nom, Code, Visibilité, Version initiale et Description.
        Crée automatiquement la première FormVersion en statut 'draft'.
        """
        code = form_in.code.strip()
        name = form_in.name.strip()
        visibility = form_in.visibility.strip().lower() if form_in.visibility else FormVisibility.PUBLIC.value
        initial_version = form_in.version.strip() if form_in.version else "1.0.0"

        # Vérifier l'unicité du code dans l'organisation
        chk_stmt = select(Form).where(
            Form.organization_id == organization_id,
            func.lower(Form.code) == code.lower(),
        )
        existing = (await db.execute(chk_stmt)).scalars().first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Un formulaire avec le code « {code} » existe déjà dans votre organisation."
            )

        form = Form(
            organization_id=organization_id,
            name=name,
            code=code,
            description=form_in.description.strip() if form_in.description else None,
            visibility=visibility,
            version=initial_version,
            status=FormStatus.DRAFT.value,
            created_by_id=user.id if user else None,
        )
        db.add(form)
        await db.flush()

        # Création automatique de la première version de formulaire (v1.0.0 par défaut en statut draft)
        first_version = FormVersion(
            form_id=form.id,
            version_number=initial_version,
            status=FormVersionStatus.DRAFT.value,
            description="Version initiale créée.",
            form_type="standard",
            settings={},
            template_governance={},
            created_by_id=user.id if user else None,
        )
        db.add(first_version)
        await db.flush()

        await db.commit()
        return await cls.get_form_by_id(db, form.id, organization_id=organization_id, is_super_admin=True)

    @classmethod
    async def validate_and_enrich_resource_binding(
        cls,
        db: AsyncSession,
        binding: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Valide l'intégrité de liaison de ressource :
        - Vérifie que la ressource liée existe.
        - Vérifie que la ressource est publiée (interdiction stricte d'utiliser un brouillon).
        - Scelle la version publiée de la ressource ('resource_version' et 'resource_version_id').
        """
        if not binding or not isinstance(binding, dict):
            return binding or {}

        res_id = binding.get("resource_id") or binding.get("source_resource_id")
        if not res_id:
            return binding

        try:
            res_id_int = int(res_id)
        except (ValueError, TypeError):
            return binding

        stmt = select(Resource).options(selectinload(Resource.versions)).where(Resource.id == res_id_int)
        res = (await db.execute(stmt)).scalars().first()
        if not res:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"La ressource référencée (#{res_id_int}) est introuvable."
            )

        published_version: Optional[ResourceVersion] = None
        for v in (res.versions or []):
            if v.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active"):
                published_version = v
                break
        if not published_version and res.status in (ResourceStatus.ACTIVE.value, ResourceStatus.PUBLISHED.value) and res.versions:
            published_version = res.versions[0]

        if not published_version or res.status == ResourceStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La ressource « {res.name} » est en brouillon. Seules les versions de ressources publiées peuvent être exploitées. Veuillez publier cette ressource au préalable."
            )

        enriched_binding = dict(binding)
        enriched_binding["resource_name"] = res.name
        enriched_binding["resource_code"] = res.code
        enriched_binding["resource_version"] = published_version.version_number
        enriched_binding["resource_version_id"] = published_version.id
        if "source_resource_id" in enriched_binding:
            enriched_binding["source_resource_version"] = published_version.version_number
            enriched_binding["source_resource_version_id"] = published_version.id

        return enriched_binding

    @classmethod
    async def update_form(
        cls,
        db: AsyncSession,
        form_id: int,
        form_in: FormUpdate,
        user: User,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
        version_id: Optional[int] = None,
    ) -> Form:
        """
        Mise à jour des métadonnées globales du formulaire ou des configurations de version.
        Règle stricte d'immuabilité : Les configurations et paramètres d'une version scellée (publiée ou archivée)
        ne peuvent JAMAIS être modifiés. Seule une version en brouillon ('draft') peut subir des modifications.
        """
        form = await cls.get_form_by_id(db, form_id, organization_id, is_super_admin, version_id=version_id)

        target_v = None
        if version_id is not None:
            target_v = next((v for v in form.versions if v.id == version_id), None)
            if not target_v:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Version {version_id} introuvable.")
        else:
            # Chercher en priorité un brouillon actif
            target_v = next((v for v in form.versions if v.status == FormVersionStatus.DRAFT.value), None)
            if not target_v:
                target_v = form.active_version

        if form_in.name is not None:
            form.name = form_in.name.strip()
        if form_in.description is not None:
            form.description = form_in.description.strip()
        if form_in.visibility is not None:
            form.visibility = form_in.visibility.strip().lower()

        if form_in.settings is not None:
            if target_v and target_v.status != FormVersionStatus.DRAFT.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Sécurité de versionnage : La version « v{target_v.version_number} » est scellée ({target_v.status}) et ne peut pas être modifiée. Pour modifier les paramètres, créez un brouillon ou clonez cette version."
                )

            curr_settings = dict(target_v.settings or {}) if target_v else dict(form.settings or {})
            new_settings = dict(form_in.settings)

            # Validation des métadonnées de type resource_lookup
            if "metadata_fields" in new_settings and isinstance(new_settings["metadata_fields"], list):
                validated_fields = []
                for field in new_settings["metadata_fields"]:
                    if isinstance(field, dict) and field.get("field_type") == "resource_lookup" and field.get("resource_binding"):
                        field_copy = dict(field)
                        field_copy["resource_binding"] = await cls.validate_and_enrich_resource_binding(db, field.get("resource_binding"))
                        validated_fields.append(field_copy)
                    else:
                        validated_fields.append(field)
                new_settings["metadata_fields"] = validated_fields

            curr_settings.update(new_settings)
            if target_v:
                target_v.settings = curr_settings
                flag_modified(target_v, "settings")
            form.settings = curr_settings
            flag_modified(form, "settings")

        form.updated_at = datetime.now(timezone.utc)
        await db.commit()
        return await cls.get_form_by_id(db, form.id, organization_id, is_super_admin, version_id=target_v.id if target_v else version_id)

    @classmethod
    async def delete_form(
        cls,
        db: AsyncSession,
        form_id: int,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> bool:
        """
        Supprime un formulaire et toutes ses versions associées en cascade.
        Règle stricte : Interdiction formelle de supprimer un formulaire qui possède au moins une version publiée.
        """
        form = await cls.get_form_by_id(db, form_id, organization_id, is_super_admin)

        has_published = any(
            v.status in (FormVersionStatus.PUBLISHED.value, "published", FormVersionStatus.ACTIVE.value, "active")
            for v in (form.versions or [])
        ) or form.status in (FormStatus.PUBLISHED.value, "published")

        if has_published:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sécurité de traçabilité : Impossible de supprimer le formulaire « {form.name} » car il possède des versions publiées. Les versions publiées sont scellées et immuables car elles peuvent être exploitées par d'autres modules, campagnes ou soumissions."
            )

        await db.delete(form)
        await db.commit()
        return True

    # =========================================================================
    # GESTION DES VERSIONS DE FORMULAIRES (FormVersion)
    # =========================================================================

    @classmethod
    async def get_form_versions(
        cls,
        db: AsyncSession,
        form_id: int,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> List[FormVersion]:
        """
        Liste toutes les versions d'un formulaire.
        """
        await cls.get_form_by_id(db, form_id, organization_id, is_super_admin)
        stmt = (
            select(FormVersion)
            .where(FormVersion.form_id == form_id)
            .options(
                selectinload(FormVersion.sections),
                selectinload(FormVersion.questions),
            )
            .order_by(desc(FormVersion.created_at))
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    async def get_form_version_by_id(
        cls,
        db: AsyncSession,
        form_id: int,
        version_id: int,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> FormVersion:
        """
        Récupère une version spécifique d'un formulaire avec son arborescence complète.
        """
        await cls.get_form_by_id(db, form_id, organization_id, is_super_admin)
        stmt = (
            select(FormVersion)
            .where(FormVersion.id == version_id, FormVersion.form_id == form_id)
            .options(
                selectinload(FormVersion.sections).selectinload(FormSection.subsections),
                selectinload(FormVersion.sections).selectinload(FormSection.questions),
                selectinload(FormVersion.questions),
            )
        )
        res = await db.execute(stmt)
        version = res.scalars().first()
        if not version:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Version de formulaire introuvable."
            )
        return version

    @classmethod
    async def create_form_version(
        cls,
        db: AsyncSession,
        form_id: int,
        req: FormVersionCreate,
        user: User,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> Form:
        """
        Crée une nouvelle version incrémentale en brouillon.
        Règle stricte : Hormis la première version créée avec le formulaire, toute nouvelle version
        doit impérativement être clonée à partir d'une version déjà publiée.
        """
        form = await cls.get_form_by_id(db, form_id, organization_id, is_super_admin)

        published_vers = [
            v for v in (form.versions or [])
            if v.status in (FormVersionStatus.PUBLISHED.value, "published", FormVersionStatus.ACTIVE.value, "active")
        ]
        if not published_vers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de créer une nouvelle version : aucune version du formulaire n'a encore été publiée. Publiez d'abord la version existante avant de créer une version suivante."
            )

        src_v = next((v for v in published_vers if v.version_number == form.version), published_vers[0])
        clone_req = FormVersionCloneRequest(
            source_version_id=src_v.id,
            new_version_number=req.version_number.strip(),
            description=req.description.strip() if req.description else None,
        )
        return await cls.clone_form_version(
            db=db,
            form_id=form_id,
            req=clone_req,
            user=user,
            organization_id=organization_id,
            is_super_admin=is_super_admin,
        )

    @classmethod
    async def clone_form_version(
        cls,
        db: AsyncSession,
        form_id: int,
        req: FormVersionCloneRequest,
        user: User,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> Form:
        """
        Clone une version existante (avec ses sections et questions) pour créer une nouvelle version en brouillon.
        Règles d'intégrité strictes :
        - Interdiction formelle de cloner une version encore en brouillon (seule une version publiée ou archivée peut être clonée).
        - Unicité absolue du brouillon : un formulaire ne peut avoir qu'un seul brouillon à la fois.
        - Copie intégrale de toute l'arborescence (paramètres, sections, sous-sections récursives, questions, règles, gouvernance).
        """
        form = await cls.get_form_by_id(db, form_id, organization_id, is_super_admin)
        src_version = await cls.get_form_version_by_id(db, form_id, req.source_version_id, organization_id, is_super_admin)

        # 1. Règle : Interdiction absolue de cloner une version en brouillon
        if src_version.status == FormVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Impossible de cloner la version « v{src_version.version_number} » car elle est encore en brouillon. Seule une version publiée ou archivée peut servir de base pour un clonage."
            )

        # 2. Règle : Unicité absolue du brouillon dans le formulaire
        draft_chk = await db.execute(
            select(FormVersion).where(
                FormVersion.form_id == form_id,
                FormVersion.status == FormVersionStatus.DRAFT.value,
            )
        )
        existing_draft = draft_chk.scalars().first()
        if existing_draft:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Une version en brouillon (« v{existing_draft.version_number} ») existe déjà pour ce formulaire. Veuillez la publier ou la supprimer avant de créer ou cloner une autre version."
            )

        # 3. Règle : Unicité du numéro de nouvelle version
        new_ver_num = req.new_version_number.strip()
        num_chk = await db.execute(
            select(FormVersion).where(
                FormVersion.form_id == form_id,
                FormVersion.version_number == new_ver_num,
            )
        )
        if num_chk.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La version « v{new_ver_num} » existe déjà pour ce formulaire."
            )

        new_v = FormVersion(
            form_id=form.id,
            version_number=new_ver_num,
            status=FormVersionStatus.DRAFT.value,
            description=req.description.strip() if req.description else f"Cloné depuis v{src_version.version_number}",
            form_type=src_version.form_type,
            settings=copy.deepcopy(src_version.settings or {}),
            template_governance=copy.deepcopy(src_version.template_governance or {}),
            created_by_id=user.id if user else None,
        )
        db.add(new_v)
        await db.flush()

        # 4. Duplication intégrale et exhaustive de l'arborescence (sections, sous-sections, questions)
        src_sections_stmt = select(FormSection).where(
            or_(
                FormSection.form_version_id == src_version.id,
                and_(FormSection.form_id == form.id, FormSection.form_version_id.is_(None)) if src_version.version_number == form.version else False
            )
        ).options(
            selectinload(FormSection.questions)
        ).order_by(FormSection.order_index)
        all_src_sections = (await db.execute(src_sections_stmt)).scalars().all()

        src_all_q_stmt = select(FormQuestion).where(
            or_(
                FormQuestion.form_version_id == src_version.id,
                and_(FormQuestion.form_id == form.id, FormQuestion.form_version_id.is_(None)) if src_version.version_number == form.version else False
            )
        ).order_by(FormQuestion.order_index)
        all_src_questions = (await db.execute(src_all_q_stmt)).scalars().all()

        # Cloner les sections par ordre hiérarchique parent -> enfant
        old_sec_id_to_new_sec_id: Dict[int, int] = {}
        sections_by_parent: Dict[Optional[int], List[FormSection]] = {}
        for s in all_src_sections:
            sections_by_parent.setdefault(s.parent_section_id, []).append(s)

        async def clone_level(parent_old_id: Optional[int], parent_new_id: Optional[int]):
            for s in sections_by_parent.get(parent_old_id, []):
                new_sec = FormSection(
                    form_id=form.id,
                    form_version_id=new_v.id,
                    parent_section_id=parent_new_id,
                    title=s.title,
                    code=s.code,
                    description=s.description,
                    order_index=s.order_index,
                    is_repeatable=s.is_repeatable,
                    min_repeats=s.min_repeats,
                    max_repeats=s.max_repeats,
                    is_sanctuarized=s.is_sanctuarized,
                    allow_custom_questions=s.allow_custom_questions,
                    weight=s.weight,
                    scoring_category=s.scoring_category,
                    is_critical=s.is_critical,
                    min_passing_score=s.min_passing_score,
                    dependency_rules=copy.deepcopy(s.dependency_rules or {}),
                    settings=copy.deepcopy(s.settings or {}),
                )
                db.add(new_sec)
                await db.flush()
                old_sec_id_to_new_sec_id[s.id] = new_sec.id
                await clone_level(s.id, new_sec.id)

        await clone_level(None, None)

        # Cloner toutes les questions en les rattachant aux nouvelles sections correspondantes
        for q in all_src_questions:
            target_sec_id = old_sec_id_to_new_sec_id.get(q.section_id) if q.section_id else None
            new_q = FormQuestion(
                form_id=form.id,
                form_version_id=new_v.id,
                section_id=target_sec_id,
                code=q.code,
                label=q.label,
                help_text=q.help_text,
                placeholder=q.placeholder,
                question_type=q.question_type,
                order_index=q.order_index,
                is_required=q.is_required,
                is_readonly=q.is_readonly,
                is_sanctuarized=q.is_sanctuarized,
                is_critical=q.is_critical,
                weight=q.weight,
                scoring_config=copy.deepcopy(q.scoring_config or {}),
                options=copy.deepcopy(q.options or []),
                resource_binding=copy.deepcopy(q.resource_binding or {}),
                formula_expression=q.formula_expression,
                validation_rules=copy.deepcopy(q.validation_rules or {}),
                dependency_rules=copy.deepcopy(q.dependency_rules or {}),
                settings=copy.deepcopy(q.settings or {}),
            )
            db.add(new_q)

        await db.commit()
        # Retourner le formulaire avec la nouvelle version active en brouillon
        return await cls.get_form_by_id(db, form.id, organization_id, is_super_admin, version_id=new_v.id)

    @classmethod
    async def _clone_section_recursive(
        cls,
        db: AsyncSession,
        form_id: int,
        new_version_id: int,
        src_sec: FormSection,
        parent_id: Optional[int] = None,
    ) -> FormSection:
        new_sec = FormSection(
            form_id=form_id,
            form_version_id=new_version_id,
            parent_section_id=parent_id,
            title=src_sec.title,
            code=src_sec.code,
            description=src_sec.description,
            order_index=src_sec.order_index,
            is_repeatable=src_sec.is_repeatable,
            min_repeats=src_sec.min_repeats,
            max_repeats=src_sec.max_repeats,
            is_sanctuarized=src_sec.is_sanctuarized,
            allow_custom_questions=src_sec.allow_custom_questions,
            weight=src_sec.weight,
            scoring_category=src_sec.scoring_category,
            is_critical=src_sec.is_critical,
            min_passing_score=src_sec.min_passing_score,
            dependency_rules=copy.deepcopy(src_sec.dependency_rules or {}),
            settings=copy.deepcopy(src_sec.settings or {}),
        )
        db.add(new_sec)
        await db.flush()

        # Cloner les questions de la section
        if src_sec.questions:
            for q in src_sec.questions:
                new_q = FormQuestion(
                    form_id=form_id,
                    form_version_id=new_version_id,
                    section_id=new_sec.id,
                    code=q.code,
                    label=q.label,
                    help_text=q.help_text,
                    placeholder=q.placeholder,
                    question_type=q.question_type,
                    order_index=q.order_index,
                    is_required=q.is_required,
                    is_readonly=q.is_readonly,
                    is_sanctuarized=q.is_sanctuarized,
                    is_critical=q.is_critical,
                    weight=q.weight,
                    scoring_config=copy.deepcopy(q.scoring_config or {}),
                    options=copy.deepcopy(q.options or []),
                    resource_binding=copy.deepcopy(q.resource_binding or {}),
                    formula_expression=q.formula_expression,
                    validation_rules=copy.deepcopy(q.validation_rules or {}),
                    dependency_rules=copy.deepcopy(q.dependency_rules or {}),
                    settings=copy.deepcopy(q.settings or {}),
                )
                db.add(new_q)

        # Cloner récursivement les sous-sections
        if src_sec.subsections:
            for sub in src_sec.subsections:
                await cls._clone_section_recursive(db, form_id, new_version_id, sub, parent_id=new_sec.id)

        return new_sec

    @classmethod
    async def publish_form_version(
        cls,
        db: AsyncSession,
        form_id: int,
        version_id: int,
        user: User,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> Form:
        """
        Publie et active une version de formulaire.
        Définit cette version comme la version active du formulaire et archive les versions antérieures.
        """
        form = await cls.get_form_by_id(db, form_id, organization_id, is_super_admin)
        ver = await cls.get_form_version_by_id(db, form_id, version_id, organization_id, is_super_admin)

        ver.status = FormVersionStatus.PUBLISHED.value
        ver.published_at = datetime.now(timezone.utc)
        ver.updated_at = datetime.now(timezone.utc)

        # Archiver les autres versions précédemment actives
        other_vers_stmt = select(FormVersion).where(
            FormVersion.form_id == form.id,
            FormVersion.id != ver.id,
        )
        for other_v in (await db.execute(other_vers_stmt)).scalars().all():
            if other_v.status in [FormVersionStatus.PUBLISHED.value, FormVersionStatus.ACTIVE.value]:
                other_v.status = FormVersionStatus.ARCHIVED.value
                other_v.updated_at = datetime.now(timezone.utc)

        form.version = ver.version_number
        form.status = FormStatus.PUBLISHED.value
        form.updated_at = datetime.now(timezone.utc)

        await db.commit()
        return await cls.get_form_by_id(db, form.id, organization_id, is_super_admin)

    @classmethod
    async def set_active_form_version(
        cls,
        db: AsyncSession,
        form_id: int,
        version_id: int,
        user: User,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> Form:
        """
        Définit une version spécifique publiée ou archivée comme étant la version active courante du formulaire.
        Permet de basculer instantanément la version active d'un simple clic.
        """
        form = await cls.get_form_by_id(db, form_id, organization_id, is_super_admin)
        ver = await cls.get_form_version_by_id(db, form_id, version_id, organization_id, is_super_admin)

        if ver.status == FormVersionStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Une version en brouillon ne peut pas être directement activée. Veuillez d'abord publier la version « v{ver.version_number} »."
            )

        # Réactiver la version sélectionnée
        ver.status = FormVersionStatus.PUBLISHED.value
        ver.updated_at = datetime.now(timezone.utc)

        # Archiver les autres versions publiées/actives
        other_vers_stmt = select(FormVersion).where(
            FormVersion.form_id == form.id,
            FormVersion.id != ver.id,
        )
        for other_v in (await db.execute(other_vers_stmt)).scalars().all():
            if other_v.status in [FormVersionStatus.PUBLISHED.value, FormVersionStatus.ACTIVE.value]:
                other_v.status = FormVersionStatus.ARCHIVED.value
                other_v.updated_at = datetime.now(timezone.utc)

        form.version = ver.version_number
        form.status = FormStatus.PUBLISHED.value
        form.updated_at = datetime.now(timezone.utc)

        await db.commit()
        return await cls.get_form_by_id(db, form.id, organization_id, is_super_admin)

    @classmethod
    async def delete_form_version(
        cls,
        db: AsyncSession,
        form_id: int,
        version_id: int,
        user: User,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> Form:
        """
        Supprime une version de formulaire.
        Règle stricte d'immuabilité : Une version publiée ou scellée ne peut JAMAIS être supprimée.
        Seules les versions en brouillon ('draft') peuvent être supprimées.
        """
        form = await cls.get_form_by_id(db, form_id, organization_id, is_super_admin)
        ver = await cls.get_form_version_by_id(db, form_id, version_id, organization_id, is_super_admin)

        # 1. Règle absolue de sécurité : Seules les versions en brouillon peuvent être supprimées
        if ver.status in (FormVersionStatus.PUBLISHED.value, "published", FormVersionStatus.ACTIVE.value, "active"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sécurité de versionnage : Impossible de supprimer la version « v{ver.version_number} » car elle est publiée. Une version publiée est scellée et immuable. Seules les versions en brouillon peuvent être supprimées."
            )

        # 2. Vérifier qu'il reste au moins une autre version
        remaining_vers = [v for v in form.versions if v.id != ver.id]
        if not remaining_vers:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de supprimer la seule version d'un formulaire."
            )

        # 3. Déterminer la version de repli (fallback) parmi les versions restantes :
        # Priorité absolue à une version publiée/active restante, sinon la plus récente
        fallback_v = next(
            (v for v in remaining_vers if v.status in (FormVersionStatus.PUBLISHED.value, "published", FormVersionStatus.ACTIVE.value, "active")),
            remaining_vers[0]
        )

        # 4. Détacher tous les objets ORM en mémoire avant les opérations directes pour prévenir tout conflit de session
        db.expunge_all()

        # 5. Suppression en cascade des questions, sections et de la version brouillon ciblée
        await db.execute(delete(FormQuestion).where(FormQuestion.form_version_id == version_id))
        await db.execute(delete(FormSection).where(FormSection.form_version_id == version_id))
        await db.execute(delete(FormVersion).where(FormVersion.id == version_id))

        # 6. Mettre à jour atomiquement la version de référence sur le formulaire parent
        from sqlalchemy import update
        await db.execute(
            update(Form)
            .where(Form.id == form_id)
            .values(
                version=fallback_v.version_number,
                status=fallback_v.status,
                updated_at=datetime.now(timezone.utc),
            )
        )

        await db.commit()
        return await cls.get_form_by_id(db, form_id, organization_id, is_super_admin, version_id=fallback_v.id)

    # =========================================================================
    # SECTIONS & QUESTIONS CRUD
    # =========================================================================

    @classmethod
    async def get_section_ancestors(cls, db: AsyncSession, section_id: Optional[int]) -> List[FormSection]:
        """
        Récupère la chaîne ascendante hiérarchique complète d'une section (de la section jusqu'à la racine).
        Permet la cascade des protocoles et contraintes sur les éléments directs et indirects.
        """
        if not section_id:
            return []
        ancestors: List[FormSection] = []
        curr_id: Optional[int] = section_id
        visited = set()
        while curr_id and curr_id not in visited:
            visited.add(curr_id)
            stmt = select(FormSection).where(FormSection.id == curr_id)
            sec = (await db.execute(stmt)).scalars().first()
            if not sec:
                break
            ancestors.append(sec)
            curr_id = sec.parent_section_id
        return ancestors

    @classmethod
    async def create_section(
        cls,
        db: AsyncSession,
        form_id: int,
        section_in: FormSectionCreate,
        version_id: Optional[int] = None,
    ) -> FormSection:
        """
        Crée une nouvelle section rattachée à un formulaire et à une version de formulaire.
        """
        target_version_id = version_id or section_in.form_version_id
        if not target_version_id:
            # Chercher en priorité absolue la version en brouillon ('draft') en cours de conception
            ver_draft_stmt = select(FormVersion).where(
                FormVersion.form_id == form_id,
                FormVersion.status == FormVersionStatus.DRAFT.value,
            )
            ver_obj = (await db.execute(ver_draft_stmt)).scalars().first()
            if ver_obj:
                target_version_id = ver_obj.id
            else:
                # Sinon récupérer la version active courante du formulaire
                form_stmt = select(Form).where(Form.id == form_id)
                form_obj = (await db.execute(form_stmt)).scalars().first()
                if form_obj:
                    ver_stmt = select(FormVersion).where(
                        FormVersion.form_id == form_id,
                        FormVersion.version_number == form_obj.version,
                    )
                    ver_obj = (await db.execute(ver_stmt)).scalars().first()
                    if ver_obj:
                        target_version_id = ver_obj.id

        if target_version_id:
            ver_chk = (await db.execute(select(FormVersion).where(FormVersion.id == target_version_id))).scalars().first()
            if ver_chk and ver_chk.status != FormVersionStatus.DRAFT.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La version « v{ver_chk.version_number} » est scellée ({ver_chk.status}) et ne peut pas être modifiée. Créez un brouillon ou clonez cette version pour apporter des modifications."
                )

        # Vérification hiérarchique de l'arborescence (éléments directs et indirects)
        if section_in.parent_section_id:
            lineage = await cls.get_section_ancestors(db, section_in.parent_section_id)
            for ancestor in lineage:
                if ancestor.is_sanctuarized:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"La section « {ancestor.title} » ou l'un de ses ancêtres est sanctuarisée. Aucun ajout de sous-section n'est autorisé."
                    )
                anc_settings = ancestor.settings or {}
                if anc_settings.get("allow_custom_subsections") is False:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"L'ajout de sous-sections est désactivé par le protocole de la section parente « {ancestor.title} » ou de ses ascendants."
                    )
                if (section_in.settings or {}).get("resource_projection") and anc_settings.get("allow_structure_add") is False:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"L'ajout d'une nouvelle structure est désactivé par le protocole de la section « {ancestor.title} » ou de ses ascendants."
                    )

        default_settings = {
            "allow_section_edit": True,
            "allow_section_delete": True,
            "allow_custom_questions": True,
            "allow_edit_questions": True,
            "allow_delete_questions": True,
            "allow_custom_subsections": True,
            "allow_edit_subsections": True,
            "allow_delete_subsections": True,
            "allow_structure_add": True,
            "allow_structure_edit": True,
            "allow_structure_reorder": True,
            "allow_structure_move": True,
            "allow_structure_delete": True,
            "allowed_question_types": [],
        }
        merged_settings = {**default_settings, **(section_in.settings or {})}

        section = FormSection(
            form_id=form_id,
            form_version_id=target_version_id,
            parent_section_id=section_in.parent_section_id,
            title=section_in.title.strip(),
            code=section_in.code.strip(),
            description=section_in.description.strip() if section_in.description else None,
            order_index=section_in.order_index,
            is_repeatable=section_in.is_repeatable,
            min_repeats=section_in.min_repeats,
            max_repeats=section_in.max_repeats,
            weight=section_in.weight,
            scoring_category=section_in.scoring_category,
            is_critical=section_in.is_critical,
            min_passing_score=section_in.min_passing_score,
            dependency_rules=section_in.dependency_rules or {},
            settings=merged_settings,
        )
        db.add(section)
        await db.flush()

        # Questions imbriquées
        if section_in.questions:
            for q_in in section_in.questions:
                q_binding = await cls.validate_and_enrich_resource_binding(db, q_in.resource_binding)
                q = FormQuestion(
                    form_id=form_id,
                    form_version_id=target_version_id,
                    section_id=section.id,
                    code=q_in.code.strip(),
                    label=q_in.label.strip(),
                    help_text=q_in.help_text,
                    placeholder=q_in.placeholder,
                    question_type=q_in.question_type,
                    order_index=q_in.order_index,
                    is_required=q_in.is_required,
                    is_readonly=q_in.is_readonly,
                    is_critical=q_in.is_critical,
                    weight=q_in.weight,
                    scoring_config=q_in.scoring_config or {},
                    options=q_in.options or [],
                    resource_binding=q_binding or {},
                    formula_expression=q_in.formula_expression,
                    validation_rules=q_in.validation_rules or {},
                    dependency_rules=q_in.dependency_rules or {},
                    settings=q_in.settings or {},
                )
                db.add(q)

        await db.commit()
        await db.refresh(section)
        return section

    @classmethod
    async def create_question(
        cls,
        db: AsyncSession,
        form_id: int,
        question_in: FormQuestionCreate,
        version_id: Optional[int] = None,
    ) -> FormQuestion:
        """
        Crée une question isolée ou rattachée à une section.
        """
        target_version_id = version_id or question_in.form_version_id
        if not target_version_id:
            # Chercher en priorité absolue la version en brouillon ('draft') en cours de conception
            ver_draft_stmt = select(FormVersion).where(
                FormVersion.form_id == form_id,
                FormVersion.status == FormVersionStatus.DRAFT.value,
            )
            ver_obj = (await db.execute(ver_draft_stmt)).scalars().first()
            if ver_obj:
                target_version_id = ver_obj.id
            else:
                form_stmt = select(Form).where(Form.id == form_id)
                form_obj = (await db.execute(form_stmt)).scalars().first()
                if form_obj:
                    ver_stmt = select(FormVersion).where(
                        FormVersion.form_id == form_id,
                        FormVersion.version_number == form_obj.version,
                    )
                    ver_obj = (await db.execute(ver_stmt)).scalars().first()
                    if ver_obj:
                        target_version_id = ver_obj.id

        if target_version_id:
            ver_chk = (await db.execute(select(FormVersion).where(FormVersion.id == target_version_id))).scalars().first()
            if ver_chk and ver_chk.status != FormVersionStatus.DRAFT.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La version « v{ver_chk.version_number} » est scellée ({ver_chk.status}) et ne peut pas être modifiée. Créez un brouillon ou clonez cette version pour apporter des modifications."
                )

        # Vérification hiérarchique de l'arborescence (éléments directs et indirects)
        if question_in.section_id:
            lineage = await cls.get_section_ancestors(db, question_in.section_id)
            for ancestor in lineage:
                if ancestor.is_sanctuarized:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"La section « {ancestor.title} » ou l'un de ses ascendants est sanctuarisée. Aucun ajout de question n'est autorisé."
                    )
                anc_settings = ancestor.settings or {}
                if ancestor.allow_custom_questions is False or anc_settings.get("allow_custom_questions") is False:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"L'ajout de questions est désactivé par le protocole de la section « {ancestor.title} » ou d'un de ses ascendants."
                    )
                allowed_types = anc_settings.get("allowed_question_types")
                if allowed_types and isinstance(allowed_types, list) and len(allowed_types) > 0:
                    if question_in.question_type not in allowed_types:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Le type de question « {question_in.question_type} » n'est pas autorisé par le protocole de la section « {ancestor.title} »."
                        )

        merged_settings = dict(question_in.settings or {})
        if question_in.default_value is not None and "default_value" not in merged_settings:
            merged_settings["default_value"] = question_in.default_value

        enriched_binding = await cls.validate_and_enrich_resource_binding(db, question_in.resource_binding)

        question = FormQuestion(
            form_id=form_id,
            form_version_id=target_version_id,
            section_id=question_in.section_id,
            code=question_in.code.strip(),
            label=question_in.label.strip(),
            help_text=question_in.help_text,
            placeholder=question_in.placeholder,
            question_type=question_in.question_type,
            order_index=question_in.order_index,
            is_required=question_in.is_required,
            is_readonly=question_in.is_readonly,
            is_critical=question_in.is_critical,
            weight=question_in.weight,
            scoring_config=question_in.scoring_config or {},
            options=question_in.options or [],
            resource_binding=enriched_binding or {},
            formula_expression=question_in.formula_expression,
            validation_rules=question_in.validation_rules or {},
            dependency_rules=question_in.dependency_rules or {},
            settings=merged_settings,
        )
        db.add(question)
        await db.commit()
        await db.refresh(question)
        return question

    @classmethod
    async def add_question_to_section(
        cls,
        db: AsyncSession,
        section_id: int,
        question_in: FormQuestionCreate,
        version_id: Optional[int] = None,
    ) -> FormQuestion:
        """
        Ajoute une question directement dans une section existante.
        """
        sec_stmt = select(FormSection).where(FormSection.id == section_id)
        sec = (await db.execute(sec_stmt)).scalars().first()
        if not sec:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section introuvable.")

        question_in.section_id = section_id
        target_version_id = version_id or question_in.form_version_id or sec.form_version_id
        return await cls.create_question(
            db=db,
            form_id=sec.form_id,
            question_in=question_in,
            version_id=target_version_id,
        )

    @classmethod
    async def update_question(
        cls,
        db: AsyncSession,
        question_id: int,
        question_in: FormQuestionUpdate,
    ) -> FormQuestion:
        """
        Met à jour une question existante si sa version est en brouillon.
        """
        stmt = select(FormQuestion).where(FormQuestion.id == question_id)
        q = (await db.execute(stmt)).scalars().first()
        if not q:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question introuvable.")

        if q.form_version_id:
            ver_chk = (await db.execute(select(FormVersion).where(FormVersion.id == q.form_version_id))).scalars().first()
            if ver_chk and ver_chk.status != FormVersionStatus.DRAFT.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La version « v{ver_chk.version_number} » est scellée ({ver_chk.status}) et ne peut pas être modifiée."
                )

        # Vérification hiérarchique de l'arborescence (éléments directs et indirects)
        if q.section_id:
            lineage = await cls.get_section_ancestors(db, q.section_id)
            for ancestor in lineage:
                if ancestor.is_sanctuarized:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"La question est rattachée à la section « {ancestor.title} » qui est sanctuarisée. Aucune modification n'est permise."
                    )
                anc_settings = ancestor.settings or {}
                if anc_settings.get("allow_edit_questions") is False:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"La modification des questions est désactivée par le protocole de la section « {ancestor.title} » ou de ses ascendants."
                    )

        dump = question_in.model_dump(exclude_unset=True)
        if "default_value" in dump:
            current_settings = dict(q.settings or {})
            current_settings["default_value"] = dump.pop("default_value")
            q.settings = current_settings

        if "resource_binding" in dump and dump["resource_binding"]:
            dump["resource_binding"] = await cls.validate_and_enrich_resource_binding(db, dump["resource_binding"])

        for key, value in dump.items():
            if hasattr(q, key):
                setattr(q, key, value)

        await db.commit()
        await db.refresh(q)
        return q

    @classmethod
    async def delete_question(
        cls,
        db: AsyncSession,
        question_id: int,
    ) -> bool:
        """
        Supprime une question.
        """
        stmt = select(FormQuestion).where(FormQuestion.id == question_id)
        q = (await db.execute(stmt)).scalars().first()
        if not q:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question introuvable.")

        if q.form_version_id:
            ver_chk = (await db.execute(select(FormVersion).where(FormVersion.id == q.form_version_id))).scalars().first()
            if ver_chk and ver_chk.status != FormVersionStatus.DRAFT.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La version « v{ver_chk.version_number} » est scellée ({ver_chk.status}) et ne peut pas être modifiée."
                )

        # Vérification hiérarchique de l'arborescence (éléments directs et indirects)
        if q.section_id:
            lineage = await cls.get_section_ancestors(db, q.section_id)
            for ancestor in lineage:
                if ancestor.is_sanctuarized:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"La question est rattachée à la section « {ancestor.title} » qui est sanctuarisée. Aucune suppression n'est permise."
                    )
                anc_settings = ancestor.settings or {}
                if anc_settings.get("allow_delete_questions") is False:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"La suppression des questions est désactivée par le protocole de la section « {ancestor.title} » ou de ses ascendants."
                    )

        await db.delete(q)
        await db.commit()
        return True

    @classmethod
    async def update_section(
        cls,
        db: AsyncSession,
        section_id: int,
        section_in: FormSectionUpdate,
    ) -> FormSection:
        """
        Met à jour une section existante.
        """
        stmt = select(FormSection).where(FormSection.id == section_id)
        sec = (await db.execute(stmt)).scalars().first()
        if not sec:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section introuvable.")

        if sec.form_version_id:
            ver_chk = (await db.execute(select(FormVersion).where(FormVersion.id == sec.form_version_id))).scalars().first()
            if ver_chk and ver_chk.status != FormVersionStatus.DRAFT.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La version « v{ver_chk.version_number} » est scellée ({ver_chk.status}) et ne peut pas être modifiée."
                )

        # Vérification hiérarchique de l'arborescence (éléments directs et indirects)
        # Note: si on modifie uniquement settings ou is_sanctuarized (protocole), c'est une action de gouvernance autorisée
        is_protocol_update_only = all(k in ("settings", "is_sanctuarized", "allow_custom_questions", "order_index") for k in section_in.model_dump(exclude_unset=True).keys())
        if not is_protocol_update_only:
            sec_settings = sec.settings or {}
            if sec.is_sanctuarized:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La section « {sec.title} » est sanctuarisée. Aucune modification de ses informations n'est autorisée."
                )
            if sec_settings.get("allow_section_edit") is False or sec_settings.get("allow_structure_edit") is False:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La modification des informations de cette section est désactivée par son protocole de gouvernance."
                )

            if sec.parent_section_id:
                lineage = await cls.get_section_ancestors(db, sec.parent_section_id)
                for ancestor in lineage:
                    if ancestor.is_sanctuarized:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"La section « {ancestor.title} » ou l'un de ses ancêtres est sanctuarisée. Aucune modification de sous-section n'est autorisée."
                        )
                    anc_settings = ancestor.settings or {}
                    if (
                        anc_settings.get("allow_section_edit") is False
                        or anc_settings.get("allow_edit_subsections") is False
                        or anc_settings.get("allow_structure_edit") is False
                    ):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"La modification des sous-sections est désactivée par le protocole de la section « {ancestor.title} » ou de ses ascendants."
                        )

        for key, value in section_in.model_dump(exclude_unset=True).items():
            if key == "settings" and isinstance(value, dict):
                current_settings = dict(sec.settings or {})
                current_settings.update(value)
                sec.settings = current_settings
            elif hasattr(sec, key):
                setattr(sec, key, value)

        await db.commit()
        await db.refresh(sec)
        return sec

    @classmethod
    async def delete_section(
        cls,
        db: AsyncSession,
        section_id: int,
    ) -> bool:
        """
        Supprime une section et ses sous-sections et questions associées.
        """
        stmt = (
            select(FormSection)
            .where(FormSection.id == section_id)
            .options(
                selectinload(FormSection.subsections),
                selectinload(FormSection.questions),
            )
        )
        sec = (await db.execute(stmt)).scalars().first()
        if not sec:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Section introuvable.")

        if sec.form_version_id:
            ver_chk = (await db.execute(select(FormVersion).where(FormVersion.id == sec.form_version_id))).scalars().first()
            if ver_chk and ver_chk.status != FormVersionStatus.DRAFT.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La version « v{ver_chk.version_number} » est scellée ({ver_chk.status}) et ne peut pas être modifiée."
                )

        # Vérification de la section elle-même
        sec_settings = sec.settings or {}
        if sec.is_sanctuarized:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La section « {sec.title} » est sanctuarisée et ne peut pas être supprimée."
            )
        if sec_settings.get("allow_section_delete") is False or sec_settings.get("allow_structure_delete") is False:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La suppression de la section « {sec.title} » est désactivée par son protocole de gouvernance."
            )

        # Vérification hiérarchique de l'arborescence (éléments directs et indirects)
        if sec.parent_section_id:
            lineage = await cls.get_section_ancestors(db, sec.parent_section_id)
            for ancestor in lineage:
                if ancestor.is_sanctuarized:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"La section parente « {ancestor.title} » ou l'un de ses ancêtres est sanctuarisée. Aucune suppression de sous-section n'est autorisée."
                    )
                anc_settings = ancestor.settings or {}
                if (
                    anc_settings.get("allow_section_delete") is False
                    or anc_settings.get("allow_delete_subsections") is False
                    or anc_settings.get("allow_structure_delete") is False
                ):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"La suppression des sous-sections est désactivée par le protocole de la section « {ancestor.title} » ou de ses ascendants."
                    )

        # Si cette section fait partie ou est une structure dynamique issue d'une ressource
        sec_settings = sec.settings or {}
        if sec_settings.get("resource_projection") or sec.parent_section_id:
            struct_check_id = sec.parent_section_id or sec.id
            lineage_struct = await cls.get_section_ancestors(db, struct_check_id)
            for ancestor in lineage_struct:
                anc_settings = ancestor.settings or {}
                if anc_settings.get("allow_structure_delete") is False:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"La suppression de la structure est désactivée par le protocole de la section « {ancestor.title} » ou de ses ascendants."
                    )

        # Récursion sur sous-sections
        for sub in list(sec.subsections or []):
            await cls.delete_section(db=db, section_id=sub.id)

        # Suppression questions
        for q in list(sec.questions or []):
            await db.delete(q)

        await db.delete(sec)
        await db.commit()
        return True
