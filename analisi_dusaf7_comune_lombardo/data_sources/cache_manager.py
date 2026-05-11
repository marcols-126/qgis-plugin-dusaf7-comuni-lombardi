# -*- coding: utf-8 -*-

"""Cache path helpers for future remote datasets.

The cache is designed to live in the current QGIS profile. This module avoids
creating directories at import time; callers must explicitly request directory
creation when a future workflow needs it.
"""

import json
import os
from dataclasses import dataclass

from qgis.core import QgsApplication


CACHE_FOLDER_NAME = "analisi_dusaf7_comune_lombardo"
CACHE_SUBFOLDER_NAME = "cache"
CACHE_MANIFEST_FILENAME = "manifest.json"
DUSAF_CACHE_FOLDER = "dusaf7"
ISTAT_CACHE_FOLDER = "istat_boundaries"


@dataclass(frozen=True)
class CachePaths:
    """Resolved cache paths for the plugin data-source layer."""

    root_dir: str
    manifest_path: str
    dusaf_dir: str
    istat_dir: str


class CacheManager:
    """Resolve and inspect plugin cache locations in the QGIS profile."""

    def __init__(self, root_dir=None):
        self._root_dir = root_dir

    def root_dir(self):
        """Return the cache root path without creating it."""
        if self._root_dir:
            return os.path.abspath(self._root_dir)

        return os.path.join(
            QgsApplication.qgisSettingsDirPath(),
            CACHE_FOLDER_NAME,
            CACHE_SUBFOLDER_NAME,
        )

    def paths(self):
        """Return all standard cache paths without creating directories."""
        root = self.root_dir()
        return CachePaths(
            root_dir=root,
            manifest_path=os.path.join(root, CACHE_MANIFEST_FILENAME),
            dusaf_dir=os.path.join(root, DUSAF_CACHE_FOLDER),
            istat_dir=os.path.join(root, ISTAT_CACHE_FOLDER),
        )

    def ensure_directories(self):
        """Create cache directories when explicitly called by future code."""
        paths = self.paths()
        os.makedirs(paths.dusaf_dir, exist_ok=True)
        os.makedirs(paths.istat_dir, exist_ok=True)
        return paths

    def read_manifest(self):
        """Read cache metadata if present; return an empty dict otherwise."""
        manifest_path = self.paths().manifest_path

        if not os.path.exists(manifest_path):
            return {}

        with open(manifest_path, "r", encoding="utf-8") as handle:
            return json.load(handle)

    def write_manifest(self, manifest):
        """Write cache metadata after explicit directory creation."""
        paths = self.ensure_directories()

        with open(paths.manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest or {}, handle, ensure_ascii=True, indent=2, sort_keys=True)

        return paths.manifest_path

    def dataset_path(self, dataset_key, filename):
        """Return a path inside a known dataset cache folder.

        Args:
            dataset_key: Either ``dusaf7`` or ``istat_boundaries``.
            filename: Relative filename expected inside that dataset folder.
        """
        paths = self.paths()
        folders = {
            DUSAF_CACHE_FOLDER: paths.dusaf_dir,
            ISTAT_CACHE_FOLDER: paths.istat_dir,
        }

        if dataset_key not in folders:
            raise ValueError(f"Unknown cache dataset key: {dataset_key}")

        return os.path.join(folders[dataset_key], filename)
