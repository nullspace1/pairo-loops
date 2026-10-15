"""Regression tests for the AST architecture quality gate."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "quality_gate.py"
spec = importlib.util.spec_from_file_location("quality_gate", MODULE_PATH)
assert spec and spec.loader
quality_gate = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = quality_gate
spec.loader.exec_module(quality_gate)


def violations_for(tmp_path: Path, source: str) -> list[object]:
    package = tmp_path / "pairo"
    package.mkdir()
    (package / "fixture.py").write_text(source, encoding="utf-8")
    return quality_gate.collect_violations(package)


def test_rejects_any_and_object_annotations(tmp_path: Path) -> None:
    violations = violations_for(tmp_path, "from typing import Any\nclass Boundary:\n    def decode(self, item: Any) -> object: ...\n")
    assert any(item.rule == "Any/object annotation" for item in violations)


def test_rejects_type_conditional_dispatch(tmp_path: Path) -> None:
    violations = violations_for(tmp_path, "class Router:\n    def route(self, value: str) -> str:\n        if isinstance(value, str): return value\n        return value\n")
    assert any(item.rule == "type-conditional dispatch" for item in violations)


def test_rejects_procedural_and_static_utilities(tmp_path: Path) -> None:
    violations = violations_for(tmp_path, "def transform(value: str) -> str: return value\nclass Formatter:\n    @staticmethod\n    def format(value: str) -> str: return value\n")
    rules = {item.rule for item in violations}
    assert "procedural module utility" in rules
    assert "static utility method" in rules


def test_permits_a_justified_boundary_exception(tmp_path: Path) -> None:
    source = "from typing import Any\nclass SdkBoundary:\n    # invariant: boundary-exception[external-sdk] -- SDK payload has no stable schema at this integration boundary.\n    def decode(self, payload: Any) -> object: return payload\n"
    assert violations_for(tmp_path, source) == []


def test_rejects_a_generic_boundary_marker(tmp_path: Path) -> None:
    source = "from typing import Any\nclass SdkBoundary:\n    # invariant: boundary-exception\n    def decode(self, payload: Any) -> str: return str(payload)\n"
    assert violations_for(tmp_path, source)


def test_rejects_boundary_marker_for_static_utility(tmp_path: Path) -> None:
    source = "class Formatter:\n    # invariant: boundary-exception[external-sdk] -- external SDK formatting boundary.\n    @staticmethod\n    def format(value: str) -> str: return value\n"
    assert any(item.rule == "static utility method" for item in violations_for(tmp_path, source))
