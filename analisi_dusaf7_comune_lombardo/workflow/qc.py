# -*- coding: utf-8 -*-

"""Geometry and attribute quality-control helpers used by the workflow.

These functions operate on already-materialised ``QgsVectorLayer`` instances
and do not rely on the algorithm class. They are imported by both the
Processing algorithm and the wizard UI when previewing QC summaries.
"""

from qgis.core import QgsField, edit

from ..compat import qfield_type_double, qfield_type_int


AUDIT_TOLERANCE_M2 = 1.0


def count_invalid_geometries(layer):
    """Return ``(total, invalid, empty)`` counts for a vector layer."""
    total = 0
    invalid = 0
    empty = 0

    for feat in layer.getFeatures():
        total += 1
        geom = feat.geometry()

        if geom is None or geom.isEmpty():
            empty += 1
        elif not geom.isGeosValid():
            invalid += 1

    return total, invalid, empty


def total_area_m2(layer):
    """Sum the planar area of all non-empty geometries in a layer."""
    total = 0.0

    for feat in layer.getFeatures():
        geom = feat.geometry()

        if geom is not None and not geom.isEmpty():
            total += geom.area()

    return total


def add_or_reset_fields(layer, field_defs):
    """Add fields to a layer, dropping any pre-existing fields with same names."""
    provider = layer.dataProvider()
    existing_names = [field.name() for field in layer.fields()]

    to_delete = []

    for field in field_defs:
        if field.name() in existing_names:
            idx = layer.fields().indexFromName(field.name())
            if idx >= 0:
                to_delete.append(idx)

    if to_delete:
        provider.deleteAttributes(to_delete)
        layer.updateFields()

    provider.addAttributes(field_defs)
    layer.updateFields()


def add_area_fields(
    layer,
    sliver_min_area_m2=None,
    total_dusaf_m2=None,
    boundary_area_m2=None,
    include_sliver=False,
    include_percentages=False,
):
    """Populate ``area_m2``, ``area_ha`` and optional QC fields on a layer.

    Behaviour is intentionally identical to the legacy implementation:
    fields are recreated when they already exist, percentages are computed only
    when ``include_percentages`` is True, and the sliver flag is added only
    when ``include_sliver`` is True.
    """
    fields = [
        QgsField("area_m2", qfield_type_double(), "double", 20, 3),
        QgsField("area_ha", qfield_type_double(), "double", 20, 6),
    ]

    if include_percentages:
        fields.append(QgsField("pct_dusaf", qfield_type_double(), "double", 20, 6))
        fields.append(QgsField("pct_comune", qfield_type_double(), "double", 20, 6))

    if include_sliver:
        fields.append(QgsField("sliver", qfield_type_int(), "integer", 1, 0))

    add_or_reset_fields(layer, fields)

    idx_area_m2 = layer.fields().indexFromName("area_m2")
    idx_area_ha = layer.fields().indexFromName("area_ha")
    idx_pct_dusaf = layer.fields().indexFromName("pct_dusaf") if include_percentages else None
    idx_pct_comune = layer.fields().indexFromName("pct_comune") if include_percentages else None
    idx_sliver = layer.fields().indexFromName("sliver") if include_sliver else None

    with edit(layer):
        for feat in layer.getFeatures():
            geom = feat.geometry()
            area_m2 = 0.0 if geom is None or geom.isEmpty() else geom.area()
            area_ha = area_m2 / 10000.0

            layer.changeAttributeValue(feat.id(), idx_area_m2, area_m2)
            layer.changeAttributeValue(feat.id(), idx_area_ha, area_ha)

            if include_percentages:
                pct_dusaf = 0.0 if not total_dusaf_m2 else (area_m2 / total_dusaf_m2) * 100.0
                pct_comune = 0.0 if not boundary_area_m2 else (area_m2 / boundary_area_m2) * 100.0

                layer.changeAttributeValue(feat.id(), idx_pct_dusaf, pct_dusaf)
                layer.changeAttributeValue(feat.id(), idx_pct_comune, pct_comune)

            if include_sliver:
                threshold = 0.0 if sliver_min_area_m2 is None else float(sliver_min_area_m2)
                sliver = 1 if 0.0 < area_m2 <= threshold else 0
                layer.changeAttributeValue(feat.id(), idx_sliver, sliver)

    return layer
