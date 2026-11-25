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
from sqlalchemy.orm import selectinload
from fastapi import HTTPException, status

from app.models.resource import Resource, ResourceItem
from app.models.resource.resource import ResourceStatus
from app.models.resource.resource_version import ResourceVersion, ResourceVersionStatus
from app.models.form import Form, FormStatus, FormSection, FormQuestion, QuestionType
from app.models.form.form_version import FormVersion, FormVersionStatus

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
        resource_version_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Récupère les options dynamiques depuis les éléments d'une ressource enregistrée.
        """
        stmt = select(Resource).options(selectinload(Resource.versions))
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

        # Règle stricte : Les ressources en brouillon ne peuvent pas être exploitées
        pub_ver = None
        if resource_version_id:
            pub_ver = next((v for v in (resource.versions or []) if v.id == resource_version_id and v.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active")), None)
        if not pub_ver:
            pub_ver = next((v for v in (resource.versions or []) if v.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active")), None)
        if not pub_ver and resource.status in (ResourceStatus.ACTIVE.value, ResourceStatus.PUBLISHED.value) and resource.versions:
            pub_ver = resource.versions[0]
        if not pub_ver or resource.status == ResourceStatus.DRAFT.value:
            logger.warning("Accès bloqué aux options d'une ressource en brouillon (#%s - %s)", resource.id, resource.name)
            return []

        # Récupération des items de la ressource (scopés à la version si disponible)
        items_stmt = select(ResourceItem).where(ResourceItem.resource_id == resource.id)
        if pub_ver and hasattr(ResourceItem, "resource_version_id") and pub_ver.id:
            items_stmt = items_stmt.where(ResourceItem.resource_version_id == pub_ver.id)
        items_stmt = items_stmt.limit(limit)
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
        resource_version_id: Optional[int] = None,
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
        is_required_default: bool = True,
        unique_id_field: str = "id",
        response_type_mode: str = "fixed",
        structure_mode: str = "auto",
        block_resource_id: Optional[int] = None,
        block_resource_version_id: Optional[int] = None,
        block_title_field: Optional[str] = "name",
        block_code_field: Optional[str] = "code",
        block_description_field: Optional[str] = None,
        section_resource_id: Optional[int] = None,
        section_resource_version_id: Optional[int] = None,
        section_title_field: Optional[str] = "name",
        section_code_field: Optional[str] = "code",
        section_description_field: Optional[str] = None,
        section_block_link_field: Optional[str] = None,
        question_section_link_field: Optional[str] = None,
        selected_item_ids: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """
        Construit l'arborescence hiérarchique projetée (Blocs -> Sections -> Questions)
        avec résolution des relations multi-ressources et filtrage.
        """
        res_stmt = select(Resource).options(selectinload(Resource.versions)).where(Resource.id == resource_id)
        resource = (await db.execute(res_stmt)).scalars().first()
        if not resource:
            raise HTTPException(status_code=404, detail="Ressource source introuvable.")

        # Vérification stricte : Seules les versions publiées de ressources sont exploitables
        published_version: Optional[ResourceVersion] = None
        if resource_version_id:
            for v in (resource.versions or []):
                if v.id == resource_version_id:
                    if v.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active"):
                        published_version = v
                    else:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"La version #{resource_version_id} de la ressource « {resource.name} » n'est pas publiée. Seules les versions publiées peuvent être exploitées."
                        )
                    break
        if not published_version:
            for v in (resource.versions or []):
                if v.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active"):
                    published_version = v
                    break
        if not published_version and resource.status in (ResourceStatus.ACTIVE.value, ResourceStatus.PUBLISHED.value) and resource.versions:
            published_version = resource.versions[0]

        if not published_version or resource.status == ResourceStatus.DRAFT.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"La ressource « {resource.name} » est en brouillon. Seules les versions de ressources publiées peuvent être exploitées dans un formulaire. Veuillez publier cette ressource au préalable."
            )

        # Chargement de la ressource Section liée (si spécifiée)
        sections_map: Dict[Any, Dict[str, Any]] = {}
        if section_resource_id:
            sec_res_stmt = select(Resource).options(selectinload(Resource.versions)).where(Resource.id == section_resource_id)
            sec_res = (await db.execute(sec_res_stmt)).scalars().first()
            if not sec_res:
                raise HTTPException(status_code=404, detail="Ressource de section liée introuvable.")
            sec_pub_ver = None
            if section_resource_version_id:
                sec_pub_ver = next((v for v in (sec_res.versions or []) if v.id == section_resource_version_id and v.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active")), None)
            if not sec_pub_ver:
                sec_pub_ver = next((v for v in (sec_res.versions or []) if v.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active")), None)
            if not sec_pub_ver and sec_res.status in (ResourceStatus.ACTIVE.value, ResourceStatus.PUBLISHED.value) and sec_res.versions:
                sec_pub_ver = sec_res.versions[0]
            if not sec_pub_ver or sec_res.status == ResourceStatus.DRAFT.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La ressource de section « {sec_res.name} » est en brouillon. Seules les versions de ressources publiées peuvent être exploitées."
                )

            # Charger TOUS les items de la ressource Section pour mapper les identifiants inter-versions
            sec_stmt = select(ResourceItem).where(ResourceItem.resource_id == section_resource_id)
            sec_all_items = (await db.execute(sec_stmt)).scalars().all()

            target_sec_items = [it for it in sec_all_items if sec_pub_ver and it.resource_version_id == sec_pub_ver.id]
            if not target_sec_items:
                target_sec_items = sec_all_items

            by_sec_code: Dict[str, Dict[str, Any]] = {}
            by_sec_num: Dict[str, Dict[str, Any]] = {}
            for s_it in target_sec_items:
                s_data = s_it.data or {}
                sections_map[s_it.id] = s_data
                sections_map[str(s_it.id)] = s_data
                s_c = s_data.get("code") or s_data.get(section_code_field or "code")
                s_n = s_data.get("numero") or s_data.get("num")
                if s_c is not None:
                    by_sec_code[str(s_c)] = s_data
                    sections_map[str(s_c)] = s_data
                if s_n is not None:
                    by_sec_num[str(s_n)] = s_data
                    sections_map[str(s_n)] = s_data

            # Réconcilier les items des autres versions vers la version sélectionnée
            other_sec_items = [it for it in sec_all_items if it not in target_sec_items]
            for o_it in other_sec_items:
                o_data = o_it.data or {}
                o_c = o_data.get("code") or o_data.get(section_code_field or "code")
                o_n = o_data.get("numero") or o_data.get("num")
                matched_data = (
                    (by_sec_code.get(str(o_c)) if o_c is not None else None)
                    or (by_sec_num.get(str(o_n)) if o_n is not None else None)
                    or o_data
                )
                sections_map[o_it.id] = matched_data
                sections_map[str(o_it.id)] = matched_data

        # Chargement de la ressource Bloc liée (si spécifiée)
        blocks_map: Dict[Any, Dict[str, Any]] = {}
        if block_resource_id:
            block_res_stmt = select(Resource).options(selectinload(Resource.versions)).where(Resource.id == block_resource_id)
            block_res = (await db.execute(block_res_stmt)).scalars().first()
            if not block_res:
                raise HTTPException(status_code=404, detail="Ressource de chapitre liée introuvable.")
            block_pub_ver = None
            if block_resource_version_id:
                block_pub_ver = next((v for v in (block_res.versions or []) if v.id == block_resource_version_id and v.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active")), None)
            if not block_pub_ver:
                block_pub_ver = next((v for v in (block_res.versions or []) if v.status in (ResourceVersionStatus.PUBLISHED.value, "published", ResourceVersionStatus.ACTIVE.value, "active")), None)
            if not block_pub_ver and block_res.status in (ResourceStatus.ACTIVE.value, ResourceStatus.PUBLISHED.value) and block_res.versions:
                block_pub_ver = block_res.versions[0]
            if not block_pub_ver or block_res.status == ResourceStatus.DRAFT.value:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"La ressource de chapitre « {block_res.name} » est en brouillon. Seules les versions de ressources publiées peuvent être exploitées."
                )

            # Charger TOUS les items de la ressource Chapitre/Bloc pour mapper les identifiants inter-versions
            b_stmt = select(ResourceItem).where(ResourceItem.resource_id == block_resource_id)
            b_all_items = (await db.execute(b_stmt)).scalars().all()

            target_b_items = [it for it in b_all_items if block_pub_ver and it.resource_version_id == block_pub_ver.id]
            if not target_b_items:
                target_b_items = b_all_items

            by_b_code: Dict[str, Dict[str, Any]] = {}
            by_b_num: Dict[str, Dict[str, Any]] = {}
            for b_it in target_b_items:
                b_data = b_it.data or {}
                blocks_map[b_it.id] = b_data
                blocks_map[str(b_it.id)] = b_data
                b_c = b_data.get("code") or b_data.get(block_code_field or "code")
                b_n = b_data.get("numero") or b_data.get("num")
                if b_c is not None:
                    by_b_code[str(b_c)] = b_data
                    blocks_map[str(b_c)] = b_data
                if b_n is not None:
                    by_b_num[str(b_n)] = b_data
                    blocks_map[str(b_n)] = b_data

            # Réconcilier les items des autres versions vers la version sélectionnée
            other_b_items = [it for it in b_all_items if it not in target_b_items]
            for o_it in other_b_items:
                o_data = o_it.data or {}
                o_c = o_data.get("code") or o_data.get(block_code_field or "code")
                o_n = o_data.get("numero") or o_data.get("num")
                matched_data = (
                    (by_b_code.get(str(o_c)) if o_c is not None else None)
                    or (by_b_num.get(str(o_n)) if o_n is not None else None)
                    or o_data
                )
                blocks_map[o_it.id] = matched_data
                blocks_map[str(o_it.id)] = matched_data

        # Résolution des champs de relation locaux pour rétrocompatibilité
        check_fields = [chapter_field]
        if section_field:
            check_fields.append(section_field)
        relation_maps = await cls._resolve_relation_mappings(db, resource, check_fields)

        # Récupération de tous les items questions ordonnés (scopés à la version)
        items_stmt = select(ResourceItem).where(ResourceItem.resource_id == resource.id)
        if published_version and hasattr(ResourceItem, "resource_version_id") and published_version.id:
            items_stmt = items_stmt.where(ResourceItem.resource_version_id == published_version.id)
        items_stmt = items_stmt.order_by(ResourceItem.id)
        items = (await db.execute(items_stmt)).scalars().all()

        # Filtrage par lignes spécifiques sélectionnées si demandé
        if selected_item_ids:
            sel_str_set = {str(x) for x in selected_item_ids}
            items = [
                it for it in items
                if str(it.id) in sel_str_set
                or str((it.data or {}).get("id", "")) in sel_str_set
                or str((it.data or {}).get("code", "")) in sel_str_set
                or str((it.data or {}).get("numero", "")) in sel_str_set
            ]

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
            chap_desc = None
            sub_desc = None
            if section_resource_id and (question_section_link_field or section_field):
                sec_ref = data.get(question_section_link_field or section_field)
                sec_data = None
                if sec_ref is not None:
                    sec_data = (
                        sections_map.get(sec_ref)
                        or sections_map.get(str(sec_ref))
                        or (sections_map.get(int(sec_ref)) if str(sec_ref).isdigit() else None)
                    )
                
                # Titre et description de la Section (Niveau 2)
                if sec_data:
                    sub_title = str(
                        (sec_data.get(section_title_field) if section_title_field else None)
                        or sec_data.get("titre")
                        or sec_data.get("title")
                        or sec_data.get("name")
                        or sec_data.get("label")
                        or f"Section #{sec_ref}"
                    )
                    sub_code = str(
                        (sec_data.get(section_code_field) if section_code_field else None)
                        or sec_data.get("code")
                        or sec_data.get("numero")
                        or sec_data.get("num")
                        or f"SEC_{sec_ref}"
                    )
                    if section_description_field and sec_data.get(section_description_field):
                        sub_desc = str(sec_data.get(section_description_field))
                    has_subsection = True

                    # Résolution du Bloc (Niveau 1) depuis la Section
                    if block_resource_id and section_block_link_field:
                        blk_ref = sec_data.get(section_block_link_field)
                        blk_data = None
                        if blk_ref is not None:
                            blk_data = (
                                blocks_map.get(blk_ref)
                                or blocks_map.get(str(blk_ref))
                                or (blocks_map.get(int(blk_ref)) if str(blk_ref).isdigit() else None)
                            )
                        if blk_data:
                            chap_title = str(
                                (blk_data.get(block_title_field) if block_title_field else None)
                                or blk_data.get("titre")
                                or blk_data.get("title")
                                or blk_data.get("name")
                                or blk_data.get("label")
                                or f"Bloc #{blk_ref}"
                            )
                            chap_code = str(
                                (blk_data.get(block_code_field) if block_code_field else None)
                                or blk_data.get("code")
                                or blk_data.get("numero")
                                or blk_data.get("num")
                                or f"BLOC_{blk_ref}"
                            )
                            if block_description_field and blk_data.get(block_description_field):
                                chap_desc = str(blk_data.get(block_description_field))
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
                    "description": chap_desc,
                    "order_index": len(chapters_tree) + 1,
                    "subsections": {},
                    "direct_questions": [],
                }

            # Création du nœud Sous-section / Section dans l'arbre
            if has_subsection and sub_slug not in chapters_tree[chap_slug]["subsections"]:
                chapters_tree[chap_slug]["subsections"][sub_slug] = {
                    "code": sub_slug,
                    "title": sub_title,
                    "description": sub_desc,
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
                "is_required": is_required_default,
                "is_critical": is_crit,
                "weight": 1.0,
                "target_group": target_val,
                "resource_binding": {
                    "source_resource_id": resource.id,
                    "source_resource_version": published_version.version_number,
                    "source_resource_version_id": published_version.id,
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
                "description": chap_val.get("description"),
                "order_index": chap_val["order_index"],
                "subsections": subs_list,
                "direct_questions": chap_val["direct_questions"],
            })

        return {
            "resource_id": resource.id,
            "resource_name": resource.name,
            "resource_code": resource.code,
            "resource_version": published_version.version_number if published_version else (resource.version or "1.0.0"),
            "resource_version_id": published_version.id if published_version else None,
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
        resource_version_id: Optional[int] = None,
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
        is_required_default: bool = True,
        unique_id_field: str = "id",
        response_type_mode: str = "fixed",
        structure_mode: str = "auto",
        block_resource_id: Optional[int] = None,
        block_resource_version_id: Optional[int] = None,
        block_title_field: Optional[str] = "name",
        block_code_field: Optional[str] = "code",
        block_description_field: Optional[str] = None,
        section_resource_id: Optional[int] = None,
        section_resource_version_id: Optional[int] = None,
        section_title_field: Optional[str] = "name",
        section_code_field: Optional[str] = "code",
        section_description_field: Optional[str] = None,
        section_block_link_field: Optional[str] = None,
        question_section_link_field: Optional[str] = None,
        selected_item_ids: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        """
        Prévisualise la structure sans persister en base.
        """
        return await cls.build_projected_structure(
            db=db,
            resource_id=resource_id,
            resource_version_id=resource_version_id,
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
            is_required_default=is_required_default,
            unique_id_field=unique_id_field,
            response_type_mode=response_type_mode,
            structure_mode=structure_mode,
            block_resource_id=block_resource_id,
            block_resource_version_id=block_resource_version_id,
            block_title_field=block_title_field,
            block_code_field=block_code_field,
            block_description_field=block_description_field,
            section_resource_id=section_resource_id,
            section_resource_version_id=section_resource_version_id,
            section_title_field=section_title_field,
            section_code_field=section_code_field,
            section_description_field=section_description_field,
            section_block_link_field=section_block_link_field,
            question_section_link_field=question_section_link_field,
            selected_item_ids=selected_item_ids,
        )

    @classmethod
    async def generate_and_persist_form_from_resource(
        cls,
        db: AsyncSession,
        form_id: int,
        resource_id: int,
        resource_version_id: Optional[int] = None,
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
        is_required_default: bool = True,
        unique_id_field: str = "id",
        response_type_mode: str = "fixed",
        structure_mode: str = "auto",
        block_resource_id: Optional[int] = None,
        block_resource_version_id: Optional[int] = None,
        block_title_field: Optional[str] = "name",
        block_code_field: Optional[str] = "code",
        block_description_field: Optional[str] = None,
        section_resource_id: Optional[int] = None,
        section_resource_version_id: Optional[int] = None,
        section_title_field: Optional[str] = "name",
        section_code_field: Optional[str] = "code",
        section_description_field: Optional[str] = None,
        section_block_link_field: Optional[str] = None,
        question_section_link_field: Optional[str] = None,
        sanctuarize_generated: bool = True,
        replace_existing: bool = True,
        target_parent_section_id: Optional[int] = None,
        update_existing_root_section_id: Optional[int] = None,
        allow_tenant_select_requirements: bool = False,
        allow_tenant_add_unselected_questions: bool = True,
        allow_tenant_deselect_questions: bool = True,
        allow_tenant_add_questions: bool = True,
        allow_tenant_add_sections: bool = True,
        allow_tenant_delete_sections: bool = True,
        allow_tenant_edit_structure: bool = True,
        allow_tenant_delete_structure: bool = True,
        selected_item_ids: Optional[List[Any]] = None,
        default_justification_config: Optional[Dict[str, Any]] = None,
        is_super_admin: bool = False,
        form_version_id: Optional[int] = None,
    ) -> Form:
        """
        Génère et insère concrètement les sections, sous-sections et questions
        dans le formulaire depuis la ressource.
        Si update_existing_root_section_id est fourni, supprime et réinjecte
        exclusivement cette section racine de structure dynamique sans altérer
        les autres sections du formulaire.
        """
        form_stmt = select(Form).options(selectinload(Form.versions)).where(Form.id == form_id)
        form = (await db.execute(form_stmt)).scalars().first()
        if not form:
            raise HTTPException(status_code=404, detail="Formulaire introuvable.")

        # Résolution de la version cible du formulaire
        target_version: Optional[FormVersion] = None
        if form_version_id is not None:
            stmt_v = select(FormVersion).where(FormVersion.id == form_version_id, FormVersion.form_id == form_id)
            target_version = (await db.execute(stmt_v)).scalars().first()
            if not target_version:
                raise HTTPException(status_code=404, detail=f"Version de formulaire #{form_version_id} introuvable.")
        else:
            # Chercher en priorité un brouillon actif
            target_v_draft = (await db.execute(
                select(FormVersion).where(
                    FormVersion.form_id == form_id,
                    FormVersion.status == FormVersionStatus.DRAFT.value
                ).order_by(FormVersion.id.desc())
            )).scalars().first()
            target_version = target_v_draft or form.active_version

        # Contrôle strict d'immuabilité : Seule une version scellée/publiée est verrouillée.
        # Une version en brouillon (draft) est 100% modifiable et régénérable.
        if target_version and target_version.status in (FormVersionStatus.PUBLISHED.value, "published", FormVersionStatus.ACTIVE.value, "active"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cette version du formulaire (v{target_version.version_number}) est publiée et verrouillée. Créez ou clonez une nouvelle version en brouillon pour régénérer sa structure."
            )
        elif not target_version and form.status in (FormStatus.PUBLISHED.value, "published"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ce formulaire est publié et verrouillé. Créez une nouvelle version pour régénérer sa structure."
            )

        target_version_id = target_version.id if target_version else None

        effective_add_questions = allow_tenant_add_questions if allow_tenant_add_questions is not None else allow_tenant_add_unselected_questions
        effective_deselect_questions = allow_tenant_deselect_questions if allow_tenant_deselect_questions is not None else True
        effective_add_sections = allow_tenant_add_sections if allow_tenant_add_sections is not None else True
        effective_delete_sections = allow_tenant_delete_sections if allow_tenant_delete_sections is not None else True
        effective_edit_structure = allow_tenant_edit_structure if allow_tenant_edit_structure is not None else True
        effective_delete_structure = allow_tenant_delete_structure if allow_tenant_delete_structure is not None else True

        if target_parent_section_id is not None:
            parent_check = (await db.execute(
                select(FormSection).where(FormSection.id == target_parent_section_id, FormSection.form_id == form_id)
            )).scalar_one_or_none()
            if not parent_check:
                logger.warning(
                    "Section parente cible #%s introuvable pour formulaire #%s. Insertion par défaut à la racine.",
                    target_parent_section_id,
                    form_id,
                )
                target_parent_section_id = None

        # Construction de l'arborescence projetée
        projection = await cls.build_projected_structure(
            db=db,
            resource_id=resource_id,
            resource_version_id=resource_version_id,
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
            is_required_default=is_required_default,
            unique_id_field=unique_id_field,
            response_type_mode=response_type_mode,
            structure_mode=structure_mode,
            block_resource_id=block_resource_id,
            block_resource_version_id=block_resource_version_id,
            block_title_field=block_title_field,
            block_code_field=block_code_field,
            block_description_field=block_description_field,
            section_resource_id=section_resource_id,
            section_resource_version_id=section_resource_version_id,
            section_title_field=section_title_field,
            section_code_field=section_code_field,
            section_description_field=section_description_field,
            section_block_link_field=section_block_link_field,
            question_section_link_field=question_section_link_field,
            selected_item_ids=selected_item_ids,
        )

        chapters = projection["chapters"]
        if not chapters:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Aucun élément trouvé dans la ressource avec les critères et filtres spécifiés."
            )

        # Nettoyage précis si modification d'une structure dynamique existante spécifique
        preserved_order: Optional[int] = None
        if update_existing_root_section_id is not None:
            # Récupérer l'ordre d'origine de la section racine avant suppression
            existing_sec = (await db.execute(
                select(FormSection).where(FormSection.id == update_existing_root_section_id, FormSection.form_id == form_id)
            )).scalar_one_or_none()
            if existing_sec:
                preserved_order = existing_sec.order_index
                target_parent_section_id = existing_sec.parent_section_id
                await db.execute(delete(FormSection).where(FormSection.id == update_existing_root_section_id))
                await db.flush()
        elif replace_existing:
            # Si remplacement global demandé (uniquement si aucune racine spécifique n'est ciblée)
            if target_version_id is not None:
                await db.execute(delete(FormSection).where(FormSection.form_id == form_id, FormSection.form_version_id == target_version_id))
                await db.execute(delete(FormQuestion).where(FormQuestion.form_id == form_id, FormQuestion.form_version_id == target_version_id))
            else:
                await db.execute(delete(FormSection).where(FormSection.form_id == form_id))
                await db.execute(delete(FormQuestion).where(FormQuestion.form_id == form_id))
            await db.flush()

        base_order = preserved_order if preserved_order is not None else 0
        if preserved_order is None and not replace_existing:
            max_order_query = select(func.max(FormSection.order_index)).where(FormSection.form_id == form_id)
            if target_version_id is not None:
                max_order_query = max_order_query.where(FormSection.form_version_id == target_version_id)
            if target_parent_section_id is not None:
                max_order_query = max_order_query.where(FormSection.parent_section_id == target_parent_section_id)
            else:
                max_order_query = max_order_query.where(FormSection.parent_section_id.is_(None))
            base_order = (await db.execute(max_order_query)).scalar() or 0

        # Configuration de projection sauvegardée sur la section racine
        projection_metadata = {
            "source_mode": "resource_driven",
            "resource_id": resource_id,
            "resource_name": projection["resource_name"],
            "resource_code": projection["resource_code"],
            "resource_version": projection.get("resource_version"),
            "resource_version_id": projection.get("resource_version_id"),
            "mapping": {
                "resource_version_id": projection.get("resource_version_id"),
                "resource_version": projection.get("resource_version"),
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
                "is_required_default": is_required_default,
                "response_type_mode": response_type_mode,
                "structure_mode": structure_mode,
                "block_resource_id": block_resource_id,
                "block_resource_version_id": block_resource_version_id,
                "block_title_field": block_title_field,
                "block_code_field": block_code_field,
                "block_description_field": block_description_field,
                "section_resource_id": section_resource_id,
                "section_resource_version_id": section_resource_version_id,
                "section_title_field": section_title_field,
                "section_code_field": section_code_field,
                "section_description_field": section_description_field,
                "section_block_link_field": section_block_link_field,
                "question_section_link_field": question_section_link_field,
                "target_parent_section_id": target_parent_section_id,
                "default_justification_config": default_justification_config,
            },
            "default_justification_config": default_justification_config,
            "allow_tenant_select_requirements": allow_tenant_select_requirements,
            "allow_tenant_add_unselected_questions": effective_add_questions,
            "allow_tenant_add_questions": effective_add_questions,
            "allow_tenant_deselect_questions": effective_deselect_questions,
            "allow_tenant_add_sections": effective_add_sections,
            "allow_tenant_delete_sections": effective_delete_sections,
            "allow_tenant_edit_structure": effective_edit_structure,
            "allow_tenant_delete_structure": effective_delete_structure,
            "selected_item_ids": selected_item_ids,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Insertion des chapitres, sous-sections et questions
        created_sections_count = 0
        created_questions_count = 0

        # Règle d'or : Si le plus haut niveau n'est pas unique (len(chapters) > 1),
        # synthétiser une section conteneur racine UNIQUE ("Point d'entrée")
        # afin de concentrer le paramétrage et le bouton "Modifier Structure"
        # en un seul endroit sans démultiplier les contrôles.
        needs_wrapper_root = len(chapters) > 1

        wrapper_sec: Optional[FormSection] = None
        if needs_wrapper_root:
            container_title = projection.get("resource_name") or "Structure Dynamique"
            container_code = f"ROOT_{projection.get('resource_code') or 'DYN'}"
            wrapper_sec = FormSection(
                form_id=form_id,
                form_version_id=target_version_id,
                parent_section_id=target_parent_section_id,
                title=container_title,
                code=container_code,
                description=f"Section conteneur racine regroupant les {len(chapters)} regroupements de « {container_title} »",
                order_index=base_order + 1,
                is_sanctuarized=sanctuarize_generated,
                allow_custom_questions=effective_add_questions,
                is_repeatable=False,
                settings={
                    "resource_projection": projection_metadata,
                    "allow_custom_questions": effective_add_questions,
                    "allow_delete_questions": effective_deselect_questions,
                    "allow_custom_subsections": effective_add_sections,
                    "allow_delete_subsections": effective_delete_sections,
                    "allow_section_delete": effective_delete_sections,
                    "allow_structure_edit": effective_edit_structure,
                    "allow_structure_delete": effective_delete_structure,
                },
            )
            db.add(wrapper_sec)
            await db.flush()
            created_sections_count += 1

        for chap_data in chapters:
            parent_id_for_chap = wrapper_sec.id if needs_wrapper_root else target_parent_section_id
            order_for_chap = chap_data["order_index"] if needs_wrapper_root else (base_order + chap_data["order_index"])

            # Si wrapper_sec existe, seul le wrapper_sec porte la configuration settings.resource_projection
            chap_settings: Dict[str, Any] = {
                "allow_custom_questions": effective_add_questions,
                "allow_delete_questions": effective_deselect_questions,
                "allow_custom_subsections": effective_add_sections,
                "allow_delete_subsections": effective_delete_sections,
                "allow_section_delete": effective_delete_sections,
                "allow_structure_edit": effective_edit_structure,
                "allow_structure_delete": effective_delete_structure,
            }
            if not needs_wrapper_root:
                chap_settings["resource_projection"] = projection_metadata

            parent_sec = FormSection(
                form_id=form_id,
                form_version_id=target_version_id,
                parent_section_id=parent_id_for_chap,
                title=chap_data["title"],
                code=chap_data["code"],
                description=chap_data.get("description"),
                order_index=order_for_chap,
                is_sanctuarized=sanctuarize_generated,
                allow_custom_questions=effective_add_questions,
                is_repeatable=False,
                settings=chap_settings,
            )
            db.add(parent_sec)
            await db.flush()
            created_sections_count += 1

            # Questions directes au niveau du chapitre
            for q_data in chap_data.get("direct_questions", []):
                q = FormQuestion(
                    form_id=form_id,
                    form_version_id=target_version_id,
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
                    settings={
                        **(q_data.get("settings") or {}),
                        **({"justification_config": default_justification_config} if default_justification_config else {})
                    },
                )
                db.add(q)
                created_questions_count += 1

            # Sous-sections de niveau 2
            for sub_data in chap_data.get("subsections", []):
                child_sec = FormSection(
                    form_id=form_id,
                    form_version_id=target_version_id,
                    parent_section_id=parent_sec.id,
                    title=sub_data["title"],
                    code=sub_data["code"],
                    description=sub_data.get("description"),
                    order_index=sub_data["order_index"],
                    is_sanctuarized=sanctuarize_generated,
                    allow_custom_questions=effective_add_questions,
                    is_repeatable=False,
                    settings={
                        "allow_custom_subsections": effective_add_sections,
                        "allow_section_delete": effective_delete_sections,
                    },
                )
                db.add(child_sec)
                await db.flush()
                created_sections_count += 1

                for q_data in sub_data.get("questions", []):
                    q = FormQuestion(
                        form_id=form_id,
                        form_version_id=target_version_id,
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
                        settings={
                            **(q_data.get("settings") or {}),
                            **({"justification_config": default_justification_config} if default_justification_config else {})
                        },
                    )
                    db.add(q)
                    created_questions_count += 1

        # Mise à jour de la configuration de liaison du formulaire
        current_binding = getattr(form, "resource_binding_config", None) or {}
        if not current_binding and isinstance(form.settings, dict):
            current_binding = form.settings.get("resource_binding_config") or {}
        current_binding.update({
            "source_mode": "resource_driven",
            "resource_id": resource_id,
            "resource_name": projection["resource_name"],
            "resource_code": projection["resource_code"],
            "mapping": {
                "resource_version_id": projection.get("resource_version_id"),
                "resource_version": projection.get("resource_version"),
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
                "is_required_default": is_required_default,
                "response_type_mode": response_type_mode,
                "structure_mode": structure_mode,
                "block_resource_id": block_resource_id,
                "block_resource_version_id": block_resource_version_id,
                "block_title_field": block_title_field,
                "block_code_field": block_code_field,
                "block_description_field": block_description_field,
                "section_resource_id": section_resource_id,
                "section_resource_version_id": section_resource_version_id,
                "section_title_field": section_title_field,
                "section_code_field": section_code_field,
                "section_description_field": section_description_field,
                "section_block_link_field": section_block_link_field,
                "question_section_link_field": question_section_link_field,
                "target_parent_section_id": target_parent_section_id,
                "default_justification_config": default_justification_config,
            },
            "default_justification_config": default_justification_config,
            "allow_tenant_select_requirements": allow_tenant_select_requirements,
            "allow_tenant_add_unselected_questions": effective_add_questions,
            "allow_tenant_add_questions": effective_add_questions,
            "allow_tenant_deselect_questions": effective_deselect_questions,
            "allow_tenant_add_sections": effective_add_sections,
            "allow_tenant_delete_sections": effective_delete_sections,
            "allow_tenant_edit_structure": effective_edit_structure,
            "allow_tenant_delete_structure": effective_delete_structure,
            "selected_item_ids": selected_item_ids,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sections_count": created_sections_count,
            "questions_count": created_questions_count,
        })
        if hasattr(form, "resource_binding_config"):
            try:
                form.resource_binding_config = dict(current_binding)
            except Exception:
                pass

        # Enregistrement dans les paramètres de gouvernance
        current_settings = dict(form.settings or {})
        governance = dict(current_settings.get("governance", {}))
        governance["allow_tenant_select_requirements"] = allow_tenant_select_requirements
        governance["allow_tenant_add_unselected_questions"] = effective_add_questions
        governance["allow_tenant_add_questions"] = effective_add_questions
        governance["allow_tenant_deselect_questions"] = effective_deselect_questions
        governance["allow_tenant_add_sections"] = effective_add_sections
        governance["allow_tenant_delete_sections"] = effective_delete_sections
        governance["allow_tenant_edit_structure"] = effective_edit_structure
        governance["allow_tenant_delete_structure"] = effective_delete_structure
        current_settings["governance"] = governance
        current_settings["resource_binding_config"] = dict(current_binding)
        form.settings = current_settings

        if target_version:
            v_settings = dict(target_version.settings or {})
            v_settings["governance"] = governance
            v_settings["resource_binding_config"] = dict(current_binding)
            target_version.settings = v_settings
            from sqlalchemy.orm.attributes import flag_modified
            flag_modified(target_version, "settings")

        await db.commit()
        await db.refresh(form)

        # Rechargement complet avec arborescence
        from app.services.form.form_service import FormService
        return await FormService.get_form_by_id(db, form_id, form.organization_id, is_super_admin, version_id=target_version_id)

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
