#!/usr/bin/env python3
"""Guard: the config entry must never persist the password.

Storing only the access token is an explicit design requirement, so this parses
config_flow.py and asserts that no password ends up in the data written to the
config entry. AST-based rather than import-based so it runs without Home
Assistant installed.

    python3 tests/test_no_password_stored.py
"""

from __future__ import annotations

import ast
import pathlib
import unittest

_FLOW = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "together_school"
    / "config_flow.py"
)


def _entry_data_keys(tree: ast.AST) -> list[list[str]]:
    """Keys of every dict passed as `data=` to async_create_entry."""
    found: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute)
                and func.attr == "async_create_entry"):
            continue
        for kw in node.keywords:
            if kw.arg == "data" and isinstance(kw.value, ast.Dict):
                keys = []
                for k in kw.value.keys:
                    if isinstance(k, ast.Name):
                        keys.append(k.id)
                    elif isinstance(k, ast.Constant):
                        keys.append(str(k.value))
                found.append(keys)
    return found


def _update_data_keys(tree: ast.AST) -> list[list[str]]:
    """Keys of every `data_updates=` dict (used by the re-auth flow)."""
    found: list[list[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg == "data_updates" and isinstance(kw.value, ast.Dict):
                keys = []
                for k in kw.value.keys:
                    if isinstance(k, ast.Name):
                        keys.append(k.id)
                    elif isinstance(k, ast.Constant):
                        keys.append(str(k.value))
                found.append(keys)
    return found


class TestNoPasswordStored(unittest.TestCase):
    def setUp(self):
        self.tree = ast.parse(_FLOW.read_text())

    def test_create_entry_omits_password(self):
        datasets = _entry_data_keys(self.tree)
        self.assertTrue(datasets, "no async_create_entry(data=...) found")
        for keys in datasets:
            self.assertNotIn("CONF_PASSWORD", keys)
            self.assertNotIn("password", keys)

    def test_reauth_update_omits_password(self):
        for keys in _update_data_keys(self.tree):
            self.assertNotIn("CONF_PASSWORD", keys)
            self.assertNotIn("password", keys)

    def test_create_entry_stores_the_token(self):
        datasets = _entry_data_keys(self.tree)
        self.assertTrue(
            any("CONF_ACCESS_TOKEN" in keys for keys in datasets),
            "the access token should be persisted instead of the password",
        )

    def test_reauth_step_exists(self):
        """Without re-auth, a dead token would need a full re-setup."""
        names = {
            n.name for n in ast.walk(self.tree)
            if isinstance(n, ast.AsyncFunctionDef)
        }
        self.assertIn("async_step_reauth", names)
        self.assertIn("async_step_reauth_confirm", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
