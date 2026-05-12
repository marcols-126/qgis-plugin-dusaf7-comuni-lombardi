# -*- coding: utf-8 -*-

"""Shared test fixtures.

Provides ``load_module(name)`` that loads a single module file from the
plugin without triggering the package ``__init__`` imports (which depend on
``qgis``). This keeps the pure-stdlib validators testable from plain Python.
"""

import importlib.util
import os
import sys

import pytest


HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_DIR = os.path.abspath(
    os.path.join(HERE, "..", "analisi_dusaf7_comune_lombardo")
)


def _load_file(module_name, relative_path):
    full_path = os.path.join(PLUGIN_DIR, relative_path)
    spec = importlib.util.spec_from_file_location(module_name, full_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module at {full_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def lombardia_comuni_client():
    return _load_file(
        "lombardia_comuni_client_under_test",
        os.path.join("data_sources", "lombardia_comuni_client.py"),
    )


@pytest.fixture(scope="session")
def lombardia_dusaf_client():
    return _load_file(
        "lombardia_dusaf_client_under_test",
        os.path.join("data_sources", "lombardia_dusaf_client.py"),
    )
