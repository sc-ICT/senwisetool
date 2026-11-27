"""
Module de Contrôle d'Intégrité Référentielle et de Verrouillage des Dépendances
==============================================================================
Ce service applique les règles strictes de non-régression et de cohérence inter-modules :
1. Ressources - Champs :
   Si un champ d'une version de ressource est utilisé par une autre version de ressource
   (liaison relationnelle) ou par un formulaire (questions, métadonnées, formules),
   ce champ ne peut être ni modifié ni supprimé, même si l'utilisateur a les permissions.
2. Ressources - Enregistrements (Lignes) :
   Si un enregistrement d'une version de ressource est référencé par une autre ressource
   (valeur de clé étrangère/relation) ou par un formulaire (valeur par défaut, options),
   cet enregistrement ne peut être ni modifié ni supprimé.
3. Ressources - Versions :
   Une version de ressource ne peut être supprimée si elle est utilisée par une autre
   version de ressource ou par une version de formulaire.
4. Formulaires - Questions :
   Une question ne peut être ni modifiée ni supprimée si elle est utilisée par une autre
   version de formulaire (cross_form_lookup, formule de calcul, règle conditionnelle).
5. Formulaires - Versions :
   Une version de formulaire ne peut être supprimée si elle est en cours d'utilisation
   par une autre version de formulaire (liaison cross_form_lookup ou modèle parent).
"""

import json
import logging
from typing import Any, Dict, List, Optional, Set
from fastapi import HTTPException, status
from sqlalchemy import select, or_
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
    OrganizationFormQuestion,
)
from app.models.resource.resource import Resource
from app.models.resource.resource_version import ResourceVersion
from app.models.resource.resource_item import ResourceItem
from app.models.form.form import Form
from app.models.form.form_version import FormVersion
from app.models.form.form_question import FormQuestion

logger = logging.getLogger("app.services.plugin.module_integrity_service")


