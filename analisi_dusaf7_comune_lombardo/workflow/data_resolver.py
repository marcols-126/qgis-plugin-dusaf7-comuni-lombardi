# -*- coding: utf-8 -*-

"""Bridge between project layers, cache and REST fetch for the workflow.

The algorithm should not have to know whether a layer is already loaded in
the QGIS project, comes from the plugin cache, or needs to be fetched from
the Regione Lombardia ArcGIS REST endpoints. This module hides that
detection behind two helper functions that return ready-to-use
``QgsVectorLayer`` instances plus a short label describing the source for
logging.
"""

from qgis.core import QgsFeatureRequest, QgsVectorLayer

from ..data_sources import (
    CacheManager,
    IstatBoundariesClient,
    LombardiaComuniClient,
    LombardiaDusafClient,
    geojson_features_to_memory_layer,
    normalize_comune_display_name,
)
from ..data_sources.comuni_list_cache import ComuniListCache


_DUSAF_DESCR_SEPARATOR = " - "


def _parse_dusaf_descr(descr_raw):
    """Split a DUSAF ``DESCR`` value into ``(code, description)``.

    The Regione Lombardia REST service only exposes ``DESCR`` and packs the
    DUSAF class code in the prefix, for example
    ``"1111 - tessuto residenziale denso"``. The desktop workflow expects
    ``COD_TOT`` and ``DESCR`` to be separate columns, so we synthesise them
    here. When the separator is missing the code is left empty and the raw
    text is kept as the description so the caller can still inspect the data.
    """
    if not isinstance(descr_raw, str):
        return "", ""

    text = descr_raw.strip()
    if not text:
        return "", ""

    if _DUSAF_DESCR_SEPARATOR in text:
        code, _, description = text.partition(_DUSAF_DESCR_SEPARATOR)
        return code.strip(), description.strip()

    head = text.split(None, 1)
    if head and head[0].isdigit():
        if len(head) == 2:
            return head[0], head[1].strip()
        return head[0], ""

    return "", text


def get_istat_cached_shapefile_path(cache_manager=None):
    """Return the path to the cached ISTAT ``.shp`` or ``None`` when missing."""
    cm = cache_manager or CacheManager()
    return IstatBoundariesClient().cached_shapefile_path(cm)


def load_comuni_layer_from_istat_cache(cache_manager=None):
    """Return a ``QgsVectorLayer`` pointing at the cached ISTAT shapefile.

    Returns ``None`` when no ISTAT cache is configured or when the shapefile
    cannot be opened. The layer is loaded with the ``ogr`` provider in the
    shapefile's native CRS (EPSG:32632 for the official 2026 dataset).
    """
    shp_path = get_istat_cached_shapefile_path(cache_manager)
    if not shp_path:
        return None

    layer = QgsVectorLayer(shp_path, "Comuni_ISTAT_cache", "ogr")
    if not layer.isValid():
        return None
    return layer


def _extract_lombard_comuni_from_istat_layer(layer):
    """Return the autocomplete-shape list of Lombard Comuni from an ISTAT layer.

    Mirrors the schema returned by ``LombardiaComuniClient.fetch_comuni_list``
    so callers can treat both sources uniformly. Only Comuni with
    ``COD_REG=3`` (Lombardia) are returned.
    """
    if layer is None or not layer.isValid():
        return []

    field_names = {f.name() for f in layer.fields()}

    def _pick(*candidates):
        for cand in candidates:
            if cand in field_names:
                return cand
        return None

    name_field = _pick("COMUNE", "DEN_COM", "DENOM_COM", "DENOMINAZIONE", "NOME_COM")
    region_field = _pick("COD_REG")
    province_name_field = _pick("DEN_PROV", "DEN_UTS", "NOME_PRO")
    province_code_field = _pick("COD_PROV", "COD_UTS", "COD_PRO")
    istat_field = _pick("PRO_COM", "PROCOM", "COD_ISTAT", "ISTAT")

    if not name_field:
        return []

    attrs = [a for a in (name_field, region_field, province_name_field, province_code_field, istat_field) if a]
    request = QgsFeatureRequest()
    request.setFlags(QgsFeatureRequest.NoGeometry)
    request.setSubsetOfAttributes(attrs, layer.fields())

    comuni = []
    for feat in layer.getFeatures(request):
        if region_field:
            region_value = feat[region_field]
            try:
                if int(str(region_value).strip()) != 3:
                    continue
            except (TypeError, ValueError):
                continue

        raw_name = feat[name_field]
        if raw_name is None:
            continue
        display_name = normalize_comune_display_name(str(raw_name).strip())
        if not display_name:
            continue

        entry = {"NOME_COM": display_name}
        if province_name_field:
            entry["NOME_PRO"] = feat[province_name_field]
        if province_code_field:
            try:
                entry["COD_PRO"] = int(feat[province_code_field])
            except (TypeError, ValueError):
                entry["COD_PRO"] = feat[province_code_field]
        if istat_field:
            try:
                entry["ISTAT"] = int(feat[istat_field])
            except (TypeError, ValueError):
                entry["ISTAT"] = feat[istat_field]

        comuni.append(entry)

    return comuni


def get_comuni_list_for_autocomplete(cache_manager=None, force_refresh=False, feedback=None):
    """Return the lightweight ``(properties_dicts, source_label)`` Comuni list.

    Source resolution order:

    1. ``"istat_cache"`` — cached ISTAT shapefile when configured by the
       optional setup dialog.
    2. ``"cache"`` — fresh entry in the lightweight JSON cache (TTL 30gg).
    3. ``"rest"`` — fresh REST fetch from Regione Lombardia, also written
       back to the JSON cache.

    Cache failures degrade gracefully: the REST result is returned even when
    persistence fails so the autocomplete still works. The ISTAT path is
    skipped when ``force_refresh=True``.
    """
    cm = cache_manager or CacheManager()

    if not force_refresh:
        istat_layer = load_comuni_layer_from_istat_cache(cm)
        if istat_layer is not None:
            comuni = _extract_lombard_comuni_from_istat_layer(istat_layer)
            if comuni:
                return comuni, "istat_cache"

    cache = ComuniListCache(cm)

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

    The Regione Lombardia DUSAF service only exposes ``OBJECTID``, ``Shape``
    and ``DESCR`` via REST: the ``COD_TOT`` column that the desktop workflow
    relies on is packed into the ``DESCR`` prefix. We unpack it here so the
    rest of the algorithm and the QML categorised renderer can keep working.
    """
    client = LombardiaDusafClient()
    features = client.fetch_features(
        geometry=envelope,
        feedback=feedback,
        max_pages=max_pages,
        max_features=max_features,
    )

    if not features:
        raise ValueError(
            "Nessuna feature DUSAF restituita dal servizio REST per l'envelope richiesto."
        )

    enriched = []
    for feature in features:
        if not isinstance(feature, dict):
            continue

        properties = feature.get("properties")
        container_key = "properties"
        if not isinstance(properties, dict):
            attributes = feature.get("attributes")
            if isinstance(attributes, dict):
                properties = attributes
                container_key = "attributes"
            else:
                properties = {}
                container_key = "properties"

        properties = dict(properties)
        descr_raw = properties.get("DESCR", "")
        cod_tot, descr_clean = _parse_dusaf_descr(descr_raw)
        properties["COD_TOT"] = cod_tot
        properties["DESCR"] = descr_clean or descr_raw
        properties["DESCR_RAW"] = descr_raw

        new_feature = dict(feature)
        new_feature[container_key] = properties
        enriched.append(new_feature)

    return geojson_features_to_memory_layer(
        enriched,
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
