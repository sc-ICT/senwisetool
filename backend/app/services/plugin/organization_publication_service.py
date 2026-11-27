"""
Service de Publication et Scellement Immuable pour les Organisations
====================================================================
Gère le cycle de publication au niveau de l'organisation :
1. Une version téléchargée a le statut 'native' (NATIF) et reste modifiable/paramétrable
   selon sa gouvernance et les contrôles d'intégrité.
2. La publication crée une COPIE IMMUABLE de la version courante avec toutes ses
   configurations, données et dépendances.
3. Cette copie prend le statut 'published' et un numéro de publication unique
   par version d'origine : ex: "1.0.1 / 1", "1.0.1 / 2", etc.
4. Une fois publiée, cette copie ne peut plus subir aucune modification ni suppression.
5. Publication en cascade : toutes les versions de modules dépendantes directes
   ou indirectes (formulaires, ressources, projets) sont également publiées en cascade
   et les liaisons dans la copie publiée sont re-câblées vers ces versions publiées.
"""

import copy
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple
from fastapi import HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.organization.organization_resource import (
    OrganizationResource,
    OrganizationResourceVersion,
    OrganizationResourceItem,
)
from app.models.organization.organization_form import (
    OrganizationForm,
    OrganizationFormVersion,
    OrganizationFormSection,
    OrganizationFormQuestion,
)
from app.models.organization.organization_project import (
    OrganizationProject,
    OrganizationProjectVersion,
)
from app.models.organization.organization_campaign import (
    OrganizationCampaign,
    OrganizationCampaignVersion,
)

logger = logging.getLogger("app.services.plugin.organization_publication_service")


