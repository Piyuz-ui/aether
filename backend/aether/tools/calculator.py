"""python_calculator: safe math and quick data crunching.

This is a small interpreter over Python's AST with a whitelist of node types and functions.
It never calls eval/exec, so there's no attribute access, imports, dunder tricks or file
access. Size limits stop `9**9**9`-style hangs and huge list repetition.

Supports: arithmetic, comparisons, variables (`x = 5; x * 2`), lists and tuples,
comprehensions (`sum(i**2 for i in range(10))`), and math + statistics functions.
"""
from __future__ import annotations

import ast
import math
import operator
import statistics
from typing import Any

from .registry import ToolError, tool

MAX_POW_EXPONENT = 10_000
MAX_SEQUENCE = 1_000_000
MAX_INT_DIGITS = 5_000
MAX_NODES = 2_000
MAX_OPERATIONS = 2_000_000   # total evaluation steps, so nested loops can't hang
MAX_FACTORIAL = 5_000
MAX_COMB_N = 100_000

_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
        ast.Pow: operator.pow}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le,
        ast.Gt: operator.gt, ast.GtE: operator.ge, ast.In: lambda a, b: a in b,
        ast.NotIn: lambda a, b: a not in b}


def _range(*args: int) -> range:
    r = range(*args)
    if len(r) > MAX_SEQUENCE:
        raise ToolError(f"range is too large (max {MAX_SEQUENCE:,} items).")
    return r


def _pow(base: Any, exp: Any, mod: Any = None) -> Any:
    if mod is None and isinstance(exp, (int, float)) and abs(exp) > MAX_POW_EXPONENT \
            and isinstance(base, (int, float)) and abs(base) > 1:
        raise ToolError(f"Exponent too large (max {MAX_POW_EXPONENT:,}).")
    return pow(base, exp, mod) if mod is not None else pow(base, exp)


def _factorial(n: int) -> int:
    if n > MAX_FACTORIAL:
        raise ToolError(f"factorial is limited to n ≤ {MAX_FACTORIAL:,}.")
    return math.factorial(n)


def _comb(n: int, k: int) -> int:
    if n > MAX_COMB_N:
        raise ToolError(f"comb is limited to n ≤ {MAX_COMB_N:,}.")
    return math.comb(n, k)


def _perm(n: int, k: int | None = None) -> int:
    if n > MAX_COMB_N:
        raise ToolError(f"perm is limited to n ≤ {MAX_COMB_N:,}.")
    return math.perm(n, k)


FUNCTIONS: dict[str, Any] = {
    # built-ins
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum, "len": len,
    "sorted": sorted, "range": _range, "int": int, "float": float, "bool": bool,
    "list": list, "tuple": tuple, "set": set, "divmod": divmod, "pow": _pow,
    "any": any, "all": all, "reversed": lambda x: list(reversed(x)),
    "zip": lambda *a: list(zip(*a)), "enumerate": lambda x: list(enumerate(x)),
    # statistics
    "mean": statistics.fmean, "median": statistics.median, "mode": statistics.mode,
    "stdev": statistics.stdev, "pstdev": statistics.pstdev, "variance": statistics.variance,
    "pvariance": statistics.pvariance,
}
FUNCTIONS.update({name: getattr(math, name) for name in (
    "sqrt", "cbrt", "exp", "log", "log2", "log10", "sin", "cos", "tan", "asin", "acos", "atan",
    "atan2", "sinh", "cosh", "tanh", "degrees", "radians", "floor", "ceil", "trunc", "gcd", "lcm", "hypot", "isclose", "fsum", "prod", "dist", "isqrt")
    if hasattr(math, name)})
FUNCTIONS.update({"factorial": _factorial, "comb": _comb, "perm": _perm})
CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf, "nan": math.nan,
             "True": True, "False": False, "None": None}


