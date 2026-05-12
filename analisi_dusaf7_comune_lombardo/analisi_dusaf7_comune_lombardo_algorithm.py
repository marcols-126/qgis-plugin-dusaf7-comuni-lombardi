# -*- coding: utf-8 -*-

import os
import re
from datetime import datetime

from qgis.PyQt.QtCore import Qt, QStringListModel
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from processing.gui.wrappers import WidgetWrapper
except Exception:
    WidgetWrapper = object

from qgis.core import (
    QgsApplication,
    QgsFeatureRequest,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingOutputFile,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsProject,
    QgsVectorLayer,
)

from .data_sources import normalize_comune_display_name
from .workflow import pipeline, qc, output
from .workflow.data_resolver import (
    envelope_from_layer_extent,
    fetch_comune_geometry_layer,
    fetch_dusaf_layer_for_envelope,
    get_comuni_list_for_autocomplete,
)
from .workflow.output import (
    STYLE_CONFINE,
    STYLE_DUSAF_CLIP_QC,
    STYLE_DUSAF_FINAL,
    STYLE_FOLDER_NAME,
    STYLE_SLIVERS,
)
from .workflow.pipeline import target_crs
from .workflow.qc import AUDIT_TOLERANCE_M2


# =============================================================================
# CONFIGURAZIONE FISSA - PROTOCOLLO QC-4
# =============================================================================

DUSAF_REQUIRED_LAYER_NAME = "DUSAF7"
COMUNI_REQUIRED_LAYER_NAME = "Com01012026_WGS84"

DUSAF_CLASS_FIELD = "COD_TOT"
DUSAF_DESC_FIELD = "DESCR"

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))

URL_CONFINI_ISTAT_2026 = (
    "https://www.istat.it/notizia/confini-delle-unita-amministrative-a-fini-statistici-al-1-gennaio-2018-2/"
)

URL_DUSAF7_RL = (
    "https://www.geoportale.regione.lombardia.it/download-pacchetti?"
    "p_p_id=dwnpackageportlet_WAR_gptdownloadportlet&"
    "p_p_lifecycle=0&"
    "p_p_state=normal&"
    "p_p_mode=view&"
    "_dwnpackageportlet_WAR_gptdownloadportlet_metadataid=r_lombar%3A7cd05e9f-b693-4d7e-a8de-71b40b45f54e&"
    "_jsfBridgeRedirect=true"
)


MUNICIPALITY_FIELD_CANDIDATES = [
    "COMUNE",
    "comune",
    "DEN_COM",
    "den_com",
    "DENOM_COM",
    "denom_com",
    "DENOMINAZ",
    "denominaz",
    "DENOMINAZI",
    "denominazi",
    "NOME_COM",
    "nome_com",
    "NOME",
    "nome",
]

REGION_CODE_FIELD_CANDIDATES = [
    "COD_REG",
    "cod_reg",
    "COD_REGION",
    "cod_region",
    "COD_REGI",
    "cod_regi",
]

REGION_NAME_FIELD_CANDIDATES = [
    "DEN_REG",
    "den_reg",
    "REGIONE",
    "regione",
    "NOME_REG",
    "nome_reg",
]

_PREREQUISITE_DIALOG_SHOWN = False


# =============================================================================
# FUNZIONI GLOBALI
# =============================================================================

def _normalize_comune_value(value):
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text.strip())
    return text.casefold()


def _first_available_field(layer, candidates):
    if layer is None or not layer.isValid():
        return None

    names = [field.name() for field in layer.fields()]
    lower_map = {name.lower(): name for name in names}

    for candidate in candidates:
        if candidate in names:
            return candidate
        if candidate.lower() in lower_map:
            return lower_map[candidate.lower()]

    return None


def _layer_has_fields(layer, required_fields):
    if layer is None or not layer.isValid():
        return False

    names = [field.name() for field in layer.fields()]
    lower_names = {name.lower() for name in names}

    for field in required_fields:
        if field not in names and field.lower() not in lower_names:
            return False

    return True


def _find_project_layer_by_name_and_fields(layer_name, required_fields=None, allow_contains=True):
    project = QgsProject.instance()
    required_fields = required_fields or []

    exact_matches = project.mapLayersByName(layer_name)
    for layer in exact_matches:
        if isinstance(layer, QgsVectorLayer) and layer.isValid() and _layer_has_fields(layer, required_fields):
            return layer

    target = layer_name.strip().lower()

    for layer in project.mapLayers().values():
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            continue

        source_name = os.path.splitext(os.path.basename(layer.source().split("|")[0]))[0].lower()
        layer_name_current = layer.name().strip()
        layer_name_clean = layer_name_current.split("[")[0].strip().lower()

        name_match = (
            layer_name_current.lower() == target
            or layer_name_clean == target
            or source_name == target
        )

        if allow_contains:
            name_match = name_match or target in layer_name_current.lower()

        if name_match and _layer_has_fields(layer, required_fields):
            return layer

    return None


def _find_dusaf_project_layer():
    candidates = [
        DUSAF_REQUIRED_LAYER_NAME,
        "DUSAF 7",
        "DUSAF_7",
        "DUSAF7_RL",
    ]

    for candidate in candidates:
        layer = _find_project_layer_by_name_and_fields(
            candidate,
            required_fields=[DUSAF_CLASS_FIELD, DUSAF_DESC_FIELD],
            allow_contains=True,
        )
        if layer:
            return layer

    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer) and layer.isValid():
            if _layer_has_fields(layer, [DUSAF_CLASS_FIELD, DUSAF_DESC_FIELD]):
                return layer

    return None


def _find_comuni_project_layer():
    candidates = [
        COMUNI_REQUIRED_LAYER_NAME,
        "Com01012026",
        "Comuni_ISTAT_2026",
        "Comuni ISTAT 2026",
        "Comuni",
    ]

    for candidate in candidates:
        layer = _find_project_layer_by_name_and_fields(
            candidate,
            required_fields=[],
            allow_contains=True,
        )
        if layer and _first_available_field(layer, MUNICIPALITY_FIELD_CANDIDATES):
            return layer

    for layer in QgsProject.instance().mapLayers().values():
        if isinstance(layer, QgsVectorLayer) and layer.isValid():
            if _first_available_field(layer, MUNICIPALITY_FIELD_CANDIDATES):
                return layer

    return None


