"""
Compliance & Multi-level Scoring Engine for SenWiseTool Dynamic Forms.

Features:
- N/A exclusion: Questions conditioned out or marked NOT_APPLICABLE are excluded from denominators.
- Critical Gatekeeping: Triggers critical failure if any is_critical question fails.
- Recursive rollup: Questions -> Sub-sections -> Sections -> Form.
- Category breakdowns: Legal compliance, Deforestation, Traceability, Environmental, Social.
"""

from typing import Dict, Any, List, Optional
from decimal import Decimal
import logging

logger = logging.getLogger("app.services.project.compliance_scorer")


class ComplianceScorer:

    @classmethod
    def evaluate_submission(
        cls,
        form_sections: List[Any],
        answers_dict: Dict[str, Any],  # keyed by question_code -> dict with 'value', 'compliance_choice', 'is_applicable'
        passing_threshold_pct: float = 80.0,
        critical_threshold: int = 1,
    ) -> Dict[str, Any]:
        """
        Calcule les scores granulaires de conformité pour toutes les sections
        et la note globale du formulaire.
        """
        critical_failures_count = 0
        section_results: Dict[str, Dict[str, Any]] = {}
        category_scores: Dict[str, Dict[str, float]] = {}

        total_form_weighted_points = 0.0
        total_form_max_weighted_points = 0.0

        for section in form_sections:
            sec_res = cls._evaluate_section(section, answers_dict)
            section_results[section.code] = sec_res
            critical_failures_count += sec_res["critical_failures_count"]

            sec_weight = float(section.weight or 1.0)
            if sec_res["applicable_questions_count"] > 0:
                total_form_weighted_points += sec_res["score"] * sec_weight
                total_form_max_weighted_points += 100.0 * sec_weight

            # Agrégation par catégorie (ex: EUDR, Droits Humains...)
            cat = section.scoring_category or "general"
            if cat not in category_scores:
                category_scores[cat] = {"points": 0.0, "max_points": 0.0}
            if sec_res["applicable_questions_count"] > 0:
                category_scores[cat]["points"] += sec_res["score"] * sec_weight
                category_scores[cat]["max_points"] += 100.0 * sec_weight

        # Calcul du score global normalisé sur 100%
        if total_form_max_weighted_points > 0:
            overall_score = round(
                (total_form_weighted_points / total_form_max_weighted_points) * 100.0, 2
            )
        else:
            overall_score = 100.0

        # Détermination du statut de conformité
        if critical_failures_count >= critical_threshold:
            compliance_status = "NON_COMPLIANT"
            status_reason = f"Rejet critique : {critical_failures_count} non-conformité(s) majeure(s) détectée(s)."
        elif overall_score >= passing_threshold_pct:
            compliance_status = "COMPLIANT"
            status_reason = f"Conforme : score obtenu {overall_score}% >= seuil {passing_threshold_pct}%."
        elif overall_score >= (passing_threshold_pct * 0.7):
            compliance_status = "PARTIALLY_COMPLIANT"
            status_reason = f"Conformité partielle : score obtenu {overall_score}% < seuil {passing_threshold_pct}%."
        else:
            compliance_status = "NON_COMPLIANT"
            status_reason = f"Non conforme : score obtenu {overall_score}% < seuil {passing_threshold_pct}%."

        # Catégories formatées en pourcentages
        formatted_categories = {}
        for cat_name, cat_data in category_scores.items():
            if cat_data["max_points"] > 0:
                formatted_categories[cat_name] = round(
                    (cat_data["points"] / cat_data["max_points"]) * 100.0, 2
                )
            else:
                formatted_categories[cat_name] = 100.0

        return {
            "overall_score": overall_score,
            "compliance_status": compliance_status,
            "status_reason": status_reason,
            "critical_failures_count": critical_failures_count,
            "section_scores": section_results,
            "category_scores": formatted_categories,
        }

    @classmethod
    def _evaluate_section(
        cls, section: Any, answers_dict: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Évalue récursivement une section et ses sous-sections.
        """
        sec_points = 0.0
        sec_max_points = 0.0
        critical_failures = 0
        applicable_count = 0
        evaluated_questions = []

        # 1. Évaluation des questions directes de la section
        questions = getattr(section, "questions", []) or []
        for q in questions:
            ans = answers_dict.get(q.code, {})
            is_applicable = ans.get("is_applicable", True)

            # Si l'utilisateur ou une condition a marqué la question non applicable -> EXCLUE du dénominateur !
            if not is_applicable or ans.get("compliance_choice") == "NOT_APPLICABLE":
                evaluated_questions.append({
                    "question_code": q.code,
                    "is_applicable": False,
                    "score": None,
                    "is_critical_failure": False,
                })
                continue

            applicable_count += 1
            q_weight = float(q.weight or 1.0)
            max_q_score = 100.0 * q_weight
            sec_max_points += max_q_score

            choice = ans.get("compliance_choice")
            is_crit_fail = False

            # Évaluation selon le type
            if q.question_type == "audit_compliance":
                if choice == "COMPLIANT":
                    awarded = 100.0 * q_weight
                elif choice == "NON_COMPLIANT":
                    awarded = 0.0
                    if q.is_critical:
                        is_crit_fail = True
                        critical_failures += 1
                else:
                    awarded = 100.0 * q_weight
            else:
                # Autres types (ex: boolean, select, radio avec options scoring)
                val = ans.get("value")
                if val is True or val == "YES" or val == "OUI":
                    awarded = 100.0 * q_weight
                elif val is False or val == "NO" or val == "NON":
                    awarded = 0.0
                    if q.is_critical:
                        is_crit_fail = True
                        critical_failures += 1
                else:
                    # Score par défaut ou plein si rempli
                    awarded = (100.0 * q_weight) if val is not None and val != "" else 0.0

            sec_points += awarded
            evaluated_questions.append({
                "question_code": q.code,
                "is_applicable": True,
                "score": awarded / q_weight if q_weight > 0 else 0.0,
                "is_critical_failure": is_crit_fail,
            })

        # 2. Évaluation récursive des sous-sections
        subsections = getattr(section, "subsections", []) or []
        sub_results = {}
        for sub in subsections:
            sub_res = cls._evaluate_section(sub, answers_dict)
            sub_results[sub.code] = sub_res
            critical_failures += sub_res["critical_failures_count"]
            applicable_count += sub_res["applicable_questions_count"]

            sub_weight = float(sub.weight or 1.0)
            if sub_res["applicable_questions_count"] > 0:
                sec_points += (sub_res["score"] / 100.0) * (100.0 * sub_weight)
                sec_max_points += 100.0 * sub_weight

        # Normalisation du score de la section
        if sec_max_points > 0:
            section_score = round((sec_points / sec_max_points) * 100.0, 2)
        else:
            section_score = 100.0

        min_pass = float(section.min_passing_score) if section.min_passing_score else 80.0
        sec_status = "COMPLIANT" if (section_score >= min_pass and critical_failures == 0) else "NON_COMPLIANT"

        return {
            "section_code": section.code,
            "title": section.title,
            "score": section_score,
            "status": sec_status,
            "critical_failures_count": critical_failures,
            "has_critical_failure": critical_failures > 0,
            "applicable_questions_count": applicable_count,
            "questions": evaluated_questions,
            "subsections": sub_results,
        }
