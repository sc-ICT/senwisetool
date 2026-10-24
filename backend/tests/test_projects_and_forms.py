import pytest
from app.services.project.condition_evaluator import UniversalConditionEvaluator
from app.services.project.formula_engine import FormulaEngine
from app.services.project.compliance_scorer import ComplianceScorer


def test_question_to_question_dependency():
    """Test Question conditionnée par rapport à une autre Question"""
    rules = {
        "logic": "AND",
        "conditions": [
            {
                "source_type": "question",
                "source_code": "Q_HAS_DEFECTS",
                "operator": "EQUALS",
                "value": "YES"
            }
        ],
        "actions": {
            "on_true": "SHOW",
            "on_false": "HIDE"
        }
    }

    # Cas 1: La condition est remplie -> Doit être visible
    eval_true = UniversalConditionEvaluator.evaluate_node(
        rules,
        answers_by_code={"Q_HAS_DEFECTS": "YES"},
        section_metrics_by_code={}
    )
    assert eval_true["is_visible"] is True
    assert eval_true["is_applicable"] is True

    # Cas 2: La condition n'est pas remplie -> Doit être masquée et inapplicable
    eval_false = UniversalConditionEvaluator.evaluate_node(
        rules,
        answers_by_code={"Q_HAS_DEFECTS": "NO"},
        section_metrics_by_code={}
    )
    assert eval_false["is_visible"] is False
    assert eval_false["is_applicable"] is False
    assert eval_false["should_reset_value"] is True


def test_section_to_question_dependency():
    """Test Question conditionnée par rapport à une Section / Sous-section"""
    rules = {
        "logic": "AND",
        "conditions": [
            {
                "source_type": "section",
                "source_code": "SEC_PRELIMINARY",
                "operator": "COMPLIANCE_GREATER_EQUAL",
                "value": 75.0
            }
        ],
        "actions": {
            "on_true": "SHOW",
            "on_false": "HIDE"
        }
    }

    # Score de la section >= 75 -> question affichée
    res_pass = UniversalConditionEvaluator.evaluate_node(
        rules,
        answers_by_code={},
        section_metrics_by_code={"SEC_PRELIMINARY": {"score": 85.0, "status": "COMPLIANT"}}
    )
    assert res_pass["is_visible"] is True

    # Score de la section < 75 -> question masquée
    res_fail = UniversalConditionEvaluator.evaluate_node(
        rules,
        answers_by_code={},
        section_metrics_by_code={"SEC_PRELIMINARY": {"score": 60.0, "status": "NON_COMPLIANT"}}
    )
    assert res_fail["is_visible"] is False


def test_question_to_section_dependency():
    """Test Section / Sous-section conditionnée par rapport à une Question"""
    rules = {
        "logic": "AND",
        "conditions": [
            {
                "source_type": "question",
                "source_code": "Q_FARMING_MODE",
                "operator": "EQUALS",
                "value": "ORGANIC"
            }
        ],
        "actions": {
            "on_true": "SHOW",
            "on_false": "HIDE"
        }
    }

    # Mode Bio -> Afficher la section d'audit Bio
    res_bio = UniversalConditionEvaluator.evaluate_node(
        rules,
        answers_by_code={"Q_FARMING_MODE": "ORGANIC"},
        section_metrics_by_code={}
    )
    assert res_bio["is_visible"] is True

    # Mode Conventionnel -> Masquer la section Bio
    res_conv = UniversalConditionEvaluator.evaluate_node(
        rules,
        answers_by_code={"Q_FARMING_MODE": "CONVENTIONAL"},
        section_metrics_by_code={}
    )
    assert res_conv["is_visible"] is False


def test_section_to_section_dependency():
    """Test Section / Sous-section conditionnée par rapport à une autre Section"""
    rules = {
        "logic": "AND",
        "conditions": [
            {
                "source_type": "section",
                "source_code": "SEC_STEP_1",
                "operator": "STATUS_EQUALS",
                "value": "COMPLIANT"
            }
        ],
        "actions": {
            "on_true": "ENABLE",
            "on_false": "DISABLE"
        }
    }

    # Étape 1 conforme -> Étape 2 activée
    res_enabled = UniversalConditionEvaluator.evaluate_node(
        rules,
        answers_by_code={},
        section_metrics_by_code={"SEC_STEP_1": {"status": "COMPLIANT", "score": 90.0}}
    )
    assert res_enabled["is_enabled"] is True

    # Étape 1 non conforme -> Étape 2 désactivée
    res_disabled = UniversalConditionEvaluator.evaluate_node(
        rules,
        answers_by_code={},
        section_metrics_by_code={"SEC_STEP_1": {"status": "NON_COMPLIANT", "score": 40.0}}
    )
    assert res_disabled["is_enabled"] is False


def test_formula_engine():
    """Test du moteur de calcul d'équations arithmétiques et logiques"""
    answers = {
        "Q_GROSS_WEIGHT": 1200,
        "Q_MOISTURE": 14,
        "Q_SURFACE": 3.5,
    }

    # Calcul du poids net ajusté à l'humidité de référence (12%)
    # 1200 * (1 - (14 - 12)/100) = 1200 * 0.98 = 1176.0
    formula_net = "ROUND({Q_GROSS_WEIGHT} * (1 - ({Q_MOISTURE} - 12) / 100), 2)"
    net_weight = FormulaEngine.evaluate(formula_net, answers)
    assert net_weight == 1176.0

    # Rendement = poids / surface
    # 1176 / 3.5 = 336.0
    formula_yield = "ROUND(1176.0 / {Q_SURFACE}, 1)"
    calc_yield = FormulaEngine.evaluate(formula_yield, answers)
    assert calc_yield == 336.0


