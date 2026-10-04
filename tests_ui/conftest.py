"""Fixtures for tests_ui. Everything lives in uikit.py, imported by that unique name: a plain `pytest -q` at the repo
root also collects tests_integration/, whose conftest.py would otherwise be the module `from conftest import` finds."""
from uikit import client, make_client, ui_settings  # noqa: F401  (pytest finds fixtures by name here)
