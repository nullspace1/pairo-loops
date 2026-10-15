"""Run the project's repeatable test, typing, and invariant checks."""
from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
import tempfile
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).parents[1]
SOURCE_ROOT = PROJECT_ROOT / "pairo"
BASELINE_PATH = PROJECT_ROOT / "docs" / "invariant-legacy-baseline.json"
BOUNDARY_MARKER = "invariant: boundary-exception"


@dataclass(frozen=True)
class Violation:
    rule: str
    path: str
    line: int
    source: str

    @property
    def fingerprint(self) -> str:
        payload = f"{self.rule}|{self.path}|{self.source}".encode()
        return hashlib.sha256(payload).hexdigest()

    def display(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.source.strip()}"


class InvariantChecker(ast.NodeVisitor):
    """Find architecture constructs which require a boundary justification."""

    def __init__(self, path: Path, lines: list[str]) -> None:
        self._path = path.relative_to(PROJECT_ROOT).as_posix() if path.is_relative_to(PROJECT_ROOT) else path.as_posix()
        self._lines = lines
        self.violations: list[Violation] = []
        self._class_depth = 0

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_depth += 1
        self.generic_visit(node)
        self._class_depth -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function(node)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_If(self, node: ast.If) -> None:
        if self._contains_type_dispatch(node.test):
            self._add("type-conditional dispatch", node)
        self.generic_visit(node)

    def _check_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if self._class_depth == 0 and node.name != "__init__":
            self._add("procedural module utility", node)
        if any(self._decorator_name(item) == "staticmethod" for item in node.decorator_list):
            self._add("static utility method", node)
        for annotation in self._annotations(node):
            if self._contains_unspecific_annotation(annotation):
                self._add("Any/object annotation", node)
                break

    @staticmethod
    def _decorator_name(node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return None

    @staticmethod
    def _annotations(node: ast.FunctionDef | ast.AsyncFunctionDef) -> Iterable[ast.expr]:
        arguments = (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)
        for argument in arguments:
            if argument.annotation is not None:
                yield argument.annotation
        if node.args.vararg and node.args.vararg.annotation:
            yield node.args.vararg.annotation
        if node.args.kwarg and node.args.kwarg.annotation:
            yield node.args.kwarg.annotation
        if node.returns is not None:
            yield node.returns

    @staticmethod
    def _contains_unspecific_annotation(annotation: ast.expr) -> bool:
        return any(isinstance(item, ast.Name) and item.id in {"Any", "object"} for item in ast.walk(annotation))

    @staticmethod
    def _contains_type_dispatch(expression: ast.expr) -> bool:
        return any(
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Name)
            and item.func.id in {"isinstance", "issubclass", "type"}
            for item in ast.walk(expression)
        )

    def _add(self, rule: str, node: ast.AST) -> None:
        line = getattr(node, "lineno", 1)
        if self._has_boundary_exception(rule, line):
            return
        source = ast.get_source_segment("\n".join(self._lines), node) or self._lines[line - 1]
        self.violations.append(Violation(rule, self._path, line, source))

    def _has_boundary_exception(self, rule: str, line: int) -> bool:
        """Allow only a justified external-schema exception for nonspecific types."""
        if rule != "Any/object annotation" or line <= 1:
            return False
        marker = self._lines[line - 2].strip()
        return bool(re.fullmatch(
            r"# invariant: boundary-exception\[external-sdk\] -- .+",
            marker,
        ))


def collect_violations(source_root: Path = SOURCE_ROOT) -> list[Violation]:
    violations: list[Violation] = []
    for path in sorted(source_root.rglob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        checker = InvariantChecker(path, lines)
        checker.visit(ast.parse("\n".join(lines), filename=str(path)))
        violations.extend(checker.violations)
    return violations


def check_architecture() -> int:
    baseline = set(json.loads(BASELINE_PATH.read_text(encoding="utf-8"))["fingerprints"])
    new_violations = [item for item in collect_violations() if item.fingerprint not in baseline]
    if not new_violations:
        return 0
    print("Invariant architecture violations:", file=sys.stderr)
    for violation in new_violations:
        print(violation.display(), file=sys.stderr)
    print(f"Add polymorphism or a specific type; boundary exceptions require '# invariant: boundary-exception[external-sdk] -- concrete SDK boundary reason'.", file=sys.stderr)
    return 1


def run(command: list[str]) -> int:
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode


def main() -> int:
    if "--architecture-only" in sys.argv:
        return check_architecture()
    with tempfile.TemporaryDirectory(prefix="quality-pytest-") as pytest_temp:
        checks = ([sys.executable, "-m", "pytest", f"--basetemp={pytest_temp}"], [sys.executable, "-m", "mypy", "--strict", "pairo"])
        for command in checks:
            if run(list(command)):
                return 1
        return check_architecture()


if __name__ == "__main__":
    raise SystemExit(main())
