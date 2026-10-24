"""
Universal Node-to-Node Condition & Dependency Evaluator for SenWiseTool.

Supports:
1. Question conditioned on another Question
2. Question conditioned on a Section / Sub-section
3. Section / Sub-section conditioned on a Question
4. Section / Sub-section conditioned on another Section / Sub-section
"""

from typing import Dict, Any, List, Optional
import re
import logging

logger = logging.getLogger("app.services.project.condition_evaluator")


class UniversalConditionEvaluator:

    @classmethod
    def evaluate_node(
        cls,
        dependency_rules: Optional[Dict[str, Any]],
        answers_by_code: Dict[str, Any],
        section_metrics_by_code: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Évalue les règles de dépendance d'un nœud (Question ou Section)
        Retourne le statut effectif :
        {
            "is_visible": bool,
            "is_required": Optional[bool],
            "is_applicable": bool,
            "is_enabled": bool,
            "should_reset_value": bool
        }
        """
        # Par défaut, sans règles, l'élément est visible, applicable et activé
        default_state = {
            "is_visible": True,
            "is_required": None,
            "is_applicable": True,
            "is_enabled": True,
            "should_reset_value": False,
        }

        if not dependency_rules or not isinstance(dependency_rules, dict):
            return default_state

        conditions = dependency_rules.get("conditions", [])
        if not conditions or not isinstance(conditions, list):
            return default_state

        logic = str(dependency_rules.get("logic", "AND")).upper()
        actions = dependency_rules.get("actions", {})

        condition_results: List[bool] = []

        for cond in conditions:
            if not isinstance(cond, dict):
                continue
            res = cls._evaluate_single_condition(
                cond, answers_by_code, section_metrics_by_code
            )
            condition_results.append(res)

        if not condition_results:
            return default_state

        if logic == "OR":
            rule_matched = any(condition_results)
        else:
            rule_matched = all(condition_results)

        action_to_apply = (
            actions.get("on_true") if rule_matched else actions.get("on_false")
        )

        return cls._apply_action(action_to_apply, default_state, rule_matched)

    @classmethod
    def _evaluate_single_condition(
        cls,
        cond: Dict[str, Any],
        answers_by_code: Dict[str, Any],
        section_metrics_by_code: Dict[str, Dict[str, Any]],
    ) -> bool:
        source_type = cond.get("source_type", "question").lower()
        source_code = cond.get("source_code")
        operator = str(cond.get("operator", "EQUALS")).upper()
        target_val = cond.get("value")

        if not source_code:
            return True

        if source_type == "question":
            actual_val = answers_by_code.get(source_code)
            return cls._compare_values(actual_val, operator, target_val)

        elif source_type == "section":
            section_metric = section_metrics_by_code.get(source_code, {})
            return cls._compare_section_metric(section_metric, operator, target_val)

        return True

    @classmethod
    def _compare_values(cls, actual: Any, operator: str, target: Any) -> bool:
        if operator == "IS_EMPTY":
            return actual is None or actual == "" or actual == [] or actual == {}

        if operator == "IS_NOT_EMPTY":
            return actual is not None and actual != "" and actual != [] and actual != {}

        if actual is None:
            return operator == "EQUALS" and target is None

        # Opérateurs d'égalité
        if operator == "EQUALS":
            # Normalisation booléenne / string
            if isinstance(target, bool):
                return bool(actual) == target
            return str(actual).strip().lower() == str(target).strip().lower()

        if operator == "NOT_EQUALS":
            if isinstance(target, bool):
                return bool(actual) != target
            return str(actual).strip().lower() != str(target).strip().lower()

        # Opérateurs de contenance
        if operator == "CONTAINS":
            if isinstance(actual, (list, tuple)):
                return any(str(item).lower() == str(target).lower() for item in actual)
            return str(target).lower() in str(actual).lower()

        if operator == "NOT_CONTAINS":
            if isinstance(actual, (list, tuple)):
                return not any(str(item).lower() == str(target).lower() for item in actual)
            return str(target).lower() not in str(actual).lower()

        # Opérateurs numériques
        try:
            num_actual = float(actual)
            num_target = float(target)

            if operator == "GREATER_THAN":
                return num_actual > num_target
            if operator == "LESS_THAN":
                return num_actual < num_target
            if operator == "GREATER_EQUAL":
                return num_actual >= num_target
            if operator == "LESS_EQUAL":
                return num_actual <= num_target
        except (ValueError, TypeError):
            pass

        # Opérateurs d'ensemble IN / NOT_IN
        if operator == "IN":
            if isinstance(target, list):
                return actual in target or str(actual) in [str(t) for t in target]
            return str(actual) in str(target)

        if operator == "NOT_IN":
            if isinstance(target, list):
                return actual not in target and str(actual) not in [str(t) for t in target]
            return str(actual) not in str(target)

        return False

    @classmethod
    def _compare_section_metric(
        cls, section_metric: Dict[str, Any], operator: str, target: Any
    ) -> bool:
        score = section_metric.get("score", 0.0)
        status = section_metric.get("status", "")
        critical_fail = section_metric.get("has_critical_failure", False)
        is_completed = section_metric.get("completed", False)
        is_visible = section_metric.get("is_visible", True)

        try:
            if operator in ["COMPLIANCE_GREATER_EQUAL", "SCORE_GREATER_EQUAL", "GREATER_EQUAL"]:
                return float(score) >= float(target)
            if operator in ["COMPLIANCE_LESS_THAN", "SCORE_LESS_THAN", "LESS_THAN"]:
                return float(score) < float(target)
            if operator in ["COMPLIANCE_GREATER_THAN", "SCORE_GREATER_THAN", "GREATER_THAN"]:
                return float(score) > float(target)
            if operator in ["COMPLIANCE_LESS_EQUAL", "SCORE_LESS_EQUAL", "LESS_EQUAL"]:
                return float(score) <= float(target)
        except (ValueError, TypeError):
            pass

        if operator in ["STATUS_EQUALS", "EQUALS"]:
            return str(status).upper() == str(target).upper()

        if operator in ["STATUS_NOT_EQUALS", "NOT_EQUALS"]:
            return str(status).upper() != str(target).upper()

        if operator == "HAS_CRITICAL_FAILURE":
            return bool(critical_fail) == bool(target)

        if operator == "IS_COMPLETED":
            return bool(is_completed) == bool(target)

        if operator == "IS_VISIBLE":
            return bool(is_visible) == bool(target)

        return False

    @classmethod
    def _apply_action(
        cls, action: Optional[str], default_state: Dict[str, Any], rule_matched: bool
    ) -> Dict[str, Any]:
        state = dict(default_state)
        if not action:
            return state

        act = str(action).upper()

        if act == "SHOW":
            state["is_visible"] = True
            state["is_applicable"] = True
        elif act == "HIDE":
            state["is_visible"] = False
            state["is_applicable"] = False  # Invisible -> exclu des calculs
            state["should_reset_value"] = True
        elif act == "ENABLE":
            state["is_enabled"] = True
        elif act == "DISABLE":
            state["is_enabled"] = False
        elif act == "MAKE_REQUIRED":
            state["is_required"] = True
        elif act == "MAKE_OPTIONAL":
            state["is_required"] = False
        elif act == "APPLICABILITY_TRUE":
            state["is_applicable"] = True
        elif act == "APPLICABILITY_FALSE":
            state["is_applicable"] = False
            state["should_reset_value"] = True
        elif act == "RESET_VALUE":
            state["should_reset_value"] = True

        return state
