# -*- coding: utf-8 -*-

"""Data source helpers for the plugin's REST-driven workflow.

This package is split in three responsibilities:

- ``cache_manager``: filesystem layout for cached datasets in the QGIS profile.
- REST clients: ``lombardia_comuni_client`` and ``lombardia_dusaf_client`` for
  Regione Lombardia ArcGIS REST services. ``istat_boundaries_client`` covers
  the optional authoritative ISTAT boundaries.
- ``layer_factory``: build in-memory ``QgsVectorLayer`` instances from the
  GeoJSON dicts returned by the REST clients.

Importing this package performs no network requests, creates no cache
directories, and does not touch the QGIS project state.
"""

from .cache_manager import CacheManager
from .istat_boundaries_client import IstatBoundariesClient
from .layer_factory import geojson_features_to_memory_layer
from .lombardia_comuni_client import LombardiaComuniClient
from .lombardia_dusaf_client import LombardiaDusafClient

__all__ = [
    "CacheManager",
    "IstatBoundariesClient",
    "LombardiaComuniClient",
    "LombardiaDusafClient",
    "geojson_features_to_memory_layer",
]
