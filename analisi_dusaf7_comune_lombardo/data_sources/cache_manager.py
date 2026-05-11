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
VALID_DATASET_KEYS = (DUSAF_CACHE_FOLDER, ISTAT_CACHE_FOLDER)


def validate_dataset_key(dataset_key):
    """Validate and return a known cache dataset key.

    Args:
        dataset_key: Dataset namespace used below the plugin cache root.

    Raises:
        ValueError: If the key is empty or not one of the supported cache
            namespaces.
    """
    if not isinstance(dataset_key, str) or not dataset_key.strip():
        raise ValueError("Cache dataset_key must be a non-empty string.")

    dataset_key = dataset_key.strip()

    if dataset_key not in VALID_DATASET_KEYS:
        raise ValueError(
            "Unknown cache dataset_key '{}'. Expected one of: {}.".format(
                dataset_key,
                ", ".join(VALID_DATASET_KEYS),
            )
        )

    return dataset_key


def validate_cache_filename(filename):
    """Validate and return a safe cache filename.

    The cache layer accepts plain filenames only. Absolute paths, nested paths,
    empty names, and traversal segments such as ``..`` are rejected.
    """
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("Cache filename must be a non-empty string.")

    filename = filename.strip()

    if os.path.isabs(filename):
        raise ValueError(f"Unsafe cache filename '{filename}': absolute paths are not allowed.")

    if filename in (".", ".."):
        raise ValueError(f"Unsafe cache filename '{filename}': reserved path segment.")

    if "/" in filename or "\\" in filename:
        raise ValueError(
            f"Unsafe cache filename '{filename}': directory separators are not allowed."
        )

    if os.path.basename(filename) != filename:
        raise ValueError(f"Unsafe cache filename '{filename}': nested paths are not allowed.")

    return filename


def validate_manifest_data(manifest):
    """Validate cache manifest data already loaded in memory.

    The function performs no file access. It returns a list of technical,
    human-readable validation errors; an empty list means the structure is
    acceptable for future cache use.
    """
    errors = []

    if manifest is None:
        return errors

    if not isinstance(manifest, dict):
        return ["Cache manifest must be a dictionary object."]

    datasets = manifest.get("datasets")
    if datasets is not None:
        if not isinstance(datasets, dict):
            errors.append("Cache manifest field 'datasets' must be a dictionary.")
        else:
            for dataset_key, dataset_info in datasets.items():
                try:
                    validate_dataset_key(dataset_key)
                except ValueError as exc:
                    errors.append(str(exc))

                if not isinstance(dataset_info, dict):
                    errors.append(
                        "Cache manifest dataset '{}' must be a dictionary.".format(dataset_key)
                    )

    version = manifest.get("version")
    if version is not None and not isinstance(version, (int, str)):
        errors.append("Cache manifest field 'version' must be a string or integer.")

    return errors


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

    def ensure_base_dir(self):
        """Create and return the base cache directory on explicit request.

        No dataset subdirectory is created by this method. It is useful for
        future workflows that only need to store top-level cache metadata such
        as the manifest.
        """
        root = self.root_dir()
        os.makedirs(root, exist_ok=True)
        return root

    def dataset_dir(self, dataset_key):
        """Return the cache directory for a known dataset without creating it."""
        dataset_key = validate_dataset_key(dataset_key)
        paths = self.paths()
        folders = {
            DUSAF_CACHE_FOLDER: paths.dusaf_dir,
            ISTAT_CACHE_FOLDER: paths.istat_dir,
        }
        return folders[dataset_key]

    def ensure_dataset_dir(self, dataset_key):
        """Create and return one dataset cache directory on explicit request."""
        dataset_dir = self.dataset_dir(dataset_key)
        os.makedirs(dataset_dir, exist_ok=True)
        return dataset_dir

    def ensure_directories(self):
        """Create all cache directories when explicitly called by future code.

        This compatibility helper creates the base cache directory and all
        known dataset directories. Importing this module never calls it.
        """
        paths = self.paths()
        self.ensure_base_dir()
        self.ensure_dataset_dir(DUSAF_CACHE_FOLDER)
        self.ensure_dataset_dir(ISTAT_CACHE_FOLDER)
        return paths

    def read_manifest(self):
        """Read and validate cache metadata from JSON.

        Returns an empty dictionary when the manifest file does not exist.

        Raises:
            ValueError: If the manifest is not valid JSON, is not a dictionary,
                or fails the in-memory manifest validation rules.
        """
        manifest_path = self.paths().manifest_path

        if not os.path.exists(manifest_path):
            return {}

        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Cache manifest is not valid JSON: {}.".format(manifest_path)
            ) from exc
        except OSError as exc:
            raise ValueError(
                "Cache manifest could not be read: {}.".format(manifest_path)
            ) from exc

        errors = validate_manifest_data(manifest)
        if errors:
            raise ValueError("Cache manifest is invalid: {}".format("; ".join(errors)))

        return manifest

    def write_manifest(self, manifest):
        """Validate and write cache metadata as ordered UTF-8 JSON.

        The base cache directory is created only because this method is called
        explicitly. Dataset subdirectories are not created by manifest writes.

        Raises:
            ValueError: If the manifest structure is invalid.
            OSError: If the file cannot be written by the host environment.
        """
        manifest_data = {} if manifest is None else manifest
        errors = validate_manifest_data(manifest_data)
        if errors:
            raise ValueError("Cache manifest is invalid: {}".format("; ".join(errors)))

        self.ensure_base_dir()
        manifest_path = self.paths().manifest_path

        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest_data, handle, ensure_ascii=True, indent=2, sort_keys=True)
            handle.write("\n")

        return manifest_path

    def dataset_exists(self, dataset_key):
        """Return True when a known dataset cache directory already exists."""
        return os.path.isdir(self.dataset_dir(dataset_key))

    def dataset_path(self, dataset_key, filename):
        """Return a path inside a known dataset cache folder.

        Args:
            dataset_key: Either ``dusaf7`` or ``istat_boundaries``.
            filename: Relative filename expected inside that dataset folder.
        """
        dataset_key = validate_dataset_key(dataset_key)
        filename = validate_cache_filename(filename)

        paths = self.paths()
        folders = {
            DUSAF_CACHE_FOLDER: paths.dusaf_dir,
            ISTAT_CACHE_FOLDER: paths.istat_dir,
        }

        return os.path.join(folders[dataset_key], filename)
