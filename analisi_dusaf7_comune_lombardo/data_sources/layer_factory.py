# -*- coding: utf-8 -*-

"""Helpers that build in-memory ``QgsVectorLayer`` instances from REST GeoJSON.

The factories live here so that ``data_sources`` clients can return raw
GeoJSON dicts (easy to test, easy to cache as JSON) and the algorithm /
wizard can convert them into QGIS layers on demand. No file is written and
no project state is altered: the produced layers live entirely in memory and
the caller decides whether to add them to the project.
"""

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsGeometry,
    QgsVectorLayer,
)

from ..compat import (
    qfield_type_double,
    qfield_type_int,
    qfield_type_string,
)


_GEOMETRY_TYPE_BY_GEOJSON = {
    "Point": "Point",
    "MultiPoint": "MultiPoint",
    "LineString": "LineString",
    "MultiLineString": "MultiLineString",
    "Polygon": "Polygon",
    "MultiPolygon": "MultiPolygon",
}


def _format_xy(coord):
    """Format a single GeoJSON coordinate as ``"x y"`` (Z and M are dropped)."""
    if not isinstance(coord, (list, tuple)) or len(coord) < 2:
        raise ValueError(f"GeoJSON coordinate must be a list of at least 2 numbers, got: {coord!r}")
    return "{} {}".format(coord[0], coord[1])


def _geojson_geometry_to_wkt(geometry):
    """Translate a GeoJSON geometry dict into a WKT string.

    Supports the six core GeoJSON geometry types (Point, MultiPoint,
    LineString, MultiLineString, Polygon, MultiPolygon). Z/M coordinate
    extensions are silently dropped because the workflow only needs planar
    operations in EPSG:32632. This avoids depending on ``QgsGeometry.fromJson``
    whose semantics differ between QGIS releases (ArcGIS-style JSON vs GeoJSON).
    """
    if not isinstance(geometry, dict):
        raise ValueError("GeoJSON geometry must be a dictionary.")

    geometry_type = geometry.get("type")
    coords = geometry.get("coordinates")

    if not isinstance(geometry_type, str):
        raise ValueError("GeoJSON geometry is missing 'type'.")
    if coords is None:
        raise ValueError("GeoJSON geometry is missing 'coordinates'.")

    if geometry_type == "Point":
        return "POINT({})".format(_format_xy(coords))

    if geometry_type == "MultiPoint":
        return "MULTIPOINT({})".format(
            ", ".join("({})".format(_format_xy(p)) for p in coords)
        )

    if geometry_type == "LineString":
        return "LINESTRING({})".format(", ".join(_format_xy(p) for p in coords))

    if geometry_type == "MultiLineString":
        return "MULTILINESTRING({})".format(
            ", ".join(
                "({})".format(", ".join(_format_xy(p) for p in line)) for line in coords
            )
        )

    if geometry_type == "Polygon":
        return "POLYGON({})".format(
            ", ".join(
                "({})".format(", ".join(_format_xy(p) for p in ring)) for ring in coords
            )
        )

    if geometry_type == "MultiPolygon":
        return "MULTIPOLYGON({})".format(
            ", ".join(
                "({})".format(
                    ", ".join(
                        "({})".format(", ".join(_format_xy(p) for p in ring))
                        for ring in poly
                    )
                )
                for poly in coords
            )
        )

    raise ValueError(f"Unsupported GeoJSON geometry type: {geometry_type}")


def _infer_field_type(values):
    """Return the most permissive ``QgsField`` type compatible with ``values``."""
    seen_float = False
    seen_int = False
    seen_string = False

    for value in values:
        if value is None:
            continue
        if isinstance(value, bool):
            seen_int = True
            continue
        if isinstance(value, int):
            seen_int = True
            continue
        if isinstance(value, float):
            seen_float = True
            continue
        seen_string = True
        break

    if seen_string:
        return qfield_type_string()
    if seen_float:
        return qfield_type_double()
    if seen_int:
        return qfield_type_int()

    return qfield_type_string()


def _collect_attributes(features):
    """Return ``(field_names, attribute_rows)`` for a list of GeoJSON features.

    Property keys are collected in stable insertion order. Missing values are
    represented as ``None`` so all rows share the same length.
    """
    field_names = []
    seen = set()

    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            continue
        for key in properties.keys():
            if key not in seen:
                seen.add(key)
                field_names.append(key)

    rows = []
    for feature in features:
        properties = feature.get("properties") if isinstance(feature, dict) else None
        if not isinstance(properties, dict):
            properties = {}
        rows.append([properties.get(name) for name in field_names])

    return field_names, rows


