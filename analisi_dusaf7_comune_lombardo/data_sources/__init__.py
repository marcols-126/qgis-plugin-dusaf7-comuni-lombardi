# -*- coding: utf-8 -*-

"""Data source helpers for future automatic dataset retrieval.

This package is intentionally not wired into the Processing algorithm yet.
Importing it must not perform network requests, create cache directories, or
change QGIS project state.
"""

from .cache_manager import CacheManager
from .istat_boundaries_client import IstatBoundariesClient
from .lombardia_dusaf_client import LombardiaDusafClient

__all__ = [
    "CacheManager",
    "IstatBoundariesClient",
    "LombardiaDusafClient",
]
