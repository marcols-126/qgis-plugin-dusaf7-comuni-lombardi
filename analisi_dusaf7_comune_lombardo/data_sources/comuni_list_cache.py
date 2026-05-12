# -*- coding: utf-8 -*-

"""Small filesystem cache for the lightweight RL Comuni list.

The cache stores the result of ``LombardiaComuniClient.fetch_comuni_list()``
as a single JSON file in the plugin cache root. It is intentionally tiny
(~150 KB for ~1500 Comuni) and has a configurable TTL so the autocomplete
widget can populate instantly after the first run while still picking up
upstream changes within a sensible window.

The file is written atomically via a temporary file + ``os.replace`` to avoid
torn writes if QGIS is closed mid-update.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from .cache_manager import CacheManager


COMUNI_LIST_FILENAME = "comuni_list_lombardia.json"
COMUNI_LIST_TTL_DAYS = 30
COMUNI_LIST_FORMAT_VERSION = 1


@dataclass(frozen=True)
class ComuniListCacheEntry:
    """In-memory representation of the cache payload."""

    fetched_at: datetime
    comuni: list

    def is_expired(self, ttl_days=COMUNI_LIST_TTL_DAYS):
        """Return True when the cached payload is older than ``ttl_days``."""
        if ttl_days is None:
            return False
        return datetime.now() - self.fetched_at > timedelta(days=ttl_days)


class ComuniListCache:
    """Read/write the JSON cache for the lightweight Comuni list."""

    def __init__(self, cache_manager=None):
        self._cache_manager = cache_manager or CacheManager()

    def path(self):
        """Return the cache file path without creating its parent directory."""
        return os.path.join(self._cache_manager.root_dir(), COMUNI_LIST_FILENAME)

    def read(self):
        """Return the parsed cache entry or ``None`` if missing/invalid.

        A missing or malformed file is treated as a cache miss. The reader
        never raises so the autocomplete widget can fall back to a fresh
        REST fetch silently.
        """
        path = self.path()
        if not os.path.exists(path):
            return None

        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(data, dict):
            return None

        fetched_at_str = data.get("fetched_at")
        comuni = data.get("comuni")
        if not isinstance(fetched_at_str, str) or not isinstance(comuni, list):
            return None

        try:
            fetched_at = datetime.fromisoformat(fetched_at_str)
        except ValueError:
            return None

        return ComuniListCacheEntry(fetched_at=fetched_at, comuni=comuni)

    def write(self, comuni):
        """Atomically write a fresh Comuni list to the cache file.

        The base cache directory is created on demand. The caller may pass
        the raw ``properties`` dicts returned by the REST client; the cache
        does not enforce a schema beyond "list of dicts".
        """
        if not isinstance(comuni, list):
            raise ValueError("ComuniListCache.write expects a list of dicts.")

        self._cache_manager.ensure_base_dir()
        path = self.path()
        temp_path = path + ".tmp"

        payload = {
            "version": COMUNI_LIST_FORMAT_VERSION,
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
            "comuni": comuni,
        }

        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=True, indent=2)
                handle.write("\n")
            os.replace(temp_path, path)
        except Exception:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise

        return path

    def clear(self):
        """Remove the cache file if it exists. No-op when missing."""
        try:
            os.remove(self.path())
        except FileNotFoundError:
            return
        except OSError:
            return
