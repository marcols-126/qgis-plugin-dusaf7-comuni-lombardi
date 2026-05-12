# -*- coding: utf-8 -*-

"""Bridge between project layers, cache and REST fetch for the workflow.

The algorithm should not have to know whether a layer is already loaded in
the QGIS project, comes from the plugin cache, or needs to be fetched from
the Regione Lombardia ArcGIS REST endpoints. This module hides that
detection behind two helper functions that return ready-to-use
``QgsVectorLayer`` instances plus a short label describing the source for
logging.
"""

from ..data_sources import (
    CacheManager,
    LombardiaComuniClient,
    LombardiaDusafClient,
    geojson_features_to_memory_layer,
)
from ..data_sources.comuni_list_cache import ComuniListCache


def get_comuni_list_for_autocomplete(cache_manager=None, force_refresh=False, feedback=None):
    """Return the lightweight ``(properties_dicts, source_label)`` Comuni list.

    The label is one of ``"cache"`` (fresh entry in the plugin cache) or
    ``"rest"`` (fresh REST fetch, also written to the cache).
    Cache failures degrade gracefully: the REST result is returned even when
    persistence fails so the autocomplete still works.
    """
    cache = ComuniListCache(cache_manager or CacheManager())

    if not force_refresh:
        entry = cache.read()
        if entry is not None and not entry.is_expired():
            return entry.comuni, "cache"

    client = LombardiaComuniClient()
    features = client.fetch_comuni_list(feedback=feedback)

    comuni = []
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if isinstance(properties, dict):
            comuni.append(properties)

    try:
        cache.write(comuni)
    except (OSError, ValueError):
        pass

    return comuni, "rest"


def fetch_comune_geometry_layer(comune_name, feedback=None):
    """Fetch a single Comune geometry via REST as a memory layer.

    Returns ``None`` when the service has no Comune matching ``comune_name``.
    The returned layer carries the same EPSG:32632 CRS as the service and
    one feature with all the Comune attributes.
    """
    client = LombardiaComuniClient()
    feature = client.fetch_comune_geometry(comune_name=comune_name, feedback=feedback)
    if feature is None:
        return None

    safe = "".join(ch if ch.isalnum() else "_" for ch in comune_name)[:60].strip("_") or "comune"
    return geojson_features_to_memory_layer(
        [feature],
        layer_name="Com_REST_{}".format(safe),
        crs_authid="EPSG:32632",
        geometry_type="MultiPolygon",
    )


def fetch_dusaf_layer_for_envelope(envelope, feedback=None, max_pages=None, max_features=None):
    """Fetch DUSAF features inside the given EPSG:32632 envelope via REST.

    ``envelope`` is a dictionary with ``xmin``, ``ymin``, ``xmax``, ``ymax``
    keys (or a 4-tuple in the same order). The function returns a memory
    layer with all the DUSAF features intersecting that envelope.
    """
    client = LombardiaDusafClient()
    features = client.fetch_validated_features(
        geometry=envelope,
        feedback=feedback,
        max_pages=max_pages,
        max_features=max_features,
    )

    if not features:
        raise ValueError(
            "Nessuna feature DUSAF restituita dal servizio REST per l'envelope richiesto."
        )

    return geojson_features_to_memory_layer(
        features,
        layer_name="DUSAF7_REST",
        crs_authid="EPSG:32632",
        geometry_type="MultiPolygon",
    )


def envelope_from_layer_extent(layer, padding_m=10.0):
    """Return an envelope dict suitable for ``LombardiaDusafClient`` from a layer.

    A small ``padding_m`` is added to mitigate floating-point edge cases when
    the layer was created via a freshly fetched single-feature memory layer.
    """
    extent = layer.extent()
    return {
        "xmin": float(extent.xMinimum()) - float(padding_m),
        "ymin": float(extent.yMinimum()) - float(padding_m),
        "xmax": float(extent.xMaximum()) + float(padding_m),
        "ymax": float(extent.yMaximum()) + float(padding_m),
    }
