# -*- coding: utf-8 -*-

"""Single primary dialog for the DUSAF 7 Lombardia workflow.

The dialog merges what used to live in the Processing form and the custom
widget wrapper into a single Qt window that is friendlier to non-technical
users: it shows the active data sources, validates the Comune name with the
same case-insensitive logic, lets the user tweak the slivers threshold and
runs the existing Processing algorithm while streaming feedback into an
in-dialog log.

Importing this module performs no QGIS or network activity. The dialog
instantiates lazily when the user clicks the toolbar action.
"""

import os

import processing

from qgis.PyQt.QtCore import Qt, QStringListModel
from qgis.PyQt.QtGui import QFont
from qgis.PyQt.QtWidgets import (
    QApplication,
    QCheckBox,
    QCompleter,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
)

from qgis.core import (
    QgsProcessingFeedback,
    QgsProject,
    QgsVectorLayer,
)

from ..compat import runtime_summary
from ..data_sources import normalize_comune_display_name
from ..workflow.data_resolver import get_comuni_list_for_autocomplete


ALGORITHM_ID = "Analisi DUSAF 7:analisi_dusaf7_comune_lombardo"

STATUS_OK_STYLE = "color:#006100; background-color:#eefbea; padding:4px; border:1px solid #3caa3c;"
STATUS_INFO_STYLE = "color:#1a4170; background-color:#e8f1fb; padding:4px; border:1px solid #5c8fce;"
STATUS_WARN_STYLE = "color:#7a4a00; background-color:#fff5e1; padding:4px; border:1px solid #e0a040;"
STATUS_ERROR_STYLE = "color:#7a0000; background-color:#fdecec; padding:4px; border:1px solid #cc3030;"


def _normalize_comune_key(value):
    """Reduce ``value`` to a comparable lowercase form (no whitespace runs)."""
    if value is None:
        return ""
    text = " ".join(str(value).split())
    return text.casefold()


def _has_field(layer, candidates):
    """Return True when ``layer`` exposes at least one of ``candidates``."""
    if layer is None or not layer.isValid():
        return False
    names = {f.name() for f in layer.fields()}
    lower = {n.lower() for n in names}
    for cand in candidates:
        if cand in names or cand.lower() in lower:
            return True
    return False


def _find_project_layer(name_candidates, required_fields):
    """Look for a vector layer in the active project matching name OR fields.

    The check mirrors the algorithm's own detection heuristics so the dialog
    status reflects what the workflow would actually use.
    """
    project = QgsProject.instance()
    lowered_names = {n.lower() for n in name_candidates}

    for layer in project.mapLayers().values():
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            continue

        layer_name = layer.name().lower()
        source_stem = os.path.splitext(
            os.path.basename(layer.source().split("|")[0])
        )[0].lower()

        name_match = any(
            cand.lower() == layer_name
            or cand.lower() == source_stem
            or cand.lower() in layer_name
            for cand in name_candidates
        )

        if name_match and _has_field(layer, required_fields):
            return layer

        if not required_fields and name_match:
            return layer

    if not lowered_names:
        return None

    return None


class _DialogFeedback(QgsProcessingFeedback):
    """QgsProcessingFeedback that mirrors messages into UI widgets."""

    def __init__(self, log_widget, progress_bar):
        super().__init__()
        self._log = log_widget
        self._progress_bar = progress_bar
        self._cancelled = False
        self.progressChanged.connect(self._on_progress)

    def _on_progress(self, value):
        try:
            self._progress_bar.setValue(int(value))
        except Exception:
            pass

    def _append(self, prefix, message):
        try:
            text = "" if message is None else str(message)
            line = (prefix + " " + text).strip()
            self._log.appendPlainText(line)
        except Exception:
            pass
        QApplication.processEvents()

    def pushInfo(self, info):
        super().pushInfo(info)
        self._append("", info)

    def pushCommandInfo(self, info):
        super().pushCommandInfo(info)
        self._append(">", info)

    def pushDebugInfo(self, info):
        super().pushDebugInfo(info)
        self._append("·", info)

    def pushConsoleInfo(self, info):
        super().pushConsoleInfo(info)
        self._append("·", info)

    def pushWarning(self, warning):
        super().pushWarning(warning)
        self._append("[WARN]", warning)

    def reportError(self, error, fatalError=False):
        super().reportError(error, fatalError)
        self._append("[ERROR]", error)

    def setProgressText(self, text):
        super().setProgressText(text)
        self._append("…", text)

    def cancel(self):
        self._cancelled = True
        super().cancel()

    def isCanceled(self):
        return self._cancelled or super().isCanceled()


