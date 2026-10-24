"""
Form Service for SenWiseTool.
Manages recursive Section/Sub-section trees, rich Questions, Universal Conditions,
Formulas, Dynamic Resource Lookups, and Submission Scoring.
"""

from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value
from fastapi import HTTPException, status
import logging

from app.models.project.project import Project
from app.models.project.form import Form, FormStatus
from app.models.project.form_section import FormSection
from app.models.project.form_question import FormQuestion
from app.models.project.form_submission import FormSubmission, SubmissionStatus, ComplianceStatus
from app.models.project.submission_answer import SubmissionAnswer
from app.schemas.project.form import FormCreate, FormUpdate, FormGovernanceUpdate
from app.schemas.project.form_section import FormSectionCreate, FormSectionUpdate
from app.schemas.project.form_question import FormQuestionCreate, FormQuestionUpdate
from app.schemas.project.form_submission import FormSubmissionCreate
from app.services.project.condition_evaluator import UniversalConditionEvaluator
from app.services.project.formula_engine import FormulaEngine
from app.services.project.compliance_scorer import ComplianceScorer

logger = logging.getLogger("app.services.project.form_service")


class FormService:

    @classmethod
    async def get_forms(
        cls,
        db: AsyncSession,
        project_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> List[Form]:
        # Vérification d'accès au projet
        stmt = select(Form).where(Form.project_id == project_id).order_by(Form.order_index)
        res = await db.execute(stmt)
        forms = res.scalars().all()
        return forms

    @classmethod
    async def get_form_by_id(
        cls,
        db: AsyncSession,
        form_id: int,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> Optional[Form]:
        stmt = select(Form).where(Form.id == form_id)
        res = await db.execute(stmt)
        form = res.scalars().first()
        if not form:
            return None

        # Charger toutes les sections du formulaire avec leurs questions associées
        sec_stmt = (
            select(FormSection)
            .where(FormSection.form_id == form_id)
            .options(selectinload(FormSection.questions))
            .order_by(FormSection.order_index)
        )
        sec_res = await db.execute(sec_stmt)
        all_sections = list(sec_res.scalars().all())

        # Construire l'arborescence complète ($N$-aire) et assigner de façon déterministe les sous-sections
        sections_by_parent: Dict[Optional[int], List[FormSection]] = {}
        for s in all_sections:
            set_committed_value(s, "subsections", [])
            sections_by_parent.setdefault(s.parent_section_id, []).append(s)

        for s in all_sections:
            children = sections_by_parent.get(s.id, [])
            set_committed_value(s, "subsections", children)

        root_sections = sections_by_parent.get(None, [])
        set_committed_value(form, "sections", root_sections)

        # Charger les questions racines (hors section)
        q_stmt = (
            select(FormQuestion)
            .where(FormQuestion.form_id == form_id, FormQuestion.section_id.is_(None))
            .order_by(FormQuestion.order_index)
        )
        q_res = await db.execute(q_stmt)
        root_questions = list(q_res.scalars().all())
        set_committed_value(form, "questions", root_questions)

        return form

    # Alias de compatibilité
    get_form_detail = get_form_by_id

    @classmethod
    async def create_form(
        cls,
        db: AsyncSession,
        project_id: int,
        data: FormCreate,
        organization_id: Optional[int],
        user_id: int,
    ) -> Form:
        form = Form(
            project_id=project_id,
            organization_id=organization_id,
            name=data.name,
            code=data.code,
            description=data.description,
            form_type=data.form_type,
            version=data.version or "1.0.0",
            status=FormStatus.DRAFT.value,
            order_index=data.order_index,
            is_mandatory=data.is_mandatory,
            is_repeatable=data.is_repeatable,
            allow_draft_save=data.allow_draft_save,
            offline_capable=data.offline_capable,
            is_sanctuarized=data.is_sanctuarized,
            allow_custom_sections=data.allow_custom_sections,
            scoring_enabled=data.scoring_enabled,
            scoring_scale=data.scoring_scale,
            passing_score_percentage=data.passing_score_percentage,
            critical_fail_threshold=data.critical_fail_threshold,
            resource_binding_config=data.resource_binding_config or {},
            settings=data.settings or {},
            created_by_id=user_id,
        )

        db.add(form)
        await db.flush()

        # Création récursive des sections et questions si fournies
        for sec_data in data.sections:
            await cls._create_section_recursive(db, form.id, None, sec_data)

        await db.commit()
        await db.refresh(form)
        return await cls.get_form_by_id(db, form.id, organization_id)

    @classmethod
    async def _create_section_recursive(
        cls,
        db: AsyncSession,
        form_id: int,
        parent_section_id: Optional[int],
        data: FormSectionCreate,
    ) -> FormSection:
        sec = FormSection(
            form_id=form_id,
            parent_section_id=parent_section_id,
            title=data.title,
            code=data.code,
            description=data.description,
            order_index=data.order_index,
            is_repeatable=data.is_repeatable,
            min_repeats=data.min_repeats,
            max_repeats=data.max_repeats,
            weight=data.weight,
            scoring_category=data.scoring_category,
            is_critical=data.is_critical,
            is_sanctuarized=data.is_sanctuarized,
            allow_custom_questions=data.allow_custom_questions,
            min_passing_score=data.min_passing_score,
            dependency_rules=data.dependency_rules or {},
            settings=data.settings or {},
        )
        db.add(sec)
        await db.flush()

        # Questions de la section
        for q_data in data.questions:
            q = FormQuestion(
                section_id=sec.id,
                code=q_data.code,
                label=q_data.label,
                help_text=q_data.help_text,
                placeholder=q_data.placeholder,
                question_type=q_data.question_type,
                order_index=q_data.order_index,
                is_required=q_data.is_required,
                is_readonly=q_data.is_readonly,
                is_sanctuarized=q_data.is_sanctuarized,
                is_critical=q_data.is_critical,
                weight=q_data.weight,
                scoring_config=q_data.scoring_config or {},
                options=q_data.options or [],
                resource_binding=q_data.resource_binding or {},
                formula_expression=q_data.formula_expression,
                validation_rules=q_data.validation_rules or {},
                dependency_rules=q_data.dependency_rules or {},
                settings=q_data.settings or {},
            )
            db.add(q)

        # Sous-sections récursives ($N$-aires)
        for sub_data in data.subsections:
            await cls._create_section_recursive(db, form_id, sec.id, sub_data)

        await db.flush()
        return sec

    @classmethod
    async def import_tree(
        cls,
        db: AsyncSession,
        form_id: int,
        target_parent_section_id: Optional[int],
        replace_existing: bool,
        sections: List[FormSectionCreate],
    ) -> Form:
        """
        Importe et persiste atomiquement une arborescence récursive de sections ($N$-aires)
        et questions dans le formulaire (à la racine ou greffée sous target_parent_section_id).
        """
        form = await cls.get_form_by_id(db, form_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")

        if form.status == FormStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ce formulaire est publié et verrouillé. Créez une nouvelle version pour modifier sa structure."
            )

        if target_parent_section_id is not None:
            parent_check = (await db.execute(
                select(FormSection).where(FormSection.id == target_parent_section_id, FormSection.form_id == form_id)
            )).scalar_one_or_none()
            if not parent_check:
                raise HTTPException(status_code=404, detail="Section parente cible introuvable dans ce formulaire.")

        if replace_existing:
            await db.execute(delete(FormSection).where(FormSection.form_id == form_id))
            await db.flush()

        base_order = 0
        if not replace_existing:
            max_order_query = select(func.max(FormSection.order_index)).where(FormSection.form_id == form_id)
            if target_parent_section_id is not None:
                max_order_query = max_order_query.where(FormSection.parent_section_id == target_parent_section_id)
            else:
                max_order_query = max_order_query.where(FormSection.parent_section_id.is_(None))
            base_order = (await db.execute(max_order_query)).scalar() or 0

        for idx, sec_data in enumerate(sections):
            if not sec_data.order_index:
                sec_data.order_index = base_order + idx + 1
            await cls._create_section_recursive(db, form_id, target_parent_section_id, sec_data)

        await db.commit()
        return await cls.get_form_by_id(db, form_id)

    @classmethod
    async def update_form(
        cls,
        db: AsyncSession,
        form_id: int,
        data: FormUpdate,
        organization_id: Optional[int],
    ) -> Form:
        form = await cls.get_form_by_id(db, form_id, organization_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")

        update_dict = data.model_dump(exclude_unset=True)

        # Si le formulaire est publié, il est verrouillé contre les modifications de contenu
        if form.status == FormStatus.PUBLISHED.value:
            allowed_published_keys = {"status"}
            disallowed = [k for k in update_dict.keys() if k not in allowed_published_keys]
            if disallowed:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ce modèle de formulaire est publié et verrouillé. Créez une nouvelle version pour modifier son contenu."
                )

        for key, value in update_dict.items():
            setattr(form, key, value)

        await db.commit()
        return await cls.get_form_by_id(db, form_id)

    @classmethod
    async def update_governance(
        cls,
        db: AsyncSession,
        form_id: int,
        data: FormGovernanceUpdate,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> Form:
        """
        Configure les règles de sanctuarisation et droits de personnalisation pour les organisations.
        """
        form = await cls.get_form_by_id(db, form_id, organization_id, is_super_admin)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")

        if form.status == FormStatus.PUBLISHED.value and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ce formulaire est publié et verrouillé. Créez une nouvelle version pour modifier sa gouvernance."
            )

        form.is_sanctuarized = data.is_sanctuarized
        form.allow_custom_sections = data.allow_custom_sections

        # Mise à jour des paramètres de gouvernance avancés
        current_settings = dict(form.settings or {})
        governance_dict = {
            "customization_policy": data.customization_policy,
            "is_sanctuarized": data.is_sanctuarized,
            "allow_custom_sections": data.allow_custom_sections,
            "allow_custom_questions": data.allow_custom_questions,
            "allow_tenant_edit_questions": data.allow_tenant_edit_questions,
            "allow_tenant_delete_questions": data.allow_tenant_delete_questions,
            "allow_tenant_select_requirements": data.allow_tenant_select_requirements,
            "metadata_policy": data.metadata_policy,
            "allow_tenant_add_metadata": data.allow_tenant_add_metadata,
            "allow_tenant_edit_metadata": data.allow_tenant_edit_metadata,
            "allow_tenant_delete_metadata": data.allow_tenant_delete_metadata,
            "enforce_sanctuarized_metadata": data.enforce_sanctuarized_metadata,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        current_settings["governance"] = governance_dict
        form.settings = current_settings

        await db.commit()
        await db.refresh(form)
        return form

    @classmethod
    async def publish_form(
        cls,
        db: AsyncSession,
        form_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> Form:
        """
        Publie un formulaire et le verrouille pour garantir la conformité et l'immuabilité des audits.
        """
        form = await cls.get_form_by_id(db, form_id, organization_id, is_super_admin)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")

        form.status = FormStatus.PUBLISHED.value
        await db.commit()
        await db.refresh(form)
        return form

    @classmethod
    async def create_new_form_version(
        cls,
        db: AsyncSession,
        form_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
        user_id: Optional[int] = None,
    ) -> Form:
        """
        Crée une nouvelle version en brouillon d'un formulaire publié pour des évolutions sans casser l'existant.
        """
        form = await cls.get_form_by_id(db, form_id, organization_id, is_super_admin)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")

        current_v = form.version or "1.0.0"
        parts = current_v.split(".")
        if len(parts) == 3 and parts[1].isdigit():
            new_v = f"{parts[0]}.{int(parts[1]) + 1}.0"
        else:
            new_v = f"{current_v}.1"

        new_code = f"{form.code}_V{new_v.replace('.', '_')}"
        new_form = Form(
            project_id=form.project_id,
            organization_id=form.organization_id,
            name=form.name,
            code=new_code,
            description=form.description,
            form_type=form.form_type,
            version=new_v,
            status=FormStatus.DRAFT.value,
            order_index=form.order_index + 1,
            is_template=form.is_template,
            is_sanctuarized=form.is_sanctuarized,
            allow_custom_sections=form.allow_custom_sections,
            is_mandatory=form.is_mandatory,
            is_repeatable=form.is_repeatable,
            allow_draft_save=form.allow_draft_save,
            offline_capable=form.offline_capable,
            scoring_enabled=form.scoring_enabled,
            scoring_scale=form.scoring_scale,
            passing_score_percentage=form.passing_score_percentage,
            critical_fail_threshold=form.critical_fail_threshold,
            resource_binding_config=form.resource_binding_config or {},
            settings=form.settings or {},
            created_by_id=user_id or form.created_by_id,
        )
        db.add(new_form)
        await db.flush()

        for sec in form.sections:
            await cls._clone_section_recursive(db, sec, new_form.id, None)

        await db.commit()
        await db.refresh(new_form)
        return await cls.get_form_by_id(db, new_form.id, organization_id, is_super_admin)

    @classmethod
    async def _clone_section_recursive(
        cls,
        db: AsyncSession,
        source_sec: FormSection,
        new_form_id: int,
        new_parent_id: Optional[int],
    ) -> FormSection:
        new_sec = FormSection(
            form_id=new_form_id,
            parent_section_id=new_parent_id,
            title=source_sec.title,
            code=source_sec.code,
            description=source_sec.description,
            order_index=source_sec.order_index,
            is_repeatable=source_sec.is_repeatable,
            min_repeats=source_sec.min_repeats,
            max_repeats=source_sec.max_repeats,
            weight=source_sec.weight,
            scoring_category=source_sec.scoring_category,
            is_critical=source_sec.is_critical,
            is_sanctuarized=source_sec.is_sanctuarized,
            allow_custom_questions=source_sec.allow_custom_questions,
            min_passing_score=source_sec.min_passing_score,
            dependency_rules=source_sec.dependency_rules or {},
            settings=source_sec.settings or {},
        )
        db.add(new_sec)
        await db.flush()

        for q in source_sec.questions:
            new_q = FormQuestion(
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
                scoring_config=q.scoring_config or {},
                options=q.options or [],
                resource_binding=q.resource_binding or {},
                formula_expression=q.formula_expression,
                validation_rules=q.validation_rules or {},
                dependency_rules=q.dependency_rules or {},
                settings=q.settings or {},
            )
            db.add(new_q)

        for sub in getattr(source_sec, "subsections", []):
            await cls._clone_section_recursive(db, sub, new_form_id, new_sec.id)

        await db.flush()
        return new_sec

    @classmethod
    async def delete_form(
        cls,
        db: AsyncSession,
        form_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> bool:
        form = await cls.get_form_by_id(db, form_id, organization_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")

        if form.status == FormStatus.PUBLISHED.value and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Un modèle de formulaire publié et verrouillé ne peut pas être supprimé."
            )

        if form.is_sanctuarized and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Ce formulaire est un modèle sanctuarisé par le Super Administrateur et ne peut être supprimé."
            )

        await db.delete(form)
        await db.commit()
        return True

    # -------------------------------------------------------------
    # GESTION DES SECTIONS
    # -------------------------------------------------------------
    @classmethod
    async def add_section(
        cls,
        db: AsyncSession,
        form_id: int,
        data: FormSectionCreate,
        is_super_admin: bool = False,
    ) -> FormSection:
        form_stmt = select(Form).where(Form.id == form_id)
        form = (await db.execute(form_stmt)).scalars().first()
        if form and form.status == FormStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ce formulaire est publié et verrouillé. Ses sections ne peuvent plus être modifiées."
            )
        if form and not form.allow_custom_sections and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Le modèle officiel interdit l'ajout de sections personnalisées dans ce formulaire certifié."
            )

        sec = FormSection(
            form_id=form_id,
            parent_section_id=data.parent_section_id,
            title=data.title,
            code=data.code,
            description=data.description,
            order_index=data.order_index,
            is_repeatable=data.is_repeatable,
            min_repeats=data.min_repeats,
            max_repeats=data.max_repeats,
            weight=data.weight,
            scoring_category=data.scoring_category,
            is_critical=data.is_critical,
            is_sanctuarized=data.is_sanctuarized,
            allow_custom_questions=data.allow_custom_questions,
            min_passing_score=data.min_passing_score,
            dependency_rules=data.dependency_rules or {},
            settings=data.settings or {},
        )
        db.add(sec)
        await db.commit()
        await db.refresh(sec)
        set_committed_value(sec, "questions", [])
        set_committed_value(sec, "subsections", [])
        return sec

    @classmethod
    async def update_section(
        cls,
        db: AsyncSession,
        section_id: int,
        data: FormSectionUpdate,
    ) -> FormSection:
        stmt = select(FormSection).where(FormSection.id == section_id)
        res = await db.execute(stmt)
        sec = res.scalars().first()
        if not sec:
            raise HTTPException(status_code=404, detail="Section introuvable.")

        form_stmt = select(Form).where(Form.id == sec.form_id)
        parent_form = (await db.execute(form_stmt)).scalars().first()
        if parent_form and parent_form.status == FormStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ce formulaire est publié et verrouillé. Ses sections ne peuvent plus être modifiées."
            )

        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(sec, key, value)

        await db.commit()
        await db.refresh(sec)
        sec_sub_stmt = (
            select(FormSection)
            .where(FormSection.id == section_id)
            .options(
                selectinload(FormSection.questions),
                selectinload(FormSection.subsections),
            )
        )
        loaded_sec = (await db.execute(sec_sub_stmt)).scalars().first()
        if loaded_sec:
            for sub in (loaded_sec.subsections or []):
                set_committed_value(sub, "questions", [])
                set_committed_value(sub, "subsections", [])
            return loaded_sec
        set_committed_value(sec, "questions", [])
        set_committed_value(sec, "subsections", [])
        return sec

    @classmethod
    async def delete_section(cls, db: AsyncSession, section_id: int, is_super_admin: bool = False) -> bool:
        stmt = (
            select(FormSection)
            .where(FormSection.id == section_id)
            .options(
                selectinload(FormSection.subsections),
                selectinload(FormSection.questions),
            )
        )
        res = await db.execute(stmt)
        sec = res.scalars().first()
        if not sec:
            raise HTTPException(status_code=404, detail="Section introuvable.")

        form_stmt = select(Form).where(Form.id == sec.form_id)
        parent_form = (await db.execute(form_stmt)).scalars().first()
        if parent_form and parent_form.status == FormStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ce formulaire est publié et verrouillé. Ses sections ne peuvent plus être modifiées."
            )

        if sec.is_sanctuarized and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cette section est sanctuarisée par le modèle officiel certifié et ne peut être supprimée."
            )

        # Suppression récursive propre de toutes les sous-sections et questions
        for sub in list(sec.subsections or []):
            await cls.delete_section(db=db, section_id=sub.id, is_super_admin=True)

        for q in list(sec.questions or []):
            # Supprimer les réponses associées s'il y en a
            q_ans_stmt = select(SubmissionAnswer).where(SubmissionAnswer.question_id == q.id)
            q_ans = (await db.execute(q_ans_stmt)).scalars().all()
            for a in q_ans:
                await db.delete(a)
            await db.delete(q)

        await db.delete(sec)
        await db.commit()
        return True

    # -------------------------------------------------------------
    # GESTION DES QUESTIONS
    # -------------------------------------------------------------
    @classmethod
    async def add_question(
        cls,
        db: AsyncSession,
        section_id: Optional[int],
        data: FormQuestionCreate,
        form_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> FormQuestion:
        resolved_form_id = form_id or data.form_id
        sec = None

        if section_id is not None:
            sec_stmt = select(FormSection).where(FormSection.id == section_id)
            sec = (await db.execute(sec_stmt)).scalars().first()
            if not sec:
                raise HTTPException(status_code=404, detail="Section introuvable.")
            resolved_form_id = sec.form_id
        elif resolved_form_id is None:
            raise HTTPException(status_code=400, detail="Soit section_id soit form_id doit être fourni pour créer une question.")

        form_stmt = select(Form).where(Form.id == resolved_form_id)
        parent_form = (await db.execute(form_stmt)).scalars().first()
        if parent_form and parent_form.status == FormStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ce formulaire est publié et verrouillé. Ses questions ne peuvent plus être modifiées."
            )

        if sec and not sec.allow_custom_questions and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Le modèle officiel interdit l'ajout de questions personnalisées dans cette section certifiée."
            )

        q = FormQuestion(
            form_id=resolved_form_id,
            section_id=section_id,
            code=data.code,
            label=data.label,
            help_text=data.help_text,
            placeholder=data.placeholder,
            question_type=data.question_type,
            order_index=data.order_index,
            is_required=data.is_required,
            is_readonly=data.is_readonly,
            is_sanctuarized=data.is_sanctuarized,
            is_critical=data.is_critical,
            weight=data.weight,
            scoring_config=data.scoring_config or {},
            options=data.options or [],
            resource_binding=data.resource_binding or {},
            formula_expression=data.formula_expression,
            validation_rules=data.validation_rules or {},
            dependency_rules=data.dependency_rules or {},
            settings=data.settings or {},
        )
        db.add(q)
        await db.commit()
        await db.refresh(q)
        return q

    @classmethod
    async def update_question(
        cls,
        db: AsyncSession,
        question_id: int,
        data: FormQuestionUpdate,
        is_super_admin: bool = False,
    ) -> FormQuestion:
        stmt = select(FormQuestion).where(FormQuestion.id == question_id)
        res = await db.execute(stmt)
        q = res.scalars().first()
        if not q:
            raise HTTPException(status_code=404, detail="Question introuvable.")

        sec_stmt = select(FormSection).where(FormSection.id == q.section_id)
        sec = (await db.execute(sec_stmt)).scalars().first()
        if sec:
            form_stmt = select(Form).where(Form.id == sec.form_id)
            parent_form = (await db.execute(form_stmt)).scalars().first()
            if parent_form and parent_form.status == FormStatus.PUBLISHED.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ce formulaire est publié et verrouillé. Ses questions ne peuvent plus être modifiées."
                )

        if q.is_sanctuarized and not is_super_admin:
            # Vérifier qu'on ne réduit pas la criticité ou le poids imposé
            if data.is_critical is not None and not data.is_critical and q.is_critical:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Impossible de désactiver le critère critique sur une question sanctuarisée par le modèle."
                )

        for key, value in data.model_dump(exclude_unset=True).items():
            setattr(q, key, value)

        await db.commit()
        await db.refresh(q)
        return q

    @classmethod
    async def delete_question(cls, db: AsyncSession, question_id: int, is_super_admin: bool = False) -> bool:
        stmt = (
            select(FormQuestion)
            .where(FormQuestion.id == question_id)
            .options(selectinload(FormQuestion.answers))
        )
        res = await db.execute(stmt)
        q = res.scalars().first()
        if not q:
            raise HTTPException(status_code=404, detail="Question introuvable.")

        sec_stmt = select(FormSection).where(FormSection.id == q.section_id)
        sec = (await db.execute(sec_stmt)).scalars().first()
        if sec:
            form_stmt = select(Form).where(Form.id == sec.form_id)
            parent_form = (await db.execute(form_stmt)).scalars().first()
            if parent_form and parent_form.status == FormStatus.PUBLISHED.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ce formulaire est publié et verrouillé. Ses questions ne peuvent plus être modifiées."
                )

        if q.is_sanctuarized and not is_super_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Cette question est un critère réglementaire sanctuarisé par le modèle officiel et ne peut être supprimée."
            )

        # Supprimer les réponses associées s'il y en a
        q_ans_stmt = select(SubmissionAnswer).where(SubmissionAnswer.question_id == q.id)
        q_ans = (await db.execute(q_ans_stmt)).scalars().all()
        for a in q_ans:
            await db.delete(a)

        await db.delete(q)
        await db.commit()
        return True

    # -------------------------------------------------------------
    # SOUMISSION ET ÉVALUATION DU FORMULAIRE
    # -------------------------------------------------------------
    @classmethod
    async def submit_form(
        cls,
        db: AsyncSession,
        form_id: int,
        payload: FormSubmissionCreate,
        organization_id: Optional[int],
        user_id: int,
    ) -> FormSubmission:
        form = await cls.get_form_by_id(db, form_id, organization_id)
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")

        # Indexer les réponses brutes par code
        answers_dict: Dict[str, Dict[str, Any]] = {}
        answers_by_code: Dict[str, Any] = {}
        for ans in payload.answers:
            answers_dict[ans.question_code] = {
                "value": ans.value,
                "raw_text": ans.raw_text or str(ans.value or ""),
                "compliance_choice": ans.compliance_choice,
                "is_applicable": ans.is_applicable,
                "evidence_notes": ans.evidence_notes,
                "evidence_media_urls": ans.evidence_media_urls or [],
            }
            answers_by_code[ans.question_code] = ans.value

        # Récupérer l'ensemble des questions du formulaire
        all_questions = []
        for sec in form.sections:
            all_questions.extend(sec.questions or [])
            for sub in (sec.subsections or []):
                all_questions.extend(sub.questions or [])

        # 1. Évaluation des formules de calcul
        for q in all_questions:
            if q.question_type == "formula_calculated" and q.formula_expression:
                calc_result = FormulaEngine.evaluate(q.formula_expression, answers_by_code)
                if calc_result is not None:
                    answers_by_code[q.code] = calc_result
                    if q.code in answers_dict:
                        answers_dict[q.code]["value"] = calc_result
                    else:
                        answers_dict[q.code] = {
                            "value": calc_result,
                            "raw_text": str(calc_result),
                            "compliance_choice": "COMPLIANT",
                            "is_applicable": True,
                            "evidence_notes": f"Calcul automatique : {q.formula_expression}",
                            "evidence_media_urls": [],
                        }

        # 2. Évaluation des conditions & dépendances universelles
        section_metrics_by_code: Dict[str, Dict[str, Any]] = {}
        for sec in form.sections:
            # Évaluation de la condition de la section
            sec_dep_eval = UniversalConditionEvaluator.evaluate_node(
                sec.dependency_rules, answers_by_code, section_metrics_by_code
            )
            section_metrics_by_code[sec.code] = {
                "score": 100.0,
                "is_visible": sec_dep_eval["is_visible"],
                "status": "PENDING"
            }

            for q in (sec.questions or []):
                q_dep_eval = UniversalConditionEvaluator.evaluate_node(
                    q.dependency_rules, answers_by_code, section_metrics_by_code
                )
                if not q_dep_eval["is_applicable"] or not q_dep_eval["is_visible"]:
                    if q.code in answers_dict:
                        answers_dict[q.code]["is_applicable"] = False

        # 3. Évaluation du scoring de conformité multi-niveaux
        eval_result = ComplianceScorer.evaluate_submission(
            form.sections,
            answers_dict,
            passing_threshold_pct=float(form.passing_score_percentage or 80.0),
            critical_threshold=int(form.critical_fail_threshold or 1),
        )

        submission_code = f"SUB-{form.code.upper()}-{uuid.uuid4().hex[:8].upper()}"

        submission = FormSubmission(
            form_id=form.id,
            organization_id=organization_id,
            submission_code=submission_code,
            submitted_by_id=user_id,
            status=SubmissionStatus.SUBMITTED.value,
            total_score=eval_result["overall_score"],
            compliance_status=eval_result["compliance_status"],
            critical_failures_count=eval_result["critical_failures_count"],
            section_scores=eval_result["section_scores"],
            geolocation=payload.geolocation,
            offline_created_at=payload.offline_created_at,
            metadata_info={
                **payload.metadata_info,
                "category_scores": eval_result["category_scores"],
                "status_reason": eval_result["status_reason"],
            },
        )
        db.add(submission)
        await db.flush()

        # Enregistrement des réponses avec leur statut
        for q in all_questions:
            ans_info = answers_dict.get(q.code)
            if not ans_info:
                continue

            sec_code = q.section.code if q.section else "DEFAULT"
            ans_record = SubmissionAnswer(
                submission_id=submission.id,
                question_id=q.id,
                question_code=q.code,
                section_code=sec_code,
                value=ans_info["value"],
                raw_text=ans_info.get("raw_text"),
                compliance_choice=ans_info.get("compliance_choice"),
                is_compliant=(ans_info.get("compliance_choice") == "COMPLIANT"),
                is_critical_failure=(ans_info.get("compliance_choice") == "NON_COMPLIANT" and q.is_critical),
                is_applicable=ans_info.get("is_applicable", True),
                evidence_notes=ans_info.get("evidence_notes"),
                evidence_media_urls=ans_info.get("evidence_media_urls", []),
            )
            db.add(ans_record)

        # Mettre à jour les métriques du formulaire
        form.metrics = {
            **form.metrics,
            "submissions_count": (form.metrics.get("submissions_count", 0) + 1),
            "last_submitted_at": datetime.now(timezone.utc).isoformat(),
        }

        await db.commit()
        await db.refresh(submission)
        return submission

    @classmethod
    async def get_submissions(
        cls,
        db: AsyncSession,
        form_id: int,
        organization_id: Optional[int],
        limit: int = 50,
    ) -> List[FormSubmission]:
        stmt = (
            select(FormSubmission)
            .where(FormSubmission.form_id == form_id)
            .order_by(FormSubmission.created_at.desc())
            .limit(limit)
        )
        res = await db.execute(stmt)
        return res.scalars().all()

    @classmethod
    async def get_submission_by_id(
        cls,
        db: AsyncSession,
        submission_id: int,
    ) -> Optional[FormSubmission]:
        stmt = (
            select(FormSubmission)
            .options(selectinload(FormSubmission.answers))
            .where(FormSubmission.id == submission_id)
        )
        res = await db.execute(stmt)
        return res.scalars().first()