def _show_prerequisite_dialog():
    global _PREREQUISITE_DIALOG_SHOWN

    if _PREREQUISITE_DIALOG_SHOWN:
        return

    _PREREQUISITE_DIALOG_SHOWN = True

    parent = QApplication.activeWindow()

    dialog = QDialog(parent)
    dialog.setWindowTitle("Prerequisiti obbligatori - Analisi DUSAF 7")
    dialog.setMinimumWidth(720)

    layout = QVBoxLayout(dialog)

    label = QLabel()
    label.setTextFormat(Qt.RichText)
    label.setOpenExternalLinks(True)
    label.setWordWrap(True)
    label.setText(
        f"""
        <h2 style="color:#cc0000;">ATTENZIONE - DATI OBBLIGATORI</h2>

        <p>
        Prima di proseguire con l'interfaccia dello strumento, verifica di avere scaricato
        i dati di base, di averli salvati o estratti nella cartella del progetto QGIS
        e di avere caricato nel progetto solo i layer necessari.
        </p>

        <h3>1. Confini amministrativi ISTAT 2026</h3>
        <p>
        Scarica i confini amministrativi dal link:
        <br>
        <a href="{URL_CONFINI_ISTAT_2026}">
        {URL_CONFINI_ISTAT_2026}
        </a>
        </p>
        <p>
        Dopo il download, estrai tutto il contenuto nella cartella del progetto QGIS
        e carica nel progetto il layer:
        <br>
        <b>{COMUNI_REQUIRED_LAYER_NAME}</b>
        </p>

        <h3>2. DUSAF 7 - Regione Lombardia</h3>
        <p>
        Scarica DUSAF 7 dal Geoportale di Regione Lombardia:
        <br>
        <a href="{URL_DUSAF7_RL}">
        Scarica DUSAF 7 - Regione Lombardia
        </a>
        </p>
        <p>
        Dopo il download, estrai tutto il contenuto nella cartella del progetto QGIS
        e carica nel progetto il layer:
        <br>
        <b>{DUSAF_REQUIRED_LAYER_NAME}</b>
        </p>

        <h3>Prima di premere OK</h3>
        <ul>
            <li>Il progetto QGIS deve essere salvato.</li>
            <li>Il layer <b>{DUSAF_REQUIRED_LAYER_NAME}</b> deve essere già caricato nel progetto.</li>
            <li>Il layer <b>{COMUNI_REQUIRED_LAYER_NAME}</b> deve essere già caricato nel progetto.</li>
            <li>Gli stili QML vengono cercati nella cartella <b>stili</b> del progetto e, se assente, nella cartella <b>stili</b> del plugin.</li>
        </ul>

        <p>
        Premi <b>OK</b> solo dopo avere completato questi passaggi.
        </p>
        """
    )

    layout.addWidget(label)

    button_box = QDialogButtonBox(QDialogButtonBox.Ok)
    button_box.button(QDialogButtonBox.Ok).setText("OK, prosegui")
    button_box.accepted.connect(dialog.accept)
    layout.addWidget(button_box)

    if hasattr(dialog, "exec_"):
        dialog.exec_()
    else:
        dialog.exec()


# =============================================================================
# WIDGET CUSTOM AUTOCOMPLETAMENTO COMUNI + ALERT DINAMICO
# =============================================================================

