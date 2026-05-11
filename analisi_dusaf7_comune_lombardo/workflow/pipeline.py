# -*- coding: utf-8 -*-

"""Pure Processing pipeline steps used by the DUSAF 7 algorithm.

Every function here is a thin wrapper around ``processing.run`` that returns a
fully materialised ``QgsVectorLayer`` and produces the same log messages the
monolithic algorithm produced before the refactor. The functions take all
inputs as explicit arguments so they can also be invoked from the wizard UI
without instantiating the algorithm class.
"""

import processing

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsProcessing,
    QgsProcessingException,
    QgsProcessingUtils,
    QgsVectorLayer,
)


TARGET_CRS_AUTHID = "EPSG:32632"


def target_crs():
    """Return the canonical operating CRS for the workflow.

    Constructed on demand so import order never touches the QGIS CRS database.
    """
    return QgsCoordinateReferenceSystem(TARGET_CRS_AUTHID)


def layer_from_output(output_object, context, layer_name):
    """Resolve a Processing OUTPUT value into a usable ``QgsVectorLayer``."""
    if isinstance(output_object, QgsVectorLayer):
        output_object.setName(layer_name)
        return output_object

    layer = QgsProcessingUtils.mapLayerFromString(str(output_object), context)

    if layer is not None and layer.isValid():
        layer.setName(layer_name)
        return layer

    layer = QgsVectorLayer(str(output_object), layer_name, "ogr")

    if layer.isValid():
        return layer

    raise QgsProcessingException(f"Output Processing non valido: {layer_name}")


def run_algorithm(alg_id, params, context, feedback, out_name):
    """Run a Processing algorithm and return its OUTPUT as a vector layer.

    Mirrors the behaviour of the legacy ``_run`` helper: emits the same log
    line, raises if cancelled, and validates that an OUTPUT key was returned.
    """
    if feedback.isCanceled():
        raise QgsProcessingException("Operazione annullata dall'utente.")

    feedback.pushInfo(f"[RUN] {alg_id} -> {out_name}")

    result = processing.run(
        alg_id,
        params,
        context=context,
        feedback=feedback,
        is_child_algorithm=True,
    )

    if "OUTPUT" not in result:
        raise QgsProcessingException(f"L'algoritmo {alg_id} non ha restituito OUTPUT.")

    return layer_from_output(result["OUTPUT"], context, out_name)


def fix_geometries(input_layer, context, feedback, out_name):
    """Apply ``native:fixgeometries`` with METHOD=1 when available."""
    params = {
        "INPUT": input_layer,
        "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
    }

    try:
        params_with_method = dict(params)
        params_with_method["METHOD"] = 1
        return run_algorithm(
            "native:fixgeometries",
            params_with_method,
            context,
            feedback,
            out_name,
        )
    except Exception:
        return run_algorithm("native:fixgeometries", params, context, feedback, out_name)


def reproject(input_layer, target_crs_obj, context, feedback, out_name):
    """Reproject a layer into the requested CRS."""
    return run_algorithm(
        "native:reprojectlayer",
        {
            "INPUT": input_layer,
            "TARGET_CRS": target_crs_obj,
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        },
        context,
        feedback,
        out_name,
    )


def extract_by_expression(input_layer, expression, context, feedback, out_name):
    """Extract features matching a QGIS expression."""
    feedback.pushInfo(f"[EXPRESSION] {expression}")

    return run_algorithm(
        "native:extractbyexpression",
        {
            "INPUT": input_layer,
            "EXPRESSION": expression,
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        },
        context,
        feedback,
        out_name,
    )


def clip(input_layer, overlay_layer, context, feedback, out_name):
    """Clip ``input_layer`` with ``overlay_layer``."""
    return run_algorithm(
        "native:clip",
        {
            "INPUT": input_layer,
            "OVERLAY": overlay_layer,
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        },
        context,
        feedback,
        out_name,
    )


def dissolve_all(input_layer, context, feedback, out_name):
    """Dissolve every feature into one without grouping fields."""
    return run_algorithm(
        "native:dissolve",
        {
            "INPUT": input_layer,
            "FIELD": [],
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        },
        context,
        feedback,
        out_name,
    )


def dissolve_by_fields(input_layer, fields, context, feedback, out_name):
    """Dissolve features grouped by the given attribute fields."""
    return run_algorithm(
        "native:dissolve",
        {
            "INPUT": input_layer,
            "FIELD": fields,
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        },
        context,
        feedback,
        out_name,
    )


def multipart_to_singleparts(input_layer, context, feedback, out_name):
    """Explode multipart geometries into singleparts."""
    return run_algorithm(
        "native:multiparttosingleparts",
        {
            "INPUT": input_layer,
            "OUTPUT": QgsProcessing.TEMPORARY_OUTPUT,
        },
        context,
        feedback,
        out_name,
    )
