"""Contract tests for the calculator action (C09, C10 + edge cases).

Runs with stdlib only:  python -m unittest discover -s tests -t .
(pytest also collects these.)
"""

from __future__ import annotations

import builtins
import decimal
import os
import random
import sys
import unittest
from unittest import mock

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from genesis_audit.env import calculator as calc  # noqa: E402
from genesis_audit.env.calculator import CalculatorError, evaluate, try_evaluate  # noqa: E402


class C09DecimalArithmetic(unittest.TestCase):
    def test_c09_point_one_plus_point_two(self):
        result = evaluate("0.1+0.2")
        self.assertIsInstance(result, str)
        self.assertEqual(result, "0.3")
        self.assertEqual(decimal.Decimal(result), decimal.Decimal("0.3"))

    def test_basic_operations(self):
        cases = {
            "1+2": "3",
            "10-4": "6",
            "6*7": "42",
            "7/2": "3.5",
            "2+3*4": "14",
            "(2+3)*4": "20",
            "10-4-3": "3",  # left associative
            "100/10/2": "5",  # left associative
            "-5+2": "-3",
            "--5": "5",
            "+5": "5",
            "-(2-7)": "5",
            "2*-3": "-6",
            " 1 +\t2 ": "3",
            ".5+.5": "1",
        }
        for expr, expected in cases.items():
            with self.subTest(expr=expr):
                self.assertEqual(evaluate(expr), expected)

    def test_split_payment_example_from_case(self):
        # invoice 120000 paid 50000 + 70000, only 20000 allocated
        self.assertEqual(evaluate("120000-50000-70000"), "0")
        self.assertEqual(evaluate("70000-20000"), "50000")

    def test_fx_conversion_precision(self):
        self.assertEqual(evaluate("1250.75*478.12"), "598008.59")
        # 34 significant digits, ROUND_HALF_EVEN: 100/3 is rounded before *3
        self.assertEqual(evaluate("100/3*3"), "99.99999999999999999999999999999999")
        third = evaluate("1/3")
        self.assertEqual(third, "0." + "3" * 34)

    def test_result_has_no_exponent_and_no_trailing_zeros(self):
        self.assertEqual(evaluate("2.50*2"), "5")
        self.assertEqual(evaluate("100*100"), "10000")
        self.assertEqual(evaluate("1000000000000*1000000000000"), "1" + "0" * 24)
        self.assertEqual(evaluate("0.000001*0.000001"), "0.000000000001")
        for r in (evaluate("100*100"), evaluate("0.000001*0.000001")):
            self.assertNotIn("E", r.upper())

    def test_zero_normalised(self):
        self.assertEqual(evaluate("0-0"), "0")
        self.assertEqual(evaluate("-0"), "0")
        self.assertEqual(evaluate("0.00*5"), "0")

    def test_large_amounts_exact(self):
        self.assertEqual(
            evaluate("999999999999999.99+0.01"), "1000000000000000"
        )

    def test_global_decimal_context_untouched(self):
        before = decimal.getcontext().copy()
        evaluate("1/3")
        try:
            evaluate("1/0")
        except CalculatorError:
            pass
        after = decimal.getcontext()
        self.assertEqual(before.prec, after.prec)
        self.assertEqual(before.Emax, after.Emax)
        self.assertEqual(before.traps, after.traps)


