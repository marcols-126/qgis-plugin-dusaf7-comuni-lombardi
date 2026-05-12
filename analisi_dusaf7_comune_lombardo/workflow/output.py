# -*- coding: utf-8 -*-

"""Output writers and style helpers for the DUSAF 7 workflow.

The functions here save layers to GeoPackage, register them in the active QGIS
project and apply the QML styles that ship with the plugin. They are imported
both by the Processing algorithm and by the wizard UI.
"""

import csv
import os

from qgis.core import (
    QgsProcessingException,
    QgsProject,
    QgsVectorFileWriter,
    QgsVectorLayer,
)


STYLE_FOLDER_NAME = "stili"
STYLE_DUSAF_FINAL = "DUSAF7 - superfici.qml"
STYLE_DUSAF_CLIP_QC = "DUSAF7 - clip QC.qml"
STYLE_CONFINE = "Confine.qml"
STYLE_SLIVERS = "QC slivers DUSAF7.qml"


def style_path(plugin_dir, project_dir, style_filename):
    """Locate a QML style file with project override taking precedence.

    The lookup order is:
    1. ``<project_dir>/stili/<style_filename>`` (user override),
    2. ``<plugin_dir>/stili/<style_filename>`` (shipped default).

    When neither exists the project path is returned so the caller can log a
    consistent message.
    """
    project_style_path = os.path.join(project_dir, STYLE_FOLDER_NAME, style_filename)
    if os.path.exists(project_style_path):
        return project_style_path

    plugin_style_path = os.path.join(plugin_dir, STYLE_FOLDER_NAME, style_filename)
    if os.path.exists(plugin_style_path):
        return plugin_style_path

    return project_style_path


def apply_style(layer, plugin_dir, project_dir, style_filename, feedback):
    """Apply a QML style to a layer, logging the outcome via ``feedback``."""
    if layer is None or not layer.isValid():
        feedback.reportError(
            f"[STYLE WARNING] Layer non valido. Stile non applicato: {style_filename}",
            fatalError=False,
        )
        return

    resolved_path = style_path(plugin_dir, project_dir, style_filename)

    if not os.path.exists(resolved_path):
        feedback.reportError(
            f"[STYLE WARNING] File stile non trovato: {resolved_path}. "
            "Il layer resta con simbologia di default.",
            fatalError=False,
        )
        return

    try:
        result = layer.loadNamedStyle(resolved_path)

        success = True
        message = ""

        if isinstance(result, tuple):
            if len(result) >= 2:
                message = str(result[0])
                success = bool(result[1])
            elif len(result) == 1:
                message = str(result[0])

        layer.triggerRepaint()

        if success:
            feedback.pushInfo(
                f"[STYLE OK] Applicato stile '{style_filename}' al layer '{layer.name()}'."
            )
        else:
            feedback.reportError(
                f"[STYLE WARNING] Stile '{style_filename}' non applicato correttamente al layer "
                f"'{layer.name()}'. Messaggio: {message}",
                fatalError=False,
            )

    except Exception as exc:
        feedback.reportError(
            f"[STYLE WARNING] Errore durante applicazione stile '{style_filename}' "
            f"al layer '{layer.name()}': {exc}",
            fatalError=False,
        )


def save_layer_to_gpkg(layer, gpkg_path, layer_name, overwrite_file, context, feedback):
    """Write a layer to a GeoPackage, creating or appending as requested."""
    options = QgsVectorFileWriter.SaveVectorOptions()
    options.driverName = "GPKG"
    options.layerName = layer_name

    if overwrite_file:
        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile
    else:
        options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer

    result = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer,
        gpkg_path,
        context.transformContext(),
        options,
    )

    err_code = result[0]
    err_msg = result[1] if len(result) > 1 else ""

    if err_code != QgsVectorFileWriter.NoError:
        raise QgsProcessingException(f"Errore salvataggio layer '{layer_name}': {err_msg}")

    feedback.pushInfo(f"[OK] Salvato layer GeoPackage: {layer_name}")


def add_saved_layer_to_project(
    gpkg_path,
    layer_name,
    display_name,
    feedback,
    plugin_dir=None,
    project_dir=None,
    style_filename=None,
):
    """Load a GeoPackage layer into the active project and optionally style it."""
    uri = f"{gpkg_path}|layername={layer_name}"
    layer = QgsVectorLayer(uri, display_name, "ogr")

    if layer.isValid():
        if plugin_dir and project_dir and style_filename:
            apply_style(layer, plugin_dir, project_dir, style_filename, feedback)

        QgsProject.instance().addMapLayer(layer)
        feedback.pushInfo(f"[OK] Aggiunto al progetto QGIS: {display_name}")
    else:
        feedback.reportError(
            f"[WARN] Layer salvato ma non ricaricato nel progetto: {display_name}",
            fatalError=False,
        )


def export_summary_csv(layer, class_field, desc_field, csv_path, feedback):
    """Write the per-class area summary as semicolon-separated UTF-8 BOM CSV."""
    rows = []

    for feat in layer.getFeatures():
        codice = feat[class_field]
        descrizione = feat[desc_field]

        rows.append(
            {
                "codice_dusaf": "" if codice is None else str(codice),
                "descrizione": "" if descrizione is None else str(descrizione),
                "area_m2": float(feat["area_m2"]),
                "area_ha": float(feat["area_ha"]),
                "pct_dusaf": float(feat["pct_dusaf"]),
                "pct_comune": float(feat["pct_comune"]),
            }
        )

    rows.sort(key=lambda row: row["area_ha"], reverse=True)

    with open(csv_path, "w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "codice_dusaf",
                "descrizione",
                "area_m2",
                "area_ha",
                "pct_dusaf",
                "pct_comune",
            ],
            delimiter=";",
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(
                {
                    "codice_dusaf": row["codice_dusaf"],
                    "descrizione": row["descrizione"],
                    "area_m2": round(row["area_m2"], 3),
                    "area_ha": round(row["area_ha"], 6),
                    "pct_dusaf": round(row["pct_dusaf"], 6),
                    "pct_comune": round(row["pct_comune"], 6),
                }
            )

    feedback.pushInfo(f"[OK] CSV esportato: {csv_path}")