class _Evaluator:
    def __init__(self) -> None:
        self.vars: dict[str, Any] = {}
        self.ops = 0

    def run(self, source: str) -> Any:
        try:
            tree = ast.parse(source.strip(), mode="exec")
        except SyntaxError as e:
            raise ToolError(f"Syntax error: {e.msg} (line {e.lineno}).") from e
        if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
            raise ToolError("Expression is too long.")
        result: Any = None
        for stmt in tree.body:
            if isinstance(stmt, ast.Expr):
                result = self.eval(stmt.value)
            elif isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and \
                    isinstance(stmt.targets[0], ast.Name):
                result = self.vars[stmt.targets[0].id] = self.eval(stmt.value)
            else:
                raise ToolError(f"'{type(stmt).__name__}' statements aren't allowed. "
                                "Use expressions and simple `name = value` assignments.")
        return result

    def eval(self, node: ast.AST, scope: dict[str, Any] | None = None) -> Any:
        scope = scope if scope is not None else {}
        self.ops += 1
        if self.ops > MAX_OPERATIONS:
            raise ToolError("Computation is too big for the calculator.")
        match node:
            case ast.Constant(value=v) if isinstance(v, (int, float, complex, str, bool)) or v is None:
                return v
            case ast.Name(id=name):
                for table in (scope, self.vars, CONSTANTS):
                    if name in table:
                        return table[name]
                if name in FUNCTIONS:
                    raise ToolError(f"'{name}' is a function; call it like {name}(...).")
                raise ToolError(f"Unknown name '{name}'.")
            case ast.BinOp(left=l, op=op, right=r) if type(op) in _BIN:
                a, b = self.eval(l, scope), self.eval(r, scope)
                self._check_binop(op, a, b)
                result = _BIN[type(op)](a, b)
                if isinstance(result, int) and not isinstance(result, bool) and \
                        abs(result).bit_length() > MAX_INT_DIGITS * 3.33:
                    raise ToolError("Result is too large.")
                return result
            case ast.UnaryOp(op=op, operand=o) if type(op) in _UNARY:
                return _UNARY[type(op)](self.eval(o, scope))
            case ast.BoolOp(op=ast.And(), values=vals):
                out: Any = True
                for v in vals:
                    out = self.eval(v, scope)
                    if not out:
                        return out
                return out
            case ast.BoolOp(op=ast.Or(), values=vals):
                out = False
                for v in vals:
                    out = self.eval(v, scope)
                    if out:
                        return out
                return out
            case ast.Compare(left=l, ops=ops, comparators=comps):
                left = self.eval(l, scope)
                for op, comp in zip(ops, comps):
                    if type(op) not in _CMP:
                        raise ToolError("That comparison isn't supported.")
                    right = self.eval(comp, scope)
                    if not _CMP[type(op)](left, right):
                        return False
                    left = right
                return True
            case ast.IfExp(test=t, body=b, orelse=o):
                return self.eval(b, scope) if self.eval(t, scope) else self.eval(o, scope)
            case ast.List(elts=elts) | ast.Tuple(elts=elts) | ast.Set(elts=elts):
                items = [self.eval(e, scope) for e in elts]
                return tuple(items) if isinstance(node, ast.Tuple) else (
                    set(items) if isinstance(node, ast.Set) else items)
            case ast.Dict(keys=keys, values=values):
                return {self.eval(k, scope): self.eval(v, scope) for k, v in zip(keys, values)
                        if k is not None}
            case ast.Subscript(value=v, slice=s):
                container = self.eval(v, scope)
                index = self._slice(s, scope)
                return container[index]
            case ast.Call(func=ast.Name(id=fname), args=args, keywords=kws):
                if fname not in FUNCTIONS:
                    raise ToolError(f"Function '{fname}' isn't available. Available: "
                                    f"{', '.join(sorted(FUNCTIONS))}.")
                pos = [self.eval(a, scope) for a in args if not isinstance(a, ast.Starred)]
                kw = {k.arg: self.eval(k.value, scope) for k in kws if k.arg}
                return FUNCTIONS[fname](*pos, **kw)
            case ast.ListComp(elt=elt, generators=gens) | ast.GeneratorExp(elt=elt, generators=gens):
                return list(self._comprehend(elt, gens, scope))
            case ast.SetComp(elt=elt, generators=gens):
                return set(self._comprehend(elt, gens, scope))
            case ast.Call():
                raise ToolError("Only plain function calls like sqrt(2) are allowed.")
            case ast.Attribute():
                raise ToolError("Attribute access (a.b) isn't allowed. Use functions like sqrt(x).")
        raise ToolError(f"'{type(node).__name__}' isn't allowed in the calculator.")

    def _slice(self, s: ast.AST, scope: dict[str, Any]) -> Any:
        if isinstance(s, ast.Slice):
            part = lambda n: None if n is None else self.eval(n, scope)  # noqa: E731
            return slice(part(s.lower), part(s.upper), part(s.step))
        return self.eval(s, scope)

    def _comprehend(self, elt: ast.AST, gens: list[ast.comprehension],
                    scope: dict[str, Any], depth: int = 0):
        if depth == len(gens):
            yield self.eval(elt, scope)
            return
        gen = gens[depth]
        if not isinstance(gen.target, ast.Name):
            raise ToolError("Comprehensions need a simple loop variable, e.g. `for x in data`.")
        iterable = self.eval(gen.iter, scope)
        for count, item in enumerate(iterable):
            if count >= MAX_SEQUENCE:
                raise ToolError("Too many items in a comprehension.")
            inner = {**scope, gen.target.id: item}
            if all(self.eval(cond, inner) for cond in gen.ifs):
                yield from self._comprehend(elt, gens, inner, depth + 1)

    @staticmethod
    def _check_binop(op: ast.operator, a: Any, b: Any) -> None:
        if isinstance(op, ast.Pow) and isinstance(b, (int, float)) and abs(b) > MAX_POW_EXPONENT \
                and isinstance(a, (int, float)) and abs(a) > 1:
            raise ToolError(f"Exponent too large (max {MAX_POW_EXPONENT:,}).")
        if isinstance(op, ast.Mult):
            for seq, n in ((a, b), (b, a)):
                if isinstance(seq, (str, list, tuple)) and isinstance(n, int) and \
                        len(seq) * n > MAX_SEQUENCE:
                    raise ToolError("Repetition result would be too large.")