class C10NoCodeExecution(unittest.TestCase):
    ATTACKS = [
        "open('/etc/passwd').read()",
        "__import__('os').system('id')",
        "eval('1+1')",
        "exec('x=1')",
        "1+__import__('os').getcwd()",
        "().__class__.__bases__[0].__subclasses__()",
        "lambda: 1",
        "[1,2,3]",
        "{'a': 1}",
        "'abc'",
        '"1"+"2"',
        "x",
        "x+1",
        "1 if 1 else 2",
        "1;2",
        "1\n+2",
        "1,2",
        "abs(-1)",
        "round(1.5)",
        "Decimal('1')",
        "2**10",
        "9**9**9**9",
        "7%3",
        "7//2",
        "1e5",
        "1E5",
        "Infinity",
        "NaN",
        "inf",
        "1_000",
        "0x10",
        "1j",
        "1 & 1",
        "~1",
        "1 << 2",
        "a.b",
        "../../truth.json",
        "/etc/passwd",
        "file:///etc/passwd",
        "$(cat /etc/passwd)",
        "`id`",
        "1+2; DROP TABLE ledger",
        "١+٢",  # Arabic-Indic digits
        "１+２",  # full-width digits
        "1 +2",  # non-breaking space
        "1+2\x00",
        "",
        "   ",
        "(",
        ")",
        "(1+2",
        "1+2)",
        "()",
        "1+",
        "*1",
        "1**2",
        "1..2",
        "1.2.3",
        ".",
        "5.",
        "1 2",
    ]

    def test_c10_attacks_are_controlled_errors(self):
        for expr in self.ATTACKS:
            with self.subTest(expr=expr):
                with self.assertRaises(CalculatorError) as ctx:
                    evaluate(expr)
                err = ctx.exception
                self.assertTrue(err.code.startswith("calc_"))
                public = err.to_public()
                self.assertEqual(set(public), {"code", "message"})
                # message never echoes the input
                if len(expr.strip()) > 2:
                    self.assertNotIn(expr.strip(), public["message"])

    def test_c10_no_eval_exec_or_open_is_called(self):
        def boom(*_a, **_k):
            raise AssertionError("forbidden builtin was called")

        with mock.patch.object(builtins, "eval", boom), mock.patch.object(
            builtins, "exec", boom
        ), mock.patch.object(builtins, "compile", boom), mock.patch.object(
            builtins, "open", boom
        ), mock.patch.object(builtins, "__import__", boom):
            self.assertEqual(evaluate("0.1+0.2"), "0.3")
            for expr in self.ATTACKS:
                with self.assertRaises(CalculatorError):
                    evaluate(expr)

    def test_c10_source_has_no_dynamic_execution(self):
        with open(calc.__file__, encoding="utf-8") as fh:
            source = fh.read()
        code_only = source.split('"""', 2)[2]  # drop module docstring
        for banned in ("eval(", "exec(", "compile(", "import ast", "__import__",
                       "subprocess", "os.system", "open(", "getattr("):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, code_only)

    def test_non_string_inputs(self):
        for value in (None, 1, 1.5, [], {}, b"1+1", decimal.Decimal("1"), True):
            with self.subTest(value=value):
                with self.assertRaises(CalculatorError) as ctx:
                    evaluate(value)
                self.assertEqual(ctx.exception.code, calc.ERR_TYPE)