def test_compliance_scoring_with_na_exclusion():
    """Test du scoring de conformité avec exclusion stricte des questions non applicables"""
    class MockQuestion:
        def __init__(self, code, q_type="audit_compliance", weight=1.0, is_critical=False):
            self.code = code
            self.question_type = q_type
            self.weight = weight
            self.is_critical = is_critical

    class MockSection:
        def __init__(self, code, questions, subsections=None, weight=1.0, scoring_category="legal"):
            self.code = code
            self.title = f"Section {code}"
            self.questions = questions
            self.subsections = subsections or []
            self.weight = weight
            self.scoring_category = scoring_category
            self.min_passing_score = 80.0

    # 3 questions : Q1 Conforme (100%), Q2 Non-applicable (doit être exclue !), Q3 Conforme (100%)
    sec = MockSection(
        "SEC_LEGAL",
        questions=[
            MockQuestion("Q1"),
            MockQuestion("Q2"),
            MockQuestion("Q3"),
        ]
    )

    answers = {
        "Q1": {"compliance_choice": "COMPLIANT", "is_applicable": True},
        "Q2": {"compliance_choice": "NOT_APPLICABLE", "is_applicable": False},
        "Q3": {"compliance_choice": "COMPLIANT", "is_applicable": True},
    }

    result = ComplianceScorer.evaluate_submission([sec], answers)

    # Comme Q2 est N/A, seules Q1 et Q3 comptent -> 200 / 200 = 100% (pas 200/300 = 66.67% !)
    assert result["overall_score"] == 100.0
    assert result["compliance_status"] == "COMPLIANT"
    assert result["critical_failures_count"] == 0


def test_compliance_scoring_critical_failure():
    """Test du blocage immédiat lors d'un échec sur une question critique"""
    class MockQuestion:
        def __init__(self, code, is_critical=False):
            self.code = code
            self.question_type = "audit_compliance"
            self.weight = 1.0
            self.is_critical = is_critical

    class MockSection:
        def __init__(self, code, questions):
            self.code = code
            self.title = f"Section {code}"
            self.questions = questions
            self.subsections = []
            self.weight = 1.0
            self.scoring_category = "deforestation"
            self.min_passing_score = 80.0

    # Q_CRIT est critique (ex: déforestation illégale après 2020)
    sec = MockSection(
        "SEC_EUDR",
        questions=[
            MockQuestion("Q_LEGAL_1", is_critical=False),
            MockQuestion("Q_CRIT", is_critical=True),
        ]
    )

    answers = {
        "Q_LEGAL_1": {"compliance_choice": "COMPLIANT", "is_applicable": True},
        "Q_CRIT": {"compliance_choice": "NON_COMPLIANT", "is_applicable": True},
    }

    result = ComplianceScorer.evaluate_submission([sec], answers, critical_threshold=1)

    assert result["critical_failures_count"] == 1
    assert result["compliance_status"] == "NON_COMPLIANT"
    assert "Rejet critique" in result["status_reason"]


def test_blueprint_export_structure():
    """Vérifie la sérialisation conforme d'un blueprint de modèle de projet pour les plugins"""
    blueprint = {
        "blueprint_version": "1.0.0",
        "project": {
            "name": "Modèle EUDR 2026",
            "code": "TMPL_EUDR_2026",
            "category": "eudr_compliance",
            "project_type": "audit",
            "is_template": True,
            "template_governance": {
                "allow_tenant_add_sections": True,
                "allow_tenant_add_questions": True,
                "allow_tenant_modify_threshold": False,
                "enforce_critical_gates": True,
                "sanctuarized_sections": ["SEC_LEGALITY", "SEC_DEFORESTATION"],
                "sanctuarized_questions": ["Q_GPS_POLYGON", "Q_CUTOFF_DATE"],
            }
        },
        "forms": [
            {
                "name": "Formulaire d'Audit EUDR",
                "code": "FORM_EUDR_V1",
                "is_sanctuarized": True,
                "allow_custom_sections": True,
                "passing_score_percentage": 85.0,
                "critical_fail_threshold": 1,
                "sections": [
                    {
                        "title": "Légalité & Déforestation",
                        "code": "SEC_DEFORESTATION",
                        "is_sanctuarized": True,
                        "allow_custom_questions": False,
                        "is_critical": True,
                        "questions": [
                            {
                                "code": "Q_CUTOFF_DATE",
                                "label": "Absence de déforestation après le 31/12/2020",
                                "question_type": "audit_compliance",
                                "is_sanctuarized": True,
                                "is_critical": True,
                                "weight": 2.0
                            }
                        ],
                        "subsections": []
                    }
                ]
            }
        ]
    }

    assert blueprint["blueprint_version"] == "1.0.0"
    assert blueprint["project"]["is_template"] is True
    assert blueprint["project"]["template_governance"]["enforce_critical_gates"] is True
    assert len(blueprint["forms"]) == 1
    form = blueprint["forms"][0]
    assert form["is_sanctuarized"] is True
    assert form["sections"][0]["is_sanctuarized"] is True
    assert form["sections"][0]["questions"][0]["is_critical"] is True

