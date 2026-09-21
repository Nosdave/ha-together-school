#!/usr/bin/env python3
"""Guards for two defects an audit found the hard way.

1. The coordinator must NOT sign in at runtime. The password is never stored,
   so an unconditional async_login() raises before a single entity is created
   and the re-auth flow then loops forever.
2. Time-of-day logic must use Home Assistant's configured timezone, not the
   process timezone, or every commute window shifts on a UTC container.

AST-based so it runs without Home Assistant installed.

    python3 tests/test_runtime_contract.py
"""

from __future__ import annotations

import ast
import pathlib
import unittest

_PKG = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "together_school"
)


def _tree(name: str) -> ast.AST:
    return ast.parse((_PKG / name).read_text())


def _called_attrs(tree: ast.AST) -> set[str]:
    return {
        n.func.attr
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }


class TestCoordinatorNeverLogsIn(unittest.TestCase):
    def setUp(self):
        self.tree = _tree("coordinator.py")

    def test_no_async_login_call(self):
        self.assertNotIn(
            "async_login",
            _called_attrs(self.tree),
            "the runtime client holds no password; signing in aborts setup",
        )

    def test_setup_fetches_the_user_directly(self):
        self.assertIn("async_get_me", _called_attrs(self.tree))


class TestTimezoneHandling(unittest.TestCase):
    def setUp(self):
        self.src = (_PKG / "coordinator.py").read_text()

    def test_uses_ha_timezone_helper(self):
        self.assertIn("dt_util.now()", self.src)

    def test_does_not_use_process_local_time(self):
        for forbidden in ("datetime.now()", "date.today()"):
            self.assertNotIn(
                forbidden, self.src, f"{forbidden} reads the process timezone"
            )


class TestWindowsAreConfigurable(unittest.TestCase):
    def test_options_flow_exists(self):
        names = {
            n.name
            for n in ast.walk(_tree("config_flow.py"))
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }
        self.assertIn("async_get_options_flow", names)
        self.assertIn("TogetherSchoolOptionsFlow", names)

    def test_coordinator_takes_windows_as_a_parameter(self):
        init = next(
            n for n in ast.walk(_tree("coordinator.py"))
            if isinstance(n, ast.FunctionDef) and n.name == "__init__"
        )
        kwonly = {a.arg for a in init.args.kwonlyargs}
        self.assertIn("active_windows", kwonly)


class TestSchoolCodeNormalisation(unittest.TestCase):
    """Users paste anything from a padded upper-case code to a full URL."""

    def setUp(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "ts_const", _PKG / "const.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.norm = mod.normalise_school_code

    def test_accepts_plain_code(self):
        self.assertEqual(self.norm("example"), "example")

    def test_strips_case_space_scheme_and_domain(self):
        for value in ("  EXAMPLE ", "https://example.together-school.com/",
                      "example.together-school.com"):
            self.assertEqual(self.norm(value), "example")

    def test_rejects_nonsense(self):
        for value in ("bad code!", "", None, "https://other.host/x"):
            self.assertEqual(self.norm(value), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