class DusafMainDialog(QDialog):
    """Single dialog that drives the DUSAF 7 Lombardia workflow."""

    DUSAF_LAYER_CANDIDATES = ["DUSAF7", "DUSAF 7", "DUSAF_7", "DUSAF7_RL"]
    DUSAF_REQUIRED_FIELDS = ["COD_TOT", "DESCR"]
    COMUNI_LAYER_CANDIDATES = [
        "Com01012026_WGS84",
        "Com01012026",
        "Comuni_ISTAT_2026",
        "Comuni ISTAT 2026",
        "Comuni",
    ]
    COMUNI_REQUIRED_FIELDS = []

    def __init__(self, iface, parent=None):
        super().__init__(parent or (iface.mainWindow() if iface else None))
        self.iface = iface

        self._valid_names = []
        self._valid_name_by_key = {}
        self._comuni_metadata_by_key = {}
        self._comuni_source_origin = None
        self._comuni_source_count = 0
        self._feedback = None

        self.setWindowTitle("Analisi DUSAF 7 - Comuni Lombardia")
        self.setMinimumSize(720, 640)

        self._build_ui()
        self._refresh_data_status()
        self._populate_comune_autocomplete()
        self._update_run_state()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        header = QLabel("<h2 style='margin:0;'>Analisi uso del suolo DUSAF 7</h2>")
        header.setTextFormat(Qt.RichText)
        root.addWidget(header)

        subtitle = QLabel(
            "Calcolo automatico delle superfici per classe d'uso del suolo, "
            "ritagliate sul perimetro di un Comune lombardo. I dati vengono "
            "scaricati al volo dai servizi REST di Regione Lombardia se non "
            "sono già caricati nel progetto."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color:#555;")
        root.addWidget(subtitle)

        # === Stato dati ===
        status_box = QGroupBox("Stato dati")
        status_layout = QVBoxLayout(status_box)

        self._comuni_status_label = QLabel("...")
        self._comuni_status_label.setWordWrap(True)
        self._comuni_status_label.setTextFormat(Qt.RichText)
        status_layout.addWidget(self._comuni_status_label)

        comuni_actions = QHBoxLayout()
        self._refresh_comuni_btn = QPushButton("Aggiorna cache lista Comuni")
        self._refresh_comuni_btn.setToolTip(
            "Forza il download della lista 1500+ Comuni dal servizio REST RL "
            "ignorando la cache locale."
        )
        self._refresh_comuni_btn.clicked.connect(self._on_refresh_comuni_clicked)
        comuni_actions.addWidget(self._refresh_comuni_btn)

        self._istat_btn = QPushButton("Usa ISTAT ufficiale (download)")
        self._istat_btn.setToolTip(
            "Funzione opzionale: scaricare lo ZIP ufficiale ISTAT 2026 dei "
            "confini comunali e usarlo come fonte autoritativa al posto del "
            "REST RL. Implementazione completa in arrivo nella prossima "
            "iterazione."
        )
        self._istat_btn.setEnabled(False)
        comuni_actions.addWidget(self._istat_btn)
        comuni_actions.addStretch(1)
        status_layout.addLayout(comuni_actions)

        self._dusaf_status_label = QLabel("...")
        self._dusaf_status_label.setWordWrap(True)
        self._dusaf_status_label.setTextFormat(Qt.RichText)
        status_layout.addWidget(self._dusaf_status_label)

        root.addWidget(status_box)

        # === Selezione Comune ===
        comune_box = QGroupBox("Comune da analizzare")
        comune_layout = QVBoxLayout(comune_box)

        self._comune_input = QLineEdit()
        self._comune_input.setPlaceholderText(
            "Digita il nome del Comune lombardo e seleziona un valore dal menu..."
        )
        self._comune_completer_model = QStringListModel([], self)
        self._comune_completer = QCompleter(self._comune_completer_model, self._comune_input)
        self._comune_completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._comune_completer.setFilterMode(Qt.MatchContains)
        self._comune_completer.setCompletionMode(QCompleter.PopupCompletion)
        self._comune_input.setCompleter(self._comune_completer)
        self._comune_input.textChanged.connect(self._on_comune_text_changed)
        comune_layout.addWidget(self._comune_input)

        self._comune_validation_label = QLabel("...")
        self._comune_validation_label.setWordWrap(True)
        self._comune_validation_label.setTextFormat(Qt.RichText)
        comune_layout.addWidget(self._comune_validation_label)

        root.addWidget(comune_box)

        # === Parametri ===
        params_box = QGroupBox("Parametri")
        params_layout = QFormLayout(params_box)

        self._sliver_spin = QDoubleSpinBox()
        self._sliver_spin.setRange(0.0, 1.0e6)
        self._sliver_spin.setDecimals(3)
        self._sliver_spin.setValue(1.0)
        self._sliver_spin.setSuffix(" m²")
        self._sliver_spin.setToolTip(
            "Soglia per segnalare frammenti residui di clip come sliver "
            "(area_m2 <= soglia)."
        )
        params_layout.addRow("Area minima slivers:", self._sliver_spin)

        self._load_into_project_chk = QCheckBox(
            "Carica i 4 layer di output nel progetto QGIS al termine"
        )
        self._load_into_project_chk.setChecked(True)
        params_layout.addRow("", self._load_into_project_chk)

        root.addWidget(params_box)

        # === Esecuzione: log + progress ===
        run_box = QGroupBox("Esecuzione")
        run_layout = QVBoxLayout(run_box)

        self._log_widget = QPlainTextEdit()
        self._log_widget.setReadOnly(True)
        self._log_widget.setMaximumBlockCount(2000)
        log_font = QFont("Consolas")
        log_font.setStyleHint(QFont.Monospace)
        log_font.setPointSize(9)
        self._log_widget.setFont(log_font)
        self._log_widget.setPlaceholderText(
            "Il log dell'esecuzione apparirà qui dopo aver premuto Esegui."
        )
        run_layout.addWidget(self._log_widget)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        run_layout.addWidget(self._progress_bar)

        runtime_lbl = QLabel(runtime_summary())
        runtime_lbl.setStyleSheet("color:#888; font-size:90%;")
        run_layout.addWidget(runtime_lbl)

        root.addWidget(run_box, stretch=1)

        # === Pulsanti ===
        buttons = QHBoxLayout()
        self._cancel_btn = QPushButton("Annulla esecuzione")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        buttons.addWidget(self._cancel_btn)

        buttons.addSpacerItem(
            QSpacerItem(40, 1, QSizePolicy.Expanding, QSizePolicy.Minimum)
        )

        self._close_btn = QPushButton("Chiudi")
        self._close_btn.clicked.connect(self.reject)
        buttons.addWidget(self._close_btn)

        self._run_btn = QPushButton("Esegui")
        self._run_btn.setDefault(True)
        self._run_btn.clicked.connect(self._on_run_clicked)
        buttons.addWidget(self._run_btn)

        root.addLayout(buttons)

    # ------------------------------------------------------------------
    # Data status
    # ------------------------------------------------------------------

    def _refresh_data_status(self):
        comuni_layer = _find_project_layer(
            self.COMUNI_LAYER_CANDIDATES, self.COMUNI_REQUIRED_FIELDS
        )
        if comuni_layer is not None:
            self._set_label(
                self._comuni_status_label,
                "<b>Confini comunali</b>: layer di progetto "
                f"<i>{comuni_layer.name()}</i>",
                STATUS_OK_STYLE,
            )
        else:
            self._set_label(
                self._comuni_status_label,
                "<b>Confini comunali</b>: nessun layer nel progetto. "
                "Verrà scaricato il singolo Comune selezionato dal servizio "
                "REST Regione Lombardia (~10 KB per Comune). "
                "Lista per autocomplete cacheata in profilo QGIS (TTL 30gg).",
                STATUS_INFO_STYLE,
            )

        dusaf_layer = _find_project_layer(
            self.DUSAF_LAYER_CANDIDATES, self.DUSAF_REQUIRED_FIELDS
        )
        if dusaf_layer is not None:
            self._set_label(
                self._dusaf_status_label,
                "<b>DUSAF 7</b>: layer di progetto "
                f"<i>{dusaf_layer.name()}</i> (precisione massima LIV5)",
                STATUS_OK_STYLE,
            )
        else:
            self._set_label(
                self._dusaf_status_label,
                "<b>DUSAF 7</b>: nessun layer nel progetto. "
                "Verrà scaricato dal servizio REST Regione Lombardia "
                "limitato al bounding box del Comune selezionato.",
                STATUS_INFO_STYLE,
            )

    @staticmethod
    def _set_label(label, html, style):
        label.setStyleSheet(f"QLabel {{ {style} }}")
        label.setText(html)

    # ------------------------------------------------------------------
    # Autocomplete Comuni
    # ------------------------------------------------------------------

    def _populate_comune_autocomplete(self, force_refresh=False):
        """Build the autocomplete list either from a project Comuni layer or
        from the cached/REST RL list."""
        try:
            comuni_layer = _find_project_layer(
                self.COMUNI_LAYER_CANDIDATES, self.COMUNI_REQUIRED_FIELDS
            )
            if comuni_layer is not None and not force_refresh:
                self._populate_from_project_layer(comuni_layer)
            else:
                self._populate_from_rest(force_refresh=force_refresh)
        except Exception as exc:
            self._valid_names = []
            self._valid_name_by_key = {}
            self._comuni_metadata_by_key = {}
            self._comune_completer_model.setStringList([])
            self._set_label(
                self._comune_validation_label,
                f"<b>Errore caricamento Comuni</b>: {exc}",
                STATUS_ERROR_STYLE,
            )

        self._on_comune_text_changed(self._comune_input.text())

    def _populate_from_project_layer(self, layer):
        from ..analisi_dusaf7_comune_lombardo_algorithm import (
            MUNICIPALITY_FIELD_CANDIDATES,
            REGION_CODE_FIELD_CANDIDATES,
            REGION_NAME_FIELD_CANDIDATES,
            _first_available_field,
        )
        from qgis.core import QgsFeatureRequest

        municipality_field = _first_available_field(layer, MUNICIPALITY_FIELD_CANDIDATES)
        if not municipality_field:
            raise ValueError(
                f"Il layer '{layer.name()}' non contiene un campo nome Comune "
                "riconosciuto."
            )

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

        names = []
        metadata = {}

        for feat in layer.getFeatures(request):
            if region_code_field:
                region_code = feat[region_code_field]
                region_code_text = (
                    "" if region_code is None else str(region_code).strip()
                )
                try:
                    if int(region_code_text) != 3:
                        continue
                except Exception:
                    if region_code_text not in ("3", "03", "003"):
                        continue
            elif region_name_field:
                region_name = feat[region_name_field]
                region_name_text = (
                    "" if region_name is None else str(region_name).strip().upper()
                )
                if region_name_text != "LOMBARDIA":
                    continue

            value = feat[municipality_field]
            if value is None:
                continue
            comune = str(value).strip()
            if not comune:
                continue
            names.append(comune)
            metadata[_normalize_comune_key(comune)] = {"NOME_COM": comune}

        names_sorted = sorted(set(names), key=lambda x: x.lower())
        self._valid_names = names_sorted
        self._valid_name_by_key = {_normalize_comune_key(n): n for n in names_sorted}
        self._comuni_metadata_by_key = metadata
        self._comune_completer_model.setStringList(names_sorted)

    def _populate_from_rest(self, force_refresh=False):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            comuni, source = get_comuni_list_for_autocomplete(force_refresh=force_refresh)
        finally:
            QApplication.restoreOverrideCursor()

        names = []
        metadata = {}
        for entry in comuni:
            raw = entry.get("NOME_COM", "")
            if not raw:
                continue
            display = normalize_comune_display_name(raw)
            names.append(display)
            metadata[_normalize_comune_key(display)] = {
                "NOME_COM": display,
                "ISTAT": entry.get("ISTAT"),
                "NOME_PRO": entry.get("NOME_PRO"),
                "SIG_PRO": entry.get("SIG_PRO"),
                "RAW_NOME_COM": raw,
            }

        names_sorted = sorted(set(names), key=lambda x: x.lower())
        self._valid_names = names_sorted
        self._valid_name_by_key = {_normalize_comune_key(n): n for n in names_sorted}
        self._comuni_metadata_by_key = metadata
        self._comune_completer_model.setStringList(names_sorted)

        origin = "cache locale" if source == "cache" else "servizio REST Regione Lombardia"
        self._comuni_source_origin = origin
        self._comuni_source_count = len(names_sorted)

    # ------------------------------------------------------------------
    # Comune validation
    # ------------------------------------------------------------------

    def _on_comune_text_changed(self, text):
        text = "" if text is None else str(text).strip()
        key = _normalize_comune_key(text)

        if not self._valid_names:
            self._set_label(
                self._comune_validation_label,
                "<b>Lista Comuni non disponibile.</b> Premere "
                "<i>Aggiorna cache</i> per ritentare.",
                STATUS_ERROR_STYLE,
            )
            self._update_run_state(False)
            return

        if not text:
            if self._comuni_source_origin:
                hint = (
                    f"Lista caricata ({self._comuni_source_count} Comuni, "
                    f"origine: {self._comuni_source_origin}). Digitare il nome "
                    "di un Comune lombardo e selezionarlo dal menu."
                )
            else:
                hint = "Digitare il nome di un Comune lombardo e selezionarlo dal menu."
            self._set_label(
                self._comune_validation_label,
                hint,
                STATUS_WARN_STYLE,
            )
            self._update_run_state(False)
            return

        if key in self._valid_name_by_key:
            canonical = self._valid_name_by_key[key]
            meta = self._comuni_metadata_by_key.get(key, {})
            province_sig = meta.get("SIG_PRO")
            extra = f" ({province_sig})" if province_sig else ""
            self._set_label(
                self._comune_validation_label,
                f"<b>Comune valido</b>: {canonical}{extra}. Pronto per l'esecuzione.",
                STATUS_OK_STYLE,
            )
            self._update_run_state(True)
            return

        suggestions = [n for n in self._valid_names if _normalize_comune_key(n).startswith(key)][:6]
        if suggestions:
            sug = ", ".join(f"<b>{s}</b>" for s in suggestions)
            self._set_label(
                self._comune_validation_label,
                f"Selezionare uno dei valori proposti, ad esempio: {sug}",
                STATUS_WARN_STYLE,
            )
        else:
            self._set_label(
                self._comune_validation_label,
                "Comune non valido. Il valore digitato non corrisponde a "
                "nessun Comune lombardo della sorgente attiva.",
                STATUS_ERROR_STYLE,
            )
        self._update_run_state(False)

    # ------------------------------------------------------------------
    # Run control
    # ------------------------------------------------------------------

    def _update_run_state(self, comune_ok=None):
        if comune_ok is None:
            comune_ok = _normalize_comune_key(self._comune_input.text()) in self._valid_name_by_key
        self._run_btn.setEnabled(comune_ok)

    def _on_refresh_comuni_clicked(self):
        self._log_widget.appendPlainText("[INFO] Forzato refresh della lista Comuni...")
        self._populate_comune_autocomplete(force_refresh=True)

    def _on_run_clicked(self):
        key = _normalize_comune_key(self._comune_input.text())
        if key not in self._valid_name_by_key:
            QMessageBox.warning(
                self,
                "Comune non valido",
                "Selezionare un Comune valido dal menu prima di eseguire.",
            )
            return

        canonical = self._valid_name_by_key[key]
        sliver_threshold = float(self._sliver_spin.value())
        load_into_project = self._load_into_project_chk.isChecked()

        self._log_widget.clear()
        self._progress_bar.setValue(0)
        self._set_running_ui(True)

        feedback = _DialogFeedback(self._log_widget, self._progress_bar)
        self._feedback = feedback

        params = {
            "COMUNE_NAME": canonical,
            "SLIVER_MIN_AREA_M2": sliver_threshold,
        }

        # Snapshot the project layer ids BEFORE the run so the optional
        # cleanup at the end can only ever touch the layers we just added.
        # This protects user-loaded layers and outputs from previous runs.
        pre_run_layer_ids = set(QgsProject.instance().mapLayers().keys())

        try:
            self._log_widget.appendPlainText(
                f"[INFO] Avvio algoritmo su {canonical} con soglia slivers={sliver_threshold} m²."
            )
            QApplication.processEvents()

            result = processing.run(
                ALGORITHM_ID,
                params,
                feedback=feedback,
            )

            gpkg = result.get("OUTPUT_GPKG", "")
            csv = result.get("OUTPUT_CSV", "")
            self._log_widget.appendPlainText("")
            self._log_widget.appendPlainText("[OK] Esecuzione completata.")
            self._log_widget.appendPlainText(f"     GeoPackage: {gpkg}")
            self._log_widget.appendPlainText(f"     CSV:        {csv}")

            if not load_into_project:
                removed = self._cleanup_newly_added_layers(pre_run_layer_ids)
                if removed:
                    self._log_widget.appendPlainText(
                        f"[INFO] Checkbox 'carica nel progetto' disattiva: "
                        f"rimossi {removed} layer di output appena aggiunti."
                    )

            QMessageBox.information(
                self,
                "Analisi DUSAF completata",
                f"Il workflow per {canonical} è terminato con successo.\n\n"
                f"Output:\n- {gpkg}\n- {csv}",
            )
        except Exception as exc:
            self._log_widget.appendPlainText(f"[ERROR] {exc}")
            QMessageBox.critical(
                self,
                "Errore di esecuzione",
                f"L'algoritmo è fallito.\nDettaglio: {exc}",
            )
        finally:
            self._feedback = None
            self._set_running_ui(False)

    def _on_cancel_clicked(self):
        if self._feedback is not None:
            self._log_widget.appendPlainText("[INFO] Annullamento richiesto...")
            self._feedback.cancel()

    def _set_running_ui(self, running):
        self._run_btn.setEnabled(not running)
        self._close_btn.setEnabled(not running)
        self._refresh_comuni_btn.setEnabled(not running)
        self._comune_input.setEnabled(not running)
        self._sliver_spin.setEnabled(not running)
        self._load_into_project_chk.setEnabled(not running)
        self._cancel_btn.setEnabled(running)
        if running:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        else:
            QApplication.restoreOverrideCursor()
            self._update_run_state()

    def _cleanup_newly_added_layers(self, pre_run_layer_ids):
        """Remove only layers added DURING the current run.

        We compare the project layer ids before and after the algorithm runs
        and remove only those that appeared in between. This guarantees we
        never touch user-loaded layers or outputs from a previous algorithm
        run, even when they share names with the new outputs.
        """
        try:
            project = QgsProject.instance()
            current_ids = set(project.mapLayers().keys())
            new_ids = [lid for lid in current_ids if lid not in pre_run_layer_ids]
            for layer_id in new_ids:
                project.removeMapLayer(layer_id)
            return len(new_ids)
        except Exception:
            return 0
