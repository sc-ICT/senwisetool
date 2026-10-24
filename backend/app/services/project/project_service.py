"""
Project Service for SenWiseTool.
Ensures strict multi-tenant isolation, template instantiation and lifecycle governance.
"""

from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status
import logging

from app.models.project.project import Project, ProjectStatus
from app.models.project.form import Form
from app.models.project.form_section import FormSection
from app.models.project.form_question import FormQuestion
from app.models.project.form_submission import FormSubmission
from app.schemas.project.project import ProjectCreate, ProjectUpdate

logger = logging.getLogger("app.services.project.project_service")


class ProjectService:

    @classmethod
    async def get_projects(
        cls,
        db: AsyncSession,
        organization_id: Optional[int],
        is_super_admin: bool = False,
        is_template: Optional[bool] = None,
        status_filter: Optional[str] = None,
        category_filter: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Project]:
        """
        Récupère les projets ou modèles de projets selon le contexte.
        - Super Admin : peut lister les modèles (is_template=True) ou tous les projets.
        - Organisations : listent leurs projets opérationnels ou templates partagés.
        """
        stmt = select(Project)

        if is_template is not None:
            stmt = stmt.where(Project.is_template == is_template)

        if is_super_admin and organization_id is None:
            # Le Super Admin voit tous les projets ou modèles
            pass
        elif organization_id:
            # Tenant isolation : projets de l'org OU templates partagés OU templates privés candidats
            stmt = stmt.where(
                or_(
                    Project.organization_id == organization_id,
                    Project.is_shared == True,
                    Project.is_template == True,
                )
            )
        else:
            stmt = stmt.where(Project.is_shared == True)

        if status_filter:
            stmt = stmt.where(Project.status == status_filter)

        if category_filter:
            stmt = stmt.where(Project.category == category_filter)

        if search:
            search_fmt = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Project.name.ilike(search_fmt),
                    Project.code.ilike(search_fmt),
                    Project.description.ilike(search_fmt),
                )
            )

        stmt = stmt.order_by(Project.created_at.desc()).offset(skip).limit(limit)
        result = await db.execute(stmt)
        projects = result.scalars().all()

        # Filtrage tenant pour les modèles privés : autorisé si is_shared OU organization_id dans authorized_organization_ids
        if organization_id and not is_super_admin:
            filtered_projects = []
            for p in projects:
                if p.organization_id == organization_id or p.is_shared:
                    filtered_projects.append(p)
                elif p.is_template:
                    auth_orgs = (p.settings or {}).get("authorized_organization_ids", [])
                    if str(organization_id) in [str(x) for x in auth_orgs]:
                        filtered_projects.append(p)
            projects = filtered_projects

        # Rafraîchir les métriques dynamiques
        for proj in projects:
            await cls._refresh_project_metrics(db, proj)

        return projects

    @classmethod
    async def get_project_by_id(
        cls,
        db: AsyncSession,
        project_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> Optional[Project]:
        stmt = select(Project).where(Project.id == project_id)
        result = await db.execute(stmt)
        project = result.scalars().first()

        if not project:
            return None

        # Contrôle strict d'isolation multi-tenant
        if not is_super_admin and project.organization_id != organization_id and not project.is_shared:
            auth_orgs = (project.settings or {}).get("authorized_organization_ids", [])
            if not (project.is_template and str(organization_id) in [str(x) for x in auth_orgs]):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Accès refusé : ce projet appartient à une autre organisation souveraine."
                )

        await cls._refresh_project_metrics(db, project)
        return project

    @classmethod
    async def create_project(
        cls,
        db: AsyncSession,
        data: ProjectCreate,
        organization_id: Optional[int],
        user_id: int,
        is_super_admin: bool = False,
    ) -> Project:
        # Vérification d'unicité du code dans l'organisation
        chk_stmt = select(Project).where(
            and_(
                Project.code == data.code,
                Project.organization_id == organization_id
            )
        )
        res = await db.execute(chk_stmt)
        if res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Un projet avec le code technique '{data.code}' existe déjà dans cet espace."
            )

        init_status = ProjectStatus.DRAFT.value if (data.is_template or is_super_admin) else (data.status or ProjectStatus.ACTIVE.value)
        project = Project(
            organization_id=organization_id,
            plugin_id=data.plugin_id,
            plugin_code=data.plugin_code,
            scope=data.scope if is_super_admin else "tenant",
            is_shared=data.is_shared if is_super_admin else False,
            is_immutable=data.is_immutable if is_super_admin else False,
            immutable_fields=data.immutable_fields if is_super_admin else [],
            parent_template_id=data.parent_template_id,
            is_template=data.is_template if data.is_template is not None else (is_super_admin),
            template_governance=data.template_governance if data.template_governance else {
                "allow_tenant_add_sections": True,
                "allow_tenant_add_questions": True,
                "allow_tenant_modify_threshold": False,
                "allow_tenant_add_forms": True,
                "enforce_critical_gates": True,
                "sanctuarized_sections": [],
                "sanctuarized_questions": []
            },
            name=data.name,
            code=data.code,
            category=data.category,
            project_type=data.project_type,
            status=init_status,
            version=data.version or "1.0.0",
            icon=data.icon,
            description=data.description,
            objectives=data.objectives,
            start_date=data.start_date,
            end_date=data.end_date,
            budget=data.budget,
            currency=data.currency,
            targets=data.targets or {},
            settings=data.settings or {},
            metrics={
                "forms_count": 0,
                "submissions_count": 0,
                "avg_compliance_score": 0.0,
                "critical_issues_count": 0
            },
            created_by_id=user_id,
        )

        db.add(project)
        await db.commit()
        await db.refresh(project)
        return project

    @classmethod
    async def update_project(
        cls,
        db: AsyncSession,
        project_id: int,
        data: ProjectUpdate,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> Project:
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        update_dict = data.model_dump(exclude_unset=True)

        # Si le projet est déjà publié, il est verrouillé contre les modifications de structure
        if project.status == ProjectStatus.PUBLISHED.value:
            allowed_published_keys = {"status"}
            disallowed = [k for k in update_dict.keys() if k not in allowed_published_keys]
            if disallowed:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Ce modèle de projet est publié et verrouillé. Créez une nouvelle version pour modifier son contenu."
                )

        # Respect de l'immutabilité pour les organisations locataires
        if not is_super_admin and project.is_immutable:
            locked_fields = project.immutable_fields or []
            for field in locked_fields:
                if field in update_dict:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=f"Le champ '{field}' est sanctuarisé par le Super Admin et ne peut être modifié."
                    )

        for key, value in update_dict.items():
            setattr(project, key, value)

        await db.commit()
        await db.refresh(project)
        await cls._refresh_project_metrics(db, project)
        return project

    @classmethod
    async def publish_project(
        cls,
        db: AsyncSession,
        project_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> Project:
        """
        Publie un modèle de projet en vérifiant strictement que tous ses formulaires associés
        sont préalablement publiés (aucun en brouillon).
        """
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        # Règle de cohérence stricte : vérifier tous les formulaires du projet
        forms_stmt = select(Form).where(Form.project_id == project_id)
        forms = (await db.execute(forms_stmt)).scalars().all()

        if len(forms) == 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Impossible de publier un modèle de projet sans formulaire. Veuillez créer et publier au moins un modèle de formulaire avant de publier le projet."
            )

        draft_forms = [f.name for f in forms if f.status != "published"]
        if draft_forms:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Impossible de publier ce modèle de projet : tous les formulaires associés doivent être préalablement publiés. Formulaire(s) encore au brouillon : {', '.join(draft_forms)}."
            )

        project.status = ProjectStatus.PUBLISHED.value
        project.is_immutable = True
        await db.commit()
        await db.refresh(project)
        return project

    @classmethod
    async def create_new_project_version(
        cls,
        db: AsyncSession,
        project_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
        user_id: Optional[int] = None,
    ) -> Project:
        """
        Crée une nouvelle version en brouillon d'un modèle de projet publié pour des évolutions non-bloquantes.
        """
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        current_v = getattr(project, "version", "1.0.0") or "1.0.0"
        parts = current_v.split(".")
        if len(parts) == 3 and parts[1].isdigit():
            new_v = f"{parts[0]}.{int(parts[1]) + 1}.0"
        else:
            new_v = f"{current_v}.1"

        new_code = f"{project.code}_V{new_v.replace('.', '_')}"
        new_project = Project(
            organization_id=project.organization_id,
            plugin_id=project.plugin_id,
            plugin_code=project.plugin_code,
            is_template=project.is_template,
            scope=project.scope,
            is_shared=project.is_shared,
            is_immutable=False,
            immutable_fields=project.immutable_fields or [],
            parent_template_id=project.id,
            template_governance=project.template_governance or {},
            name=project.name,
            code=new_code,
            category=project.category,
            project_type=project.project_type,
            status=ProjectStatus.DRAFT.value,
            version=new_v,
            icon=project.icon,
            description=project.description,
            objectives=project.objectives,
            targets=project.targets or {},
            settings=project.settings or {},
            metrics={
                "forms_count": 0,
                "submissions_count": 0,
                "avg_compliance_score": 0.0,
                "critical_issues_count": 0,
            },
            created_by_id=user_id or project.created_by_id,
        )
        db.add(new_project)
        await db.flush()

        from app.services.project.form_service import FormService
        forms_stmt = select(Form).where(Form.project_id == project_id)
        forms = (await db.execute(forms_stmt)).scalars().all()
        for f in forms:
            full_form = await FormService.get_form_by_id(db, f.id, organization_id, is_super_admin)
            if full_form:
                clone_f = Form(
                    project_id=new_project.id,
                    organization_id=full_form.organization_id,
                    name=full_form.name,
                    code=full_form.code,
                    description=full_form.description,
                    form_type=full_form.form_type,
                    version=full_form.version,
                    status="draft",
                    order_index=full_form.order_index,
                    is_template=full_form.is_template,
                    is_sanctuarized=full_form.is_sanctuarized,
                    allow_custom_sections=full_form.allow_custom_sections,
                    is_mandatory=full_form.is_mandatory,
                    is_repeatable=full_form.is_repeatable,
                    allow_draft_save=full_form.allow_draft_save,
                    offline_capable=full_form.offline_capable,
                    scoring_enabled=full_form.scoring_enabled,
                    scoring_scale=full_form.scoring_scale,
                    passing_score_percentage=full_form.passing_score_percentage,
                    critical_fail_threshold=full_form.critical_fail_threshold,
                    resource_binding_config=full_form.resource_binding_config or {},
                    settings=full_form.settings or {},
                    created_by_id=user_id or full_form.created_by_id,
                )
                db.add(clone_f)
                await db.flush()
                for sec in full_form.sections:
                    await FormService._clone_section_recursive(db, sec, clone_f.id, None)

        await db.commit()
        await db.refresh(new_project)
        await cls._refresh_project_metrics(db, new_project)
        return new_project

    @classmethod
    async def delete_project(
        cls,
        db: AsyncSession,
        project_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> bool:
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        if not is_super_admin and project.is_immutable:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Impossible de supprimer un projet verrouillé par un plugin certifié."
            )

        await db.delete(project)
        await db.commit()
        return True

    @classmethod
    async def _refresh_project_metrics(cls, db: AsyncSession, project: Project) -> None:
        """
        Recalcule à chaud les indicateurs de synthèse du projet.
        """
        forms_stmt = select(func.count(Form.id)).where(Form.project_id == project.id)
        forms_count = (await db.execute(forms_stmt)).scalar() or 0

        subs_stmt = select(
            func.count(FormSubmission.id),
            func.avg(FormSubmission.total_score),
            func.sum(FormSubmission.critical_failures_count)
        ).select_from(FormSubmission).join(Form).where(Form.project_id == project.id)

        subs_res = (await db.execute(subs_stmt)).first()
        subs_count = subs_res[0] or 0
        avg_score = round(float(subs_res[1]), 2) if subs_res[1] is not None else 0.0
        critical_count = int(subs_res[2]) if subs_res[2] is not None else 0

        project.metrics = {
            "forms_count": forms_count,
            "submissions_count": subs_count,
            "avg_compliance_score": avg_score,
            "critical_issues_count": critical_count,
        }

    @classmethod
    async def instantiate_project_from_template(
        cls,
        db: AsyncSession,
        template_id: int,
        organization_id: int,
        user_id: int,
        name_override: Optional[str] = None,
        code_override: Optional[str] = None,
    ) -> Project:
        """
        Instancie un projet opérationnel pour une organisation à partir d'un modèle (blueprint) certifié.
        Clone récursivement :
        - Le projet et ses gouvernances
        - Tous les formulaires modèles
        - Toutes les sections et sous-sections récursives avec sanctuarisation
        - Toutes les questions avec règles de dépendances et formules
        """
        stmt = (
            select(Project)
            .options(
                selectinload(Project.forms)
                .selectinload(Form.sections)
                .selectinload(FormSection.questions),
                selectinload(Project.forms)
                .selectinload(Form.sections)
                .selectinload(FormSection.subsections)
                .selectinload(FormSection.questions),
            )
            .where(Project.id == template_id)
        )
        res = await db.execute(stmt)
        template = res.scalars().first()
        if not template:
            raise HTTPException(status_code=404, detail="Modèle de projet introuvable.")

        # Vérifier code unique pour l'organisation
        target_code = code_override or f"{template.code}_ORG{organization_id}"
        chk_stmt = select(Project).where(
            and_(
                Project.code == target_code,
                Project.organization_id == organization_id
            )
        )
        if (await db.execute(chk_stmt)).scalars().first():
            import uuid
            target_code = f"{template.code}_{uuid.uuid4().hex[:6].upper()}"

        # 1. Cloner le Projet
        instantiated_project = Project(
            organization_id=organization_id,
            plugin_id=template.plugin_id,
            plugin_code=template.plugin_code,
            is_template=False,  # C'est une instance opérationnelle
            scope="tenant",
            is_shared=False,
            is_immutable=template.is_immutable,
            immutable_fields=template.immutable_fields or [],
            parent_template_id=template.id,
            template_governance=template.template_governance or {},
            name=name_override or template.name,
            code=target_code,
            category=template.category,
            project_type=template.project_type,
            status=ProjectStatus.ACTIVE.value,
            icon=template.icon,
            description=template.description,
            objectives=template.objectives,
            budget=template.budget,
            currency=template.currency,
            targets=template.targets or {},
            settings=template.settings or {},
            metrics={
                "forms_count": len(template.forms or []),
                "submissions_count": 0,
                "avg_compliance_score": 0.0,
                "critical_issues_count": 0
            },
            created_by_id=user_id,
        )
        db.add(instantiated_project)
        await db.flush()

        # 2. Cloner les Formulaires
        for form_tpl in (template.forms or []):
            new_form = Form(
                project_id=instantiated_project.id,
                organization_id=organization_id,
                name=form_tpl.name,
                code=form_tpl.code,
                description=form_tpl.description,
                form_type=form_tpl.form_type,
                version=form_tpl.version,
                status=form_tpl.status,
                order_index=form_tpl.order_index,
                is_template=False,
                is_sanctuarized=form_tpl.is_sanctuarized,
                allow_custom_sections=form_tpl.allow_custom_sections,
                is_mandatory=form_tpl.is_mandatory,
                is_repeatable=form_tpl.is_repeatable,
                allow_draft_save=form_tpl.allow_draft_save,
                offline_capable=form_tpl.offline_capable,
                scoring_enabled=form_tpl.scoring_enabled,
                scoring_scale=form_tpl.scoring_scale,
                passing_score_percentage=form_tpl.passing_score_percentage,
                critical_fail_threshold=form_tpl.critical_fail_threshold,
                resource_binding_config=form_tpl.resource_binding_config or {},
                settings=form_tpl.settings or {},
                created_by_id=user_id,
            )
            db.add(new_form)
            await db.flush()

            # 3. Cloner les Sections et Questions récursivement
            for sec_tpl in (form_tpl.sections or []):
                if sec_tpl.parent_section_id is None:
                    await cls._clone_section_recursive(db, new_form.id, None, sec_tpl)

        await db.commit()
        await db.refresh(instantiated_project)
        return instantiated_project

    @classmethod
    async def _clone_section_recursive(
        cls,
        db: AsyncSession,
        form_id: int,
        parent_section_id: Optional[int],
        sec_tpl: FormSection,
    ) -> FormSection:
        new_sec = FormSection(
            form_id=form_id,
            parent_section_id=parent_section_id,
            title=sec_tpl.title,
            code=sec_tpl.code,
            description=sec_tpl.description,
            order_index=sec_tpl.order_index,
            is_repeatable=sec_tpl.is_repeatable,
            min_repeats=sec_tpl.min_repeats,
            max_repeats=sec_tpl.max_repeats,
            is_sanctuarized=sec_tpl.is_sanctuarized,
            allow_custom_questions=sec_tpl.allow_custom_questions,
            weight=sec_tpl.weight,
            scoring_category=sec_tpl.scoring_category,
            is_critical=sec_tpl.is_critical,
            min_passing_score=sec_tpl.min_passing_score,
            dependency_rules=sec_tpl.dependency_rules or {},
            settings=sec_tpl.settings or {},
        )
        db.add(new_sec)
        await db.flush()

        # Questions
        for q_tpl in (sec_tpl.questions or []):
            new_q = FormQuestion(
                section_id=new_sec.id,
                code=q_tpl.code,
                label=q_tpl.label,
                help_text=q_tpl.help_text,
                placeholder=q_tpl.placeholder,
                question_type=q_tpl.question_type,
                order_index=q_tpl.order_index,
                is_required=q_tpl.is_required,
                is_readonly=q_tpl.is_readonly,
                is_sanctuarized=q_tpl.is_sanctuarized,
                is_critical=q_tpl.is_critical,
                weight=q_tpl.weight,
                scoring_config=q_tpl.scoring_config or {},
                options=q_tpl.options or [],
                resource_binding=q_tpl.resource_binding or {},
                formula_expression=q_tpl.formula_expression,
                validation_rules=q_tpl.validation_rules or {},
                dependency_rules=q_tpl.dependency_rules or {},
                settings=q_tpl.settings or {},
            )
            db.add(new_q)

        # Sous-sections récursives
        for sub_tpl in (sec_tpl.subsections or []):
            await cls._clone_section_recursive(db, form_id, new_sec.id, sub_tpl)

        await db.flush()
        return new_sec

    @classmethod
    async def export_project_blueprint(
        cls,
        db: AsyncSession,
        project_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        Exporte la structure complète d'un modèle de projet (blueprint) en JSON autonome :
        Projet, Gouvernance, Formulaires, Arborescence N-aire des sections, Questions,
        Formules, Règles de dépendances, Paramètres de scoring et Sanctuarisation.
        """
        from datetime import datetime, timezone

        stmt = (
            select(Project)
            .options(
                selectinload(Project.forms)
                .selectinload(Form.sections)
                .selectinload(FormSection.questions),
                selectinload(Project.forms)
                .selectinload(Form.sections)
                .selectinload(FormSection.subsections)
                .selectinload(FormSection.questions),
            )
            .where(Project.id == project_id)
        )
        res = await db.execute(stmt)
        project = res.scalars().first()
        if not project:
            raise HTTPException(status_code=404, detail="Projet ou modèle introuvable.")

        if not is_super_admin and project.organization_id != organization_id and not project.is_shared:
            raise HTTPException(status_code=403, detail="Accès refusé.")

        def serialize_section(sec: FormSection) -> Dict[str, Any]:
            return {
                "title": sec.title,
                "code": sec.code,
                "description": sec.description,
                "order_index": sec.order_index,
                "is_repeatable": sec.is_repeatable,
                "min_repeats": sec.min_repeats,
                "max_repeats": sec.max_repeats,
                "is_sanctuarized": sec.is_sanctuarized,
                "allow_custom_questions": sec.allow_custom_questions,
                "weight": float(sec.weight) if sec.weight else 1.0,
                "scoring_category": sec.scoring_category,
                "is_critical": sec.is_critical,
                "min_passing_score": float(sec.min_passing_score) if sec.min_passing_score else None,
                "dependency_rules": sec.dependency_rules or {},
                "settings": sec.settings or {},
                "questions": [
                    {
                        "code": q.code,
                        "label": q.label,
                        "help_text": q.help_text,
                        "placeholder": q.placeholder,
                        "question_type": q.question_type,
                        "order_index": q.order_index,
                        "is_required": q.is_required,
                        "is_readonly": q.is_readonly,
                        "is_sanctuarized": q.is_sanctuarized,
                        "is_critical": q.is_critical,
                        "weight": float(q.weight) if q.weight else 1.0,
                        "scoring_config": q.scoring_config or {},
                        "options": q.options or [],
                        "resource_binding": q.resource_binding or {},
                        "formula_expression": q.formula_expression,
                        "validation_rules": q.validation_rules or {},
                        "dependency_rules": q.dependency_rules or {},
                        "settings": q.settings or {},
                    }
                    for q in (sec.questions or [])
                ],
                "subsections": [
                    serialize_section(sub)
                    for sub in (sec.subsections or [])
                ]
            }

        blueprint_data = {
            "blueprint_version": "1.0.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "project": {
                "name": project.name,
                "code": project.code,
                "category": project.category,
                "project_type": project.project_type,
                "description": project.description,
                "objectives": project.objectives,
                "icon": project.icon,
                "targets": project.targets or {},
                "settings": project.settings or {},
                "template_governance": project.template_governance or {},
                "plugin_code": project.plugin_code,
                "is_template": True,
            },
            "forms": [
                {
                    "name": f.name,
                    "code": f.code,
                    "description": f.description,
                    "form_type": f.form_type,
                    "version": f.version,
                    "order_index": f.order_index,
                    "is_sanctuarized": f.is_sanctuarized,
                    "allow_custom_sections": f.allow_custom_sections,
                    "is_mandatory": f.is_mandatory,
                    "is_repeatable": f.is_repeatable,
                    "allow_draft_save": f.allow_draft_save,
                    "offline_capable": f.offline_capable,
                    "scoring_enabled": f.scoring_enabled,
                    "scoring_scale": f.scoring_scale,
                    "passing_score_percentage": float(f.passing_score_percentage) if f.passing_score_percentage else 80.0,
                    "critical_fail_threshold": f.critical_fail_threshold,
                    "resource_binding_config": f.resource_binding_config or {},
                    "settings": f.settings or {},
                    "sections": [
                        serialize_section(s)
                        for s in (f.sections or [])
                        if s.parent_section_id is None
                    ]
                }
                for f in (project.forms or [])
            ]
        }
        return blueprint_data

    @classmethod
    async def import_project_blueprint(
        cls,
        db: AsyncSession,
        blueprint_data: Dict[str, Any],
        organization_id: Optional[int],
        user_id: int,
        is_super_admin: bool = False,
    ) -> Project:
        """
        Reconstruit un modèle de projet (blueprint) complet à partir d'un package JSON exporté.
        """
        import uuid
        proj_data = blueprint_data.get("project", {})
        code = proj_data.get("code", f"TMPL_{uuid.uuid4().hex[:6].upper()}")

        # Unicité du code
        chk = await db.execute(
            select(Project).where(and_(Project.code == code, Project.organization_id == organization_id))
        )
        if chk.scalars().first():
            code = f"{code}_{uuid.uuid4().hex[:4].upper()}"

        project = Project(
            organization_id=organization_id,
            plugin_id=proj_data.get("plugin_id"),
            plugin_code=proj_data.get("plugin_code"),
            is_template=proj_data.get("is_template", True),
            scope="template" if is_super_admin else "tenant",
            is_shared=is_super_admin,
            template_governance=proj_data.get("template_governance") or {},
            name=proj_data.get("name", "Modèle Importé"),
            code=code,
            category=proj_data.get("category", "general"),
            project_type=proj_data.get("project_type", "audit"),
            status=ProjectStatus.ACTIVE.value,
            icon=proj_data.get("icon", "FolderKanban"),
            description=proj_data.get("description"),
            objectives=proj_data.get("objectives"),
            targets=proj_data.get("targets") or {},
            settings=proj_data.get("settings") or {},
            created_by_id=user_id,
        )
        db.add(project)
        await db.flush()

        # Importer les formulaires
        for f_data in blueprint_data.get("forms", []):
            form = Form(
                project_id=project.id,
                organization_id=organization_id,
                name=f_data.get("name"),
                code=f_data.get("code"),
                description=f_data.get("description"),
                form_type=f_data.get("form_type", "audit"),
                version=f_data.get("version", "1.0.0"),
                is_template=True,
                is_sanctuarized=f_data.get("is_sanctuarized", False),
                allow_custom_sections=f_data.get("allow_custom_sections", True),
                is_mandatory=f_data.get("is_mandatory", True),
                is_repeatable=f_data.get("is_repeatable", False),
                passing_score_percentage=f_data.get("passing_score_percentage", 80.0),
                critical_fail_threshold=f_data.get("critical_fail_threshold", 1),
                resource_binding_config=f_data.get("resource_binding_config") or {},
                settings=f_data.get("settings") or {},
                created_by_id=user_id,
            )
            db.add(form)
            await db.flush()

            async def create_section_tree(sec_dict: Dict[str, Any], parent_id: Optional[int]):
                sec = FormSection(
                    form_id=form.id,
                    parent_section_id=parent_id,
                    title=sec_dict.get("title"),
                    code=sec_dict.get("code"),
                    description=sec_dict.get("description"),
                    order_index=sec_dict.get("order_index", 0),
                    is_sanctuarized=sec_dict.get("is_sanctuarized", False),
                    allow_custom_questions=sec_dict.get("allow_custom_questions", True),
                    weight=sec_dict.get("weight", 1.0),
                    scoring_category=sec_dict.get("scoring_category"),
                    is_critical=sec_dict.get("is_critical", False),
                    min_passing_score=sec_dict.get("min_passing_score"),
                    dependency_rules=sec_dict.get("dependency_rules") or {},
                    settings=sec_dict.get("settings") or {},
                )
                db.add(sec)
                await db.flush()

                for q_dict in sec_dict.get("questions", []):
                    q = FormQuestion(
                        section_id=sec.id,
                        code=q_dict.get("code"),
                        label=q_dict.get("label"),
                        help_text=q_dict.get("help_text"),
                        placeholder=q_dict.get("placeholder"),
                        question_type=q_dict.get("question_type", "text_short"),
                        order_index=q_dict.get("order_index", 0),
                        is_required=q_dict.get("is_required", True),
                        is_sanctuarized=q_dict.get("is_sanctuarized", False),
                        is_critical=q_dict.get("is_critical", False),
                        weight=q_dict.get("weight", 1.0),
                        scoring_config=q_dict.get("scoring_config") or {},
                        options=q_dict.get("options") or [],
                        resource_binding=q_dict.get("resource_binding") or {},
                        formula_expression=q_dict.get("formula_expression"),
                        validation_rules=q_dict.get("validation_rules") or {},
                        dependency_rules=q_dict.get("dependency_rules") or {},
                        settings=q_dict.get("settings") or {},
                    )
                    db.add(q)

                for sub_dict in sec_dict.get("subsections", []):
                    await create_section_tree(sub_dict, sec.id)

            for s_dict in f_data.get("sections", []):
                await create_section_tree(s_dict, None)

        await db.commit()
        await db.refresh(project)
        return project

    @classmethod
    async def export_compliance_report(
        cls,
        db: AsyncSession,
        project_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        Génère un rapport de conformité complet pour l'organisation utilisatrice :
        Rollup des scores, vérification des exigences sanctuarisées, identification des non-conformités.
        """
        from datetime import datetime, timezone

        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        # Charger toutes les soumissions
        subs_stmt = (
            select(FormSubmission)
            .join(Form)
            .where(Form.project_id == project_id)
            .order_by(FormSubmission.submitted_at.desc())
        )
        subs_res = await db.execute(subs_stmt)
        submissions = subs_res.scalars().all()

        total_audits = len(submissions)
        compliant_audits = sum(1 for s in submissions if s.compliance_status == ComplianceStatus.COMPLIANT.value)
        non_compliant_audits = sum(1 for s in submissions if s.compliance_status == ComplianceStatus.NON_COMPLIANT.value)
        total_critical = sum(s.critical_failures_count for s in submissions)

        avg_score = (
            round(sum(float(s.total_score) for s in submissions) / total_audits, 2)
            if total_audits > 0 else 0.0
        )

        return {
            "report_title": f"Rapport de Conformité Réglementaire - {project.name}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_code": project.code,
            "category": project.category,
            "parent_template_id": project.parent_template_id,
            "governance_requirements": project.template_governance or {},
            "metrics": {
                "total_audits_conducted": total_audits,
                "compliant_audits": compliant_audits,
                "non_compliant_audits": non_compliant_audits,
                "compliance_rate_percent": round((compliant_audits / total_audits) * 100, 2) if total_audits > 0 else 0.0,
                "average_score": avg_score,
                "total_critical_breaches": total_critical,
                "is_global_compliant": total_critical == 0 and avg_score >= 80.0,
            },
            "recent_audits": [
                {
                    "submission_code": s.submission_code,
                    "date": s.submitted_at.isoformat(),
                    "score": float(s.total_score),
                    "status": s.compliance_status,
                    "critical_failures": s.critical_failures_count,
                    "geolocation": s.geolocation,
                }
                for s in submissions[:20]
            ]
        }