def _format(value: Any) -> str:
    if isinstance(value, float):
        if value.is_integer() and abs(value) < 1e16:
            return str(int(value))
        return f"{value:.12g}"
    if isinstance(value, (list, tuple)) and len(value) > 200:
        return f"{list(value)[:200]} … ({len(value)} items)"
    return repr(value)


def calculate(expression: str) -> str:
    """Evaluate an expression (or a few `;`/newline-separated statements) safely."""
    try:
        lines = (line.strip() for line in expression.replace(";", "\n").splitlines())
        result = _Evaluator().run("\n".join(line for line in lines if line))
    except ToolError:
        raise
    except ZeroDivisionError:
        raise ToolError("Division by zero.") from None
    except (TypeError, ValueError, KeyError, IndexError, OverflowError,
            statistics.StatisticsError) as e:
        raise ToolError(f"{type(e).__name__}: {e}") from None
    return _format(result)


@tool(description=(
    "Exact math and quick data computation. ALWAYS use this instead of doing arithmetic in "
    "your head. Supports + - * / // % **, variables (`a = 3; a**2`), lists, comprehensions "
    "(`sum(x**2 for x in range(1, 11))`), math functions (sqrt, log, sin, factorial, comb…) "
    "and statistics (mean, median, stdev, variance, mode)."),
    params={"expression": "The expression, e.g. 'mean([72, 85, 91]) * 1.1' or "
                          "'p = 0.3; comb(10, 3) * p**3 * (1-p)**7'"},
    category="compute", icon="calculator", timeout_s=10)
def python_calculator(expression: str) -> str:
    return f"{expression.strip()} = {calculate(expression)}"
