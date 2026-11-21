"""Safe Decimal calculator for the public `calculator` action.

The policy sends an arbitrary string; this module must never execute it.
There is no eval/exec/compile/ast here: the input is tokenised by hand and
parsed by a small recursive-descent parser over a fixed grammar.

Grammar (ASCII only, spaces and tabs ignored):

    expr    := term   (("+" | "-") term)*
    term    := unary  (("*" | "/") unary)*
    unary   := ("+" | "-") unary | primary
    primary := NUMBER | "(" expr ")"
    NUMBER  := DIGITS ["." DIGITS] | "." DIGITS

Not supported on purpose: names, function calls, attributes, strings,
exponent notation (1e9), the power operator, `%`, `//`. Anything outside the
grammar is a controlled `CalculatorError`, never a crash.

Result: a plain decimal string without exponent and without trailing zeros,
e.g. "0.1+0.2" -> "0.3", "2.50*2" -> "5", "1/3" -> 34 significant digits.
Rounding of money to minor units is the policy's job (see policy.json);
the calculator does not round to currency exponent.
"""

from __future__ import annotations

import decimal
from dataclasses import dataclass
from typing import List, Optional

__all__ = [
    "CalculatorError",
    "evaluate",
    "try_evaluate",
    "MAX_EXPRESSION_LENGTH",
    "MAX_TOKENS",
    "MAX_DEPTH",
    "MAX_LITERAL_DIGITS",
]

# --- limits -----------------------------------------------------------------

MAX_EXPRESSION_LENGTH = 512
MAX_TOKENS = 256
MAX_DEPTH = 32
MAX_LITERAL_DIGITS = 30

_PRECISION = 34  # decimal128
_EMAX = 99  # |result| < 10**100: far beyond any ledger amount
_EMIN = -99

# --- public error codes (stable; text is not evaluated by the jury) ---------

ERR_TYPE = "calc_invalid_type"
ERR_TOO_LONG = "calc_too_long"
ERR_TOO_COMPLEX = "calc_too_complex"
ERR_SYNTAX = "calc_syntax_error"
ERR_DIV_ZERO = "calc_division_by_zero"
ERR_OVERFLOW = "calc_overflow"