class OrganizationPublicationService:

    # =========================================================================
    # CALCUL DU NUMÉRO DE PUBLICATION UNIQUE PAR VERSION D'ORIGINE
    # =========================================================================
    @classmethod
    def _compute_next_publication_number(
        cls,
        base_version_number: str,
        existing_versions: List[Any],
        origin_version_id: int,
    ) -> Tuple[int, str]:
        """
        Calcule le prochain numéro de publication séquentiel (1, 2, 3...)
        et le version_number associé au format '{base_version_number} / {next_pub_num}'.
        """
        pub_nums: Set[int] = set()
        clean_base = str(base_version_number).split("/")[0].strip()

        for v in existing_versions:
            if getattr(v, "origin_version_id", None) == origin_version_id:
                if getattr(v, "publication_number", None) is not None:
                    pub_nums.add(v.publication_number)
            v_num = str(getattr(v, "version_number", ""))
            if "/" in v_num:
                parts = [p.strip() for p in v_num.split("/")]
                if parts[0] == clean_base and len(parts) > 1 and parts[1].isdigit():
                    pub_nums.add(int(parts[1]))

        next_pub_num = (max(pub_nums) + 1) if pub_nums else 1
        formatted_version_number = f"{clean_base} / {next_pub_num}"
        return next_pub_num, formatted_version_number

    # =========================================================================
    # 1. PUBLICATION D'UNE RESSOURCE (ET CASCADE DES RELATIONS INTER-RESSOURCES)
    # =========================================================================
    @classmethod
    async def publish_resource_version(
        cls,
        db: AsyncSession,
        resource_id: int,
        version_id: int,
        organization_id: int,
        user_id: Optional[int] = None,
        cascade_context: Optional[Dict[str, Dict[int, Any]]] = None,
    ) -> OrganizationResourceVersion:
        """
        Crée une copie publiée immuable d'une version de ressource
        avec duplication complète de son schéma, ses items et publication en cascade
        des ressources ciblées par ses relations.
        """
        is_root_call = cascade_context is None
        if cascade_context is None:
            cascade_context = {
                "resources": {},
                "forms": {},
                "projects": {},
                "campaigns": {},
            }

        # Éviter toute récursion / duplication dans le même lot de publication
        if version_id in cascade_context["resources"]:
            return cascade_context["resources"][version_id]

        stmt = select(OrganizationResourceVersion).options(
            selectinload(OrganizationResourceVersion.organization_resource),
            selectinload(OrganizationResourceVersion.items),
        ).where(
            OrganizationResourceVersion.id == version_id,
            OrganizationResourceVersion.organization_id == organization_id,
        )
        origin_ver = (await db.execute(stmt)).scalars().first()
        if not origin_ver:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version de ressource #{version_id} introuvable pour votre organisation.",
            )

        # Si cette version est déjà une publication immuable scellée
        if origin_ver.status == "published" and (origin_ver.publication_number is not None or "/" in str(origin_ver.version_number)):
            if is_root_call:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La version « {origin_ver.version_number} » est déjà une copie scellée et immuable. Seule une version native de travail peut faire l'objet d'une publication.",
                )
            cascade_context["resources"][version_id] = origin_ver
            return origin_ver

        parent_res = origin_ver.organization_resource
        all_vers_stmt = select(OrganizationResourceVersion).where(
            OrganizationResourceVersion.organization_resource_id == origin_ver.organization_resource_id,
            OrganizationResourceVersion.organization_id == organization_id,
        )
        existing_versions = (await db.execute(all_vers_stmt)).scalars().all()

        next_pub_num, pub_vnum = cls._compute_next_publication_number(
            base_version_number=origin_ver.version_number,
            existing_versions=existing_versions,
            origin_version_id=origin_ver.id,
        )

        # Cascade sur les champs relations vers d'autres ressources
        new_schema_fields = copy.deepcopy(origin_ver.schema_fields or [])
        for field in new_schema_fields:
            if isinstance(field, dict) and field.get("type") == "relation":
                t_rid = field.get("target_resource_id")
                t_rvid = field.get("target_resource_version_id")
                if t_rid and t_rvid:
                    # Publier en cascade la ressource cible
                    try:
                        pub_target_rv = await cls.publish_resource_version(
                            db=db,
                            resource_id=t_rid,
                            version_id=t_rvid,
                            organization_id=organization_id,
                            user_id=user_id,
                            cascade_context=cascade_context,
                        )
                        field["target_resource_version_id"] = pub_target_rv.id
                        field["target_resource_version_number"] = pub_target_rv.version_number
                    except Exception as e:
                        logger.warning(f"Cascade publish target resource {t_rid} v{t_rvid}: {e}")

        # Création de la copie de version publiée immuable
        pub_ver = OrganizationResourceVersion(
            organization_resource_id=origin_ver.organization_resource_id,
            organization_plugin_version_id=origin_ver.organization_plugin_version_id,
            source_resource_version_id=origin_ver.source_resource_version_id,
            organization_id=organization_id,
            downloaded_by_user_id=user_id or origin_ver.downloaded_by_user_id,
            version_number=pub_vnum,
            status="published",
            publication_number=next_pub_num,
            origin_version_id=origin_ver.id,
            published_at=datetime.now(timezone.utc),
            description=origin_ver.description,
            changelog=origin_ver.changelog or f"Publication #{next_pub_num} issue de v{origin_ver.version_number}",
            schema_fields=new_schema_fields,
            metrics=dict(origin_ver.metrics or {}),
            template_governance=dict(origin_ver.template_governance or {}),
            settings=dict(origin_ver.settings or {}),
            parameters=dict(origin_ver.parameters or {}),
        )
        db.add(pub_ver)
        await db.flush()

        cascade_context["resources"][origin_ver.id] = pub_ver

        # Duplication intégrale des enregistrements (items) pour cette version scellée
        for orig_item in (origin_ver.items or []):
            copied_item = OrganizationResourceItem(
                organization_resource_id=orig_item.organization_resource_id,
                organization_resource_version_id=pub_ver.id,
                organization_plugin_version_id=orig_item.organization_plugin_version_id,
                source_resource_item_id=orig_item.source_resource_item_id,
                organization_id=organization_id,
                downloaded_by_user_id=user_id or orig_item.downloaded_by_user_id,
                data=copy.deepcopy(orig_item.data or {}),
                settings=copy.deepcopy(orig_item.settings or {}),
                parameters=copy.deepcopy(orig_item.parameters or {}),
            )
            db.add(copied_item)

        await db.flush()
        return pub_ver

    # =========================================================================
    # 2. PUBLICATION D'UN FORMULAIRE (ET CASCADE RESSOURCES + CROSS-FORMS)
    # =========================================================================
    @classmethod
    async def publish_form_version(
        cls,
        db: AsyncSession,
        form_id: int,
        version_id: int,
        organization_id: int,
        user_id: Optional[int] = None,
        cascade_context: Optional[Dict[str, Dict[int, Any]]] = None,
    ) -> OrganizationFormVersion:
        """
        Crée une copie publiée immuable d'une version de formulaire avec
        duplication intégrale des sections et questions, et publication en cascade
        de toutes les ressources liées (resource_binding) et formulaires inter-liés (cross_form_lookup).
        """
        is_root_call = cascade_context is None
        if cascade_context is None:
            cascade_context = {
                "resources": {},
                "forms": {},
                "projects": {},
                "campaigns": {},
            }

        if version_id in cascade_context["forms"]:
            return cascade_context["forms"][version_id]

        stmt = select(OrganizationFormVersion).options(
            selectinload(OrganizationFormVersion.organization_form),
            selectinload(OrganizationFormVersion.sections),
            selectinload(OrganizationFormVersion.questions),
        ).where(
            OrganizationFormVersion.id == version_id,
            OrganizationFormVersion.organization_id == organization_id,
        )
        origin_ver = (await db.execute(stmt)).scalars().first()
        if not origin_ver:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version de formulaire #{version_id} introuvable pour votre organisation.",
            )

        # Si cette version est déjà une publication immuable scellée
        if origin_ver.status == "published" and (origin_ver.publication_number is not None or "/" in str(origin_ver.version_number)):
            if is_root_call:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La version « {origin_ver.version_number} » est déjà une copie scellée et immuable. Seule une version native de travail peut faire l'objet d'une publication.",
                )
            cascade_context["forms"][version_id] = origin_ver
            return origin_ver

        all_vers_stmt = select(OrganizationFormVersion).where(
            OrganizationFormVersion.organization_form_id == origin_ver.organization_form_id,
            OrganizationFormVersion.organization_id == organization_id,
        )
        existing_versions = (await db.execute(all_vers_stmt)).scalars().all()

        next_pub_num, pub_vnum = cls._compute_next_publication_number(
            base_version_number=origin_ver.version_number,
            existing_versions=existing_versions,
            origin_version_id=origin_ver.id,
        )

        # Création de la copie de version publiée immuable
        pub_ver = OrganizationFormVersion(
            organization_form_id=origin_ver.organization_form_id,
            organization_plugin_version_id=origin_ver.organization_plugin_version_id,
            source_form_version_id=origin_ver.source_form_version_id,
            organization_id=organization_id,
            downloaded_by_user_id=user_id or origin_ver.downloaded_by_user_id,
            version_number=pub_vnum,
            status="published",
            publication_number=next_pub_num,
            origin_version_id=origin_ver.id,
            published_at=datetime.now(timezone.utc),
            description=origin_ver.description,
            form_type=origin_ver.form_type or "standard",
            template_governance=copy.deepcopy(origin_ver.template_governance or {}),
            settings=copy.deepcopy(origin_ver.settings or {}),
            parameters=copy.deepcopy(origin_ver.parameters or {}),
        )
        db.add(pub_ver)
        await db.flush()

        cascade_context["forms"][origin_ver.id] = pub_ver

        # Duplication des sections avec préservation de la hiérarchie parent_section_id
        old_sec_to_new_sec: Dict[int, int] = {}
        sorted_sections = sorted(origin_ver.sections or [], key=lambda s: (s.parent_section_id is not None, s.order_index))
        for orig_sec in sorted_sections:
            new_parent_id = old_sec_to_new_sec.get(orig_sec.parent_section_id) if orig_sec.parent_section_id else None
            new_sec = OrganizationFormSection(
                organization_form_id=orig_sec.organization_form_id,
                organization_form_version_id=pub_ver.id,
                organization_plugin_version_id=orig_sec.organization_plugin_version_id,
                source_form_section_id=orig_sec.source_form_section_id,
                organization_id=organization_id,
                downloaded_by_user_id=user_id or orig_sec.downloaded_by_user_id,
                parent_section_id=new_parent_id,
                title=orig_sec.title,
                code=orig_sec.code,
                description=orig_sec.description,
                order_index=orig_sec.order_index,
                is_repeatable=orig_sec.is_repeatable,
                min_repeats=orig_sec.min_repeats,
                max_repeats=orig_sec.max_repeats,
                is_sanctuarized=orig_sec.is_sanctuarized,
                allow_custom_questions=orig_sec.allow_custom_questions,
                weight=orig_sec.weight,
                scoring_category=orig_sec.scoring_category,
                is_critical=orig_sec.is_critical,
                min_passing_score=orig_sec.min_passing_score,
                dependency_rules=copy.deepcopy(orig_sec.dependency_rules or {}),
                settings=copy.deepcopy(orig_sec.settings or {}),
                parameters=copy.deepcopy(orig_sec.parameters or {}),
            )
            db.add(new_sec)
            await db.flush()
            old_sec_to_new_sec[orig_sec.id] = new_sec.id

        # Duplication des questions et cascade sur resource_binding et cross_form_lookup
        for orig_q in (origin_ver.questions or []):
            new_sec_id = old_sec_to_new_sec.get(orig_q.organization_form_section_id) if orig_q.organization_form_section_id else None

            # a) Traitement resource_binding
            binding = copy.deepcopy(orig_q.resource_binding or {})
            r_id = binding.get("resource_id") or binding.get("target_resource_id")
            r_vid = (
                binding.get("resource_version_id")
                or binding.get("target_resource_version_id")
                or binding.get("target_version_id")
            )
            if r_id and r_vid:
                try:
                    pub_rv = await cls.publish_resource_version(
                        db=db,
                        resource_id=int(r_id),
                        version_id=int(r_vid),
                        organization_id=organization_id,
                        user_id=user_id,
                        cascade_context=cascade_context,
                    )
                    binding["resource_version_id"] = pub_rv.id
                    binding["target_resource_version_id"] = pub_rv.id
                    binding["resource_version_number"] = pub_rv.version_number
                except Exception as e:
                    logger.warning(f"Cascade publish resource for form question {orig_q.code}: {e}")

            # b) Traitement cross_form_lookup dans settings
            q_settings = copy.deepcopy(orig_q.settings or {})
            cross_cfg = q_settings.get("cross_form_lookup")
            if isinstance(cross_cfg, dict):
                tgt_fid = cross_cfg.get("target_form_id")
                tgt_fvid = cross_cfg.get("target_form_version_id")
                if tgt_fid and tgt_fvid and tgt_fvid != origin_ver.id:
                    try:
                        pub_tgt_fv = await cls.publish_form_version(
                            db=db,
                            form_id=tgt_fid,
                            version_id=tgt_fvid,
                            organization_id=organization_id,
                            user_id=user_id,
                            cascade_context=cascade_context,
                        )
                        cross_cfg["target_form_version_id"] = pub_tgt_fv.id
                        cross_cfg["target_form_version_number"] = pub_tgt_fv.version_number
                    except Exception as e:
                        logger.warning(f"Cascade publish cross_form for question {orig_q.code}: {e}")

            new_q = OrganizationFormQuestion(
                organization_form_id=orig_q.organization_form_id,
                organization_form_version_id=pub_ver.id,
                organization_form_section_id=new_sec_id,
                organization_plugin_version_id=orig_q.organization_plugin_version_id,
                source_form_question_id=orig_q.source_form_question_id,
                organization_id=organization_id,
                downloaded_by_user_id=user_id or orig_q.downloaded_by_user_id,
                code=orig_q.code,
                label=orig_q.label,
                help_text=orig_q.help_text,
                placeholder=orig_q.placeholder,
                question_type=orig_q.question_type,
                order_index=orig_q.order_index,
                is_required=orig_q.is_required,
                is_readonly=orig_q.is_readonly,
                is_sanctuarized=orig_q.is_sanctuarized,
                is_critical=orig_q.is_critical,
                weight=orig_q.weight,
                scoring_config=copy.deepcopy(orig_q.scoring_config or {}),
                options=copy.deepcopy(orig_q.options or []),
                resource_binding=binding,
                formula_expression=orig_q.formula_expression,
                validation_rules=copy.deepcopy(orig_q.validation_rules or {}),
                dependency_rules=copy.deepcopy(orig_q.dependency_rules or {}),
                settings=q_settings,
                parameters=copy.deepcopy(orig_q.parameters or {}),
            )
            db.add(new_q)

        await db.flush()
        return pub_ver

    # =========================================================================
    # 3. PUBLICATION D'UN PROJET (ET CASCADE DES FORMULAIRES RATTACHÉS)
    # =========================================================================
    @classmethod
    async def publish_project_version(
        cls,
        db: AsyncSession,
        project_id: int,
        version_id: int,
        organization_id: int,
        user_id: Optional[int] = None,
        cascade_context: Optional[Dict[str, Dict[int, Any]]] = None,
    ) -> OrganizationProjectVersion:
        """
        Crée une copie publiée immuable d'une version de projet avec
        publication en cascade de tous les formulaires rattachés (attached_form_versions).
        """
        is_root_call = cascade_context is None
        if cascade_context is None:
            cascade_context = {
                "resources": {},
                "forms": {},
                "projects": {},
                "campaigns": {},
            }

        if version_id in cascade_context["projects"]:
            return cascade_context["projects"][version_id]

        stmt = select(OrganizationProjectVersion).where(
            OrganizationProjectVersion.id == version_id,
            OrganizationProjectVersion.organization_id == organization_id,
        )
        origin_ver = (await db.execute(stmt)).scalars().first()
        if not origin_ver:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version de projet #{version_id} introuvable pour votre organisation.",
            )

        # Si cette version est déjà une publication immuable scellée
        if origin_ver.status == "published" and (origin_ver.publication_number is not None or "/" in str(origin_ver.version_number)):
            if is_root_call:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La version « {origin_ver.version_number} » est déjà une copie scellée et immuable. Seule une version native de travail peut faire l'objet d'une publication.",
                )
            cascade_context["projects"][version_id] = origin_ver
            return origin_ver

        all_vers_stmt = select(OrganizationProjectVersion).where(
            OrganizationProjectVersion.organization_project_id == origin_ver.organization_project_id,
            OrganizationProjectVersion.organization_id == organization_id,
        )
        existing_versions = (await db.execute(all_vers_stmt)).scalars().all()

        next_pub_num, pub_vnum = cls._compute_next_publication_number(
            base_version_number=origin_ver.version_number,
            existing_versions=existing_versions,
            origin_version_id=origin_ver.id,
        )

        # Cascade sur les formulaires rattachés
        new_settings = copy.deepcopy(origin_ver.settings or {})
        attached_fvs = new_settings.get("attached_form_versions") or {}
        new_attached_fvs = {}
        for fid_str, fvid in attached_fvs.items():
            try:
                pub_fv = await cls.publish_form_version(
                    db=db,
                    form_id=int(fid_str),
                    version_id=int(fvid),
                    organization_id=organization_id,
                    user_id=user_id,
                    cascade_context=cascade_context,
                )
                new_attached_fvs[str(pub_fv.organization_form_id)] = pub_fv.id
            except Exception as e:
                logger.warning(f"Cascade publish attached form {fid_str} v{fvid}: {e}")
                new_attached_fvs[fid_str] = fvid

        new_settings["attached_form_versions"] = new_attached_fvs

        pub_ver = OrganizationProjectVersion(
            organization_project_id=origin_ver.organization_project_id,
            organization_plugin_version_id=origin_ver.organization_plugin_version_id,
            source_project_version_id=origin_ver.source_project_version_id,
            organization_id=organization_id,
            downloaded_by_user_id=user_id or origin_ver.downloaded_by_user_id,
            version_number=pub_vnum,
            status="published",
            publication_number=next_pub_num,
            origin_version_id=origin_ver.id,
            published_at=datetime.now(timezone.utc),
            description=origin_ver.description,
            changelog=origin_ver.changelog or f"Publication #{next_pub_num} issue de v{origin_ver.version_number}",
            metrics=copy.deepcopy(origin_ver.metrics or {}),
            template_governance=copy.deepcopy(origin_ver.template_governance or {}),
            settings=new_settings,
            parameters=copy.deepcopy(origin_ver.parameters or {}),
        )
        db.add(pub_ver)
        await db.flush()

        cascade_context["projects"][origin_ver.id] = pub_ver
        return pub_ver

    # =========================================================================
    # 4. PUBLICATION D'UNE CAMPAGNE (ET CASCADE PROJETS + FORMULAIRES)
    # =========================================================================
    @classmethod
    async def publish_campaign_version(
        cls,
        db: AsyncSession,
        campaign_id: int,
        version_id: int,
        organization_id: int,
        user_id: Optional[int] = None,
        cascade_context: Optional[Dict[str, Dict[int, Any]]] = None,
    ) -> OrganizationCampaignVersion:
        """
        Crée une copie publiée immuable d'une version de campagne avec
        publication en cascade de tous les projets et formulaires dépendants.
        """
        is_root_call = cascade_context is None
        if cascade_context is None:
            cascade_context = {
                "resources": {},
                "forms": {},
                "projects": {},
                "campaigns": {},
            }

        if version_id in cascade_context["campaigns"]:
            return cascade_context["campaigns"][version_id]

        stmt = select(OrganizationCampaignVersion).where(
            OrganizationCampaignVersion.id == version_id,
            OrganizationCampaignVersion.organization_id == organization_id,
        )
        origin_ver = (await db.execute(stmt)).scalars().first()
        if not origin_ver:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Version de campagne #{version_id} introuvable pour votre organisation.",
            )

        # Si cette version est déjà une publication immuable scellée
        if origin_ver.status == "published" and (origin_ver.publication_number is not None or "/" in str(origin_ver.version_number)):
            if is_root_call:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La version « {origin_ver.version_number} » est déjà une copie scellée et immuable. Seule une version native de travail peut faire l'objet d'une publication.",
                )
            cascade_context["campaigns"][version_id] = origin_ver
            return origin_ver

        all_vers_stmt = select(OrganizationCampaignVersion).where(
            OrganizationCampaignVersion.organization_campaign_id == origin_ver.organization_campaign_id,
            OrganizationCampaignVersion.organization_id == organization_id,
        )
        existing_versions = (await db.execute(all_vers_stmt)).scalars().all()

        next_pub_num, pub_vnum = cls._compute_next_publication_number(
            base_version_number=origin_ver.version_number,
            existing_versions=existing_versions,
            origin_version_id=origin_ver.id,
        )

        # a) Cascade sur les projets cibles
        new_target_projects = []
        for p_item in (origin_ver.target_projects or []):
            if isinstance(p_item, dict):
                p_id = p_item.get("id") or p_item.get("project_id")
                p_vid = p_item.get("version_id") or p_item.get("project_version_id")
                if p_id and p_vid:
                    try:
                        pub_pv = await cls.publish_project_version(
                            db=db,
                            project_id=int(p_id),
                            version_id=int(p_vid),
                            organization_id=organization_id,
                            user_id=user_id,
                            cascade_context=cascade_context,
                        )
                        p_item_copy = dict(p_item)
                        p_item_copy["version_id"] = pub_pv.id
                        p_item_copy["project_version_id"] = pub_pv.id
                        p_item_copy["version_number"] = pub_pv.version_number
                        new_target_projects.append(p_item_copy)
                        continue
                    except Exception as e:
                        logger.warning(f"Cascade publish project {p_id} v{p_vid}: {e}")
            new_target_projects.append(p_item)

        # b) Cascade sur les formulaires cibles
        new_target_forms = []
        for f_item in (origin_ver.target_forms or []):
            if isinstance(f_item, dict):
                f_id = f_item.get("id") or f_item.get("form_id")
                f_vid = f_item.get("version_id") or f_item.get("form_version_id")
                if f_id and f_vid:
                    try:
                        pub_fv = await cls.publish_form_version(
                            db=db,
                            form_id=int(f_id),
                            version_id=int(f_vid),
                            organization_id=organization_id,
                            user_id=user_id,
                            cascade_context=cascade_context,
                        )
                        f_item_copy = dict(f_item)
                        f_item_copy["version_id"] = pub_fv.id
                        f_item_copy["form_version_id"] = pub_fv.id
                        f_item_copy["version_number"] = pub_fv.version_number
                        new_target_forms.append(f_item_copy)
                        continue
                    except Exception as e:
                        logger.warning(f"Cascade publish form {f_id} v{f_vid}: {e}")
            new_target_forms.append(f_item)

        pub_ver = OrganizationCampaignVersion(
            organization_campaign_id=origin_ver.organization_campaign_id,
            organization_plugin_version_id=origin_ver.organization_plugin_version_id,
            source_campaign_version_id=origin_ver.source_campaign_version_id,
            organization_id=organization_id,
            downloaded_by_user_id=user_id or origin_ver.downloaded_by_user_id,
            version_number=pub_vnum,
            status="published",
            publication_number=next_pub_num,
            origin_version_id=origin_ver.id,
            published_at=datetime.now(timezone.utc),
            description=origin_ver.description,
            objectives=origin_ver.objectives,
            targets=copy.deepcopy(origin_ver.targets or {}),
            start_date=origin_ver.start_date,
            end_date=origin_ver.end_date,
            budget=origin_ver.budget,
            currency=origin_ver.currency,
            target_forms=new_target_forms,
            target_projects=new_target_projects,
            changelog=origin_ver.changelog or f"Publication #{next_pub_num} issue de v{origin_ver.version_number}",
            metrics=copy.deepcopy(origin_ver.metrics or {}),
            template_governance=copy.deepcopy(origin_ver.template_governance or {}),
            settings=copy.deepcopy(origin_ver.settings or {}),
            parameters=copy.deepcopy(origin_ver.parameters or {}),
        )
        db.add(pub_ver)
        await db.flush()

        cascade_context["campaigns"][origin_ver.id] = pub_ver
        return pub_ver
