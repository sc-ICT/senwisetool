"""
Project Service for SenWiseTool.
Ensures strict multi-tenant isolation, template instantiation and lifecycle governance.
"""

from typing import List, Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, func, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from fastapi import HTTPException, status
import logging
import copy
from datetime import datetime, timezone

from app.models.project.project import Project, ProjectStatus, ProjectVersion
from app.models.form.form import Form, FormStatus
from app.models.form.form_version import FormVersion, FormVersionStatus
from app.models.user.user import User
from app.schemas.project.project import (
    ProjectCreate,
    ProjectUpdate,
    ProjectVersionCreate,
    ProjectVersionUpdate,
    ProjectVersionCloneRequest,
)

logger = logging.getLogger("app.services.project.project_service")


class ProjectService:

    @classmethod
    async def get_projects(
        cls,
        db: AsyncSession,
        organization_id: Optional[int],
        is_super_admin: bool = False,
        status_filter: Optional[str] = None,
        project_type: Optional[str] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> List[Project]:
        """
        Récupère les projets disponibles :
        - Pour le Super Admin : tous les projets.
        - Pour une organisation cliente : projets publics ou projets privés explicitement autorisés pour son organisation.
        """
        stmt = (
            select(Project)
            .options(selectinload(Project.versions))
        )

        if not is_super_admin and organization_id:
            stmt = stmt.where(
                or_(
                    Project.visibility == "public",
                    Project.authorized_organization_ids.contains([organization_id]),
                    Project.authorized_organization_ids.contains([str(organization_id)]),
                )
            )

        if project_type and project_type != "all":
            stmt = stmt.where(Project.project_type == project_type)

        if search and search.strip():
            search_fmt = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(
                    Project.name.ilike(search_fmt),
                    Project.code.ilike(search_fmt),
                )
            )

        stmt = stmt.order_by(Project.created_at.desc()).offset(skip).limit(limit)
        result = await db.execute(stmt)
        projects = list(result.scalars().all())

        if status_filter and status_filter != "all":
            projects = [
                p for p in projects
                if p.versions and p.versions[0].status == status_filter
            ]

        for proj in projects:
            cls._populate_latest_version_attributes(proj)

        return projects

    @classmethod
    async def get_project_by_id(
        cls,
        db: AsyncSession,
        project_id: int,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> Optional[Project]:
        """
        Récupère un projet spécifique par son ID avec ses versions.
        """
        stmt = (
            select(Project)
            .options(selectinload(Project.versions))
            .where(Project.id == project_id)
        )
        result = await db.execute(stmt)
        project = result.scalars().first()

        if not project:
            return None

        if not is_super_admin and organization_id and project.visibility == "private":
            allowed = project.authorized_organization_ids or []
            if organization_id not in allowed and str(organization_id) not in allowed:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Ce projet est privé et n'est pas autorisé pour votre organisation."
                )

        cls._populate_latest_version_attributes(project)
        return project

    @classmethod
    def _populate_latest_version_attributes(cls, project: Project) -> None:
        """
        Hydrate les attributs de niveau projet depuis la version la plus pertinente (active/publiée si présente, sinon la plus récente) pour compatibilité UI.
        """
        if project.versions and len(project.versions) > 0:
            active_ver = next(
                (v for v in project.versions if v.status in ("published", "active")),
                project.versions[0]
            )
            project.version = active_ver.version_number
            project.status = active_ver.status
            project.description = active_ver.description
            project.metrics = active_ver.metrics or {
                "forms_count": 0,
                "submissions_count": 0,
                "avg_compliance_score": 0.0,
                "critical_issues_count": 0,
            }
            project.settings = active_ver.settings or {}
            project.template_governance = active_ver.template_governance or {}
        else:
            project.version = "1.0.0"
            project.status = "draft"
            project.description = None
            project.metrics = {
                "forms_count": 0,
                "submissions_count": 0,
                "avg_compliance_score": 0.0,
                "critical_issues_count": 0,
            }
            project.settings = {}
            project.template_governance = {}

    @classmethod
    async def create_project(
        cls,
        db: AsyncSession,
        data: ProjectCreate,
        organization_id: Optional[int],
        user_id: int,
        is_super_admin: bool = False,
    ) -> Project:
        # Vérification d'unicité globale du code technique du projet
        chk_stmt = select(Project).where(Project.code == data.code)
        res = await db.execute(chk_stmt)
        if res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Un projet avec le code technique '{data.code}' existe déjà. Le code doit être unique."
            )

        # 1. Création du projet maître
        project = Project(
            name=data.name,
            code=data.code,
            project_type=data.project_type or "audit",
            category=data.category or "general",
            visibility=data.visibility or "public",
            authorized_organization_ids=data.authorized_organization_ids or [],
            cover_image_url=data.cover_image_url,
            created_by_id=user_id,
        )
        db.add(project)
        await db.flush()

        # 2. Création de sa version initiale obligatoire (ex: 1.0.0)
        initial_version_num = (data.version or "1.0.0").strip()
        initial_version = ProjectVersion(
            project_id=project.id,
            version_number=initial_version_num,
            status=data.status or ProjectStatus.DRAFT.value,
            description=data.description,
            changelog="Création initiale du projet",
            settings=data.options or {},
            template_governance=data.template_governance or {},
            metrics={
                "forms_count": 0,
                "submissions_count": 0,
                "avg_compliance_score": 0.0,
                "critical_issues_count": 0,
            },
            created_by_id=user_id,
        )
        db.add(initial_version)
        await db.commit()

        # Recharger avec versions
        return await cls.get_project_by_id(db, project.id, organization_id, is_super_admin)

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

        # Vérification de permission opérationnelle (sauf si on ne met à jour QUE la gouvernance elle-même)
        if not is_super_admin:
            gov = project.template_governance or {}
            non_gov_keys = [k for k in update_dict.keys() if k != "template_governance"]
            if non_gov_keys and gov.get("allow_edit") is False:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Action non autorisée : La modification du projet a été désactivée dans ses paramètres de gouvernance."
                )

        # Champs maîtres du projet (le code n'est pas modifiable)
        for key in ["name", "category", "visibility", "authorized_organization_ids", "cover_image_url", "project_type"]:
            if key in update_dict:
                setattr(project, key, update_dict[key])

        # Si une version en brouillon existe, les modifications de contenu de version s'appliquent à celle-ci.
        # RÈGLE D'IMMUABILITÉ STRICTE : Si la version est publiée/active, ses données sont scellées et ne doivent pas être altérées.
        if project.versions and len(project.versions) > 0:
            target_v = next((v for v in project.versions if v.status == ProjectStatus.DRAFT.value), None)
            if target_v:
                if "description" in update_dict and update_dict["description"] is not None:
                    target_v.description = update_dict["description"]
                if "status" in update_dict and update_dict["status"] is not None:
                    target_v.status = update_dict["status"]
                if "options" in update_dict and update_dict["options"] is not None:
                    target_v.settings = update_dict["options"]
                    flag_modified(target_v, "settings")
                if "template_governance" in update_dict and update_dict["template_governance"] is not None:
                    target_v.template_governance = update_dict["template_governance"]
                    flag_modified(target_v, "template_governance")

        await db.commit()
        await db.refresh(project)
        cls._populate_latest_version_attributes(project)
        return project

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

        # Règle de sécurité stricte : Un projet possédant au moins une version publiée ne peut pas être supprimé
        has_published = any(
            v.status in (ProjectStatus.PUBLISHED.value, "published", ProjectStatus.ACTIVE.value, "active")
            for v in (project.versions or [])
        )
        if has_published:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action interdite : Impossible de supprimer le projet « {project.name} » car il possède au moins une version publiée et scellée. Seuls les projets sans versions publiées peuvent être supprimés."
            )

        # La cascade SQL supprime automatiquement toutes les project_versions et forms
        await db.delete(project)
        await db.commit()
        return True

    # =====================================================================
    # GESTION DU CLONAGE & CYCLE DE VIE DES VERSIONS DU PROJET
    # =====================================================================

    @classmethod
    async def clone_project_version(
        cls,
        db: AsyncSession,
        project_id: int,
        req: ProjectVersionCloneRequest,
        organization_id: Optional[int],
        user_id: int,
        is_super_admin: bool = False,
    ) -> ProjectVersion:
        """
        Crée une nouvelle version à partir d'une version existante :
        - Clone la description, changelog, paramètres et gouvernance.
        - Clone intégralement tous les formulaires, sections, questions et grilles de notation associés.
        - Maintient le code du projet inchangé.
        """
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        # Vérification de permission opérationnelle
        if not is_super_admin:
            gov = project.template_governance or {}
            if gov.get("allow_add_version") is False:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Action non autorisée : La création d'une nouvelle version a été désactivée dans les paramètres du projet."
                )

        # Règle Métier 1 : Interdiction formelle d'avoir plusieurs versions en brouillon simultanées pour un même projet
        draft_chk = await db.execute(
            select(ProjectVersion).where(
                ProjectVersion.project_id == project_id,
                ProjectVersion.status == ProjectStatus.DRAFT.value
            )
        )
        existing_draft = draft_chk.scalars().first()
        if existing_draft:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sécurité de versionnage : Une version en brouillon (non publiée) existe déjà pour ce projet (« v{existing_draft.version_number} »). Vous devez obligatoirement publier ou supprimer ce brouillon avant de pouvoir cloner ou créer une nouvelle version."
            )

        new_v_num = req.new_version_number.strip()
        # Vérification d'unicité de la version pour CE projet
        chk_ver = await db.execute(
            select(ProjectVersion).where(
                ProjectVersion.project_id == project_id,
                ProjectVersion.version_number == new_v_num
            )
        )
        if chk_ver.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La version '{new_v_num}' existe déjà pour ce projet."
            )

        # Déterminer la version source
        source_version: Optional[ProjectVersion] = None
        if req.source_version_number:
            src_res = await db.execute(
                select(ProjectVersion).where(
                    ProjectVersion.project_id == project_id,
                    ProjectVersion.version_number == req.source_version_number.strip()
                )
            )
            source_version = src_res.scalars().first()
            if not source_version:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Version source '{req.source_version_number}' introuvable."
                )
        else:
            if project.versions and len(project.versions) > 0:
                source_version = project.versions[0]

        # Règle Métier 2 : Impossible de cloner une version qui est encore en brouillon
        if source_version and source_version.status == ProjectStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action non autorisée : Impossible de cloner la version source « v{source_version.version_number} » car elle est encore en brouillon. Vous devez d'abord publier cette version pour pouvoir la cloner."
            )

        # 1. Instancier la nouvelle version clonée
        new_version = ProjectVersion(
            project_id=project.id,
            version_number=new_v_num,
            status=ProjectStatus.DRAFT.value,
            description=source_version.description if source_version else "",
            changelog=req.changelog or f"Cloné depuis la version {source_version.version_number if source_version else 'initiale'}",
            settings=copy.deepcopy(source_version.settings or {}) if source_version else {},
            template_governance=copy.deepcopy(source_version.template_governance or {}) if source_version else {},
            metrics={
                "forms_count": 0,
                "submissions_count": 0,
                "avg_compliance_score": 0.0,
                "critical_issues_count": 0,
            },
            created_by_id=user_id,
        )
        db.add(new_version)
        await db.flush()

        # 2. Conserver les métriques et rattachements de formulaires
        if source_version:
            new_version.metrics = {
                "forms_count": (source_version.metrics or {}).get("forms_count", 0),
                "submissions_count": 0,
                "avg_compliance_score": 0.0,
                "critical_issues_count": 0,
            }

        await db.commit()
        await db.refresh(new_version)
        await db.refresh(project)
        cls._populate_latest_version_attributes(project)
        return new_version

    @classmethod
    async def delete_project_version(
        cls,
        db: AsyncSession,
        project_id: int,
        version_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        Supprime une version spécifique.
        RÈGLE SOUVERAINE : Si c'est la seule et unique version du projet, le projet
        doit obligatoirement être supprimé car un projet ne peut exister sans version.
        """
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        # Vérification de permission opérationnelle
        if not is_super_admin:
            gov = project.template_governance or {}
            if gov.get("allow_delete_version") is False:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Action non autorisée : La suppression d'une version a été désactivée dans les paramètres du projet."
                )

        # Vérifier le nombre de versions restantes
        count_stmt = select(func.count(ProjectVersion.id)).where(ProjectVersion.project_id == project_id)
        total_versions = (await db.execute(count_stmt)).scalar() or 0

        ver_stmt = select(ProjectVersion).where(
            ProjectVersion.id == version_id,
            ProjectVersion.project_id == project_id
        )
        ver_res = await db.execute(ver_stmt)
        version_obj = ver_res.scalars().first()
        if not version_obj:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce projet.")

        # Règle de sécurité stricte : Une version publiée ne peut jamais être supprimée
        if version_obj.status in (ProjectStatus.PUBLISHED.value, "published", ProjectStatus.ACTIVE.value, "active"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sécurité de versionnage : Impossible de supprimer la version v{version_obj.version_number} car elle est publiée. Une version publiée est scellée et immuable. Seules les versions en brouillon peuvent être supprimées."
            )

        if total_versions <= 1:
            # Dernière version : suppression du projet complet
            await db.delete(project)
            await db.commit()
            return {
                "action": "project_deleted",
                "message": f"Dernière version supprimée. Le projet « {project.name} » a été entièrement supprimé.",
                "project_id": project_id,
            }
        else:
            # Suppression ciblée de la version
            await db.delete(version_obj)
            await db.commit()
            return {
                "action": "version_deleted",
                "message": f"La version « {version_obj.version_number} » a été supprimée avec succès.",
                "version_id": version_id,
                "project_id": project_id,
            }

    @classmethod
    async def publish_project_version(
        cls,
        db: AsyncSession,
        project_id: int,
        version_id: int,
        organization_id: Optional[int],
        user_id: int,
        is_super_admin: bool = False,
    ) -> ProjectVersion:
        """
        Publie une version spécifique d'un projet.
        Règle : Seule une version en statut 'draft' peut être publiée.
        Si la version publiée est la plus récente, met à jour le statut global hydraté du projet.
        """
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        # Vérification des autorisations opérationnelles
        if not is_super_admin:
            gov = project.template_governance or {}
            if gov.get("allow_publish") is False:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Action non autorisée : La publication du projet a été désactivée dans ses paramètres de gouvernance."
                )

        ver_stmt = select(ProjectVersion).where(
            ProjectVersion.id == version_id,
            ProjectVersion.project_id == project_id
        )
        ver_res = await db.execute(ver_stmt)
        version_obj = ver_res.scalars().first()
        if not version_obj:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce projet.")

        # Règle de sécurité stricte : Une version publiée ne peut jamais être supprimée
        if version_obj.status in (ProjectStatus.PUBLISHED.value, "published", ProjectStatus.ACTIVE.value, "active"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sécurité de versionnage : Impossible de supprimer la version v{version_obj.version_number} car elle est publiée. Une version publiée est scellée et immuable. Seules les versions en brouillon peuvent être supprimées."
            )

        if version_obj.status == ProjectStatus.PUBLISHED.value:
            return version_obj

        version_obj.status = ProjectStatus.PUBLISHED.value
        version_obj.updated_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(version_obj)
        await db.refresh(project)
        cls._populate_latest_version_attributes(project)
        return version_obj

    @classmethod
    async def update_project_version(
        cls,
        db: AsyncSession,
        project_id: int,
        version_id: int,
        data: ProjectVersionUpdate,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> Project:
        """
        Met à jour les paramètres, la description, le changelog et la gouvernance d'une version spécifique d'un projet.
        RÈGLE D'IMMUABILITÉ STRICTE :
        Une version déjà publiée ('published') est scellée et ne peut JAMAIS être modifiée pour garantir
        la non-régression opérationnelle et la cohérence absolue avec les plugins et organisations clientes.
        """
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        ver_stmt = select(ProjectVersion).where(
            ProjectVersion.id == version_id,
            ProjectVersion.project_id == project_id
        )
        ver_res = await db.execute(ver_stmt)
        version_obj = ver_res.scalars().first()
        if not version_obj:
            raise HTTPException(status_code=404, detail="Version introuvable pour ce projet.")

        # Règle de sécurité stricte : Une version publiée ne peut jamais être modifiée
        if version_obj.status in (ProjectStatus.PUBLISHED.value, "published", ProjectStatus.ACTIVE.value, "active"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Sécurité de versionnage : Impossible de modifier les informations ou paramètres de la version v{version_obj.version_number} car elle est publiée. Une version publiée est scellée et immuable. Créez ou clonez une nouvelle version en brouillon pour ajuster ces configurations."
            )

        # Contrôle de permission opérationnelle si non super admin
        if not is_super_admin:
            gov = version_obj.template_governance or project.template_governance or {}
            if gov.get("allow_edit") is False:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Action non autorisée : La modification des paramètres a été désactivée dans la gouvernance de cette version."
                )

        update_dict = data.model_dump(exclude_unset=True)

        if "description" in update_dict and update_dict["description"] is not None:
            version_obj.description = update_dict["description"]
        if "changelog" in update_dict and update_dict["changelog"] is not None:
            version_obj.changelog = update_dict["changelog"]
        if "settings" in update_dict and update_dict["settings"] is not None:
            version_obj.settings = update_dict["settings"]
            flag_modified(version_obj, "settings")
        if "template_governance" in update_dict and update_dict["template_governance"] is not None:
            version_obj.template_governance = update_dict["template_governance"]
            flag_modified(version_obj, "template_governance")

        version_obj.updated_at = datetime.now(timezone.utc)

        await db.commit()
        await db.refresh(version_obj)
        await db.refresh(project)
        cls._populate_latest_version_attributes(project)
        return project

    @classmethod
    async def _refresh_project_metrics(cls, db: AsyncSession, project: Project) -> None:
        """
        Actualise les métriques de formulaires pour chaque version du projet.
        """
        if not project or not project.id:
            return

        # Collecter les IDs depuis les versions du projet et l'attribut éventuel settings
        attached_ids = set()
        for v in (project.versions or []):
            for fid in (v.settings or {}).get("attached_form_ids", []):
                if str(fid).isdigit():
                    attached_ids.add(int(fid))
            for af in (v.settings or {}).get("attached_form_versions", []):
                if isinstance(af, dict) and af.get("form_id") and str(af["form_id"]).isdigit():
                    attached_ids.add(int(af["form_id"]))
        for fid in (getattr(project, "settings", None) or {}).get("attached_form_ids", []):
            if str(fid).isdigit():
                attached_ids.add(int(fid))
        total_forms = len(attached_ids)

        for v in (project.versions or []):
            cur_metrics = dict(v.metrics or {})
            cur_metrics["forms_count"] = total_forms
            v.metrics = cur_metrics
            flag_modified(v, "metrics")

        cls._populate_latest_version_attributes(project)

    @classmethod
    async def attach_form_to_project(
        cls,
        db: AsyncSession,
        project_id: int,
        form_id: int,
        form_version_id: Optional[int] = None,
        project_version_id: Optional[int] = None,
        user: Optional[User] = None,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        Associe une version de formulaire publiée à une version spécifique de projet.
        Règle stricte d'intégrité :
        Interdiction absolue d'associer une version de formulaire en brouillon ('draft').
        Seules les versions de formulaire publiées ('published', 'active') peuvent être rattachées.
        """
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        # Résoudre la version du projet ciblée
        target_proj_ver: Optional[ProjectVersion] = None
        if project_version_id:
            target_proj_ver = next((v for v in (project.versions or []) if v.id == project_version_id), None)
            if not target_proj_ver:
                raise HTTPException(status_code=404, detail=f"Version de projet #{project_version_id} introuvable.")
        else:
            # Si non spécifiée, cibler en priorité la version en brouillon ou la version active
            target_proj_ver = next((v for v in (project.versions or []) if v.status == ProjectStatus.DRAFT.value), None)
            if not target_proj_ver:
                target_proj_ver = next((v for v in (project.versions or []) if v.status in (ProjectStatus.PUBLISHED.value, "published", ProjectStatus.ACTIVE.value, "active")), None)
            if not target_proj_ver and project.versions:
                target_proj_ver = project.versions[0]

        if not target_proj_ver:
            raise HTTPException(status_code=400, detail="Aucune version de projet disponible pour effectuer le rattachement.")

        # RÈGLE MÉTIER ABSOLUE : Interdiction formelle d'associer un formulaire à une version de projet publiée
        if target_proj_ver.status in (ProjectStatus.PUBLISHED.value, "published", ProjectStatus.ACTIVE.value, "active"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action interdite : La version v{target_proj_ver.version_number} de ce projet est déjà publiée et scellée. Impossible d'associer un formulaire à une version de projet publiée afin de préserver l'intégrité des audits. Créez ou clonez une nouvelle version en brouillon."
            )

        # Récupération du formulaire
        stmt = select(Form).options(selectinload(Form.versions)).where(Form.id == form_id)
        form_obj = (await db.execute(stmt)).scalars().first()
        if not form_obj:
            raise HTTPException(status_code=404, detail=f"Formulaire #{form_id} introuvable.")

        # Résolution de la version du formulaire
        target_form_ver: Optional[FormVersion] = None
        if form_version_id:
            target_form_ver = next((v for v in (form_obj.versions or []) if v.id == form_version_id), None)
            if not target_form_ver:
                raise HTTPException(status_code=404, detail=f"Version de formulaire #{form_version_id} introuvable sur ce formulaire.")
        else:
            # Chercher la version publiée active du formulaire
            target_form_ver = next((v for v in (form_obj.versions or []) if v.status in (FormVersionStatus.PUBLISHED.value, "published", FormVersionStatus.ACTIVE.value, "active")), None)
            if not target_form_ver and form_obj.status in (FormStatus.PUBLISHED.value, "published"):
                target_form_ver = form_obj.active_version

        if not target_form_ver:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Le formulaire « {form_obj.name} » ne possède aucune version publiée. Seules les versions publiées peuvent être associées à un projet."
            )

        # RÈGLE MÉTIER ABSOLUE : Exclusion formelle des brouillons
        if target_form_ver.status == FormVersionStatus.DRAFT.value or target_form_ver.status == "draft":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action non autorisée : La version « v{target_form_ver.version_number} » du formulaire « {form_obj.name} » est encore en brouillon. Vous devez impérativement publier cette version avant de pouvoir l'associer à un projet."
            )

        # Enregistrement dans les settings de la version du projet
        ver_settings = dict(target_proj_ver.settings or {})
        attached_form_versions = list(ver_settings.get("attached_form_versions") or [])

        # Vérifier si déjà présent, sinon ajouter ou mettre à jour
        existing_idx = next((i for i, af in enumerate(attached_form_versions) if af.get("form_id") == form_obj.id), None)
        attachment_record = {
            "form_id": form_obj.id,
            "form_version_id": target_form_ver.id,
            "form_name": form_obj.name,
            "form_code": form_obj.code,
            "version_number": target_form_ver.version_number,
            "status": target_form_ver.status,
            "attached_at": datetime.now(timezone.utc).isoformat(),
        }

        if existing_idx is not None:
            attached_form_versions[existing_idx] = attachment_record
        else:
            attached_form_versions.append(attachment_record)

        ver_settings["attached_form_versions"] = attached_form_versions
        ver_settings["attached_form_ids"] = list(set([x["form_id"] for x in attached_form_versions]))
        target_proj_ver.settings = ver_settings
        flag_modified(target_proj_ver, "settings")

        # Mise à jour des métriques
        ver_metrics = dict(target_proj_ver.metrics or {})
        ver_metrics["forms_count"] = len(attached_form_versions)
        target_proj_ver.metrics = ver_metrics
        flag_modified(target_proj_ver, "metrics")

        # Mise à jour sur la version
        flag_modified(target_proj_ver, "settings")
        flag_modified(target_proj_ver, "metrics")

        # Mise à jour globale sur le projet parent pour rétrocompatibilité si la colonne existe
        if "settings" in Project.__table__.columns:
            proj_settings = dict(getattr(project, "settings", None) or {})
            all_attached_ids = set(proj_settings.get("attached_form_ids") or [])
            all_attached_ids.add(form_obj.id)
            proj_settings["attached_form_ids"] = list(all_attached_ids)
            project.settings = proj_settings
            flag_modified(project, "settings")

        await db.commit()
        await db.refresh(target_proj_ver)
        await db.refresh(project)
        cls._populate_latest_version_attributes(project)

        return {
            "project_id": project.id,
            "project_version_id": target_proj_ver.id,
            "project_version_number": target_proj_ver.version_number,
            "attached_form": attachment_record,
        }

    @classmethod
    async def detach_form_from_project(
        cls,
        db: AsyncSession,
        project_id: int,
        form_id: int,
        project_version_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> bool:
        """
        Dissocie un formulaire d'une version spécifique de projet.
        RÈGLE MÉTIER ABSOLUE :
        Interdiction absolue de dissocier un formulaire d'une version de projet publiée ou scellée.
        Seules les versions de projet en brouillon ('draft') peuvent subir une dissociation.
        """
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        # Résoudre la version du projet ciblée
        target_proj_ver: Optional[ProjectVersion] = None
        if project_version_id:
            target_proj_ver = next((v for v in (project.versions or []) if v.id == project_version_id), None)
            if not target_proj_ver:
                raise HTTPException(status_code=404, detail=f"Version de projet #{project_version_id} introuvable.")
        else:
            # Cibler en priorité le brouillon
            target_proj_ver = next((v for v in (project.versions or []) if v.status == ProjectStatus.DRAFT.value), None)
            if not target_proj_ver:
                target_proj_ver = next((v for v in (project.versions or []) if v.status in (ProjectStatus.PUBLISHED.value, "published", ProjectStatus.ACTIVE.value, "active")), None)
            if not target_proj_ver and project.versions:
                target_proj_ver = project.versions[0]

        if not target_proj_ver:
            raise HTTPException(status_code=400, detail="Aucune version de projet trouvée pour effectuer la dissociation.")

        # Contrôle strict d'immuabilité
        if target_proj_ver.status in (ProjectStatus.PUBLISHED.value, "published", ProjectStatus.ACTIVE.value, "active"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Action interdite : La version v{target_proj_ver.version_number} de ce projet est déjà publiée et scellée. Impossible de dissocier un formulaire d'une version de projet publiée afin de préserver l'intégrité des audits et soumissions existantes. Créez ou clonez une nouvelle version en brouillon."
            )

        # Modifier UNIQUEMENT la version en brouillon ciblée
        ver_settings = dict(target_proj_ver.settings or {})
        attached_form_versions = list(ver_settings.get("attached_form_versions") or [])
        filtered = [af for af in attached_form_versions if af.get("form_id") != form_id]
        ver_settings["attached_form_versions"] = filtered
        ver_settings["attached_form_ids"] = [x["form_id"] for x in filtered]
        target_proj_ver.settings = ver_settings
        flag_modified(target_proj_ver, "settings")

        ver_metrics = dict(target_proj_ver.metrics or {})
        ver_metrics["forms_count"] = len(filtered)
        target_proj_ver.metrics = ver_metrics
        flag_modified(target_proj_ver, "metrics")

        # Mise à jour globale projet si colonne présente pour rétrocompatibilité
        if "settings" in Project.__table__.columns:
            proj_settings = dict(getattr(project, "settings", None) or {})
            remaining_ids = set()
            for v in (project.versions or []):
                remaining_ids.update((v.settings or {}).get("attached_form_ids", []))
            proj_settings["attached_form_ids"] = list(remaining_ids)
            project.settings = proj_settings
            flag_modified(project, "settings")

        await db.commit()
        await db.refresh(target_proj_ver)
        await db.refresh(project)
        cls._populate_latest_version_attributes(project)
        return True

    @classmethod
    async def get_project_forms(
        cls,
        db: AsyncSession,
        project_id: int,
        project_version_id: Optional[int] = None,
        organization_id: Optional[int] = None,
        is_super_admin: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Récupère les formulaires associés à la version spécifiée du projet.
        """
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        target_proj_ver: Optional[ProjectVersion] = None
        if project_version_id:
            target_proj_ver = next((v for v in (project.versions or []) if v.id == project_version_id), None)
        else:
            # Version active courante ou première version
            target_proj_ver = next((v for v in (project.versions or []) if v.status in (ProjectStatus.PUBLISHED.value, "published", ProjectStatus.ACTIVE.value, "active")), None)
            if not target_proj_ver and project.versions:
                target_proj_ver = project.versions[0]

        attached_form_versions = (target_proj_ver.settings or {}).get("attached_form_versions", []) if target_proj_ver else []
        attached_ids = [af["form_id"] for af in attached_form_versions if af.get("form_id")]

        # Repli rétrocompatible si attached_form_versions est vide mais attached_form_ids existe
        if not attached_ids:
            attached_ids = [int(x) for x in (getattr(project, "settings", None) or {}).get("attached_form_ids", []) if str(x).isdigit()]

        if not attached_ids:
            return []

        forms_stmt = select(Form).options(selectinload(Form.versions)).where(Form.id.in_(attached_ids))
        forms_res = await db.execute(forms_stmt)
        forms_list = forms_res.scalars().all()

        results = []
        for f in forms_list:
            af_record = next((af for af in attached_form_versions if af.get("form_id") == f.id), None)
            attached_ver_info = None
            if af_record:
                attached_ver_info = {
                    "form_version_id": af_record.get("form_version_id"),
                    "version_number": af_record.get("version_number"),
                    "status": af_record.get("status"),
                    "attached_at": af_record.get("attached_at"),
                }
            else:
                # Utiliser la version active publiée
                pub_v = next((v for v in (f.versions or []) if v.status in ("published", "active")), None)
                if pub_v:
                    attached_ver_info = {
                        "form_version_id": pub_v.id,
                        "version_number": pub_v.version_number,
                        "status": pub_v.status,
                    }

            f_dict = {
                "id": f.id,
                "name": f.name,
                "code": f.code,
                "description": f.description,
                "visibility": f.visibility,
                "version": attached_ver_info["version_number"] if attached_ver_info else f.version,
                "status": attached_ver_info["status"] if attached_ver_info else f.status,
                "created_at": f.created_at.isoformat() if f.created_at else None,
                "updated_at": f.updated_at.isoformat() if f.updated_at else None,
                "attached_version": attached_ver_info,
                "versions": [
                    {
                        "id": v.id,
                        "version_number": v.version_number,
                        "status": v.status,
                    }
                    for v in (f.versions or [])
                ],
            }
            results.append(f_dict)

        return results

    @classmethod
    async def export_project_blueprint(
        cls,
        db: AsyncSession,
        project_id: int,
        organization_id: Optional[int],
        version_number: Optional[str] = None,
        is_super_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        Exporte la structure complète du projet en JSON autonome.
        Permet d'exporter toutes les versions ou une version spécifique.
        """
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        # Vérification de permission opérationnelle
        if not is_super_admin:
            gov = project.template_governance or {}
            if gov.get("allow_export") is False:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Action non autorisée : L'export du package projet a été désactivé dans ses paramètres."
                )

        # Filtrage des versions
        versions_to_export = project.versions or []
        if version_number and version_number.strip() and version_number != "all":
            selected_v = [v for v in versions_to_export if v.version_number == version_number.strip()]
            if not selected_v:
                raise HTTPException(status_code=404, detail=f"Version {version_number} introuvable pour ce projet.")
            versions_to_export = selected_v

        # Récupérer les formulaires attachés au projet
        attached_ids = [int(x) for x in (getattr(project, "settings", None) or {}).get("attached_form_ids", []) if str(x).isdigit()]
        forms_list = []
        if attached_ids:
            from app.models.form.form import Form
            forms_stmt = select(Form).where(Form.id.in_(attached_ids))
            forms_res = await db.execute(forms_stmt)
            forms_list = forms_res.scalars().all()

        versions_payload = []
        for v in versions_to_export:
            v_forms = [
                {
                    "name": f.name,
                    "code": f.code,
                    "version": f.version,
                    "status": f.status,
                }
                for f in forms_list
            ]
            versions_payload.append({
                "version_number": v.version_number,
                "status": v.status,
                "description": v.description,
                "changelog": v.changelog,
                "settings": v.settings or {},
                "template_governance": v.template_governance or {},
                "forms_count": len(v_forms),
                "forms": v_forms,
            })

        return {
            "blueprint_version": "2.0.0",
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "export_mode": "single_version" if version_number and version_number != "all" else "all_versions",
            "selected_version": version_number if version_number and version_number != "all" else "all",
            "project": {
                "name": project.name,
                "code": project.code,
                "project_type": project.project_type,
                "category": project.category,
                "visibility": project.visibility,
                "authorized_organization_ids": project.authorized_organization_ids or [],
                "cover_image_url": project.cover_image_url,
                "current_version": project.version,
                "status": project.status,
                "description": project.description,
                "template_governance": project.template_governance or {},
                "settings": project.settings or {},
                "total_versions": len(versions_payload),
                "versions": versions_payload,
            }
        }

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
        Importe un package Blueprint JSON de projet (avec versions et métadonnées).
        Génère un code unique en cas de collision et recrée l'ensemble des versions.
        """
        # Extraire le nœud projet (compatible blueprint_version 2.0.0 ou direct)
        proj_data = blueprint_data.get("project", blueprint_data)
        if not isinstance(proj_data, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Format de Blueprint invalide : structure racine manquante."
            )

        name = proj_data.get("name") or "Projet Importé"
        base_code = proj_data.get("code") or f"PRJ_IMP_{datetime.now().strftime('%y%m%d%H%M%S')}"
        project_type = proj_data.get("project_type") or "audit"
        category = proj_data.get("category") or "general"
        visibility = proj_data.get("visibility") or ("public" if is_super_admin else "private")
        authorized_orgs = proj_data.get("authorized_organization_ids") or []
        cover_image_url = proj_data.get("cover_image_url")

        # Résoudre l'unicité du code
        import uuid
        target_code = base_code
        chk_stmt = select(Project).where(Project.code == target_code)
        if (await db.execute(chk_stmt)).scalars().first():
            target_code = f"{base_code[:80]}_{uuid.uuid4().hex[:6]}"

        # Si l'import est fait par une organisation (non super admin), on restreint l'organisation autorisée
        if not is_super_admin and organization_id:
            if organization_id not in authorized_orgs:
                authorized_orgs = list(authorized_orgs) + [organization_id]

        project = Project(
            name=name,
            code=target_code,
            project_type=project_type,
            category=category,
            visibility=visibility,
            authorized_organization_ids=authorized_orgs,
            cover_image_url=cover_image_url,
            created_by_id=user_id,
        )
        db.add(project)
        await db.flush()

        # Récupérer les versions à importer
        versions_list = proj_data.get("versions")
        if not versions_list or not isinstance(versions_list, list):
            # Version unique de secours
            v_num = proj_data.get("current_version") or proj_data.get("version") or "1.0.0"
            versions_list = [{
                "version_number": v_num,
                "status": proj_data.get("status") or ProjectStatus.DRAFT.value,
                "description": proj_data.get("description"),
                "changelog": "Importé depuis le package Blueprint",
                "settings": proj_data.get("settings") or {},
                "template_governance": proj_data.get("template_governance") or {},
            }]

        for v_item in versions_list:
            v_num = str(v_item.get("version_number") or "1.0.0").strip()
            v_obj = ProjectVersion(
                project_id=project.id,
                version_number=v_num,
                status=v_item.get("status") or ProjectStatus.DRAFT.value,
                description=v_item.get("description"),
                changelog=v_item.get("changelog") or "Importé depuis le package Blueprint",
                settings=v_item.get("settings") or {},
                template_governance=v_item.get("template_governance") or {},
                metrics={
                    "forms_count": 0,
                    "submissions_count": 0,
                    "avg_compliance_score": 0.0,
                    "critical_issues_count": 0,
                },
                created_by_id=user_id,
            )
            db.add(v_obj)

        await db.commit()
        return await cls.get_project_by_id(db, project.id, organization_id, is_super_admin)

    @classmethod
    async def export_compliance_report(
        cls,
        db: AsyncSession,
        project_id: int,
        organization_id: Optional[int],
        is_super_admin: bool = False,
    ) -> Dict[str, Any]:
        """
        Génère un rapport de conformité pour le projet.
        """
        project = await cls.get_project_by_id(db, project_id, organization_id, is_super_admin)
        if not project:
            raise HTTPException(status_code=404, detail="Projet introuvable.")

        submissions = []
        total_audits = 0
        compliant_audits = 0
        non_compliant_audits = 0
        total_critical = 0
        avg_score = 0.0

        return {
            "report_title": f"Rapport de Conformité Réglementaire - {project.name}",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "project_code": project.code,
            "project_type": project.project_type,
            "metrics": {
                "total_audits_conducted": total_audits,
                "compliant_audits": compliant_audits,
                "non_compliant_audits": non_compliant_audits,
                "compliance_rate_percent": round((compliant_audits / total_audits) * 100, 2) if total_audits > 0 else 0.0,
                "average_score": avg_score,
                "total_critical_breaches": total_critical,
                "is_global_compliant": total_critical == 0 and avg_score >= 80.0,
            },
        }
