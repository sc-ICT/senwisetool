"""
Formula Calculation Engine for Dynamic Forms in SenWiseTool.

Safely evaluates arithmetic and logical expressions using an AST parser
with support for token interpolation ({Q_CODE}), math operators (+, -, *, /, %, **),
and safe built-ins (ROUND, MIN, MAX, ABS, IF).
"""

import ast
import operator
import re
import math
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger("app.services.project.formula_engine")

# Safe operators
OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
    ast.Not: operator.not_,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


class FormulaEngine:

    @classmethod
    def evaluate(
        cls, formula: str, answers_by_code: Dict[str, Any]
    ) -> Optional[Any]:
        """
        Évalue une formule mathématique ou logique avec interpolation des codes de questions et métadonnées.
        Ex: "ROUND({Q_GROSS_WEIGHT} * (1 - ({Q_MOISTURE} - 12) / 100), 2)"
        Ex: "{Q_VAL1} ** 2 * ({Q_VAL2} % 2)"
        Ex: "IF({Q_TAUX} > 50, {Q_PRIX} * 1.2, {Q_PRIX})"
        """
        if not formula or not isinstance(formula, str):
            return None

        # 1. Remplacement des tokens {CODE} par les valeurs réelles
        expression = formula
        token_pattern = re.compile(r"\{([a-zA-Z0-9_-]+)\}")

        def replace_token(match):
            code = match.group(1)
            val = answers_by_code.get(code)
            if val is None or val == "":
                return "0"
            if isinstance(val, bool):
                return "True" if val else "False"
            try:
                # Vérifie que c'est bien convertible en float
                return str(float(val))
            except (ValueError, TypeError):
                # Pour les chaînes (ex dans les IF)
                sanitized_val = str(val).replace("'", "\\'")
                return f"'{sanitized_val}'"

        resolved_expr = token_pattern.sub(replace_token, expression)

        # 2. Parsing sécurisé par AST
        try:
            tree = ast.parse(resolved_expr, mode="eval")
            return cls._eval_ast_node(tree.body)
        except Exception as e:
            logger.warning(
                f"Erreur d'évaluation de formule '{formula}' (résolu: '{resolved_expr}'): {e}"
            )
            return None

    @classmethod
    def _eval_ast_node(cls, node):
        if isinstance(node, ast.Constant):  # Python 3.8+ pour les nombres, strings, booléens
            return node.value

        elif isinstance(node, ast.UnaryOp):
            op_type = type(node.op)
            if op_type in OPERATORS:
                return OPERATORS[op_type](cls._eval_ast_node(node.operand))

        elif isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type in OPERATORS:
                left = cls._eval_ast_node(node.left)
                right = cls._eval_ast_node(node.right)
                return OPERATORS[op_type](left, right)

        elif isinstance(node, ast.BoolOp):
            # and / or
            if isinstance(node.op, ast.And):
                res = True
                for val in node.values:
                    res = cls._eval_ast_node(val)
                    if not res:
                        return res
                return res
            elif isinstance(node.op, ast.Or):
                res = False
                for val in node.values:
                    res = cls._eval_ast_node(val)
                    if res:
                        return res
                return res

        elif isinstance(node, ast.Compare):
            left = cls._eval_ast_node(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = cls._eval_ast_node(comparator)
                op_type = type(op)
                if op_type in OPERATORS:
                    if not OPERATORS[op_type](left, right):
                        return False
                    left = right
            return True

        elif isinstance(node, ast.IfExp):
            # ternary a if condition else b
            test = cls._eval_ast_node(node.test)
            return cls._eval_ast_node(node.body) if test else cls._eval_ast_node(node.orelse)

        elif isinstance(node, ast.Call):
            func_name = node.func.id.upper() if isinstance(node.func, ast.Name) else ""
            args = [cls._eval_ast_node(arg) for arg in node.args]

            if func_name == "ROUND":
                precision = int(args[1]) if len(args) > 1 else 0
                return round(float(args[0]), precision)

            elif func_name == "ABS":
                return abs(float(args[0]))

            elif func_name == "SQRT":
                val = float(args[0])
                return math.sqrt(val) if val >= 0 else 0

            elif func_name == "FLOOR":
                return math.floor(float(args[0]))

            elif func_name == "CEIL":
                return math.ceil(float(args[0]))

            elif func_name == "POW":
                return pow(float(args[0]), float(args[1]))

            elif func_name == "MOD":
                return float(args[0]) % float(args[1])

            elif func_name == "MIN":
                return min(*[float(a) for a in args])

            elif func_name == "MAX":
                return max(*[float(a) for a in args])

            elif func_name == "IF":
                # IF(condition, value_if_true, value_if_false)
                cond = bool(args[0])
                return args[1] if cond else (args[2] if len(args) > 2 else 0)

        raise ValueError(f"Expression or node not permitted: {ast.dump(node)}")