class ComuneAutocompleteWidgetWrapper(WidgetWrapper):

    def createWidget(self):
        # Phase 3: the modal prerequisite popup is no longer shown. The alert
        # label below the line edit now communicates the actual data source
        # (project layer vs REST/cache) without forcing a modal interruption.
        self._valid_names = []
        self._valid_name_by_norm = {}

        self._container = QWidget()
        layout = QVBoxLayout(self._container)
        layout.setContentsMargins(0, 0, 0, 0)

        self._line_edit = QLineEdit()
        self._line_edit.setPlaceholderText(
            "Digita il nome del Comune lombardo e seleziona un valore esatto tra quelli proposti."
        )

        self._alert_label = QLabel()
        self._alert_label.setWordWrap(True)
        self._alert_label.setTextFormat(Qt.RichText)

        self._model = QStringListModel([])
        self._completer = QCompleter(self._model, self._line_edit)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchStartsWith)
        self._completer.setCompletionMode(QCompleter.PopupCompletion)

        self._line_edit.setCompleter(self._completer)

        layout.addWidget(self._line_edit)
        layout.addWidget(self._alert_label)

        self._update_completer_from_project_layer()
        self._line_edit.textChanged.connect(self._on_text_changed)

        return self._container

    def postInitialize(self, wrappers):
        self._update_completer_from_project_layer()
        self._on_text_changed(self.value())

    def _set_alert(self, html_text, color="#555555", background="#f7f7f7", border="#cccccc"):
        self._alert_label.setStyleSheet(
            f"QLabel {{ color: {color}; background-color: {background}; "
            f"border: 1px solid {border}; padding: 4px; }}"
        )
        self._alert_label.setText(html_text)

    def _update_completer_from_project_layer(self):
        try:
            layer = _find_comuni_project_layer()

            if layer is None or not layer.isValid():
                self._populate_completer_from_rest()
                return

            municipality_field = _first_available_field(layer, MUNICIPALITY_FIELD_CANDIDATES)
            if not municipality_field:
                self._valid_names = []
                self._valid_name_by_norm = {}
                self._model.setStringList([])
                self._set_alert(
                    "<b>Comune non verificabile.</b> Il layer Comuni non contiene un campo nome Comune riconosciuto.",
                    color="#990000",
                    background="#fff0f0",
                    border="#cc0000",
                )
                return

            region_code_field = _first_available_field(layer, REGION_CODE_FIELD_CANDIDATES)
            region_name_field = _first_available_field(layer, REGION_NAME_FIELD_CANDIDATES)

            attrs = [municipality_field]
            if region_code_field:
                attrs.append(region_code_field)
            if region_name_field:
                attrs.append(region_name_field)

            request = QgsFeatureRequest()
            request.setFlags(QgsFeatureRequest.NoGeometry)
            request.setSubsetOfAttributes(attrs, layer.fields())

            names = set()

            for feat in layer.getFeatures(request):
                if region_code_field:
                    region_code = feat[region_code_field]
                    region_code_text = "" if region_code is None else str(region_code).strip()
                    try:
                        if int(region_code_text) != 3:
                            continue
                    except Exception:
                        if region_code_text not in ("3", "03", "003"):
                            continue

                elif region_name_field:
                    region_name = feat[region_name_field]
                    region_name_text = "" if region_name is None else str(region_name).strip().upper()
                    if region_name_text != "LOMBARDIA":
                        continue

                value = feat[municipality_field]
                if value is None:
                    continue

                comune = str(value).strip()
                if comune:
                    names.add(comune)

            self._valid_names = sorted(names, key=lambda x: x.lower())
            self._valid_name_by_norm = {
                _normalize_comune_value(name): name for name in self._valid_names
            }

            self._model.setStringList(self._valid_names)
            self._on_text_changed(self.value())

        except Exception as e:
            self._valid_names = []
            self._valid_name_by_norm = {}
            self._model.setStringList([])
            self._set_alert(
                f"<b>Errore durante la lettura dei Comuni.</b> {e}",
                color="#990000",
                background="#fff0f0",
                border="#cc0000",
            )

    def _populate_completer_from_rest(self):
        """Fallback when no Comuni layer is loaded: fetch the list from
        the Regione Lombardia REST service (cached in the QGIS profile)."""
        try:
            self._set_alert(
                "Nessun layer Comuni nel progetto: scarico la lista ufficiale "
                "da Regione Lombardia (~3 sec la prima volta, poi in cache)...",
                color="#5c4300",
                background="#fff8e1",
                border="#e0b400",
            )
            QApplication.setOverrideCursor(Qt.WaitCursor)
            QApplication.processEvents()

            try:
                comuni, source = get_comuni_list_for_autocomplete()
            finally:
                QApplication.restoreOverrideCursor()

            display_names = sorted(
                {
                    normalize_comune_display_name(entry.get("NOME_COM", ""))
                    for entry in comuni
                    if entry.get("NOME_COM")
                },
                key=lambda x: x.lower(),
            )

            self._valid_names = display_names
            self._valid_name_by_norm = {
                _normalize_comune_value(name): name for name in self._valid_names
            }
            self._model.setStringList(self._valid_names)

            origin = "cache locale" if source == "cache" else "servizio REST RL"
            self._set_alert(
                f"<b>Comuni caricati da {origin}</b> ({len(self._valid_names)} Comuni lombardi). "
                "Digitare il nome e selezionare un valore dal menu a tendina.",
                color="#5c4300",
                background="#fff8e1",
                border="#e0b400",
            )

            self._on_text_changed(self.value())

        except Exception as exc:
            self._valid_names = []
            self._valid_name_by_norm = {}
            self._model.setStringList([])
            self._set_alert(
                "<b>Comuni non disponibili.</b> Nessun layer nel progetto e fetch REST fallito: "
                f"{exc}",
                color="#990000",
                background="#fff0f0",
                border="#cc0000",
            )

    def _on_text_changed(self, text):
        text = "" if text is None else str(text).strip()
        norm_text = _normalize_comune_value(text)

        if not self._valid_names:
            self._set_alert(
                "<b>Comune non verificabile.</b> Lista Comuni non disponibile "
                "(nessun layer in progetto e fetch REST fallito).",
                color="#990000",
                background="#fff0f0",
                border="#cc0000",
            )
            return

        if not text:
            self._set_alert(
                "Digitare il nome del Comune e selezionare un valore esatto tra quelli proposti dal menu a tendina.",
                color="#5c4300",
                background="#fff8e1",
                border="#e0b400",
            )
            return

        if norm_text in self._valid_name_by_norm:
            canonical = self._valid_name_by_norm[norm_text]
            self._set_alert(
                f"<b>Comune valido:</b> {canonical}. Lo strumento può essere eseguito.",
                color="#006100",
                background="#eefbea",
                border="#3caa3c",
            )
            return

        suggestions = [
            name for name in self._valid_names
            if _normalize_comune_value(name).startswith(norm_text)
        ][:8]

        if suggestions:
            suggestion_text = ", ".join(f"<b>{name}</b>" for name in suggestions)
            self._set_alert(
                "Comune non ancora valido. Selezionare uno dei valori esatti proposti, ad esempio: "
                f"{suggestion_text}",
                color="#5c4300",
                background="#fff8e1",
                border="#e0b400",
            )
        else:
            self._set_alert(
                "Comune non valido. Il valore digitato non corrisponde a un Comune lombardo "
                "tra quelli noti dalla sorgente attiva.",
                color="#990000",
                background="#fff0f0",
                border="#cc0000",
            )

    def value(self):
        try:
            return self._line_edit.text().strip()
        except Exception:
            return ""

    def setValue(self, value):
        try:
            self._line_edit.setText("" if value is None else str(value))
        except Exception:
            pass

    def widgetValue(self):
        return self.value()

    def setWidgetValue(self, value, context=None):
        self.setValue(value)


# =============================================================================
# ALGORITMO PROCESSING
# =============================================================================

