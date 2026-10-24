"""
Resource Binding Service for SenWiseTool Projects & Dynamic Forms.

Handles:
1. Dynamic Options Lookup: Querying resource items for question dropdowns/selects.
2. Universal Resource Projection & Generation: Projecting form sections & questions
   from reference resources (e.g. EUDR norm, ISO standards, custom registries).
3. Relation Resolution: Resolving foreign keys / links between multiple resources
   (e.g. Chapters resource -> Sections resource -> Requirements resource).
4. Submission-to-Resource Sync: Automatically feeding submitted answers into target resources.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import re
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, delete, func
from fastapi import HTTPException, status

from app.models.resource import Resource, ResourceItem
from app.models.project.form import Form, FormStatus
from app.models.project.form_section import FormSection
from app.models.project.form_question import FormQuestion, QuestionType

logger = logging.getLogger("app.services.project.resource_binding")


def re_slug(text: str) -> str:
    """Transforme un libellé en slug propre majuscule."""
    slug = re.sub(r"[^a-zA-Z0-9_]+", "_", text).strip("_").upper()
    return slug[:40] if slug else "ITEM"


class ResourceBindingService:

    @classmethod
    async def get_question_dynamic_options(
        cls,
        db: AsyncSession,
        resource_id: Optional[int],
        resource_code: Optional[str],
        organization_id: Optional[int],
        label_field: str = "name",
        value_field: str = "id",
        secondary_label_field: Optional[str] = None,
        filter_criteria: Optional[Dict[str, Any]] = None,
        limit: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        Récupère les options dynamiques depuis les éléments d'une ressource enregistrée.
        """
        stmt = select(Resource)
        if resource_id:
            stmt = stmt.where(Resource.id == resource_id)
        elif resource_code:
            stmt = stmt.where(
                and_(
                    Resource.code == resource_code,
                    (Resource.organization_id == organization_id) | (Resource.is_shared == True)
                )
            )
        else:
            return []

        res = await db.execute(stmt)
        resource = res.scalars().first()
        if not resource:
            return []

        # Récupération des items de la ressource
        items_stmt = select(ResourceItem).where(ResourceItem.resource_id == resource.id).limit(limit)
        items_res = await db.execute(items_stmt)
        items = items_res.scalars().all()

        options = []
        for item in items:
            item_data = item.data or {}

            # Filtrage éventuel
            if filter_criteria:
                match = True
                for f_key, f_val in filter_criteria.items():
                    if item_data.get(f_key) != f_val:
                        match = False
                        break
                if not match:
                    continue

            # Extraction de la valeur
            val = item_data.get(value_field) if value_field != "id" else item.id

            # Extraction du libellé
            lbl = item_data.get(label_field) or item_data.get("name") or item_data.get("title") or f"Item #{item.id}"
            if secondary_label_field and secondary_label_field in item_data:
                lbl = f"{lbl} ({item_data[secondary_label_field]})"

            options.append({
                "label": str(lbl),
                "value": val,
                "item_id": item.id,
                "data_snapshot": item_data,
            })

        return options

    @classmethod
    async def _resolve_relation_mappings(
        cls,
        db: AsyncSession,
        resource: Resource,
        fields_to_check: List[str]
    ) -> Dict[str, Dict[Any, str]]:
        """
        Pour chaque champ qui est une relation vers une autre ressource,
        récupère la table de correspondance id -> libellé.
        """
        mappings: Dict[str, Dict[Any, str]] = {}
        schema_fields = resource.schema_fields or []

        for f_name in fields_to_check:
            if not f_name:
                continue
            field_def = next((f for f in schema_fields if f.get("name") == f_name), None)
            if not field_def:
                continue

            # Vérifier si c'est un champ de type 'relation'
            if field_def.get("type") == "relation":
                target_res_id = field_def.get("target_resource_id")
                target_res_code = field_def.get("target_resource_code")
                display_field = field_def.get("display_field") or "name"

                target_stmt = select(Resource)
                if target_res_id:
                    target_stmt = target_stmt.where(Resource.id == target_res_id)
                elif target_res_code:
                    target_stmt = target_stmt.where(Resource.code == target_res_code)
                else:
                    continue

                target_res = (await db.execute(target_stmt)).scalars().first()
                if not target_res:
                    continue

                # Charger les items de la ressource cible
                t_items_stmt = select(ResourceItem).where(ResourceItem.resource_id == target_res.id)
                t_items = (await db.execute(t_items_stmt)).scalars().all()

                item_map: Dict[Any, str] = {}
                for t_item in t_items:
                    t_data = t_item.data or {}
                    lbl = (
                        t_data.get(display_field)
                        or t_data.get("title")
                        or t_data.get("name")
                        or t_data.get("label")
                        or f"#{t_item.id}"
                    )
                    item_map[t_item.id] = str(lbl)
                    item_map[str(t_item.id)] = str(lbl)

                mappings[f_name] = item_map

        return mappings

    @classmethod
    async def build_projected_structure(
        cls,
        db: AsyncSession,
        resource_id: int,
        chapter_field: str = "chapter",
        section_field: Optional[str] = None,
        code_field: str = "criterion_code",
        title_field: str = "criterion_title",
        help_field: Optional[str] = "verification_guideline",
        type_field: Optional[str] = None,
        critical_field: Optional[str] = None,
        target_group_field: Optional[str] = None,
        target_group_filter: Optional[str] = "all",
        default_question_type: str = "audit_compliance",
        unique_id_field: str = "id",
        response_type_mode: str = "fixed",
        structure_mode: str = "auto",
        block_resource_id: Optional[int] = None,
        block_title_field: Optional[str] = "name",
        block_code_field: Optional[str] = "code",
        section_resource_id: Optional[int] = None,
        section_title_field: Optional[str] = "name",
        section_code_field: Optional[str] = "code",
        section_block_link_field: Optional[str] = None,
        question_section_link_field: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Construit l'arborescence hiérarchique projetée (Blocs -> Sections -> Questions)
        avec résolution des relations multi-ressources et filtrage.
        """
        res_stmt = select(Resource).where(Resource.id == resource_id)
        resource = (await db.execute(res_stmt)).scalars().first()
        if not resource:
            raise HTTPException(status_code=404, detail="Ressource source introuvable.")

        # Chargement de la ressource Section liée (si spécifiée)
        sections_map: Dict[Any, Dict[str, Any]] = {}
        if section_resource_id:
            sec_stmt = select(ResourceItem).where(ResourceItem.resource_id == section_resource_id)
            sec_items = (await db.execute(sec_stmt)).scalars().all()
            for s_item in sec_items:
                s_data = s_item.data or {}
                sections_map[s_item.id] = s_data
                sections_map[str(s_item.id)] = s_data
                s_code = s_data.get(section_code_field or "code") or s_data.get("id")
                if s_code:
                    sections_map[str(s_code)] = s_data

        # Chargement de la ressource Bloc liée (si spécifiée)
        blocks_map: Dict[Any, Dict[str, Any]] = {}
        if block_resource_id:
            b_stmt = select(ResourceItem).where(ResourceItem.resource_id == block_resource_id)
            b_items = (await db.execute(b_stmt)).scalars().all()
            for b_item in b_items:
                b_data = b_item.data or {}
                blocks_map[b_item.id] = b_data
                blocks_map[str(b_item.id)] = b_data
                b_code = b_data.get(block_code_field or "code") or b_data.get("id")
                if b_code:
                    blocks_map[str(b_code)] = b_data

        # Résolution des champs de relation locaux pour rétrocompatibilité
        check_fields = [chapter_field]
        if section_field:
            check_fields.append(section_field)
        relation_maps = await cls._resolve_relation_mappings(db, resource, check_fields)

        # Récupération de tous les items questions ordonnés
        items_stmt = select(ResourceItem).where(ResourceItem.resource_id == resource.id).order_by(ResourceItem.id)
        items = (await db.execute(items_stmt)).scalars().all()

        chapters_tree: Dict[str, Dict[str, Any]] = {}
        filtered_items_count = 0
        valid_types = {t.value for t in QuestionType}

        for idx, item in enumerate(items):
            data = item.data or {}

            # Filtrage par groupe cible si configuré
            if target_group_field and target_group_filter and target_group_filter != "all":
                item_target = str(data.get(target_group_field, "")).strip().lower()
                filter_val = target_group_filter.strip().lower()
                if item_target and item_target not in ("all", "tous", filter_val):
                    continue

            filtered_items_count += 1

            # Résolution selon le mode relationnel chaîné ou standard
            if section_resource_id and (question_section_link_field or section_field):
                sec_ref = data.get(question_section_link_field or section_field)
                sec_data = sections_map.get(sec_ref) if sec_ref is not None else None
                
                # Titre de la Section (Niveau 2)
                if sec_data:
                    sub_title = str(
                        sec_data.get(section_title_field or "name")
                        or sec_data.get("title")
                        or sec_data.get("label")
                        or f"Section #{sec_ref}"
                    )
                    sub_code = str(sec_data.get(section_code_field or "code") or f"SEC_{sec_ref}")
                    has_subsection = True

                    # Résolution du Bloc (Niveau 1) depuis la Section
                    if block_resource_id and section_block_link_field:
                        blk_ref = sec_data.get(section_block_link_field)
                        blk_data = blocks_map.get(blk_ref) if blk_ref is not None else None
                        if blk_data:
                            chap_title = str(
                                blk_data.get(block_title_field or "name")
                                or blk_data.get("title")
                                or blk_data.get("label")
                                or f"Bloc #{blk_ref}"
                            )
                            chap_code = str(blk_data.get(block_code_field or "code") or f"BLOC_{blk_ref}")
                        else:
                            chap_title = str(blk_ref) if blk_ref is not None else "Bloc Général"
                            chap_code = f"BLOC_{re_slug(chap_title)}"
                    else:
                        raw_c = sec_data.get(chapter_field or "block") or "Bloc Général"
                        chap_title = str(raw_c)
                        chap_code = f"BLOC_{re_slug(chap_title)}"
                else:
                    sub_title = str(sec_ref) if sec_ref is not None else "Section Générale"
                    sub_code = f"SEC_{re_slug(sub_title)}"
                    has_subsection = bool(sec_ref)
                    chap_title = "Bloc Général"
                    chap_code = "BLOC_GENERAL"

                chap_slug = chap_code
                sub_slug = sub_code
            else:
                # Mode mono-ressource standard
                raw_chap = data.get(chapter_field)
                if chapter_field in relation_maps and raw_chap in relation_maps[chapter_field]:
                    chap_title = relation_maps[chapter_field][raw_chap]
                else:
                    chap_title = str(raw_chap) if raw_chap is not None else "Chapitre Général"

                chap_slug = f"CHAP_{re_slug(chap_title)}"

                has_subsection = bool(section_field and data.get(section_field))
                sub_title = ""
                sub_slug = ""

                if has_subsection:
                    raw_sub = data.get(section_field)
                    if section_field in relation_maps and raw_sub in relation_maps[section_field]:
                        sub_title = relation_maps[section_field][raw_sub]
                    else:
                        sub_title = str(raw_sub)

                    sub_slug = f"SEC_{re_slug(sub_title)}"

            # Création du nœud Chapitre / Bloc dans l'arbre
            if chap_slug not in chapters_tree:
                chapters_tree[chap_slug] = {
                    "code": chap_slug,
                    "title": chap_title,
                    "order_index": len(chapters_tree) + 1,
                    "subsections": {},
                    "direct_questions": [],
                }

            # Création du nœud Sous-section / Section dans l'arbre
            if has_subsection and sub_slug not in chapters_tree[chap_slug]["subsections"]:
                chapters_tree[chap_slug]["subsections"][sub_slug] = {
                    "code": sub_slug,
                    "title": sub_title,
                    "order_index": len(chapters_tree[chap_slug]["subsections"]) + 1,
                    "questions": [],
                }

            # Résolution de la Question / Critère
            q_uid = str(data.get(unique_id_field) or item.id)
            q_code = str(data.get(code_field) or f"Q_{chap_slug}_{idx + 1}")
            q_label = str(data.get(title_field) or f"Critère #{idx + 1}")
            q_help = str(data.get(help_field) or "") if help_field else ""

            # Détermination du type de question (dynamique ou fixe)
            q_type = default_question_type
            if response_type_mode in ("dynamic", "dynamic_from_field") and type_field and data.get(type_field):
                cand = str(data.get(type_field)).strip().lower()
                if cand in valid_types:
                    q_type = cand
            elif response_type_mode == "fixed":
                q_type = default_question_type

            # Détermination de la criticité
            is_crit = False
            if critical_field and critical_field in data:
                raw_crit = data.get(critical_field)
                is_crit = raw_crit in (True, "true", "True", "1", 1, "majeur", "critical", "obligatoire")

            target_val = data.get(target_group_field) if target_group_field else None

            question_dict = {
                "code": q_code,
                "label": q_label,
                "help_text": q_help,
                "question_type": q_type,
                "is_required": True,
                "is_critical": is_crit,
                "weight": 1.0,
                "target_group": target_val,
                "resource_binding": {
                    "source_resource_id": resource.id,
                    "source_item_id": item.id,
                    "unique_id_field": unique_id_field,
                    "unique_id_value": q_uid,
                    "label_field": title_field,
                    "label_value": q_label,
                    "target_group": target_val,
                    "response_type_mode": response_type_mode,
                },
            }

            if has_subsection:
                q_idx = len(chapters_tree[chap_slug]["subsections"][sub_slug]["questions"]) + 1
                question_dict["order_index"] = q_idx
                chapters_tree[chap_slug]["subsections"][sub_slug]["questions"].append(question_dict)
            else:
                q_idx = len(chapters_tree[chap_slug]["direct_questions"]) + 1
                question_dict["order_index"] = q_idx
                chapters_tree[chap_slug]["direct_questions"].append(question_dict)

        # Transformation en liste plate pour la réponse
        result_chapters = []
        total_questions = 0

        for chap_key, chap_val in chapters_tree.items():
            subs_list = list(chap_val["subsections"].values())
            for s in subs_list:
                total_questions += len(s["questions"])
            total_questions += len(chap_val["direct_questions"])

            result_chapters.append({
                "code": chap_val["code"],
                "title": chap_val["title"],
                "order_index": chap_val["order_index"],
                "subsections": subs_list,
                "direct_questions": chap_val["direct_questions"],
            })

        return {
            "resource_id": resource.id,
            "resource_name": resource.name,
            "resource_code": resource.code,
            "total_items": len(items),
            "filtered_items_count": filtered_items_count,
            "chapters_count": len(result_chapters),
            "questions_count": total_questions,
            "chapters": result_chapters,
        }

    @classmethod
    async def preview_form_projection(
        cls,
        db: AsyncSession,
        resource_id: int,
        chapter_field: str = "chapter",
        section_field: Optional[str] = None,
        code_field: str = "criterion_code",
        title_field: str = "criterion_title",
        help_field: Optional[str] = "verification_guideline",
        type_field: Optional[str] = None,
        critical_field: Optional[str] = None,
        target_group_field: Optional[str] = None,
        target_group_filter: Optional[str] = "all",
        default_question_type: str = "audit_compliance",
        unique_id_field: str = "id",
        response_type_mode: str = "fixed",
        structure_mode: str = "auto",
        block_resource_id: Optional[int] = None,
        block_title_field: Optional[str] = "name",
        block_code_field: Optional[str] = "code",
        section_resource_id: Optional[int] = None,
        section_title_field: Optional[str] = "name",
        section_code_field: Optional[str] = "code",
        section_block_link_field: Optional[str] = None,
        question_section_link_field: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Prévisualise la structure sans persister en base.
        """
        return await cls.build_projected_structure(
            db=db,
            resource_id=resource_id,
            chapter_field=chapter_field,
            section_field=section_field,
            code_field=code_field,
            title_field=title_field,
            help_field=help_field,
            type_field=type_field,
            critical_field=critical_field,
            target_group_field=target_group_field,
            target_group_filter=target_group_filter,
            default_question_type=default_question_type,
            unique_id_field=unique_id_field,
            response_type_mode=response_type_mode,
            structure_mode=structure_mode,
            block_resource_id=block_resource_id,
            block_title_field=block_title_field,
            block_code_field=block_code_field,
            section_resource_id=section_resource_id,
            section_title_field=section_title_field,
            section_code_field=section_code_field,
            section_block_link_field=section_block_link_field,
            question_section_link_field=question_section_link_field,
        )

    @classmethod
    async def generate_and_persist_form_from_resource(
        cls,
        db: AsyncSession,
        form_id: int,
        resource_id: int,
        chapter_field: str = "chapter",
        section_field: Optional[str] = None,
        code_field: str = "criterion_code",
        title_field: str = "criterion_title",
        help_field: Optional[str] = "verification_guideline",
        type_field: Optional[str] = None,
        critical_field: Optional[str] = None,
        target_group_field: Optional[str] = None,
        target_group_filter: Optional[str] = "all",
        default_question_type: str = "audit_compliance",
        unique_id_field: str = "id",
        response_type_mode: str = "fixed",
        structure_mode: str = "auto",
        block_resource_id: Optional[int] = None,
        block_title_field: Optional[str] = "name",
        block_code_field: Optional[str] = "code",
        section_resource_id: Optional[int] = None,
        section_title_field: Optional[str] = "name",
        section_code_field: Optional[str] = "code",
        section_block_link_field: Optional[str] = None,
        question_section_link_field: Optional[str] = None,
        sanctuarize_generated: bool = True,
        replace_existing: bool = True,
        target_parent_section_id: Optional[int] = None,
        allow_tenant_select_requirements: bool = False,
        is_super_admin: bool = False,
    ) -> Form:
        """
        Génère et insère concrètement les sections, sous-sections et questions
        dans le formulaire depuis la ressource.
        """
        form_stmt = select(Form).where(Form.id == form_id)
        form = (await db.execute(form_stmt)).scalars().first()
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")

        if form.status == FormStatus.PUBLISHED.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ce formulaire est publié et verrouillé. Créez une nouvelle version pour régénérer sa structure."
            )

        if target_parent_section_id is not None:
            parent_check = (await db.execute(
                select(FormSection).where(FormSection.id == target_parent_section_id, FormSection.form_id == form_id)
            )).scalar_one_or_none()
            if not parent_check:
                raise HTTPException(status_code=404, detail="Section parente cible introuvable dans ce formulaire.")

        # Construction de l'arborescence projetée
        projection = await cls.build_projected_structure(
            db=db,
            resource_id=resource_id,
            chapter_field=chapter_field,
            section_field=section_field,
            code_field=code_field,
            title_field=title_field,
            help_field=help_field,
            type_field=type_field,
            critical_field=critical_field,
            target_group_field=target_group_field,
            target_group_filter=target_group_filter,
            default_question_type=default_question_type,
        )

        chapters = projection["chapters"]
        if not chapters:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucun élément trouvé dans la ressource avec les critères et filtres spécifiés."
            )

        # Nettoyage si remplacement demandé
        if replace_existing:
            # Supprimer les sections racines existantes (la cascade supprime sous-sections et questions)
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

        # Insertion des chapitres, sous-sections et questions
        created_sections_count = 0
        created_questions_count = 0

        for chap_data in chapters:
            parent_sec = FormSection(
                form_id=form_id,
                parent_section_id=target_parent_section_id,
                title=chap_data["title"],
                code=chap_data["code"],
                order_index=base_order + chap_data["order_index"],
                is_sanctuarized=sanctuarize_generated,
                allow_custom_questions=True,
                is_repeatable=False,
            )
            db.add(parent_sec)
            await db.flush()
            created_sections_count += 1

            # Questions directes au niveau du chapitre
            for q_data in chap_data.get("direct_questions", []):
                q = FormQuestion(
                    section_id=parent_sec.id,
                    code=q_data["code"],
                    label=q_data["label"],
                    help_text=q_data["help_text"],
                    question_type=q_data["question_type"],
                    order_index=q_data["order_index"],
                    is_required=q_data["is_required"],
                    is_sanctuarized=sanctuarize_generated,
                    is_critical=q_data["is_critical"],
                    weight=q_data["weight"],
                    resource_binding=q_data.get("resource_binding") or {},
                )
                db.add(q)
                created_questions_count += 1

            # Sous-sections de niveau 2
            for sub_data in chap_data.get("subsections", []):
                child_sec = FormSection(
                    form_id=form_id,
                    parent_section_id=parent_sec.id,
                    title=sub_data["title"],
                    code=sub_data["code"],
                    order_index=sub_data["order_index"],
                    is_sanctuarized=sanctuarize_generated,
                    allow_custom_questions=True,
                    is_repeatable=False,
                )
                db.add(child_sec)
                await db.flush()
                created_sections_count += 1

                for q_data in sub_data.get("questions", []):
                    q = FormQuestion(
                        section_id=child_sec.id,
                        code=q_data["code"],
                        label=q_data["label"],
                        help_text=q_data["help_text"],
                        question_type=q_data["question_type"],
                        order_index=q_data["order_index"],
                        is_required=q_data["is_required"],
                        is_sanctuarized=sanctuarize_generated,
                        is_critical=q_data["is_critical"],
                        weight=q_data["weight"],
                        resource_binding=q_data.get("resource_binding") or {},
                    )
                    db.add(q)
                    created_questions_count += 1

        # Mise à jour de la configuration de liaison du formulaire
        current_binding = form.resource_binding_config or {}
        current_binding.update({
            "source_mode": "resource_driven",
            "resource_id": resource_id,
            "resource_name": projection["resource_name"],
            "resource_code": projection["resource_code"],
            "mapping": {
                "chapter_field": chapter_field,
                "section_field": section_field,
                "code_field": code_field,
                "title_field": title_field,
                "help_field": help_field,
                "type_field": type_field,
                "critical_field": critical_field,
                "target_group_field": target_group_field,
                "target_group_filter": target_group_filter,
                "default_question_type": default_question_type,
            },
            "allow_tenant_select_requirements": allow_tenant_select_requirements,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections_count": created_sections_count,
            "questions_count": created_questions_count,
        })
        form.resource_binding_config = dict(current_binding)

        # Enregistrement dans les paramètres de gouvernance
        current_settings = dict(form.settings or {})
        governance = dict(current_settings.get("governance", {}))
        governance["allow_tenant_select_requirements"] = allow_tenant_select_requirements
        current_settings["governance"] = governance
        form.settings = current_settings

        await db.commit()
        await db.refresh(form)

        # Rechargement complet avec arborescence
        from app.services.project.form_service import FormService
        return await FormService.get_form_by_id(db, form_id, form.organization_id, is_super_admin)

    @classmethod
    async def project_form_from_resource(
        cls,
        db: AsyncSession,
        resource_id: int,
        group_by_field: str = "chapter",
        title_field: str = "criterion_title",
        code_field: str = "criterion_code",
        type_field: str = "verification_type",
    ) -> List[Dict[str, Any]]:
        """
        Méthode de compatibilité descendante.
        """
        proj = await cls.build_projected_structure(
            db=db,
            resource_id=resource_id,
            chapter_field=group_by_field,
            title_field=title_field,
            code_field=code_field,
            type_field=type_field,
        )
        # Convertir en liste de sections avec questions
        compat_sections = []
        for chap in proj["chapters"]:
            questions = list(chap.get("direct_questions", []))
            for sub in chap.get("subsections", []):
                questions.extend(sub.get("questions", []))
            compat_sections.append({
                "title": chap["title"],
                "code": chap["code"],
                "order_index": chap["order_index"],
                "questions": questions,
            })
        return compat_sections