def _build_field_defs(field_names, rows):
    """Return a list of ``QgsField`` whose types fit the collected values."""
    fields = []

    for column_index, name in enumerate(field_names):
        column_values = [row[column_index] for row in rows]
        field_type = _infer_field_type(column_values)
        fields.append(QgsField(str(name), field_type))

    return fields


def _detect_geometry_type(features, default="MultiPolygon"):
    """Pick a QGIS memory-layer geometry token from the first valid feature."""
    for feature in features:
        if not isinstance(feature, dict):
            continue
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict):
            continue
        geometry_type = geometry.get("type")
        if geometry_type in _GEOMETRY_TYPE_BY_GEOJSON:
            return _GEOMETRY_TYPE_BY_GEOJSON[geometry_type]

    return default


def geojson_features_to_memory_layer(
    features,
    layer_name,
    crs_authid="EPSG:32632",
    geometry_type=None,
):
    """Build a ``QgsVectorLayer`` (memory provider) from GeoJSON features.

    Args:
        features: Iterable of GeoJSON feature dicts with ``geometry`` and
            ``properties`` keys. ArcGIS-style ``attributes`` is also accepted
            and is normalised to ``properties`` internally.
        layer_name: Display name of the resulting layer.
        crs_authid: Authority id of the CRS to declare. The factory does not
            reproject: it simply tags the layer with this CRS.
        geometry_type: Optional override. When ``None`` the type is inferred
            from the first feature; falls back to ``MultiPolygon``.

    Returns:
        QgsVectorLayer: A new memory layer populated with the input features.

    Raises:
        ValueError: If the features list is malformed, empty, or contains
            invalid geometry that QGIS cannot parse.
    """
    if not isinstance(layer_name, str) or not layer_name.strip():
        raise ValueError("Memory layer name must be a non-empty string.")

    if not isinstance(crs_authid, str) or not crs_authid.strip():
        raise ValueError("Memory layer CRS authid must be a non-empty string.")

    features_list = list(features) if features is not None else []
    if not features_list:
        raise ValueError("Cannot build a memory layer from an empty feature list.")

    normalised = []
    for index, feature in enumerate(features_list):
        if not isinstance(feature, dict):
            raise ValueError(f"Feature at index {index} is not a dictionary.")

        properties = feature.get("properties")
        if properties is None and isinstance(feature.get("attributes"), dict):
            properties = feature["attributes"]
        if properties is None:
            properties = {}
        if not isinstance(properties, dict):
            raise ValueError(f"Feature at index {index} has invalid properties.")

        geometry = feature.get("geometry")
        if geometry is None:
            raise ValueError(f"Feature at index {index} is missing geometry.")
        if not isinstance(geometry, dict):
            raise ValueError(f"Feature at index {index} has non-dict geometry.")

        normalised.append({"properties": properties, "geometry": geometry})

    detected_geometry_type = geometry_type or _detect_geometry_type(normalised)
    uri = "{}?crs={}".format(detected_geometry_type, crs_authid)
    layer = QgsVectorLayer(uri, layer_name.strip(), "memory")

    if not layer.isValid():
        raise ValueError(
            "Failed to create memory layer with uri='{}' and crs='{}'.".format(uri, crs_authid)
        )

    field_names, rows = _collect_attributes(normalised)
    field_defs = _build_field_defs(field_names, rows)

    provider = layer.dataProvider()
    if field_defs:
        provider.addAttributes(field_defs)
        layer.updateFields()

    qgis_features = []
    for feature_index, (feature, row) in enumerate(zip(normalised, rows)):
        try:
            wkt = _geojson_geometry_to_wkt(feature["geometry"])
        except ValueError as exc:
            raise ValueError(
                "Feature at index {} has invalid GeoJSON geometry: {}".format(
                    feature_index, exc
                )
            ) from exc

        qgs_geom = QgsGeometry.fromWkt(wkt)
        if qgs_geom.isNull() or qgs_geom.isEmpty():
            raise ValueError(
                "Feature at index {} produced an invalid QGIS geometry (WKT length {}).".format(
                    feature_index, len(wkt)
                )
            )

        qgs_feat = QgsFeature(layer.fields())
        qgs_feat.setGeometry(qgs_geom)
        qgs_feat.setAttributes(list(row))
        qgis_features.append(qgs_feat)

    if qgis_features:
        ok, _ = provider.addFeatures(qgis_features)
        if not ok:
            raise ValueError("Failed to insert features into the memory layer.")
        layer.updateExtents()

    target_crs = QgsCoordinateReferenceSystem(crs_authid)
    if target_crs.isValid():
        layer.setCrs(target_crs)

    return layer