class AnalisiDusaf7ComuneLombardoPluginAlgorithm(QgsProcessingAlgorithm):

    COMUNE_NAME = "COMUNE_NAME"
    SLIVER_MIN_AREA_M2 = "SLIVER_MIN_AREA_M2"

    OUTPUT_GPKG = "OUTPUT_GPKG"
    OUTPUT_CSV = "OUTPUT_CSV"

    def name(self):
        return "analisi_dusaf7_comune_lombardo"

    def displayName(self):
        return "Analisi DUSAF 7 - Comune Lombardo"

    def group(self):
        return "Analisi Territoriale"

    def groupId(self):
        return "analisi_territoriale"

    def createInstance(self):
        return AnalisiDusaf7ComuneLombardoPluginAlgorithm()

    def shortHelpString(self):
        return f"""
        <h3>Analisi DUSAF 7 - Comune Lombardo</h3>

        <div style="border:2px solid #cc0000; padding:12px; background-color:#fff3f3;">
            <h2 style="color:#cc0000;">ATTENZIONE - DATI DA SCARICARE E CARICARE PRIMA DELL'ESECUZIONE</h2>

            <p>
            Prima di utilizzare questo strumento è necessario scaricare i dati di base,
            estrarre tutto il contenuto nella cartella del progetto QGIS e caricare nel progetto
            solo i layer necessari.
            </p>

            <h3>1. Confini amministrativi ISTAT 2026</h3>
            <p>
            <a href="{URL_CONFINI_ISTAT_2026}">
            Scarica Confini amministrativi ISTAT 2026
            </a>
            </p>
            <p>
            Layer da caricare in QGIS:
            <br>
            <b>{COMUNI_REQUIRED_LAYER_NAME}</b>
            </p>

            <h3>2. DUSAF 7 - Regione Lombardia</h3>
            <p>
            <a href="{URL_DUSAF7_RL}">
            Scarica DUSAF 7 - Regione Lombardia
            </a>
            </p>
            <p>
            Layer da caricare in QGIS:
            <br>
            <b>{DUSAF_REQUIRED_LAYER_NAME}</b>
            </p>

            <h3>Prerequisiti operativi</h3>
            <ul>
                <li>Il progetto QGIS deve essere salvato.</li>
                <li>Il layer <b>{DUSAF_REQUIRED_LAYER_NAME}</b> deve essere già caricato nel progetto.</li>
                <li>Il layer <b>{COMUNI_REQUIRED_LAYER_NAME}</b> deve essere già caricato nel progetto.</li>
                <li>Gli stili QML vengono cercati nella cartella <b>stili</b> del progetto e, se assente, nella cartella <b>stili</b> del plugin.</li>
            </ul>

            <p>
            Il nome Comune deve corrispondere a un valore valido del layer
            <b>{COMUNI_REQUIRED_LAYER_NAME}</b>. Lo strumento non procede se il Comune digitato
            non è presente nell'elenco dei Comuni lombardi.
            </p>
        </div>

        <h4>Interfaccia utente</h4>
        <ul>
            <li>Nome del Comune da analizzare, con completamento automatico.</li>
            <li>Area minima degli slivers in m².</li>
        </ul>

        <p>
        I layer <b>{DUSAF_REQUIRED_LAYER_NAME}</b> e <b>{COMUNI_REQUIRED_LAYER_NAME}</b>
        vengono riconosciuti automaticamente dal progetto QGIS attivo.
        </p>

        <h4>Stili QML attesi</h4>
        <ul>
            <li><b>{STYLE_DUSAF_FINAL}</b></li>
            <li><b>{STYLE_DUSAF_CLIP_QC}</b></li>
            <li><b>{STYLE_CONFINE}</b></li>
            <li><b>{STYLE_SLIVERS}</b></li>
        </ul>

        <h4>Workflow</h4>
        <ul>
            <li>Verifica preliminare dei layer caricati.</li>
            <li>Validazione del Comune selezionato.</li>
            <li>Fix geometries sugli input.</li>
            <li>Riproiezione in EPSG:32632.</li>
            <li>Estrazione del Comune indicato.</li>
            <li>Clip DUSAF sul perimetro comunale.</li>
            <li>Fix geometries post-clip.</li>
            <li>Gestione slivers con flag dedicato.</li>
            <li>Dissolve per classe DUSAF.</li>
            <li>Calcolo superfici in m², ettari e percentuali.</li>
            <li>Data Audit tra superficie DUSAF calcolata e superficie del perimetro comunale.</li>
            <li>Esportazione GeoPackage e CSV nella cartella del progetto QGIS attivo.</li>
        </ul>

        <h4>Configurazione fissa DUSAF - protocollo QC-4</h4>
        <ul>
            <li>Campo codice DUSAF: <b>{DUSAF_CLASS_FIELD}</b></li>
            <li>Campo descrizione DUSAF: <b>{DUSAF_DESC_FIELD}</b></li>
        </ul>
        """

    def initAlgorithm(self, config=None):

        comune_param = QgsProcessingParameterString(
            self.COMUNE_NAME,
            "Nome del Comune da analizzare",
            defaultValue="",
            optional=False,
        )

        comune_param.setMetadata(
            {
                "widget_wrapper": {
                    "class": ComuneAutocompleteWidgetWrapper,
                }
            }
        )

        self.addParameter(comune_param)

        self.addParameter(
            QgsProcessingParameterNumber(
                self.SLIVER_MIN_AREA_M2,
                "Area minima slivers in m²",
                type=QgsProcessingParameterNumber.Double,
                defaultValue=1.0,
                minValue=0.0,
            )
        )

        self.addOutput(QgsProcessingOutputFile(self.OUTPUT_GPKG, "GeoPackage di output"))
        self.addOutput(QgsProcessingOutputFile(self.OUTPUT_CSV, "CSV riepilogo superfici"))

    # -------------------------------------------------------------------------
    # LOG
    # -------------------------------------------------------------------------

    def _msg(self, feedback, text):
        feedback.pushInfo(str(text))

    def _warn(self, feedback, text):
        feedback.reportError(str(text), fatalError=False)

    # -------------------------------------------------------------------------
    # UTILITY
    # -------------------------------------------------------------------------

    def _qfield(self, field_name):
        return '"' + str(field_name).replace('"', '""') + '"'

    def _qstr(self, value):
        return "'" + str(value).replace("'", "''") + "'"

    def _safe_name(self, value):
        text = str(value).strip().lower()
        text = text.replace("à", "a").replace("è", "e").replace("é", "e")
        text = text.replace("ì", "i").replace("ò", "o").replace("ù", "u")
        text = re.sub(r"[^a-z0-9]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("_")
        return text if text else "comune"

    def _project_dir(self):
        project = QgsProject.instance()

        if project.fileName():
            return os.path.dirname(project.fileName())

        home = project.homePath()
        if home and str(home).strip() not in ("", "."):
            return os.path.abspath(home)

        raise QgsProcessingException(
            "MANCA_DATO: il progetto QGIS non è salvato. "
            "Salva prima il progetto .qgz/.qgs per usare output relativi alla cartella del progetto."
        )

    # -------------------------------------------------------------------------
    # LAYER E VALIDAZIONE COMUNE
    # -------------------------------------------------------------------------

    def _get_required_dusaf_layer(self, comune_geometry_layer=None, feedback=None):
        """Return a DUSAF layer.

        Priority: layer already loaded in the QGIS project (back-compat); when
        not available the layer is fetched from the Regione Lombardia ArcGIS
        REST service for the envelope of ``comune_geometry_layer`` and returned
        as an in-memory layer in EPSG:32632.
        """
        layer = _find_dusaf_project_layer()

        if layer is not None and layer.isValid():
            if feedback is not None:
                feedback.pushInfo(f"[DATA] DUSAF da progetto: {layer.name()}")
            return layer

        if comune_geometry_layer is None:
            raise QgsProcessingException(
                f"MANCA_DATO: il layer '{DUSAF_REQUIRED_LAYER_NAME}' non è caricato nel progetto "
                "e non posso interrogare il servizio REST senza una geometria del Comune."
            )

        if feedback is not None:
            feedback.pushInfo(
                "[DATA] DUSAF non in progetto: fetch REST dal servizio Regione Lombardia "
                "per l'envelope del Comune..."
            )

        envelope = envelope_from_layer_extent(comune_geometry_layer, padding_m=50.0)
        try:
            rest_layer = fetch_dusaf_layer_for_envelope(envelope, feedback=feedback)
        except (ValueError, RuntimeError) as exc:
            raise QgsProcessingException(
                "REST: fetch DUSAF fallito dal servizio Regione Lombardia. "
                f"Dettaglio: {exc}"
            ) from exc

        if not rest_layer.isValid():
            raise QgsProcessingException(
                "REST: il layer DUSAF in memoria risulta non valido dopo il fetch."
            )

        if feedback is not None:
            feedback.pushInfo(
                f"[DATA] DUSAF da REST: {rest_layer.featureCount()} feature in EPSG:32632."
            )

        return rest_layer

    def _get_required_comuni_layer(self, comune_name=None, feedback=None):
        """Return a Comuni layer.

        Priority: layer already loaded in the QGIS project (back-compat). When
        not available, ``comune_name`` is required and the corresponding
        single feature is fetched via REST from Regione Lombardia
        Ambiti_Amministrativi.
        """
        layer = _find_comuni_project_layer()

        if layer is not None and layer.isValid():
            if feedback is not None:
                feedback.pushInfo(f"[DATA] Comuni da progetto: {layer.name()}")
            return layer

        if not comune_name:
            raise QgsProcessingException(
                f"MANCA_DATO: il layer '{COMUNI_REQUIRED_LAYER_NAME}' non è caricato nel progetto "
                "e nessun nome Comune è disponibile per il fetch REST."
            )

        if feedback is not None:
            feedback.pushInfo(
                f"[DATA] Comuni non in progetto: fetch REST del Comune '{comune_name}' "
                "dal servizio Regione Lombardia Ambiti_Amministrativi..."
            )

        try:
            rest_layer = fetch_comune_geometry_layer(comune_name, feedback=feedback)
        except (ValueError, RuntimeError) as exc:
            raise QgsProcessingException(
                "REST: fetch geometria Comune fallito dal servizio Regione Lombardia. "
                f"Dettaglio: {exc}"
            ) from exc

        if rest_layer is None:
            raise QgsProcessingException(
                f"REST: nessun Comune con nome '{comune_name}' trovato nel servizio "
                "Regione Lombardia Ambiti_Amministrativi. Verificare la digitazione."
            )

        if not rest_layer.isValid():
            raise QgsProcessingException(
                "REST: il layer Comune in memoria risulta non valido dopo il fetch."
            )

        if feedback is not None:
            feedback.pushInfo(
                f"[DATA] Comune da REST: 1 feature in EPSG:32632 ({rest_layer.name()})."
            )

        return rest_layer

    def _validate_comune_name_on_layer(self, comuni_layer, comune_name, feedback):
        municipality_field = self._resolve_first_available_field(
            comuni_layer,
            MUNICIPALITY_FIELD_CANDIDATES,
            "nome Comune",
        )

        region_code_field = self._find_optional_field(comuni_layer, REGION_CODE_FIELD_CANDIDATES)
        region_name_field = self._find_optional_field(comuni_layer, REGION_NAME_FIELD_CANDIDATES)

        attrs = [municipality_field]
        if region_code_field:
            attrs.append(region_code_field)
        if region_name_field:
            attrs.append(region_name_field)

        request = QgsFeatureRequest()
        request.setFlags(QgsFeatureRequest.NoGeometry)
        request.setSubsetOfAttributes(attrs, comuni_layer.fields())

        valid_names = {}
        requested_norm = _normalize_comune_value(comune_name)

        for feat in comuni_layer.getFeatures(request):
            if region_code_field:
                region_code = feat[region_code_field]
                region_code_text = "" if region_code is None else str(region_code).strip()
                try:
                    if int(region_code_text) != 3:
                        continue
                except Exception:
                    if region_code_text not in ("3", "03", "003"):
                        continue

            elif region_name_field:
                region_name = feat[region_name_field]
                region_name_text = "" if region_name is None else str(region_name).strip().upper()
                if region_name_text != "LOMBARDIA":
                    continue

            value = feat[municipality_field]
            if value is None:
                continue

            canonical = str(value).strip()
            if canonical:
                valid_names[_normalize_comune_value(canonical)] = canonical

        if requested_norm not in valid_names:
            suggestions = [
                name for key, name in sorted(valid_names.items(), key=lambda item: item[1].lower())
                if key.startswith(requested_norm)
            ][:10]

            if suggestions:
                suggestions_text = ", ".join(suggestions)
                raise QgsProcessingException(
                    "COMUNE NON VALIDO: il valore digitato non corrisponde esattamente a un Comune valido. "
                    f"Valore inserito: '{comune_name}'. "
                    f"Valori validi che iniziano allo stesso modo: {suggestions_text}. "
                    "Selezionare un Comune dal menu di completamento automatico."
                )

            raise QgsProcessingException(
                "COMUNE NON VALIDO: il valore digitato non è presente tra i Comuni lombardi del layer "
                f"'{COMUNI_REQUIRED_LAYER_NAME}'. Valore inserito: '{comune_name}'. "
                "Selezionare un Comune valido dal menu di completamento automatico."
            )

        canonical_name = valid_names[requested_norm]
        self._msg(feedback, f"[QC COMUNE OK] Comune validato: {canonical_name}")

        return canonical_name, municipality_field, region_code_field, region_name_field

    # -------------------------------------------------------------------------
    # CAMPI
    # -------------------------------------------------------------------------

    def _resolve_exact_or_case_field(self, layer, configured_field, label):
        names = [field.name() for field in layer.fields()]

        if configured_field in names:
            return configured_field

        lower_map = {name.lower(): name for name in names}

        if configured_field.lower() in lower_map:
            return lower_map[configured_field.lower()]

        raise QgsProcessingException(
            f"QC-4 FAIL: campo {label} '{configured_field}' non trovato nel layer '{layer.name()}'.\n"
            f"Campi disponibili: {names}"
        )

    def _resolve_first_available_field(self, layer, candidates, label):
        field = _first_available_field(layer, candidates)

        if field:
            return field

        names = [field.name() for field in layer.fields()]

        raise QgsProcessingException(
            f"MANCA_DATO: impossibile identificare il campo {label} nel layer '{layer.name()}'.\n"
            f"Campi verificati: {candidates}\n"
            f"Campi disponibili: {names}"
        )

    def _find_optional_field(self, layer, candidates):
        return _first_available_field(layer, candidates)

    # -------------------------------------------------------------------------
    # MAIN
    # -------------------------------------------------------------------------

    def processAlgorithm(self, parameters, context, feedback):

        try:
            from qgis.analysis import QgsNativeAlgorithms

            if not any(
                provider.id() == "native"
                for provider in QgsApplication.processingRegistry().providers()
            ):
                QgsApplication.processingRegistry().addProvider(QgsNativeAlgorithms())
        except Exception:
            pass

        comune_name_input = self.parameterAsString(parameters, self.COMUNE_NAME, context).strip()
        sliver_min_area_m2 = self.parameterAsDouble(parameters, self.SLIVER_MIN_AREA_M2, context)

        if not comune_name_input:
            raise QgsProcessingException(
                "COMUNE NON VALIDO: il campo Comune è vuoto. "
                "Digitare e selezionare un Comune valido dal menu di completamento automatico."
            )

        if sliver_min_area_m2 < 0:
            raise QgsProcessingException("L'area minima degli slivers non può essere negativa.")

        project_dir = self._project_dir()

        # === RISOLUZIONE FONTE COMUNI ==========================================
        # Priorità: layer caricato nel progetto (back-compat); altrimenti fetch
        # REST del singolo Comune dal servizio Regione Lombardia
        # Ambiti_Amministrativi (memory layer in EPSG:32632 con 1 feature).
        comuni = self._get_required_comuni_layer(
            comune_name=comune_name_input,
            feedback=feedback,
        )

        comune_name, municipality_field, region_code_field, region_name_field = self._validate_comune_name_on_layer(
            comuni,
            comune_name_input,
            feedback,
        )

        safe_comune = self._safe_name(comune_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        output_dir = os.path.join(project_dir, f"output_dusaf7_{safe_comune}")
        os.makedirs(output_dir, exist_ok=True)

        style_dir = os.path.join(project_dir, STYLE_FOLDER_NAME)
        plugin_style_dir = os.path.join(PLUGIN_DIR, STYLE_FOLDER_NAME)

        gpkg_path = os.path.join(output_dir, f"{safe_comune}_dusaf7_{timestamp}.gpkg")
        csv_path = os.path.join(output_dir, f"{safe_comune}_dusaf7_superfici_{timestamp}.csv")

        feedback.setProgress(0)

        self._msg(feedback, "======================================================")
        self._msg(feedback, "AVVIO - Analisi DUSAF 7 per Comune Lombardo")
        self._msg(feedback, "======================================================")
        self._msg(feedback, f"[OK] Comune richiesto: {comune_name}")
        self._msg(feedback, f"[OK] Layer Comuni: {comuni.name()}")
        self._msg(feedback, f"[OK] Cartella progetto QGIS: {project_dir}")
        self._msg(feedback, f"[OK] Cartella output: {output_dir}")
        self._msg(feedback, f"[OK] Cartella stili progetto: {style_dir}")
        self._msg(feedback, f"[OK] Cartella stili plugin: {plugin_style_dir}")
        self._msg(feedback, f"[OK] CRS operativo: {target_crs().authid()}")
        self._msg(feedback, f"[OK] Campo codice DUSAF fisso: {DUSAF_CLASS_FIELD}")
        self._msg(feedback, f"[OK] Campo descrizione DUSAF fisso: {DUSAF_DESC_FIELD}")
        self._msg(feedback, f"[OK] Soglia slivers: <= {sliver_min_area_m2} m²")
        self._msg(feedback, f"[OK] Campo nome Comune: {municipality_field}")

        if not os.path.isdir(style_dir) and not os.path.isdir(plugin_style_dir):
            self._warn(
                feedback,
                f"[STYLE WARNING] Cartella stili non trovata né nel progetto ({style_dir}) "
                f"né nel plugin ({plugin_style_dir}). "
                "Gli output saranno caricati senza simbologia QML personalizzata."
            )

        total, invalid, empty = qc.count_invalid_geometries(comuni)
        self._msg(
            feedback,
            f"[QC INPUT] {comuni.name()} | feature: {total} | invalid: {invalid} | empty: {empty}",
        )

        feedback.setProgress(5)

        self._msg(feedback, "------------------------------------------------------")
        self._msg(feedback, "FASE 1 - Fix geometries Comuni")
        self._msg(feedback, "------------------------------------------------------")

        comuni_fix_pre = pipeline.fix_geometries(comuni, context, feedback, "Comuni_fix_pre")

        feedback.setProgress(12)

        self._msg(feedback, "------------------------------------------------------")
        self._msg(feedback, "FASE 2 - Riproiezione Comuni in EPSG:32632")
        self._msg(feedback, "------------------------------------------------------")

        comuni_32632 = pipeline.reproject(comuni_fix_pre, target_crs(), context, feedback, "Comuni_EPSG32632")
        comuni_32632_fix = pipeline.fix_geometries(comuni_32632, context, feedback, "Comuni_EPSG32632_fix")

        feedback.setProgress(20)

        self._msg(feedback, "------------------------------------------------------")
        self._msg(feedback, "FASE 3 - Estrazione Comune")
        self._msg(feedback, "------------------------------------------------------")

        comune_expr = (
            f"upper(trim(to_string({self._qfield(municipality_field)}))) = "
            f"upper(trim({self._qstr(comune_name)}))"
        )

        if region_code_field:
            comune_expr += f" AND to_int({self._qfield(region_code_field)}) = 3"
            self._msg(feedback, f"[OK] Filtro Regione Lombardia tramite campo: {region_code_field}")
        elif region_name_field:
            comune_expr += (
                f" AND upper(trim(to_string({self._qfield(region_name_field)}))) = 'LOMBARDIA'"
            )
            self._msg(feedback, f"[OK] Filtro Regione Lombardia tramite campo: {region_name_field}")
        else:
            self._warn(
                feedback,
                "[WARN] Nessun campo regione trovato. "
                "Il filtro userà solo il nome del Comune; verificare eventuali omonimie fuori Lombardia.",
            )

        comune_extract = pipeline.extract_by_expression(
            comuni_32632_fix,
            comune_expr,
            context,
            feedback,
            f"Confine_{safe_comune}_extract",
        )

        if comune_extract.featureCount() == 0:
            raise QgsProcessingException(
                f"MANCA_DATO: nessun Comune trovato con espressione:\n{comune_expr}"
            )

        if comune_extract.featureCount() > 1:
            self._warn(
                feedback,
                f"[WARN] Estratte {comune_extract.featureCount()} feature per '{comune_name}'. "
                "Verranno dissolte in un unico perimetro. Verificare eventuali omonimie.",
            )

        comune_diss = pipeline.dissolve_all(
            comune_extract,
            context,
            feedback,
            f"Confine_{safe_comune}_dissolve",
        )

        comune_fix = pipeline.fix_geometries(
            comune_diss,
            context,
            feedback,
            f"Confine_{safe_comune}_fix",
        )

        boundary_area_m2 = qc.total_area_m2(comune_fix)
        boundary_area_ha = boundary_area_m2 / 10000.0

        self._msg(feedback, f"[QC] Superficie perimetro comunale originale: {boundary_area_ha:.6f} ha")

        if boundary_area_m2 <= 0:
            raise QgsProcessingException("QC FAIL: superficie del perimetro comunale nulla o non valida.")

        feedback.setProgress(40)

        # === RISOLUZIONE FONTE DUSAF ===========================================
        # Priorità: layer caricato nel progetto (back-compat); altrimenti fetch
        # REST dal servizio Regione Lombardia dusaf7 limitato all'envelope del
        # confine comunale appena calcolato (riduce traffico e tempo).
        self._msg(feedback, "------------------------------------------------------")
        self._msg(feedback, "FASE 4 - Preparazione DUSAF (risoluzione, fix, reproject)")
        self._msg(feedback, "------------------------------------------------------")

        dusaf = self._get_required_dusaf_layer(
            comune_geometry_layer=comune_fix,
            feedback=feedback,
        )

        if dusaf.source() == comuni.source():
            raise QgsProcessingException(
                "ERRORE DI CONFIGURAZIONE: il layer DUSAF e il layer Comuni risultano "
                "identici. Caricare due layer distinti o lasciare che il plugin li "
                "scarichi da REST."
            )

        class_field = self._resolve_exact_or_case_field(
            dusaf,
            DUSAF_CLASS_FIELD,
            "codice DUSAF",
        )

        desc_field = self._resolve_exact_or_case_field(
            dusaf,
            DUSAF_DESC_FIELD,
            "descrizione DUSAF",
        )

        total, invalid, empty = qc.count_invalid_geometries(dusaf)
        self._msg(
            feedback,
            f"[QC INPUT] {dusaf.name()} | feature: {total} | invalid: {invalid} | empty: {empty}",
        )

        null_codes = 0
        for feat in dusaf.getFeatures():
            value = feat[class_field]
            if value is None or str(value).strip() == "":
                null_codes += 1

        if null_codes > 0:
            raise QgsProcessingException(
                f"QC-4 FAIL: presenti {null_codes} feature DUSAF con codice '{class_field}' nullo o vuoto."
            )

        self._msg(feedback, "[QC-4 OK] Nessun codice DUSAF nullo o vuoto rilevato.")

        dusaf_fix_pre = pipeline.fix_geometries(dusaf, context, feedback, "DUSAF7_fix_pre")
        dusaf_32632 = pipeline.reproject(dusaf_fix_pre, target_crs(), context, feedback, "DUSAF7_EPSG32632")
        dusaf_32632_fix = pipeline.fix_geometries(dusaf_32632, context, feedback, "DUSAF7_EPSG32632_fix")

        feedback.setProgress(50)

        self._msg(feedback, "------------------------------------------------------")
        self._msg(feedback, "FASE 4b - Clip DUSAF sul Comune")
        self._msg(feedback, "------------------------------------------------------")

        dusaf_clip = pipeline.clip(
            dusaf_32632_fix,
            comune_fix,
            context,
            feedback,
            f"DUSAF7_clip_{safe_comune}",
        )

        if dusaf_clip.featureCount() == 0:
            raise QgsProcessingException(
                "QC FAIL: il clip DUSAF sul Comune non ha prodotto feature. "
                "Verificare CRS, sovrapposizione geografica e layer di input."
            )

        dusaf_clip_fix = pipeline.fix_geometries(
            dusaf_clip,
            context,
            feedback,
            f"DUSAF7_clip_{safe_comune}_fix",
        )

        dusaf_clip_single = pipeline.multipart_to_singleparts(
            dusaf_clip_fix,
            context,
            feedback,
            f"DUSAF7_clip_{safe_comune}_singlepart",
        )

        dusaf_clip_single_fix = pipeline.fix_geometries(
            dusaf_clip_single,
            context,
            feedback,
            f"DUSAF7_clip_{safe_comune}_singlepart_fix",
        )

        feedback.setProgress(60)

        self._msg(feedback, "------------------------------------------------------")
        self._msg(feedback, "FASE 5 - Calcolo aree preliminari e slivers")
        self._msg(feedback, "------------------------------------------------------")

        dusaf_clip_qc = qc.add_area_fields(
            dusaf_clip_single_fix,
            sliver_min_area_m2=sliver_min_area_m2,
            include_sliver=True,
            include_percentages=False,
        )

        sliver_count = 0
        sliver_area_m2 = 0.0

        for feat in dusaf_clip_qc.getFeatures():
            if int(feat["sliver"]) == 1:
                sliver_count += 1
                sliver_area_m2 += float(feat["area_m2"])

        self._msg(
            feedback,
            f"[QC SLIVERS] soglia <= {sliver_min_area_m2} m² | "
            f"feature sliver: {sliver_count} | area sliver: {sliver_area_m2:.6f} m²",
        )

        slivers_layer = pipeline.extract_by_expression(
            dusaf_clip_qc,
            '"sliver" = 1',
            context,
            feedback,
            f"QC_slivers_{safe_comune}",
        )

        feedback.setProgress(70)

        self._msg(feedback, "------------------------------------------------------")
        self._msg(feedback, "FASE 6 - Dissolve per classe DUSAF")
        self._msg(feedback, "------------------------------------------------------")

        dusaf_diss = pipeline.dissolve_by_fields(
            dusaf_clip_qc,
            [class_field, desc_field],
            context,
            feedback,
            f"DUSAF7_{safe_comune}_dissolve_by_class",
        )

        dusaf_diss_fix = pipeline.fix_geometries(
            dusaf_diss,
            context,
            feedback,
            f"DUSAF7_{safe_comune}_dissolve_by_class_fix",
        )

        total_dusaf_m2 = qc.total_area_m2(dusaf_diss_fix)
        total_dusaf_ha = total_dusaf_m2 / 10000.0

        dusaf_final = qc.add_area_fields(
            dusaf_diss_fix,
            total_dusaf_m2=total_dusaf_m2,
            boundary_area_m2=boundary_area_m2,
            include_sliver=False,
            include_percentages=True,
        )

        feedback.setProgress(82)

        self._msg(feedback, "------------------------------------------------------")
        self._msg(feedback, "FASE 7 - Data Audit QC-4")
        self._msg(feedback, "------------------------------------------------------")

        sum_area_ha = 0.0
        sum_pct_dusaf = 0.0
        sum_pct_comune = 0.0

        for feat in dusaf_final.getFeatures():
            sum_area_ha += float(feat["area_ha"])
            sum_pct_dusaf += float(feat["pct_dusaf"])
            sum_pct_comune += float(feat["pct_comune"])

        diff_m2 = total_dusaf_m2 - boundary_area_m2
        diff_ha = diff_m2 / 10000.0
        diff_pct = (abs(diff_m2) / boundary_area_m2 * 100.0) if boundary_area_m2 else 0.0

        self._msg(feedback, "========== DATA AUDIT FINALE ==========")
        self._msg(feedback, f"[QC] Superficie perimetro originale:     {boundary_area_ha:.6f} ha")
        self._msg(feedback, f"[QC] Superficie DUSAF calcolata:        {total_dusaf_ha:.6f} ha")
        self._msg(feedback, f"[QC] Somma area_ha attributi:           {sum_area_ha:.6f} ha")
        self._msg(feedback, f"[QC] Somma pct_dusaf:                   {sum_pct_dusaf:.6f} %")
        self._msg(feedback, f"[QC] Somma pct_comune:                  {sum_pct_comune:.6f} %")
        self._msg(feedback, f"[QC] Differenza DUSAF - perimetro:      {diff_ha:.6f} ha")
        self._msg(feedback, f"[QC] Scostamento relativo:              {diff_pct:.6f} %")

        if abs(diff_m2) > AUDIT_TOLERANCE_M2:
            self._warn(
                feedback,
                "[DATA AUDIT WARNING] La superficie DUSAF calcolata differisce "
                "dalla superficie del perimetro comunale originale. "
                f"Differenza: {diff_ha:.6f} ha ({diff_pct:.6f}%). "
                "Verificare confine ISTAT, CRS, geometrie, copertura DUSAF e slivers.",
            )
        else:
            self._msg(
                feedback,
                f"[DATA AUDIT OK] Differenza entro tolleranza di {AUDIT_TOLERANCE_M2} m².",
            )

        if abs(sum_pct_dusaf - 100.0) > 0.0001:
            self._warn(
                feedback,
                f"[QC-4 WARNING] La somma pct_dusaf non è esattamente 100%. "
                f"Valore: {sum_pct_dusaf:.6f}%",
            )
        else:
            self._msg(feedback, "[QC-4 OK] Somma pct_dusaf coerente con 100%.")

        feedback.setProgress(90)

        self._msg(feedback, "------------------------------------------------------")
        self._msg(feedback, "FASE 8 - Salvataggio output")
        self._msg(feedback, "------------------------------------------------------")

        output.save_layer_to_gpkg(
            dusaf_final,
            gpkg_path,
            f"dusaf7_{safe_comune}_superfici",
            overwrite_file=True,
            context=context,
            feedback=feedback,
        )

        output.save_layer_to_gpkg(
            dusaf_clip_qc,
            gpkg_path,
            f"dusaf7_{safe_comune}_clip_qc",
            overwrite_file=False,
            context=context,
            feedback=feedback,
        )

        output.save_layer_to_gpkg(
            comune_fix,
            gpkg_path,
            f"confine_{safe_comune}_fix",
            overwrite_file=False,
            context=context,
            feedback=feedback,
        )

        if slivers_layer.featureCount() > 0:
            output.save_layer_to_gpkg(
                slivers_layer,
                gpkg_path,
                f"qc_slivers_{safe_comune}",
                overwrite_file=False,
                context=context,
                feedback=feedback,
            )

        output.export_summary_csv(
            dusaf_final,
            class_field,
            desc_field,
            csv_path,
            feedback,
        )

        feedback.setProgress(96)

        self._msg(feedback, "------------------------------------------------------")
        self._msg(feedback, "FASE 9 - Caricamento output nel progetto e stili QML")
        self._msg(feedback, "------------------------------------------------------")

        output.add_saved_layer_to_project(
            gpkg_path,
            f"dusaf7_{safe_comune}_superfici",
            f"DUSAF7 {comune_name} - superfici ha %",
            feedback,
            plugin_dir=PLUGIN_DIR,
            project_dir=project_dir,
            style_filename=STYLE_DUSAF_FINAL,
        )

        output.add_saved_layer_to_project(
            gpkg_path,
            f"dusaf7_{safe_comune}_clip_qc",
            f"DUSAF7 {comune_name} - clip QC",
            feedback,
            plugin_dir=PLUGIN_DIR,
            project_dir=project_dir,
            style_filename=STYLE_DUSAF_CLIP_QC,
        )

        output.add_saved_layer_to_project(
            gpkg_path,
            f"confine_{safe_comune}_fix",
            f"Confine {comune_name} fix",
            feedback,
            plugin_dir=PLUGIN_DIR,
            project_dir=project_dir,
            style_filename=STYLE_CONFINE,
        )

        if slivers_layer.featureCount() > 0:
            output.add_saved_layer_to_project(
                gpkg_path,
                f"qc_slivers_{safe_comune}",
                f"QC slivers DUSAF7 {comune_name}",
                feedback,
                plugin_dir=PLUGIN_DIR,
                project_dir=project_dir,
                style_filename=STYLE_SLIVERS,
            )

        feedback.setProgress(100)

        self._msg(feedback, "======================================================")
        self._msg(feedback, "WORKFLOW COMPLETATO")
        self._msg(feedback, "======================================================")
        self._msg(feedback, f"GeoPackage: {gpkg_path}")
        self._msg(feedback, f"CSV:        {csv_path}")

        return {
            self.OUTPUT_GPKG: gpkg_path,
            self.OUTPUT_CSV: csv_path,
        }