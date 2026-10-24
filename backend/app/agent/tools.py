"""Deterministic, side-effect-free tools available to the agent.

There is deliberately no shell, network, or free-form database tool. Document tools are
closures bound to the caller's tenant by the chat service, so they cannot reach other tenants.
"""

import ast
import operator
import re
from collections.abc import Callable
from typing import Any

MAX_EXPRESSION_CHARS = 200
MAX_EXPONENT = 100
MAX_MAGNITUDE = 1e15

_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class CalculationError(ValueError):
    pass


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return float(_UNARY[type(node.op)](_evaluate(node.operand)))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise CalculationError("Exponent is too large")
        try:
            value = float(_BINARY[type(node.op)](left, right))
        except ZeroDivisionError:
            raise CalculationError("Division by zero") from None
        except TypeError:  # e.g. a complex result from (-8) ** 0.5
            raise CalculationError("The result is not a real number") from None
        except OverflowError:
            raise CalculationError("Result is too large") from None
        if abs(value) > MAX_MAGNITUDE:
            raise CalculationError("Result is too large")
        return value
    raise CalculationError("Only numbers and + - * / // % ** ( ) are allowed")


def calculate(expression: str) -> str:
    """Evaluate arithmetic via a whitelisted AST walk (never ``eval``)."""
    cleaned = expression.strip().replace("^", "**").replace("×", "*").replace("÷", "/")
    cleaned = re.sub(r"(?<=\d),(?=\d{3}\b)", "", cleaned)  # 1,000 -> 1000
    if not cleaned or len(cleaned) > MAX_EXPRESSION_CHARS:
        raise CalculationError("Expression is empty or too long")
    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError:
        raise CalculationError("Not a valid arithmetic expression") from None
    value = _evaluate(tree)
    return f"{value:.10g}"


_INJECTION_PATTERNS = re.compile(
    r"(ignore|disregard|forget)\s+(all\s+|any\s+|the\s+)?(previous|prior|above|earlier)\s+"
    r"(instructions?|prompts?|rules?|messages?)"
    r"|you\s+are\s+now\s+|new\s+instructions?\s*:|system\s+prompt|reveal\s+(your|the)\s+(prompt|instructions?)"
    r"|act\s+as\s+(an?\s+)?(unrestricted|jailbroken|developer)|<\s*/?\s*system\s*>",
    re.IGNORECASE,
)


def looks_like_injection(text: str) -> bool:
    """Heuristic flag for instruction-like text in retrieved documents.

    It is a signal for the UI and the prompt, not a security boundary: the real defences are
    the prompt structure, the router not seeing documents, and output citation validation.
    """
    return bool(_INJECTION_PATTERNS.search(text))