class CalculatorLimits(unittest.TestCase):
    def test_division_by_zero(self):
        for expr in ("1/0", "1/(2-2)", "0/0", "1/0.000"):
            with self.subTest(expr=expr):
                with self.assertRaises(CalculatorError) as ctx:
                    evaluate(expr)
                self.assertEqual(ctx.exception.code, calc.ERR_DIV_ZERO)

    def test_too_long(self):
        expr = "1+" * (calc.MAX_EXPRESSION_LENGTH // 2) + "1"
        with self.assertRaises(CalculatorError) as ctx:
            evaluate(expr)
        self.assertEqual(ctx.exception.code, calc.ERR_TOO_LONG)

    def test_too_many_tokens(self):
        expr = "+".join(["1"] * (calc.MAX_TOKENS // 2 + 1))
        self.assertLessEqual(len(expr), calc.MAX_EXPRESSION_LENGTH)
        with self.assertRaises(CalculatorError) as ctx:
            evaluate(expr)
        self.assertEqual(ctx.exception.code, calc.ERR_TOO_COMPLEX)

    def test_deep_parentheses(self):
        ok = "(" * calc.MAX_DEPTH + "1" + ")" * calc.MAX_DEPTH
        self.assertEqual(evaluate(ok), "1")
        bad = "(" * (calc.MAX_DEPTH + 1) + "1" + ")" * (calc.MAX_DEPTH + 1)
        with self.assertRaises(CalculatorError) as ctx:
            evaluate(bad)
        self.assertEqual(ctx.exception.code, calc.ERR_TOO_COMPLEX)

    def test_deep_unary_chain(self):
        with self.assertRaises(CalculatorError) as ctx:
            evaluate("-" * 200 + "1")
        self.assertEqual(ctx.exception.code, calc.ERR_TOO_COMPLEX)

    def test_long_literal(self):
        self.assertEqual(evaluate("1" * calc.MAX_LITERAL_DIGITS), "1" * calc.MAX_LITERAL_DIGITS)
        with self.assertRaises(CalculatorError) as ctx:
            evaluate("1" * (calc.MAX_LITERAL_DIGITS + 1))
        self.assertEqual(ctx.exception.code, calc.ERR_TOO_COMPLEX)

    def test_overflow(self):
        big = "9" * 30
        expr = "*".join([big] * 5)  # ~10**150 > Emax
        with self.assertRaises(CalculatorError) as ctx:
            evaluate(expr)
        self.assertEqual(ctx.exception.code, calc.ERR_OVERFLOW)

    def test_try_evaluate_shape(self):
        self.assertEqual(try_evaluate("1+1"), ("2", None))
        result, error = try_evaluate("open('x')")
        self.assertIsNone(result)
        self.assertEqual(set(error), {"code", "message"})


class CalculatorFuzz(unittest.TestCase):
    """Random byte soup must only ever yield a string or CalculatorError."""

    ALPHABET = "0123456789.+-*/() \t" + "eE_xj%,;'\"[]{}abc\\\n\x00" + "١１"

    def test_random_inputs_never_crash(self):
        rng = random.Random(20261121)
        for _ in range(5000):
            n = rng.randint(0, 40)
            expr = "".join(rng.choice(self.ALPHABET) for _ in range(n))
            try:
                out = evaluate(expr)
            except CalculatorError:
                continue
            self.assertIsInstance(out, str)
            decimal.Decimal(out)  # always a valid decimal string

    def test_random_valid_expressions_match_reference(self):
        rng = random.Random(7)

        # Operands <1000 with 1 decimal, depth <=3: at most 8 leaves, so every
        # intermediate has <34 significant digits and must be exact.
        def gen(depth=0):
            if depth >= 3 or rng.random() < 0.3:
                return str(rng.randint(0, 999)) + (
                    "." + str(rng.randint(0, 9)) if rng.random() < 0.5 else ""
                )
            op = rng.choice("+-*")
            return f"({gen(depth + 1)}{op}{gen(depth + 1)})"

        for _ in range(500):
            expr = gen()
            # reference: independent evaluator with a huge context
            with decimal.localcontext() as ctx:
                ctx.prec = 200
                ref = _reference_eval(expr)
            got = decimal.Decimal(evaluate(expr))
            with self.subTest(expr=expr):
                self.assertEqual(got, ref)


def _reference_eval(expr: str) -> decimal.Decimal:
    """Tiny independent evaluator for fully parenthesised (a op b) trees."""
    pos = 0

    def parse():
        nonlocal pos
        if expr[pos] == "(":
            pos += 1
            a = parse()
            op = expr[pos]
            pos += 1
            b = parse()
            assert expr[pos] == ")"
            pos += 1
            return {"+": a + b, "-": a - b, "*": a * b}[op]
        start = pos
        while pos < len(expr) and (expr[pos].isdigit() or expr[pos] == "."):
            pos += 1
        return decimal.Decimal(expr[start:pos])

    return parse()


if __name__ == "__main__":
    unittest.main()