class ModuleIntegrityService:

    # =========================================================================
    # 1. CONTRÔLE SUR LES CHAMPS DE RESSOURCE
    # =========================================================================
    @classmethod
    async def validate_resource_field_can_be_modified_or_deleted(
        cls,
        db: AsyncSession,
        resource_id: int,
        version_id: int,
        schema_fields_new: List[Dict[str, Any]],
        schema_fields_old: List[Dict[str, Any]],
        organization_id: Optional[int] = None,
    ) -> None:
        """
        Vérifie qu'aucun champ supprimé ou modifié dans le schéma n'est en cours d'utilisation
        par une autre version de ressource ou un formulaire (questions ou métadonnées).
        """
        old_map = {
            f.get("name"): f
            for f in (schema_fields_old or [])
            if isinstance(f, dict) and f.get("name")
        }
        new_map = {
            f.get("name"): f
            for f in (schema_fields_new or [])
            if isinstance(f, dict) and f.get("name")
        }

        # Déterminer les champs modifiés ou supprimés
        critical_changes: Dict[str, Dict[str, Any]] = {}
        for fname, old_f in old_map.items():
            if fname not in new_map:
                critical_changes[fname] = {
                    "action": "supprimer",
                    "old_field": old_f,
                }
            else:
                new_f = new_map[fname]
                # Changements critiques sur un champ existant
                differs = False
                for prop in ("type", "target_resource_id", "target_resource_code", "target_field", "display_field"):
                    if old_f.get(prop) != new_f.get(prop):
                        differs = True
                        break
                if differs:
                    critical_changes[fname] = {
                        "action": "modifier",
                        "old_field": old_f,
                    }

        if not critical_changes:
            return

        # Récupérer les identifiants de la ressource et de la version ciblée
        res_info = await cls._resolve_resource_info(db, resource_id, version_id, organization_id)
        if not res_info:
            return

        res_ids = res_info["resource_ids"]
        ver_ids = res_info["version_ids"]
        res_codes = res_info["resource_codes"]
        res_names = res_info["resource_names"]

        for fname, meta in critical_changes.items():
            action = meta["action"]
            old_field = meta["old_field"]
            field_label = old_field.get("label") or fname

            # A) Vérification dans les autres ressources (relations)
            await cls._check_field_used_by_other_resources(
                db=db,
                organization_id=organization_id,
                current_ver_ids=ver_ids,
                target_res_ids=res_ids,
                target_res_codes=res_codes,
                target_res_names=res_names,
                field_name=fname,
                field_label=field_label,
                action=action,
            )

            # B) Vérification dans les formulaires (questions et métadonnées)
            await cls._check_field_used_by_forms(
                db=db,
                organization_id=organization_id,
                target_res_ids=res_ids,
                target_res_ver_ids=ver_ids,
                target_res_codes=res_codes,
                field_name=fname,
                field_label=field_label,
                action=action,
            )

    @classmethod
    async def _check_field_used_by_other_resources(
        cls,
        db: AsyncSession,
        organization_id: Optional[int],
        current_ver_ids: Set[int],
        target_res_ids: Set[int],
        target_res_codes: Set[str],
        target_res_names: Set[str],
        field_name: str,
        field_label: str,
        action: str,
    ) -> None:
        """
        Vérifie si un champ est ciblé comme `target_field` ou `display_field` par un champ 'relation'
        d'une autre version de ressource.
        """
        if organization_id:
            stmt = select(OrganizationResourceVersion).options(
                selectinload(OrganizationResourceVersion.organization_resource)
            ).where(
                OrganizationResourceVersion.organization_id == organization_id,
                ~OrganizationResourceVersion.id.in_(list(current_ver_ids)),
            )
            other_vers = (await db.execute(stmt)).scalars().all()
        else:
            stmt = select(ResourceVersion).options(
                selectinload(ResourceVersion.resource)
            ).where(~ResourceVersion.id.in_(list(current_ver_ids)))
            other_vers = (await db.execute(stmt)).scalars().all()

        for ov in other_vers:
            parent_res = getattr(ov, "organization_resource", None) or getattr(ov, "resource", None)
            other_res_name = getattr(parent_res, "name", "Ressource") if parent_res else "Ressource"
            ov_num = ov.version_number

            for f in (ov.schema_fields or []):
                if not isinstance(f, dict) or f.get("type") != "relation":
                    continue

                t_id = f.get("target_resource_id")
                t_code = str(f.get("target_resource_code") or "").strip().lower()
                t_field = f.get("target_field")
                d_field = f.get("display_field")

                targets_this_res = (
                    (t_id in target_res_ids)
                    or (t_code and t_code in target_res_codes)
                )

                if targets_this_res:
                    if t_field == field_name or d_field == field_name:
                        role = "cible" if t_field == field_name else "libellé d'affichage"
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                f"Action refusée : Impossible de {action} le champ « {field_label} » (clé: '{field_name}') de cette ressource. "
                                f"Il est actuellement utilisé comme {role} pour la liaison relationnelle « {f.get('label') or f.get('name')} » "
                                f"dans la version v{ov_num} de la ressource « {other_res_name} »."
                            ),
                        )

    @classmethod
    async def _check_field_used_by_forms(
        cls,
        db: AsyncSession,
        organization_id: Optional[int],
        target_res_ids: Set[int],
        target_res_ver_ids: Set[int],
        target_res_codes: Set[str],
        field_name: str,
        field_label: str,
        action: str,
    ) -> None:
        """
        Vérifie si un champ est lié dans des questions ou métadonnées de formulaires.
        """
        if organization_id:
            stmt = select(OrganizationFormQuestion).options(
                selectinload(OrganizationFormQuestion.organization_form),
                selectinload(OrganizationFormQuestion.organization_form_version),
            ).where(OrganizationFormQuestion.organization_id == organization_id)
            questions = (await db.execute(stmt)).scalars().all()
        else:
            stmt = select(FormQuestion).options(
                selectinload(FormQuestion.form),
                selectinload(FormQuestion.version),
            )
            questions = (await db.execute(stmt)).scalars().all()

        for q in questions:
            form_obj = getattr(q, "organization_form", None) or getattr(q, "form", None)
            ver_obj = getattr(q, "organization_form_version", None) or getattr(q, "version", None)
            form_name = getattr(form_obj, "name", "Formulaire") if form_obj else "Formulaire"
            ver_num = getattr(ver_obj, "version_number", "1.0.0") if ver_obj else "1.0.0"

            rb = q.resource_binding or {}
            if isinstance(rb, dict):
                r_id = rb.get("resource_id")
                r_vid = rb.get("resource_version_id")
                r_code = str(rb.get("resource_code") or "").strip().lower()

                matches_res = (
                    (r_id in target_res_ids)
                    or (r_code and r_code in target_res_codes)
                )
                matches_ver = (r_vid is None) or (r_vid in target_res_ver_ids)

                if matches_res and matches_ver:
                    v_field = rb.get("value_field")
                    l_field = rb.get("label_field")
                    sec_l_field = rb.get("secondary_label_field")
                    field_maps = rb.get("field_mappings") or rb.get("columns") or []

                    used_cols: Set[str] = set()
                    if v_field:
                        used_cols.add(v_field)
                    if l_field:
                        used_cols.add(l_field)
                    if sec_l_field:
                        used_cols.add(sec_l_field)

                    if isinstance(field_maps, dict):
                        used_cols.update(field_maps.keys())
                        used_cols.update(str(v) for v in field_maps.values())
                    elif isinstance(field_maps, list):
                        for m in field_maps:
                            if isinstance(m, str):
                                used_cols.add(m)
                            elif isinstance(m, dict):
                                used_cols.update(m.keys())
                                used_cols.update(str(v) for v in m.values())

                    if field_name in used_cols:
                        raise HTTPException(
                            status_code=status.HTTP_409_CONFLICT,
                            detail=(
                                f"Action refusée : Impossible de {action} le champ « {field_label} » (clé: '{field_name}') de cette ressource. "
                                f"Il est actuellement en cours d'utilisation par la question « {q.label} » (code: '{q.code}') "
                                f"dans la version v{ver_num} du formulaire « {form_name} »."
                            ),
                        )

            # Vérification dans q.settings
            q_settings = q.settings or {}
            if isinstance(q_settings, dict):
                s_rid = q_settings.get("resource_id")
                s_vf = q_settings.get("value_field")
                s_lf = q_settings.get("label_field")
                if s_rid in target_res_ids and (s_vf == field_name or s_lf == field_name):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"Action refusée : Impossible de {action} le champ « {field_label} » (clé: '{field_name}') de cette ressource. "
                            f"Il est configuré dans les métadonnées de la question « {q.label} » "
                            f"dans la version v{ver_num} du formulaire « {form_name} »."
                        ),
                    )

    # =========================================================================
    # 2. CONTRÔLE SUR LES LIGNES D'ENREGISTREMENT (ITEMS) DE RESSOURCE
    # =========================================================================
    @classmethod
    async def validate_resource_row_can_be_modified_or_deleted(
        cls,
        db: AsyncSession,
        resource_id: int,
        item_id: int,
        organization_id: Optional[int] = None,
        is_delete: bool = False,
    ) -> None:
        """
        Vérifie qu'un enregistrement d'une version de ressource n'est pas actuellement
        référencé par un enregistrement d'une autre ressource (liaison relationnelle)
        ou par un formulaire (questions ou métadonnées).
        """
        action_verb = "supprimer" if is_delete else "modifier"

        # 1. Charger l'item
        if organization_id:
            stmt = select(OrganizationResourceItem).where(
                OrganizationResourceItem.id == item_id,
                OrganizationResourceItem.organization_id == organization_id,
            )
            item = (await db.execute(stmt)).scalars().first()
        else:
            stmt = select(ResourceItem).where(ResourceItem.id == item_id)
            item = (await db.execute(stmt)).scalars().first()

        if not item:
            return

        item_data = dict(item.data or {})

        # 2. Constituer l'ensemble des clés et identifiants représentant cette ligne
        identifiers: Set[str] = {str(item.id), str(int(item.id))}
        src_item_id = getattr(item, "source_resource_item_id", None)
        if src_item_id:
            identifiers.add(str(src_item_id))

        # Clés métiers usuelles pour identifier une ligne
        for k in (
            "id", "code", "uuid", "matricule", "reference", "slug", "key",
            "numero_exigence", "code_exigence", "nom_court", "identifier"
        ):
            val = item_data.get(k)
            if val is not None and str(val).strip():
                identifiers.add(str(val).strip())

        # 3. Vérifier les liaisons dans d'autres ressources
        res_info = await cls._resolve_resource_info(
            db,
            getattr(item, "organization_resource_id", None) or getattr(item, "resource_id", resource_id),
            getattr(item, "organization_resource_version_id", None) or getattr(item, "resource_version_id", None),
            organization_id,
        )
        if not res_info:
            return

        target_res_ids = res_info["resource_ids"]
        target_res_codes = res_info["resource_codes"]

        # Chercher les autres ressources ayant des champs relations ciblant cette ressource
        if organization_id:
            other_res_stmt = select(OrganizationResource).options(
                selectinload(OrganizationResource.versions),
                selectinload(OrganizationResource.items),
            ).where(
                OrganizationResource.organization_id == organization_id,
                ~OrganizationResource.id.in_(list(target_res_ids)),
            )
            other_resources = (await db.execute(other_res_stmt)).scalars().all()
        else:
            other_res_stmt = select(Resource).options(
                selectinload(Resource.versions),
                selectinload(Resource.items),
            ).where(~Resource.id.in_(list(target_res_ids)))
            other_resources = (await db.execute(other_res_stmt)).scalars().all()

        for other_r in other_resources:
            # Trouver les champs de type relation pointant vers notre ressource
            all_fields = list(other_r.schema_fields or [])
            for ov in (other_r.versions or []):
                for f in (ov.schema_fields or []):
                    if f not in all_fields:
                        all_fields.append(f)

            rel_fields: List[str] = []
            for f in all_fields:
                if not isinstance(f, dict) or f.get("type") != "relation":
                    continue
                t_id = f.get("target_resource_id")
                t_code = str(f.get("target_resource_code") or "").strip().lower()
                if (t_id in target_res_ids) or (t_code and t_code in target_res_codes):
                    rel_fields.append(f.get("name"))

            if not rel_fields:
                continue

            # Vérifier si un item de cette autre ressource référence l'une de nos clés
            for o_item in (other_r.items or []):
                o_data = o_item.data or {}
                for rf in rel_fields:
                    val = o_data.get(rf)
                    if val is None:
                        continue
                    # val peut être un scalaire ou une liste (relation multiple)
                    vals = [str(val).strip()] if not isinstance(val, list) else [str(x).strip() for x in val]
                    for v in vals:
                        if v in identifiers:
                            raise HTTPException(
                                status_code=status.HTTP_409_CONFLICT,
                                detail=(
                                    f"Action refusée : Impossible de {action_verb} cet enregistrement (ID #{item_id}). "
                                    f"Il est actuellement utilisé et référencé par la ressource « {other_r.name} » "
                                    f"(enregistrement ID #{o_item.id}, champ de liaison « {rf} »)."
                                ),
                            )

        # 4. Vérifier les formulaires (valeur par défaut liée ou métadonnées)
        if organization_id:
            q_stmt = select(OrganizationFormQuestion).options(
                selectinload(OrganizationFormQuestion.organization_form),
                selectinload(OrganizationFormQuestion.organization_form_version),
            ).where(OrganizationFormQuestion.organization_id == organization_id)
            form_questions = (await db.execute(q_stmt)).scalars().all()
        else:
            q_stmt = select(FormQuestion).options(
                selectinload(FormQuestion.form),
                selectinload(FormQuestion.version),
            )
            form_questions = (await db.execute(q_stmt)).scalars().all()

        for q in form_questions:
            rb = q.resource_binding or {}
            q_set = q.settings or {}
            r_id = rb.get("resource_id") or q_set.get("resource_id")
            r_code = str(rb.get("resource_code") or "").strip().lower()

            if (r_id in target_res_ids) or (r_code and r_code in target_res_codes):
                # Vérifier si cet enregistrement est explicitement configuré en valeur par défaut
                def_val = str(rb.get("default_value") or q_set.get("default_value") or "").strip()
                if def_val and def_val in identifiers:
                    form_obj = getattr(q, "organization_form", None) or getattr(q, "form", None)
                    ver_obj = getattr(q, "organization_form_version", None) or getattr(q, "version", None)
                    f_name = getattr(form_obj, "name", "Formulaire") if form_obj else "Formulaire"
                    v_num = getattr(ver_obj, "version_number", "1.0.0") if ver_obj else "1.0.0"
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"Action refusée : Impossible de {action_verb} cet enregistrement (ID #{item_id}). "
                            f"Il est configuré comme valeur de référence par défaut dans la question « {q.label} » "
                            f"(version v{v_num} du formulaire « {f_name} »)."
                        ),
                    )

    # =========================================================================
    # 3. CONTRÔLE SUR LA SUPPRESSION D'UNE VERSION DE RESSOURCE
    # =========================================================================
    @classmethod
    async def validate_resource_version_can_be_deleted(
        cls,
        db: AsyncSession,
        resource_id: int,
        version_id: int,
        organization_id: Optional[int] = None,
    ) -> None:
        """
        Vérifie qu'une version de ressource n'est pas en cours d'utilisation
        par une autre version de ressource ou un formulaire.
        """
        res_info = await cls._resolve_resource_info(db, resource_id, version_id, organization_id)
        if not res_info:
            return

        res_ids = res_info["resource_ids"]
        ver_ids = res_info["version_ids"]
        res_codes = res_info["resource_codes"]
        res_name = list(res_info["resource_names"])[0] if res_info["resource_names"] else "Ressource"

        # A) Vérifier les autres versions de ressources
        if organization_id:
            stmt = select(OrganizationResourceVersion).options(
                selectinload(OrganizationResourceVersion.organization_resource)
            ).where(
                OrganizationResourceVersion.organization_id == organization_id,
                ~OrganizationResourceVersion.id.in_(list(ver_ids)),
            )
            other_vers = (await db.execute(stmt)).scalars().all()
        else:
            stmt = select(ResourceVersion).options(
                selectinload(ResourceVersion.resource)
            ).where(~ResourceVersion.id.in_(list(ver_ids)))
            other_vers = (await db.execute(stmt)).scalars().all()

        for ov in other_vers:
            for f in (ov.schema_fields or []):
                if not isinstance(f, dict) or f.get("type") != "relation":
                    continue
                t_id = f.get("target_resource_id")
                t_code = str(f.get("target_resource_code") or "").strip().lower()
                t_vid = f.get("target_resource_version_id")

                if ((t_id in res_ids) or (t_code and t_code in res_codes)) and (t_vid in ver_ids):
                    parent_res = getattr(ov, "organization_resource", None) or getattr(ov, "resource", None)
                    o_name = getattr(parent_res, "name", "Ressource") if parent_res else "Ressource"
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"Action refusée : Impossible de supprimer cette version de la ressource « {res_name} ». "
                            f"Elle est actuellement ciblée par la relation « {f.get('label') or f.get('name')} » "
                            f"dans la version v{ov.version_number} de la ressource « {o_name} »."
                        ),
                    )

        # B) Vérifier les questions de formulaires
        if organization_id:
            q_stmt = select(OrganizationFormQuestion).options(
                selectinload(OrganizationFormQuestion.organization_form),
                selectinload(OrganizationFormQuestion.organization_form_version),
            ).where(OrganizationFormQuestion.organization_id == organization_id)
            form_questions = (await db.execute(q_stmt)).scalars().all()
        else:
            q_stmt = select(FormQuestion).options(
                selectinload(FormQuestion.form),
                selectinload(FormQuestion.version),
            )
            form_questions = (await db.execute(q_stmt)).scalars().all()

        for q in form_questions:
            rb = q.resource_binding or {}
            q_vid = rb.get("resource_version_id")
            if q_vid in ver_ids:
                form_obj = getattr(q, "organization_form", None) or getattr(q, "form", None)
                ver_obj = getattr(q, "organization_form_version", None) or getattr(q, "version", None)
                f_name = getattr(form_obj, "name", "Formulaire") if form_obj else "Formulaire"
                v_num = getattr(ver_obj, "version_number", "1.0.0") if ver_obj else "1.0.0"
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Action refusée : Impossible de supprimer cette version de la ressource « {res_name} ». "
                        f"Elle est directement liée à la question « {q.label} » "
                        f"dans la version v{v_num} du formulaire « {f_name} »."
                    ),
                )

    # =========================================================================
    # 4. CONTRÔLE SUR LES QUESTIONS DE FORMULAIRE
    # =========================================================================
    @classmethod
    async def validate_form_question_can_be_modified_or_deleted(
        cls,
        db: AsyncSession,
        question_id: int,
        organization_id: Optional[int] = None,
        is_delete: bool = False,
    ) -> None:
        """
        Vérifie qu'une question de formulaire n'est pas en cours d'utilisation
        par une autre version de formulaire (liaison cross_form_lookup, formule de calcul,
        ou règles conditionnelles).
        """
        action_verb = "supprimer" if is_delete else "modifier"

        # 1. Charger la question
        if organization_id:
            stmt = select(OrganizationFormQuestion).where(
                OrganizationFormQuestion.id == question_id,
                OrganizationFormQuestion.organization_id == organization_id,
            )
            q = (await db.execute(stmt)).scalars().first()
            if not q:
                # Fallback sur question standard
                q = (await db.execute(select(FormQuestion).where(FormQuestion.id == question_id))).scalars().first()
        else:
            q = (await db.execute(select(FormQuestion).where(FormQuestion.id == question_id))).scalars().first()
            if not q:
                q = (await db.execute(select(OrganizationFormQuestion).where(OrganizationFormQuestion.id == question_id))).scalars().first()

        if not q:
            return

        q_code = str(q.code).strip()
        q_label = q.label or q_code
        q_ids: Set[int] = {q.id}
        src_qid = getattr(q, "source_form_question_id", None)
        if src_qid:
            q_ids.add(src_qid)

        current_ver_id = getattr(q, "organization_form_version_id", None) or getattr(q, "form_version_id", None)

        # 2. Chercher dans toutes les autres questions de formulaires
        if organization_id:
            other_q_stmt = select(OrganizationFormQuestion).options(
                selectinload(OrganizationFormQuestion.organization_form),
                selectinload(OrganizationFormQuestion.organization_form_version),
            ).where(
                OrganizationFormQuestion.organization_id == organization_id,
                OrganizationFormQuestion.organization_form_version_id != current_ver_id,
            )
            other_questions = (await db.execute(other_q_stmt)).scalars().all()
        else:
            other_q_stmt = select(FormQuestion).options(
                selectinload(FormQuestion.form),
                selectinload(FormQuestion.version),
            ).where(FormQuestion.form_version_id != current_ver_id)
            other_questions = (await db.execute(other_q_stmt)).scalars().all()

        for o_q in other_questions:
            parent_form = getattr(o_q, "organization_form", None) or getattr(o_q, "form", None)
            parent_ver = getattr(o_q, "organization_form_version", None) or getattr(o_q, "version", None)
            f_name = getattr(parent_form, "name", "Formulaire") if parent_form else "Formulaire"
            v_num = getattr(parent_ver, "version_number", "1.0.0") if parent_ver else "1.0.0"

            # a) Vérification cross_form_lookup
            cross_cfg = (
                (o_q.settings or {}).get("cross_form_lookup")
                or getattr(o_q, "cross_form_lookup", None)
                or {}
            )
            if isinstance(cross_cfg, dict):
                t_qid = cross_cfg.get("target_question_id")
                t_qcode = str(cross_cfg.get("target_question_code") or "").strip().lower()

                if (t_qid in q_ids) or (t_qcode and t_qcode == q_code.lower()):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"Action refusée : Impossible de {action_verb} la question « {q_label} » (code: '{q_code}'). "
                            f"Elle est actuellement utilisée par la question « {o_q.label} » (liaison inter-formulaires) "
                            f"dans la version v{v_num} du formulaire « {f_name} »."
                        ),
                    )

            # b) Vérification dans les formules de calcul
            formula = o_q.formula_expression or (o_q.settings or {}).get("formula_expression")
            if formula and isinstance(formula, str):
                if f"{{{q_code}}}" in formula or f"'{q_code}'" in formula or f'"{q_code}"' in formula:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"Action refusée : Impossible de {action_verb} la question « {q_label} » (code: '{q_code}'). "
                            f"Son code est utilisé dans la formule de calcul de la question « {o_q.label} » "
                            f"dans la version v{v_num} du formulaire « {f_name} »."
                        ),
                    )

            # c) Vérification dans les règles de dépendance conditionnelle
            dep_rules = o_q.dependency_rules or (o_q.settings or {}).get("dependency_rules")
            if dep_rules and isinstance(dep_rules, dict):
                dep_str = json.dumps(dep_rules)
                if q_code in dep_str:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"Action refusée : Impossible de {action_verb} la question « {q_label} » (code: '{q_code}'). "
                            f"Elle est référencée dans les règles de dépendance de la question « {o_q.label} » "
                            f"dans la version v{v_num} du formulaire « {f_name} »."
                        ),
                    )

    # =========================================================================
    # 5. CONTRÔLE SUR LA SUPPRESSION D'UNE VERSION DE FORMULAIRE
    # =========================================================================
    @classmethod
    async def validate_form_version_can_be_deleted(
        cls,
        db: AsyncSession,
        form_id: int,
        version_id: int,
        organization_id: Optional[int] = None,
    ) -> None:
        """
        Vérifie qu'une version de formulaire n'est pas en cours d'utilisation
        par une autre version de formulaire.
        """
        ver_ids: Set[int] = {version_id}
        if organization_id:
            ver_stmt = select(OrganizationFormVersion).options(
                selectinload(OrganizationFormVersion.organization_form)
            ).where(
                OrganizationFormVersion.id == version_id,
                OrganizationFormVersion.organization_id == organization_id,
            )
            v_obj = (await db.execute(ver_stmt)).scalars().first()
        else:
            ver_stmt = select(FormVersion).options(
                selectinload(FormVersion.form)
            ).where(FormVersion.id == version_id)
            v_obj = (await db.execute(ver_stmt)).scalars().first()

        if not v_obj:
            return

        src_vid = getattr(v_obj, "source_form_version_id", None)
        if src_vid:
            ver_ids.add(src_vid)

        parent_f = getattr(v_obj, "organization_form", None) or getattr(v_obj, "form", None)
        f_name = getattr(parent_f, "name", "Formulaire") if parent_f else "Formulaire"
        v_num = v_obj.version_number

        # Chercher les questions d'autres versions qui ciblent cette version de formulaire
        if organization_id:
            q_stmt = select(OrganizationFormQuestion).options(
                selectinload(OrganizationFormQuestion.organization_form),
                selectinload(OrganizationFormQuestion.organization_form_version),
            ).where(
                OrganizationFormQuestion.organization_id == organization_id,
                ~OrganizationFormQuestion.organization_form_version_id.in_(list(ver_ids)),
            )
            other_questions = (await db.execute(q_stmt)).scalars().all()
        else:
            q_stmt = select(FormQuestion).options(
                selectinload(FormQuestion.form),
                selectinload(FormQuestion.version),
            ).where(~FormQuestion.form_version_id.in_(list(ver_ids)))
            other_questions = (await db.execute(q_stmt)).scalars().all()

        for o_q in other_questions:
            cross_cfg = (
                (o_q.settings or {}).get("cross_form_lookup")
                or getattr(o_q, "cross_form_lookup", None)
                or {}
            )
            if isinstance(cross_cfg, dict):
                t_vid = cross_cfg.get("target_form_version_id")
                if t_vid in ver_ids:
                    p_form = getattr(o_q, "organization_form", None) or getattr(o_q, "form", None)
                    p_ver = getattr(o_q, "organization_form_version", None) or getattr(o_q, "version", None)
                    o_fname = getattr(p_form, "name", "Formulaire") if p_form else "Formulaire"
                    o_vnum = getattr(p_ver, "version_number", "1.0.0") if p_ver else "1.0.0"
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=(
                            f"Action refusée : Impossible de supprimer la version v{v_num} du formulaire « {f_name} ». "
                            f"Elle est actuellement ciblée par la question « {o_q.label} » (liaison inter-formulaires) "
                            f"dans la version v{o_vnum} du formulaire « {o_fname} »."
                        ),
                    )

    # =========================================================================
    # HELPERS
    # =========================================================================
    @classmethod
    async def _resolve_resource_info(
        cls,
        db: AsyncSession,
        resource_id: int,
        version_id: Optional[int],
        organization_id: Optional[int],
    ) -> Optional[Dict[str, Any]]:
        """
        Résout l'ensemble des IDs, codes et noms équivalents pour une ressource et sa version
        (couvrant les tables miroirs locales de l'organisation et les tables sources du catalogue).
        """
        res_ids: Set[int] = {resource_id}
        ver_ids: Set[int] = {version_id} if version_id else set()
        res_codes: Set[str] = set()
        res_names: Set[str] = set()

        if organization_id:
            r_stmt = select(OrganizationResource).options(
                selectinload(OrganizationResource.versions)
            ).where(
                (OrganizationResource.id == resource_id) | (OrganizationResource.source_resource_id == resource_id),
                OrganizationResource.organization_id == organization_id,
            )
            org_res = (await db.execute(r_stmt)).scalars().first()
            if org_res:
                res_ids.add(org_res.id)
                if org_res.source_resource_id:
                    res_ids.add(org_res.source_resource_id)
                if org_res.code:
                    res_codes.add(str(org_res.code).strip().lower())
                if org_res.name:
                    res_names.add(str(org_res.name).strip())

                if version_id:
                    for v in (org_res.versions or []):
                        if v.id == version_id or v.source_resource_version_id == version_id:
                            ver_ids.add(v.id)
                            if v.source_resource_version_id:
                                ver_ids.add(v.source_resource_version_id)
        else:
            r_stmt = select(Resource).options(selectinload(Resource.versions)).where(Resource.id == resource_id)
            res = (await db.execute(r_stmt)).scalars().first()
            if res:
                res_ids.add(res.id)
                if res.code:
                    res_codes.add(str(res.code).strip().lower())
                if res.name:
                    res_names.add(str(res.name).strip())

                if version_id:
                    for v in (res.versions or []):
                        if v.id == version_id:
                            ver_ids.add(v.id)

        return {
            "resource_ids": res_ids,
            "version_ids": ver_ids,
            "resource_codes": res_codes,
            "resource_names": res_names,
        }