class CalculatorError(Exception):
    """Controlled error. `code` and `message` are safe to show the policy.

    The message never echoes the input expression, only a position.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code, message)
        self.code = code
        self.message = message

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.code}: {self.message}"

    def to_public(self) -> dict:
        return {"code": self.code, "message": self.message}


# --- tokenizer --------------------------------------------------------------

_DIGITS = frozenset("0123456789")
_OPERATORS = frozenset("+-*/()")
_SPACES = frozenset(" \t")


@dataclass(frozen=True)
class _Token:
    kind: str  # "num" | "op" | "end"
    value: str
    pos: int


def _tokenize(text: str) -> List[_Token]:
    tokens: List[_Token] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in _SPACES:
            i += 1
            continue
        if ch in _OPERATORS:
            tokens.append(_Token("op", ch, i))
            i += 1
        elif ch in _DIGITS or ch == ".":
            start = i
            while i < n and text[i] in _DIGITS:
                i += 1
            if i < n and text[i] == ".":
                i += 1
                frac_start = i
                while i < n and text[i] in _DIGITS:
                    i += 1
                if i == frac_start:
                    raise CalculatorError(
                        ERR_SYNTAX, f"digits expected after '.' at position {i}"
                    )
            literal = text[start:i]
            if literal == ".":
                raise CalculatorError(ERR_SYNTAX, f"bad number at position {start}")
            if sum(c in _DIGITS for c in literal) > MAX_LITERAL_DIGITS:
                raise CalculatorError(
                    ERR_TOO_COMPLEX, f"number too long at position {start}"
                )
            tokens.append(_Token("num", literal, start))
        else:
            raise CalculatorError(
                ERR_SYNTAX, f"unsupported character at position {i}"
            )
        if len(tokens) > MAX_TOKENS:
            raise CalculatorError(ERR_TOO_COMPLEX, "too many tokens")
    tokens.append(_Token("end", "", n))
    return tokens


# --- parser / evaluator -----------------------------------------------------


class _Parser:
    def __init__(self, tokens: List[_Token]) -> None:
        self._tokens = tokens
        self._i = 0
        self._depth = 0

    def _peek(self) -> _Token:
        return self._tokens[self._i]

    def _take(self) -> _Token:
        tok = self._tokens[self._i]
        self._i += 1
        return tok

    def _enter(self) -> None:
        self._depth += 1
        if self._depth > MAX_DEPTH:
            raise CalculatorError(ERR_TOO_COMPLEX, "expression nested too deeply")

    def _leave(self) -> None:
        self._depth -= 1

    def parse(self) -> decimal.Decimal:
        if self._peek().kind == "end":
            raise CalculatorError(ERR_SYNTAX, "empty expression")
        value = self._expr()
        tok = self._peek()
        if tok.kind != "end":
            raise CalculatorError(ERR_SYNTAX, f"unexpected token at position {tok.pos}")
        return value

    def _expr(self) -> decimal.Decimal:
        value = self._term()
        while self._peek().kind == "op" and self._peek().value in "+-":
            op = self._take().value
            rhs = self._term()
            value = value + rhs if op == "+" else value - rhs
        return value

    def _term(self) -> decimal.Decimal:
        value = self._unary()
        while self._peek().kind == "op" and self._peek().value in "*/":
            op = self._take()
            rhs = self._unary()
            if op.value == "*":
                value = value * rhs
            else:
                if rhs.is_zero():
                    raise CalculatorError(
                        ERR_DIV_ZERO, f"division by zero at position {op.pos}"
                    )
                value = value / rhs
        return value

    def _unary(self) -> decimal.Decimal:
        tok = self._peek()
        if tok.kind == "op" and tok.value in "+-":
            self._take()
            self._enter()
            try:
                operand = self._unary()
            finally:
                self._leave()
            return -operand if tok.value == "-" else +operand
        return self._primary()

    def _primary(self) -> decimal.Decimal:
        tok = self._take()
        if tok.kind == "num":
            return decimal.Decimal(tok.value)
        if tok.kind == "op" and tok.value == "(":
            self._enter()
            try:
                value = self._expr()
            finally:
                self._leave()
            close = self._take()
            if close.kind != "op" or close.value != ")":
                raise CalculatorError(
                    ERR_SYNTAX, f"')' expected at position {close.pos}"
                )
            return value
        if tok.kind == "end":
            raise CalculatorError(ERR_SYNTAX, "unexpected end of expression")
        raise CalculatorError(ERR_SYNTAX, f"unexpected token at position {tok.pos}")


def _context() -> decimal.Context:
    return decimal.Context(
        prec=_PRECISION,
        rounding=decimal.ROUND_HALF_EVEN,
        Emax=_EMAX,
        Emin=_EMIN,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[decimal.InvalidOperation, decimal.DivisionByZero, decimal.Overflow],
    )


def _format(value: decimal.Decimal) -> str:
    if value.is_zero():
        return "0"
    text = format(value.normalize(), "f")
    return text


def evaluate(expression: object) -> str:
    """Evaluate `expression` and return a decimal string.

    Raises CalculatorError for anything that is not valid arithmetic.
    Never raises any other exception type for any input.
    """
    if not isinstance(expression, str):
        raise CalculatorError(ERR_TYPE, "expression must be a string")
    if len(expression) > MAX_EXPRESSION_LENGTH:
        raise CalculatorError(
            ERR_TOO_LONG, f"expression longer than {MAX_EXPRESSION_LENGTH} characters"
        )
    try:
        with decimal.localcontext(_context()):
            tokens = _tokenize(expression)
            value = _Parser(tokens).parse()
            # Re-apply context so the final value respects precision/limits.
            value = +value
            return _format(value)
    except CalculatorError:
        raise
    except decimal.Overflow:
        raise CalculatorError(ERR_OVERFLOW, "result out of range") from None
    except decimal.DivisionByZero:
        raise CalculatorError(ERR_DIV_ZERO, "division by zero") from None
    except (decimal.DecimalException, ArithmeticError):
        raise CalculatorError(ERR_SYNTAX, "invalid arithmetic") from None
    except RecursionError:  # defence in depth; MAX_DEPTH should stop earlier
        raise CalculatorError(ERR_TOO_COMPLEX, "expression nested too deeply") from None


def try_evaluate(expression: object) -> "tuple[Optional[str], Optional[dict]]":
    """Convenience for the env step: returns (result, error_object)."""
    try:
        return evaluate(expression), None
    except CalculatorError as exc:
        return None, exc.to_public()
