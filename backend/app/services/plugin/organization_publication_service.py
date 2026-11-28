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
from sqlalchemy import select, func, or_
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
        raw_attached_fvs = new_settings.get("attached_form_versions")
        if isinstance(raw_attached_fvs, dict):
            new_attached_fvs = {}
            for fid_str, fvid in raw_attached_fvs.items():
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
        elif isinstance(raw_attached_fvs, list):
            new_attached_list = []
            for item in raw_attached_fvs:
                if isinstance(item, dict):
                    item_copy = copy.deepcopy(item)
                    fid = item.get("form_id") or item.get("id")
                    fvid = item.get("form_version_id") or item.get("version_id")
                    if fid:
                        try:
                            pub_fv = await cls.publish_form_version(
                                db=db,
                                form_id=int(fid),
                                version_id=int(fvid) if fvid else None,
                                organization_id=organization_id,
                                user_id=user_id,
                                cascade_context=cascade_context,
                            )
                            item_copy["form_version_id"] = pub_fv.id
                            item_copy["version_number"] = pub_fv.version_number
                            item_copy["status"] = pub_fv.status
                        except Exception as e:
                            logger.warning(f"Cascade publish attached form {fid}: {e}")
                    new_attached_list.append(item_copy)
                else:
                    new_attached_list.append(item)
            new_settings["attached_form_versions"] = new_attached_list

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

    # =========================================================================
    # HELPERS DE RECHERCHE LOCALE DANS L'ORGANISATION
    # =========================================================================
    @classmethod
    async def _find_org_resource(
        cls, db: AsyncSession, organization_id: int, target_id: Optional[int], target_code: Optional[str]
    ) -> Optional[OrganizationResource]:
        if not target_id and not target_code:
            return None

        # Priorité absolue au code s'il est renseigné
        if target_code:
            stmt = select(OrganizationResource).options(
                selectinload(OrganizationResource.versions)
            ).where(
                OrganizationResource.organization_id == organization_id,
                OrganizationResource.code == str(target_code),
            )
            obj = (await db.execute(stmt)).scalars().first()
            if obj:
                return obj

        if target_id:
            try:
                tid = int(target_id)
                # D'abord par source_resource_id
                stmt = select(OrganizationResource).options(
                    selectinload(OrganizationResource.versions)
                ).where(
                    OrganizationResource.organization_id == organization_id,
                    OrganizationResource.source_resource_id == tid,
                )
                obj = (await db.execute(stmt)).scalars().first()
                if obj:
                    return obj

                # Puis par id local
                stmt2 = select(OrganizationResource).options(
                    selectinload(OrganizationResource.versions)
                ).where(
                    OrganizationResource.organization_id == organization_id,
                    OrganizationResource.id == tid,
                )
                return (await db.execute(stmt2)).scalars().first()
            except Exception:
                pass
        return None

    @classmethod
    async def _find_org_form(
        cls, db: AsyncSession, organization_id: int, target_id: Optional[int], target_code: Optional[str]
    ) -> Optional[OrganizationForm]:
        if not target_id and not target_code:
            return None

        if target_code:
            stmt = select(OrganizationForm).options(
                selectinload(OrganizationForm.versions)
            ).where(
                OrganizationForm.organization_id == organization_id,
                OrganizationForm.code == str(target_code),
            )
            obj = (await db.execute(stmt)).scalars().first()
            if obj:
                return obj

        if target_id:
            try:
                tid = int(target_id)
                stmt = select(OrganizationForm).options(
                    selectinload(OrganizationForm.versions)
                ).where(
                    OrganizationForm.organization_id == organization_id,
                    OrganizationForm.source_form_id == tid,
                )
                obj = (await db.execute(stmt)).scalars().first()
                if obj:
                    return obj

                stmt2 = select(OrganizationForm).options(
                    selectinload(OrganizationForm.versions)
                ).where(
                    OrganizationForm.organization_id == organization_id,
                    OrganizationForm.id == tid,
                )
                return (await db.execute(stmt2)).scalars().first()
            except Exception:
                pass
        return None

    @classmethod
    async def _find_org_project(
        cls, db: AsyncSession, organization_id: int, target_id: Optional[int], target_code: Optional[str]
    ) -> Optional[OrganizationProject]:
        if not target_id and not target_code:
            return None

        if target_code:
            stmt = select(OrganizationProject).options(
                selectinload(OrganizationProject.versions)
            ).where(
                OrganizationProject.organization_id == organization_id,
                OrganizationProject.code == str(target_code),
            )
            obj = (await db.execute(stmt)).scalars().first()
            if obj:
                return obj

        if target_id:
            try:
                tid = int(target_id)
                stmt = select(OrganizationProject).options(
                    selectinload(OrganizationProject.versions)
                ).where(
                    OrganizationProject.organization_id == organization_id,
                    OrganizationProject.source_project_id == tid,
                )
                obj = (await db.execute(stmt)).scalars().first()
                if obj:
                    return obj

                stmt2 = select(OrganizationProject).options(
                    selectinload(OrganizationProject.versions)
                ).where(
                    OrganizationProject.organization_id == organization_id,
                    OrganizationProject.id == tid,
                )
                return (await db.execute(stmt2)).scalars().first()
            except Exception:
                pass
        return None

    # =========================================================================
    # 5. ARBRE D'INSPECTION DE DRAFT DE PUBLICATION (DEPENDENCY TREE)
    # =========================================================================
    @classmethod
    async def get_publication_dependency_tree(
        cls,
        db: AsyncSession,
        module_type: str,
        module_id: int,
        version_id: int,
        organization_id: int,
    ) -> Dict[str, Any]:
        """
        Construit l'arbre complet des dépendances pour une version native donnée
        en vue de la configuration du draft de publication sous forme d'arbre.
        """
        # Normalisation du type de module
        norm_type = str(module_type).lower().rstrip("s")
        if norm_type not in ("resource", "form", "project", "campaign"):
            norm_type = "resource"

        async def _build_tree_node(
            m_type: str,
            m_id: int,
            v_id: int,
            field_ctx: Optional[Dict[str, Any]] = None,
            ancestors: Optional[Set[Tuple[str, int]]] = None,
            tree_path: str = "root",
        ) -> Optional[Dict[str, Any]]:
            if ancestors is None:
                ancestors = set()

            node_key = (m_type, m_id)
            is_cycle = node_key in ancestors
            curr_ancestors = set(ancestors)
            curr_ancestors.add(node_key)

            node_data: Dict[str, Any] = {
                "node_id": f"{tree_path}_{m_type}_{m_id}_{v_id}",
                "tree_path": tree_path,
                "module_type": m_type,
                "module_id": m_id,
                "module_name": f"{m_type.capitalize()} #{m_id}",
                "module_code": f"{m_type}_{m_id}",
                "version_id": v_id,
                "version_number": "1.0.0",
                "status": "native",
                "field_context": field_ctx or {},
                "published_versions": [],
                "has_published_versions": False,
                "dependencies": [],
                "has_dependencies": False,
                "is_cycle": is_cycle,
                "default_action": "new",
            }

            if is_cycle:
                return node_data

            if m_type == "resource":
                stmt = select(OrganizationResource).options(
                    selectinload(OrganizationResource.versions).selectinload(OrganizationResourceVersion.items)
                ).where(
                    OrganizationResource.id == m_id,
                    OrganizationResource.organization_id == organization_id,
                )
                res_obj = (await db.execute(stmt)).scalars().first()
                if not res_obj:
                    return None

                node_data["module_name"] = res_obj.name
                node_data["module_code"] = res_obj.code

                # Identifier la version cible
                ver_obj = next((v for v in (res_obj.versions or []) if v.id == v_id), None)
                if not ver_obj:
                    # Fallback sur la dernière version native / active
                    ver_obj = next(
                        (v for v in (res_obj.versions or []) if v.status != "published"),
                        (res_obj.versions or [None])[0],
                    )
                if ver_obj:
                    node_data["version_id"] = ver_obj.id
                    node_data["version_number"] = ver_obj.version_number
                    node_data["status"] = ver_obj.status

                # Versions publiées existantes de cette ressource
                published = [
                    {
                        "id": pv.id,
                        "version_number": pv.version_number,
                        "publication_number": pv.publication_number,
                        "published_at": pv.published_at.isoformat() if pv.published_at else None,
                        "items_count": len(pv.items or []),
                        "description": pv.description,
                    }
                    for pv in (res_obj.versions or [])
                    if pv.status == "published" and (pv.publication_number is not None or "/" in str(pv.version_number))
                ]
                # Trier par publication_number décroissant
                published.sort(key=lambda x: x["publication_number"] or 0, reverse=True)
                node_data["published_versions"] = published
                node_data["has_published_versions"] = len(published) > 0

                # Dépendances (relations schema_fields)
                if ver_obj and ver_obj.schema_fields:
                    child_deps = []
                    for f in ver_obj.schema_fields:
                        if isinstance(f, dict) and f.get("type") == "relation":
                            target_rid = f.get("target_resource_id")
                            target_rcode = f.get("target_resource_code")
                            target_res = await cls._find_org_resource(db, organization_id, target_rid, target_rcode)
                            if target_res and target_res.id != m_id:
                                # Trouver sa version native courante
                                target_vid = f.get("target_resource_version_id")
                                t_ver = None
                                if target_vid:
                                    t_ver = next((v for v in (target_res.versions or []) if v.id == target_vid), None)
                                if not t_ver:
                                    t_ver = next(
                                        (v for v in (target_res.versions or []) if v.status != "published"),
                                        (target_res.versions or [None])[0],
                                    )
                                if t_ver:
                                    child_node = await _build_tree_node(
                                        m_type="resource",
                                        m_id=target_res.id,
                                        v_id=t_ver.id,
                                        field_ctx={
                                            "field_name": f.get("name"),
                                            "field_label": f.get("label") or f.get("name"),
                                            "type": "relation",
                                            "relation_type": f.get("relation_type", "many_to_one"),
                                        },
                                        ancestors=curr_ancestors,
                                        tree_path=f"{tree_path}.{len(child_deps)}",
                                    )
                                    if child_node:
                                        child_deps.append(child_node)
                    node_data["dependencies"] = child_deps
                    node_data["has_dependencies"] = len(child_deps) > 0

            elif m_type == "form":
                stmt = select(OrganizationForm).options(
                    selectinload(OrganizationForm.versions).selectinload(OrganizationFormVersion.questions),
                    selectinload(OrganizationForm.versions).selectinload(OrganizationFormVersion.sections),
                ).where(
                    OrganizationForm.id == m_id,
                    OrganizationForm.organization_id == organization_id,
                )
                form_obj = (await db.execute(stmt)).scalars().first()
                if not form_obj:
                    return None

                node_data["module_name"] = form_obj.name
                node_data["module_code"] = form_obj.code

                ver_obj = next((v for v in (form_obj.versions or []) if v.id == v_id), None)
                if not ver_obj:
                    ver_obj = next(
                        (v for v in (form_obj.versions or []) if v.status != "published"),
                        (form_obj.versions or [None])[0],
                    )
                if ver_obj:
                    node_data["version_id"] = ver_obj.id
                    node_data["version_number"] = ver_obj.version_number
                    node_data["status"] = ver_obj.status

                published = [
                    {
                        "id": pv.id,
                        "version_number": pv.version_number,
                        "publication_number": pv.publication_number,
                        "published_at": pv.published_at.isoformat() if pv.published_at else None,
                        "description": pv.description,
                    }
                    for pv in (form_obj.versions or [])
                    if pv.status == "published" and (pv.publication_number is not None or "/" in str(pv.version_number))
                ]
                published.sort(key=lambda x: x["publication_number"] or 0, reverse=True)
                node_data["published_versions"] = published
                node_data["has_published_versions"] = len(published) > 0

                child_deps = []
                if ver_obj:
                    for q in (ver_obj.questions or []):
                        # a) resource_binding
                        binding = q.resource_binding or {}
                        if isinstance(binding, dict) and (binding.get("resource_id") or binding.get("target_resource_id")):
                            r_id = binding.get("resource_id") or binding.get("target_resource_id")
                            r_code = binding.get("resource_code") or binding.get("target_resource_code")
                            t_res = await cls._find_org_resource(db, organization_id, r_id, r_code)
                            if t_res:
                                r_vid = binding.get("resource_version_id") or binding.get("target_resource_version_id")
                                t_ver = None
                                if r_vid:
                                    t_ver = next((v for v in (t_res.versions or []) if v.id == r_vid), None)
                                if not t_ver:
                                    t_ver = next(
                                        (v for v in (t_res.versions or []) if v.status != "published"),
                                        (t_res.versions or [None])[0],
                                    )
                                if t_ver:
                                    c_node = await _build_tree_node(
                                        m_type="resource",
                                        m_id=t_res.id,
                                        v_id=t_ver.id,
                                        field_ctx={
                                            "field_name": q.code,
                                            "field_label": f"Question « {q.label or q.code} »",
                                            "type": "resource_binding",
                                        },
                                        ancestors=curr_ancestors,
                                        tree_path=f"{tree_path}.{len(child_deps)}",
                                    )
                                    if c_node:
                                        child_deps.append(c_node)

                        # b) cross_form_lookup
                        q_set = q.settings or {}
                        cross_cfg = q_set.get("cross_form_lookup")
                        if isinstance(cross_cfg, dict) and cross_cfg.get("target_form_id"):
                            tgt_fid = cross_cfg.get("target_form_id")
                            tgt_fcode = cross_cfg.get("target_form_code")
                            t_form = await cls._find_org_form(db, organization_id, tgt_fid, tgt_fcode)
                            if t_form and t_form.id != m_id:
                                tgt_fvid = cross_cfg.get("target_form_version_id")
                                t_ver = None
                                if tgt_fvid:
                                    t_ver = next((v for v in (t_form.versions or []) if v.id == tgt_fvid), None)
                                if not t_ver:
                                    t_ver = next(
                                        (v for v in (t_form.versions or []) if v.status != "published"),
                                        (t_form.versions or [None])[0],
                                    )
                                if t_ver:
                                    c_node = await _build_tree_node(
                                        m_type="form",
                                        m_id=t_form.id,
                                        v_id=t_ver.id,
                                        field_ctx={
                                            "field_name": q.code,
                                            "field_label": f"Question « {q.label or q.code} » (Cross-Form)",
                                            "type": "cross_form_lookup",
                                        },
                                        ancestors=curr_ancestors,
                                        tree_path=f"{tree_path}.{len(child_deps)}",
                                    )
                                    if c_node:
                                        child_deps.append(c_node)

                node_data["dependencies"] = child_deps
                node_data["has_dependencies"] = len(child_deps) > 0

            elif m_type == "project":
                stmt = select(OrganizationProject).options(
                    selectinload(OrganizationProject.versions)
                ).where(
                    OrganizationProject.id == m_id,
                    OrganizationProject.organization_id == organization_id,
                )
                proj_obj = (await db.execute(stmt)).scalars().first()
                if not proj_obj:
                    return None

                node_data["module_name"] = proj_obj.name
                node_data["module_code"] = proj_obj.code

                ver_obj = next((v for v in (proj_obj.versions or []) if v.id == v_id), None)
                if not ver_obj:
                    ver_obj = next(
                        (v for v in (proj_obj.versions or []) if v.status != "published"),
                        (proj_obj.versions or [None])[0],
                    )
                if ver_obj:
                    node_data["version_id"] = ver_obj.id
                    node_data["version_number"] = ver_obj.version_number
                    node_data["status"] = ver_obj.status

                published = [
                    {
                        "id": pv.id,
                        "version_number": pv.version_number,
                        "publication_number": pv.publication_number,
                        "published_at": pv.published_at.isoformat() if pv.published_at else None,
                        "description": pv.description,
                    }
                    for pv in (proj_obj.versions or [])
                    if pv.status == "published" and (pv.publication_number is not None or "/" in str(pv.version_number))
                ]
                published.sort(key=lambda x: x["publication_number"] or 0, reverse=True)
                node_data["published_versions"] = published
                node_data["has_published_versions"] = len(published) > 0

                child_deps = []
                if ver_obj and ver_obj.settings:
                    attached_fvs = (ver_obj.settings or {}).get("attached_form_versions")
                    form_version_pairs = []
                    if isinstance(attached_fvs, dict):
                        for fid_str, fvid in attached_fvs.items():
                            try:
                                form_version_pairs.append((int(fid_str), int(fvid) if fvid else None))
                            except (ValueError, TypeError):
                                pass
                    elif isinstance(attached_fvs, list):
                        for item in attached_fvs:
                            if isinstance(item, dict):
                                fid = item.get("form_id") or item.get("id")
                                fvid = item.get("form_version_id") or item.get("version_id")
                                if fid is not None:
                                    try:
                                        form_version_pairs.append((int(fid), int(fvid) if fvid else None))
                                    except (ValueError, TypeError):
                                        pass
                            elif isinstance(item, (int, str)) and str(item).isdigit():
                                form_version_pairs.append((int(item), None))

                    # Fallback vers attached_form_ids si aucun formulaire trouvé
                    if not form_version_pairs:
                        for fid in (ver_obj.settings or {}).get("attached_form_ids") or []:
                            if isinstance(fid, (int, str)) and str(fid).isdigit():
                                form_version_pairs.append((int(fid), None))

                    for f_id_num, f_vid_num in form_version_pairs:
                        try:
                            t_form = await cls._find_org_form(db, organization_id, f_id_num, None)
                            if t_form:
                                t_ver = None
                                if f_vid_num:
                                    t_ver = next((v for v in (t_form.versions or []) if v.id == f_vid_num), None)
                                if not t_ver:
                                    t_ver = next(
                                        (v for v in (t_form.versions or []) if v.status != "published"),
                                        (t_form.versions or [None])[0],
                                    )
                                if t_ver:
                                    c_node = await _build_tree_node(
                                        m_type="form",
                                        m_id=t_form.id,
                                        v_id=t_ver.id,
                                        field_ctx={
                                            "field_name": "attached_form",
                                            "field_label": f"Formulaire « {t_form.name} »",
                                            "type": "project_form",
                                        },
                                        ancestors=curr_ancestors,
                                        tree_path=f"{tree_path}.{len(child_deps)}",
                                    )
                                    if c_node:
                                        child_deps.append(c_node)
                        except Exception as e:
                            logger.warning(f"Error building child node for form {f_id_num}: {e}")
                node_data["dependencies"] = child_deps
                node_data["has_dependencies"] = len(child_deps) > 0

            elif m_type == "campaign":
                stmt = select(OrganizationCampaign).options(
                    selectinload(OrganizationCampaign.versions)
                ).where(
                    OrganizationCampaign.id == m_id,
                    OrganizationCampaign.organization_id == organization_id,
                )
                camp_obj = (await db.execute(stmt)).scalars().first()
                if not camp_obj:
                    return None

                node_data["module_name"] = camp_obj.name
                node_data["module_code"] = camp_obj.code

                ver_obj = next((v for v in (camp_obj.versions or []) if v.id == v_id), None)
                if not ver_obj:
                    ver_obj = next(
                        (v for v in (camp_obj.versions or []) if v.status != "published"),
                        (camp_obj.versions or [None])[0],
                    )
                if ver_obj:
                    node_data["version_id"] = ver_obj.id
                    node_data["version_number"] = ver_obj.version_number
                    node_data["status"] = ver_obj.status

                published = [
                    {
                        "id": pv.id,
                        "version_number": pv.version_number,
                        "publication_number": pv.publication_number,
                        "published_at": pv.published_at.isoformat() if pv.published_at else None,
                        "description": pv.description,
                    }
                    for pv in (camp_obj.versions or [])
                    if pv.status == "published" and (pv.publication_number is not None or "/" in str(pv.version_number))
                ]
                published.sort(key=lambda x: x["publication_number"] or 0, reverse=True)
                node_data["published_versions"] = published
                node_data["has_published_versions"] = len(published) > 0

                child_deps = []
                if ver_obj:
                    # target_projects
                    for p_item in (ver_obj.target_projects or []):
                        if isinstance(p_item, dict):
                            pid = p_item.get("id") or p_item.get("project_id")
                            pvid = p_item.get("version_id") or p_item.get("project_version_id")
                            t_proj = await cls._find_org_project(db, organization_id, pid, None)
                            if t_proj:
                                t_ver = next((v for v in (t_proj.versions or []) if v.id == pvid), None) if pvid else None
                                if not t_ver:
                                    t_ver = (t_proj.versions or [None])[0]
                                if t_ver:
                                    c_node = await _build_tree_node(
                                        m_type="project",
                                        m_id=t_proj.id,
                                        v_id=t_ver.id,
                                        field_ctx={
                                            "field_name": "target_project",
                                            "field_label": f"Projet « {t_proj.name} »",
                                            "type": "campaign_project",
                                        },
                                        ancestors=curr_ancestors,
                                        tree_path=f"{tree_path}.{len(child_deps)}",
                                    )
                                    if c_node:
                                        child_deps.append(c_node)

                    # target_forms
                    for f_item in (ver_obj.target_forms or []):
                        if isinstance(f_item, dict):
                            fid = f_item.get("id") or f_item.get("form_id")
                            fvid = f_item.get("version_id") or f_item.get("form_version_id")
                            t_form = await cls._find_org_form(db, organization_id, fid, None)
                            if t_form:
                                t_ver = next((v for v in (t_form.versions or []) if v.id == fvid), None) if fvid else None
                                if not t_ver:
                                    t_ver = (t_form.versions or [None])[0]
                                if t_ver:
                                    c_node = await _build_tree_node(
                                        m_type="form",
                                        m_id=t_form.id,
                                        v_id=t_ver.id,
                                        field_ctx={
                                            "field_name": "target_form",
                                            "field_label": f"Formulaire « {t_form.name} »",
                                            "type": "campaign_form",
                                        },
                                        ancestors=curr_ancestors,
                                        tree_path=f"{tree_path}.{len(child_deps)}",
                                    )
                                    if c_node:
                                        child_deps.append(c_node)

                node_data["dependencies"] = child_deps
                node_data["has_dependencies"] = len(child_deps) > 0

            return node_data

        root_tree = await _build_tree_node(norm_type, module_id, version_id)
        if not root_tree:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Module {norm_type} #{module_id} introuvable pour votre organisation.",
            )

        # Calculer les métriques globales de l'arbre
        def _count_nodes(n: Dict[str, Any]) -> int:
            return 1 + sum(_count_nodes(c) for c in n.get("dependencies", []))

        root_tree["total_tree_nodes"] = _count_nodes(root_tree)
        return root_tree

    # =========================================================================
    # 6. EXÉCUTION D'UN PLAN D'ARBRE DE PUBLICATION (EXECUTE TREE PLAN)
    # =========================================================================
    @classmethod
    async def execute_publication_tree(
        cls,
        db: AsyncSession,
        module_type: str,
        module_id: int,
        version_id: int,
        organization_id: int,
        user_id: Optional[int],
        tree_plan: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Exécute le plan de publication configuré :
        - Parcourt l'arbre en post-ordre (feuilles d'abord).
        - Si action == "existing" : réutilise directement la publication existante sans rien créer.
        - Si action == "new" : crée la publication scellée vX.Y.Z / N, lie ses champs dépendants
          vers les versions publiées de ses enfants, et enregistre published_dependencies dans settings.
        - Garantit l'atomicité et l'indépendance souveraine des publications créées.
        """
        norm_type = str(module_type).lower().rstrip("s")
        if norm_type not in ("resource", "form", "project", "campaign"):
            norm_type = "resource"

        # Identifier la version de plugin de la racine pour rattacher tout le lot scellé ensemble
        root_opv_id = None
        if norm_type == "resource":
            root_opv_id = await db.scalar(
                select(OrganizationResourceVersion.organization_plugin_version_id).where(
                    OrganizationResourceVersion.id == version_id,
                    OrganizationResourceVersion.organization_id == organization_id,
                )
            )
        elif norm_type == "form":
            root_opv_id = await db.scalar(
                select(OrganizationFormVersion.organization_plugin_version_id).where(
                    OrganizationFormVersion.id == version_id,
                    OrganizationFormVersion.organization_id == organization_id,
                )
            )
        elif norm_type == "project":
            root_opv_id = await db.scalar(
                select(OrganizationProjectVersion.organization_plugin_version_id).where(
                    OrganizationProjectVersion.id == version_id,
                    OrganizationProjectVersion.organization_id == organization_id,
                )
            )
        elif norm_type == "campaign":
            root_opv_id = await db.scalar(
                select(OrganizationCampaignVersion.organization_plugin_version_id).where(
                    OrganizationCampaignVersion.id == version_id,
                    OrganizationCampaignVersion.organization_id == organization_id,
                )
            )

        created_versions = []
        reused_versions = []

        async def _execute_sub_tree(node: Dict[str, Any]) -> Any:
            n_type = str(node.get("module_type", "resource")).lower().rstrip("s")
            n_mid = int(node.get("module_id", 0))
            n_vid = int(node.get("version_id", 0))
            n_action = str(node.get("action", "new")).lower()
            field_ctx = node.get("field_context") or {}

            # CAS 1 : REUTILISATION D'UNE PUBLICATION EXISTANTE
            if n_action == "existing":
                selected_vid = node.get("selected_version_id")
                if not selected_vid:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Pour le module {n_type} #{n_mid}, vous avez choisi « Publication existante » mais aucune version n'a été sélectionnée.",
                    )
                selected_vid = int(selected_vid)

                # Charger la version publiée existante
                if n_type == "resource":
                    stmt = select(OrganizationResourceVersion).where(
                        OrganizationResourceVersion.id == selected_vid,
                        OrganizationResourceVersion.organization_id == organization_id,
                    )
                    pub_obj = (await db.execute(stmt)).scalars().first()
                elif n_type == "form":
                    stmt = select(OrganizationFormVersion).where(
                        OrganizationFormVersion.id == selected_vid,
                        OrganizationFormVersion.organization_id == organization_id,
                    )
                    pub_obj = (await db.execute(stmt)).scalars().first()
                elif n_type == "project":
                    stmt = select(OrganizationProjectVersion).where(
                        OrganizationProjectVersion.id == selected_vid,
                        OrganizationProjectVersion.organization_id == organization_id,
                    )
                    pub_obj = (await db.execute(stmt)).scalars().first()
                elif n_type == "campaign":
                    stmt = select(OrganizationCampaignVersion).where(
                        OrganizationCampaignVersion.id == selected_vid,
                        OrganizationCampaignVersion.organization_id == organization_id,
                    )
                    pub_obj = (await db.execute(stmt)).scalars().first()
                else:
                    pub_obj = None

                if not pub_obj:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Publication existante #{selected_vid} introuvable pour {n_type}.",
                    )

                reused_versions.append({
                    "module_type": n_type,
                    "module_id": n_mid,
                    "version_id": pub_obj.id,
                    "version_number": pub_obj.version_number,
                })
                return pub_obj

            # CAS 2 : CRÉATION D'UNE NOUVELLE PUBLICATION (NOUVEAU)
            # 1. Résoudre récursivement toutes les dépendances enfants d'abord !
            resolved_child_records = []
            child_version_map: Dict[str, Any] = {}

            for child_node in (node.get("dependencies") or []):
                child_pub = await _execute_sub_tree(child_node)
                c_type = str(child_node.get("module_type", "resource")).lower().rstrip("s")
                c_mid = int(child_node.get("module_id", 0))
                c_ctx = child_node.get("field_context") or {}

                # Enregistrement pour l'arbre de traçabilité Git-like
                resolved_child_records.append({
                    "module_type": c_type,
                    "module_id": c_mid,
                    "module_name": child_node.get("module_name"),
                    "module_code": child_node.get("module_code"),
                    "version_id": child_pub.id,
                    "version_number": child_pub.version_number,
                    "publication_number": getattr(child_pub, "publication_number", None),
                    "field_name": c_ctx.get("field_name"),
                    "field_label": c_ctx.get("field_label"),
                    "relation_type": c_ctx.get("relation_type"),
                    "action_taken": child_node.get("action", "new"),
                    "link_url": f"/dashboard/publications/{c_type}s/{c_mid}?version_id={child_pub.id}",
                })
                child_version_map[f"{c_type}_{c_mid}"] = child_pub

            # 2. Créer la version scellée du nœud courant en re-câblant vers child_version_map
            if n_type == "resource":
                # Charger la version native de travail
                stmt = select(OrganizationResourceVersion).options(
                    selectinload(OrganizationResourceVersion.organization_resource),
                    selectinload(OrganizationResourceVersion.items),
                ).where(
                    OrganizationResourceVersion.id == n_vid,
                    OrganizationResourceVersion.organization_id == organization_id,
                )
                orig_v = (await db.execute(stmt)).scalars().first()
                if not orig_v:
                    raise HTTPException(status_code=404, detail=f"Version ressource #{n_vid} introuvable.")

                # Calculer numéro de publication séquentiel
                all_v_stmt = select(OrganizationResourceVersion).where(
                    OrganizationResourceVersion.organization_resource_id == orig_v.organization_resource_id,
                    OrganizationResourceVersion.organization_id == organization_id,
                )
                existing_v = (await db.execute(all_v_stmt)).scalars().all()
                next_pnum, pub_vnum = cls._compute_next_publication_number(
                    base_version_number=orig_v.version_number,
                    existing_versions=existing_v,
                    origin_version_id=orig_v.id,
                )

                # Re-câblage des relations vers les publications d'enfants
                new_schema = copy.deepcopy(orig_v.schema_fields or [])
                for fld in new_schema:
                    if isinstance(fld, dict) and fld.get("type") == "relation":
                        target_rid = fld.get("target_resource_id")
                        c_pub = child_version_map.get(f"resource_{target_rid}")
                        if not c_pub and target_rid:
                            # Essayer par code ou correspondance
                            matched = next((c for k, c in child_version_map.items() if k.startswith("resource_")), None)
                            if matched:
                                c_pub = matched
                        if c_pub:
                            fld["target_resource_version_id"] = c_pub.id
                            fld["target_resource_version_number"] = c_pub.version_number

                # Mettre à jour settings avec l'historique complet des dépendances publiées directes
                new_settings = copy.deepcopy(orig_v.settings or {})
                new_settings["published_dependencies"] = resolved_child_records

                target_opv_id = root_opv_id or orig_v.organization_plugin_version_id
                pub_res_v = OrganizationResourceVersion(
                    organization_resource_id=orig_v.organization_resource_id,
                    organization_plugin_version_id=target_opv_id,
                    source_resource_version_id=orig_v.source_resource_version_id,
                    organization_id=organization_id,
                    downloaded_by_user_id=user_id or orig_v.downloaded_by_user_id,
                    version_number=pub_vnum,
                    status="published",
                    publication_number=next_pnum,
                    origin_version_id=orig_v.id,
                    published_at=datetime.now(timezone.utc),
                    description=orig_v.description,
                    changelog=orig_v.changelog or f"Publication #{next_pnum} issue de v{orig_v.version_number}",
                    schema_fields=new_schema,
                    metrics=dict(orig_v.metrics or {}),
                    template_governance=dict(orig_v.template_governance or {}),
                    settings=new_settings,
                    parameters=dict(orig_v.parameters or {}),
                )
                db.add(pub_res_v)
                await db.flush()

                # Duplication intégrale des enregistrements
                for itm in (orig_v.items or []):
                    db.add(OrganizationResourceItem(
                        organization_resource_id=itm.organization_resource_id,
                        organization_resource_version_id=pub_res_v.id,
                        organization_plugin_version_id=target_opv_id,
                        source_resource_item_id=itm.source_resource_item_id,
                        organization_id=organization_id,
                        downloaded_by_user_id=user_id or itm.downloaded_by_user_id,
                        data=copy.deepcopy(itm.data or {}),
                        settings=copy.deepcopy(itm.settings or {}),
                        parameters=copy.deepcopy(itm.parameters or {}),
                    ))
                await db.flush()
                created_versions.append({
                    "module_type": "resource",
                    "module_id": orig_v.organization_resource_id,
                    "version_id": pub_res_v.id,
                    "version_number": pub_res_v.version_number,
                })
                return pub_res_v

            elif n_type == "form":
                stmt = select(OrganizationFormVersion).options(
                    selectinload(OrganizationFormVersion.organization_form),
                    selectinload(OrganizationFormVersion.sections),
                    selectinload(OrganizationFormVersion.questions),
                ).where(
                    OrganizationFormVersion.id == n_vid,
                    OrganizationFormVersion.organization_id == organization_id,
                )
                orig_v = (await db.execute(stmt)).scalars().first()
                if not orig_v:
                    raise HTTPException(status_code=404, detail=f"Version formulaire #{n_vid} introuvable.")

                all_v_stmt = select(OrganizationFormVersion).where(
                    OrganizationFormVersion.organization_form_id == orig_v.organization_form_id,
                    OrganizationFormVersion.organization_id == organization_id,
                )
                existing_v = (await db.execute(all_v_stmt)).scalars().all()
                next_pnum, pub_vnum = cls._compute_next_publication_number(
                    base_version_number=orig_v.version_number,
                    existing_versions=existing_v,
                    origin_version_id=orig_v.id,
                )

                new_settings = copy.deepcopy(orig_v.settings or {})
                new_settings["published_dependencies"] = resolved_child_records

                target_opv_id = root_opv_id or orig_v.organization_plugin_version_id
                pub_form_v = OrganizationFormVersion(
                    organization_form_id=orig_v.organization_form_id,
                    organization_plugin_version_id=target_opv_id,
                    source_form_version_id=orig_v.source_form_version_id,
                    organization_id=organization_id,
                    downloaded_by_user_id=user_id or orig_v.downloaded_by_user_id,
                    version_number=pub_vnum,
                    status="published",
                    publication_number=next_pnum,
                    origin_version_id=orig_v.id,
                    published_at=datetime.now(timezone.utc),
                    description=orig_v.description,
                    form_type=orig_v.form_type or "standard",
                    template_governance=copy.deepcopy(orig_v.template_governance or {}),
                    settings=new_settings,
                    parameters=copy.deepcopy(orig_v.parameters or {}),
                )
                db.add(pub_form_v)
                await db.flush()

                # Duplication des sections
                old_sec_to_new_sec = {}
                sorted_secs = sorted(orig_v.sections or [], key=lambda s: (s.parent_section_id is not None, s.order_index))
                for s in sorted_secs:
                    new_sec = OrganizationFormSection(
                        organization_form_id=s.organization_form_id,
                        organization_form_version_id=pub_form_v.id,
                        organization_plugin_version_id=target_opv_id,
                        source_form_section_id=s.source_form_section_id,
                        organization_id=organization_id,
                        downloaded_by_user_id=user_id or s.downloaded_by_user_id,
                        parent_section_id=old_sec_to_new_sec.get(s.parent_section_id) if s.parent_section_id else None,
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
                        parameters=copy.deepcopy(s.parameters or {}),
                    )
                    db.add(new_sec)
                    await db.flush()
                    old_sec_to_new_sec[s.id] = new_sec.id

                # Duplication des questions avec re-câblage bindings & cross_form
                for q in (orig_v.questions or []):
                    b = copy.deepcopy(q.resource_binding or {})
                    r_id = b.get("resource_id") or b.get("target_resource_id")
                    if r_id:
                        c_pub = child_version_map.get(f"resource_{r_id}")
                        if c_pub:
                            b["resource_version_id"] = c_pub.id
                            b["target_resource_version_id"] = c_pub.id
                            b["resource_version_number"] = c_pub.version_number

                    q_s = copy.deepcopy(q.settings or {})
                    cross = q_s.get("cross_form_lookup")
                    if isinstance(cross, dict) and cross.get("target_form_id"):
                        c_pub = child_version_map.get(f"form_{cross.get('target_form_id')}")
                        if c_pub:
                            cross["target_form_version_id"] = c_pub.id
                            cross["target_form_version_number"] = c_pub.version_number

                    db.add(OrganizationFormQuestion(
                        organization_form_id=q.organization_form_id,
                        organization_form_version_id=pub_form_v.id,
                        organization_form_section_id=old_sec_to_new_sec.get(q.organization_form_section_id),
                        organization_plugin_version_id=target_opv_id,
                        source_form_question_id=q.source_form_question_id,
                        organization_id=organization_id,
                        downloaded_by_user_id=user_id or q.downloaded_by_user_id,
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
                        resource_binding=b,
                        formula_expression=q.formula_expression,
                        validation_rules=copy.deepcopy(q.validation_rules or {}),
                        dependency_rules=copy.deepcopy(q.dependency_rules or {}),
                        settings=q_s,
                        parameters=copy.deepcopy(q.parameters or {}),
                    ))

                await db.flush()
                created_versions.append({
                    "module_type": "form",
                    "module_id": orig_v.organization_form_id,
                    "version_id": pub_form_v.id,
                    "version_number": pub_form_v.version_number,
                })
                return pub_form_v

            elif n_type == "project":
                stmt = select(OrganizationProjectVersion).where(
                    OrganizationProjectVersion.id == n_vid,
                    OrganizationProjectVersion.organization_id == organization_id,
                )
                orig_v = (await db.execute(stmt)).scalars().first()
                if not orig_v:
                    raise HTTPException(status_code=404, detail=f"Version projet #{n_vid} introuvable.")

                all_v_stmt = select(OrganizationProjectVersion).where(
                    OrganizationProjectVersion.organization_project_id == orig_v.organization_project_id,
                    OrganizationProjectVersion.organization_id == organization_id,
                )
                existing_v = (await db.execute(all_v_stmt)).scalars().all()
                next_pnum, pub_vnum = cls._compute_next_publication_number(
                    base_version_number=orig_v.version_number,
                    existing_versions=existing_v,
                    origin_version_id=orig_v.id,
                )

                new_settings = copy.deepcopy(orig_v.settings or {})
                raw_attached = new_settings.get("attached_form_versions")
                if isinstance(raw_attached, dict):
                    new_attached = {}
                    for fid_str, fvid in raw_attached.items():
                        c_pub = child_version_map.get(f"form_{fid_str}")
                        new_attached[fid_str] = c_pub.id if c_pub else fvid
                    new_settings["attached_form_versions"] = new_attached
                elif isinstance(raw_attached, list):
                    new_attached_list = []
                    for item in raw_attached:
                        if isinstance(item, dict):
                            item_copy = copy.deepcopy(item)
                            fid = item.get("form_id") or item.get("id")
                            c_pub = child_version_map.get(f"form_{fid}") if fid else None
                            if c_pub:
                                item_copy["form_version_id"] = c_pub.id
                                item_copy["version_number"] = c_pub.version_number
                                item_copy["status"] = c_pub.status
                            new_attached_list.append(item_copy)
                        else:
                            new_attached_list.append(item)
                    new_settings["attached_form_versions"] = new_attached_list
                new_settings["published_dependencies"] = resolved_child_records

                target_opv_id = root_opv_id or orig_v.organization_plugin_version_id
                pub_proj_v = OrganizationProjectVersion(
                    organization_project_id=orig_v.organization_project_id,
                    organization_plugin_version_id=target_opv_id,
                    source_project_version_id=orig_v.source_project_version_id,
                    organization_id=organization_id,
                    downloaded_by_user_id=user_id or orig_v.downloaded_by_user_id,
                    version_number=pub_vnum,
                    status="published",
                    publication_number=next_pnum,
                    origin_version_id=orig_v.id,
                    published_at=datetime.now(timezone.utc),
                    description=orig_v.description,
                    changelog=orig_v.changelog or f"Publication #{next_pnum} issue de v{orig_v.version_number}",
                    metrics=copy.deepcopy(orig_v.metrics or {}),
                    template_governance=copy.deepcopy(orig_v.template_governance or {}),
                    settings=new_settings,
                    parameters=copy.deepcopy(orig_v.parameters or {}),
                )
                db.add(pub_proj_v)
                await db.flush()

                created_versions.append({
                    "module_type": "project",
                    "module_id": orig_v.organization_project_id,
                    "version_id": pub_proj_v.id,
                    "version_number": pub_proj_v.version_number,
                })
                return pub_proj_v

            elif n_type == "campaign":
                stmt = select(OrganizationCampaignVersion).where(
                    OrganizationCampaignVersion.id == n_vid,
                    OrganizationCampaignVersion.organization_id == organization_id,
                )
                orig_v = (await db.execute(stmt)).scalars().first()
                if not orig_v:
                    raise HTTPException(status_code=404, detail=f"Version campagne #{n_vid} introuvable.")

                all_v_stmt = select(OrganizationCampaignVersion).where(
                    OrganizationCampaignVersion.organization_campaign_id == orig_v.organization_campaign_id,
                    OrganizationCampaignVersion.organization_id == organization_id,
                )
                existing_v = (await db.execute(all_v_stmt)).scalars().all()
                next_pnum, pub_vnum = cls._compute_next_publication_number(
                    base_version_number=orig_v.version_number,
                    existing_versions=existing_v,
                    origin_version_id=orig_v.id,
                )

                new_projects = []
                for p_item in (orig_v.target_projects or []):
                    c_p = dict(p_item)
                    pid = c_p.get("id") or c_p.get("project_id")
                    if pid and f"project_{pid}" in child_version_map:
                        cp = child_version_map[f"project_{pid}"]
                        c_p["version_id"] = cp.id
                        c_p["project_version_id"] = cp.id
                        c_p["version_number"] = cp.version_number
                    new_projects.append(c_p)

                new_forms = []
                for f_item in (orig_v.target_forms or []):
                    c_f = dict(f_item)
                    fid = c_f.get("id") or c_f.get("form_id")
                    if fid and f"form_{fid}" in child_version_map:
                        cf = child_version_map[f"form_{fid}"]
                        c_f["version_id"] = cf.id
                        c_f["form_version_id"] = cf.id
                        c_f["version_number"] = cf.version_number
                    new_forms.append(c_f)

                new_settings = copy.deepcopy(orig_v.settings or {})
                new_settings["published_dependencies"] = resolved_child_records

                target_opv_id = root_opv_id or orig_v.organization_plugin_version_id
                pub_camp_v = OrganizationCampaignVersion(
                    organization_campaign_id=orig_v.organization_campaign_id,
                    organization_plugin_version_id=target_opv_id,
                    source_campaign_version_id=orig_v.source_campaign_version_id,
                    organization_id=organization_id,
                    downloaded_by_user_id=user_id or orig_v.downloaded_by_user_id,
                    version_number=pub_vnum,
                    status="published",
                    publication_number=next_pnum,
                    origin_version_id=orig_v.id,
                    published_at=datetime.now(timezone.utc),
                    description=orig_v.description,
                    objectives=orig_v.objectives,
                    targets=copy.deepcopy(orig_v.targets or {}),
                    start_date=orig_v.start_date,
                    end_date=orig_v.end_date,
                    budget=orig_v.budget,
                    currency=orig_v.currency,
                    target_forms=new_forms,
                    target_projects=new_projects,
                    changelog=orig_v.changelog or f"Publication #{next_pnum} issue de v{orig_v.version_number}",
                    metrics=copy.deepcopy(orig_v.metrics or {}),
                    template_governance=copy.deepcopy(orig_v.template_governance or {}),
                    settings=new_settings,
                    parameters=copy.deepcopy(orig_v.parameters or {}),
                )
                db.add(pub_camp_v)
                await db.flush()

                created_versions.append({
                    "module_type": "campaign",
                    "module_id": orig_v.organization_campaign_id,
                    "version_id": pub_camp_v.id,
                    "version_number": pub_camp_v.version_number,
                })
                return pub_camp_v

            raise HTTPException(status_code=400, detail=f"Type de module inconnu : {n_type}")

        # Lancement de l'exécution à partir du nœud racine du plan
        plan_root = dict(tree_plan)
        # Forcer le type et les identifiants racines s'ils ne sont pas spécifiés
        plan_root["module_type"] = plan_root.get("module_type") or norm_type
        plan_root["module_id"] = plan_root.get("module_id") or module_id
        plan_root["version_id"] = plan_root.get("version_id") or version_id
        plan_root["action"] = "new"  # La racine est toujours une nouvelle publication

        root_pub_obj = await _execute_sub_tree(plan_root)
        await db.commit()

        return {
            "success": True,
            "message": f"Publication scellée {root_pub_obj.version_number} créée avec succès.",
            "root_published_version": {
                "id": root_pub_obj.id,
                "version_number": root_pub_obj.version_number,
                "publication_number": getattr(root_pub_obj, "publication_number", None),
                "module_type": norm_type,
                "module_id": module_id,
            },
            "summary": {
                "created_count": len(created_versions),
                "reused_count": len(reused_versions),
                "created_versions": created_versions,
                "reused_versions": reused_versions,
            },
        }
