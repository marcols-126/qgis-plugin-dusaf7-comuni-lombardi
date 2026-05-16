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
import shutil
import zipfile

import processing

from qgis.PyQt.QtCore import Qt, QSettings, QStringListModel, QUrl
from qgis.PyQt.QtGui import QDesktopServices, QFont, QTextCursor
from qgis.PyQt.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCompleter,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSlider,
    QSpinBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
    QWidget,
)

from qgis.core import (
    QgsProcessingFeedback,
    QgsProject,
    QgsVectorLayer,
)

from ..compat import (
    CASE_INSENSITIVE,
    COMPLETER_POPUP,
    CURSOR_WAIT,
    FEATURE_REQUEST_NO_GEOMETRY,
    FONT_MONOSPACE,
    FRAME_NO_FRAME,
    MATCH_CONTAINS,
    ORIENT_HORIZONTAL,
    SIZE_POLICY_EXPANDING,
    SIZE_POLICY_MINIMUM,
    TEXT_FORMAT_RICH,
    TEXTCURSOR_LINE_UNDER_CURSOR,
    runtime_summary,
)
from ..data_sources import normalize_comune_display_name
from ..workflow.data_resolver import (
    get_comuni_list_for_autocomplete,
    get_istat_cached_shapefile_path,
)


ALGORITHM_ID = "Analisi DUSAF 7:analisi_dusaf7_comune_lombardo"
README_URL = "https://github.com/marcols-126/qgis-plugin-dusaf7-comuni-lombardi#readme"

SETTINGS_PREFIX = "analisi_dusaf7_comune_lombardo"
SETTINGS_OUTPUT_MODE = f"{SETTINGS_PREFIX}/output_mode"          # "memory"|"project"|"custom"
SETTINGS_OUTPUT_DIR = f"{SETTINGS_PREFIX}/output_dir"            # last custom dir
SETTINGS_SLIVER_M2 = f"{SETTINGS_PREFIX}/sliver_min_area_m2"
SETTINGS_LOAD_INTO_PROJECT = f"{SETTINGS_PREFIX}/load_into_project"
# Opacità (%) da applicare al layer "DUSAF7 <Comune> - clip QC" appena
# uscito dal workflow. Range 0-100, default 100 (totalmente opaco).
SETTINGS_CLIP_QC_OPACITY_PCT = f"{SETTINGS_PREFIX}/clip_qc_opacity_pct"

OUTPUT_MODE_MEMORY = "memory"
OUTPUT_MODE_PROJECT = "project"
OUTPUT_MODE_CUSTOM = "custom"


class _ClickablePathLog(QPlainTextEdit):
    """Log widget that opens the file/folder under the cursor on double click.

    Recognises absolute paths to ``.gpkg`` / ``.csv`` files in the log lines.
    On double click, opens the parent folder in the system file manager
    (Explorer / Finder / xdg-open) so the user can find the outputs quickly.
    """

    def mouseDoubleClickEvent(self, event):
        cursor = self.cursorForPosition(event.pos())
        cursor.select(TEXTCURSOR_LINE_UNDER_CURSOR)
        line = cursor.selectedText()
        target = self._extract_path(line)
        if target:
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))
            return
        super().mouseDoubleClickEvent(event)

    @staticmethod
    def _extract_path(line):
        if not line:
            return None
        candidate = line.strip()
        for token in (candidate.rsplit(": ", 1)[-1], candidate):
            token = token.strip().strip("'\"")
            if not token:
                continue
            if os.path.isfile(token):
                return os.path.dirname(token) or token
            if os.path.isdir(token):
                return token
        return None


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


_DIALOG_OUTPUT_LAYER_PREFIXES = (
    "DUSAF7 ",
    "Confine ",
    "QC slivers ",
    "Com_REST_",
)


def _looks_like_workflow_output(layer):
    """Return True when ``layer`` looks like a previous workflow output.

    Kept in sync with ``analisi_dusaf7_comune_lombardo_algorithm._looks_like_output_layer``
    so the dialog status panel agrees with the algorithm's own detection
    (otherwise the panel would show a misleading "layer di progetto" badge
    pointing at an output of a previous run).
    """
    if layer is None:
        return False
    name = layer.name() or ""
    return any(name.startswith(p) for p in _DIALOG_OUTPUT_LAYER_PREFIXES)


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

    Skips layers that look like outputs of a previous run, so the dialog
    badge agrees with the algorithm's own detection.
    """
    project = QgsProject.instance()

    for layer in project.mapLayers().values():
        if not isinstance(layer, QgsVectorLayer) or not layer.isValid():
            continue

        if _looks_like_workflow_output(layer):
            continue

        layer_name = layer.name().lower()
        source_stem = os.path.splitext(
            os.path.basename(layer.source().split("|")[0])
        )[0].lower()

        def _matches(cand):
            cand_lower = cand.lower()
            return any((
                cand_lower == layer_name,
                cand_lower == source_stem,
                cand_lower in layer_name,
            ))

        name_match = any(_matches(cand) for cand in name_candidates)

        if name_match and _has_field(layer, required_fields):
            return layer

        if not required_fields and name_match:
            return layer

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
        self._last_output_dir = ""
        self._settings = QSettings()

        self.setWindowTitle("Analisi DUSAF 7 - Comuni Lombardia")
        # Keep the minimum small enough that the dialog fits on low-resolution
        # screens (e.g. 1366x768 with Windows scaling, or 1280x720 laptops).
        # The content is wrapped in a QScrollArea so nothing becomes unreachable
        # when the window is shrunk below the preferred size.
        self.setMinimumSize(480, 360)
        self.resize(760, 680)

        self._build_ui()
        self._load_settings()
        self._refresh_data_status()
        self._refresh_output_mode_availability()
        self._populate_comune_autocomplete()
        self._update_run_state()

        # Ascolto dei cambiamenti del progetto: se l'utente carica o
        # rimuove un layer DUSAF/Comuni mentre il dialog e' aperto,
        # vogliamo che lo "stato dati" (badge verde/azzurro + banner
        # rosso consigliato) si aggiorni automaticamente, senza dover
        # chiudere e riaprire il dialog.
        try:
            project = QgsProject.instance()
            project.layersAdded.connect(self._on_project_layers_changed)
            project.layersRemoved.connect(self._on_project_layers_changed)
        except Exception:
            # Su QGIS molto vecchi o configurazioni anomale i segnali
            # potrebbero non essere disponibili; non e' fatale per il
            # plugin, solo niente refresh automatico in quei casi.
            pass

    def showEvent(self, event):
        """Refresh project-aware state every time the dialog is shown.

        The dialog instance is reused across openings (it's a singleton on
        the plugin side), so we re-check whether the project is now saved
        before letting the user run.
        """
        super().showEvent(event)
        try:
            self._refresh_data_status()
            self._refresh_output_mode_availability()
        except Exception:
            pass

    def _on_project_layers_changed(self, *_args):
        """Slot collegato a QgsProject.layersAdded/layersRemoved.

        Quando un layer entra o esce dal progetto, lo stato dei badge
        e del banner "Consigliato: carica DUSAF7" puo' diventare
        obsoleto. Un refresh leggero risolve. Gli argomenti del segnale
        (lista di layer / lista di id) non ci interessano: ricalcoliamo
        sempre da zero leggendo lo stato attuale del progetto.
        """
        try:
            self._refresh_data_status()
        except Exception:
            pass

    def closeEvent(self, event):
        """Stacca i segnali del progetto quando il dialog si chiude.

        Evita che callback su un widget orfano vengano invocate dopo
        che l'utente ha chiuso il dialog (improbabile ma pulito).
        """
        try:
            project = QgsProject.instance()
            project.layersAdded.disconnect(self._on_project_layers_changed)
            project.layersRemoved.disconnect(self._on_project_layers_changed)
        except Exception:
            pass
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # Outer chrome: a single QScrollArea so the dialog stays usable on
        # low-resolution screens. All widgets that used to live directly on
        # ``self`` now live inside ``content`` and ``root`` builds the same
        # layout as before.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(FRAME_NO_FRAME)
        outer.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)

        root = QVBoxLayout(content)
        root.setSpacing(10)

        header = QLabel("<h2 style='margin:0;'>Analisi uso del suolo DUSAF 7</h2>")
        header.setTextFormat(TEXT_FORMAT_RICH)
        root.addWidget(header)

        subtitle = QLabel(
            "<b>Procedura in 3 passi</b>:<br>"
            "&nbsp;&nbsp;<b>1)</b> verifica gli <b>INPUT</b> qui sotto "
            "(consigliato: DUSAF7 come layer di progetto)<br>"
            "&nbsp;&nbsp;<b>2)</b> digita il <b>Comune</b> da analizzare<br>"
            "&nbsp;&nbsp;<b>3)</b> clicca <b>Esegui</b><br>"
            "<i>Il plugin calcola le superfici per classe d'uso del suolo "
            "ritagliate sul perimetro del Comune e produce 4 layer + 1 CSV.</i>"
        )
        subtitle.setTextFormat(TEXT_FORMAT_RICH)
        subtitle.setWordWrap(True)
        # No hardcoded colour: let the theme decide so the subtitle stays
        # legible on both light (3.x) and dark (4.0) QGIS themes.
        root.addWidget(subtitle)

        # =================================================================
        # Stato dati: due sottosezioni peer-level con scopo distinto.
        #
        # 1) Confini comunali: chi è il "ritaglio" geografico del Comune
        #    su cui ritagliamo il DUSAF. Fonte: cache ISTAT (autoritativa)
        #    o servizio REST RL al volo.
        # 2) DUSAF 7.0: i DATI analizzati - uso del suolo per classe. Fonte:
        #    layer di progetto (consigliato, offline) o servizio REST RL
        #    limitato al Comune.
        # =================================================================

        # ---- Sezione 1: Confini comunali (ISTAT) ----
        comuni_box = QGroupBox(
            "INPUT - Confini comunali (ISTAT, perimetro del Comune)"
        )
        comuni_layout = QVBoxLayout(comuni_box)

        comuni_purpose = QLabel(
            "Definiscono il <b>perimetro del Comune</b> usato per ritagliare "
            "i dati DUSAF. Il plugin scarica dal servizio REST RL il solo "
            "Comune selezionato, oppure usa la cache ISTAT 2026 ufficiale "
            "se configurata."
        )
        comuni_purpose.setWordWrap(True)
        comuni_purpose.setTextFormat(TEXT_FORMAT_RICH)
        comuni_layout.addWidget(comuni_purpose)

        self._comuni_status_label = QLabel("...")
        self._comuni_status_label.setWordWrap(True)
        self._comuni_status_label.setTextFormat(TEXT_FORMAT_RICH)
        comuni_layout.addWidget(self._comuni_status_label)

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
            "Apre la configurazione opzionale ISTAT: scaricare lo ZIP "
            "ufficiale 2026 dei confini comunali e usarlo come fonte "
            "autoritativa al posto del servizio REST RL."
        )
        self._istat_btn.clicked.connect(self._on_istat_setup_clicked)
        comuni_actions.addWidget(self._istat_btn)
        comuni_actions.addStretch(1)
        comuni_layout.addLayout(comuni_actions)

        root.addWidget(comuni_box)

        # ---- Sezione 2: DUSAF 7.0 (uso del suolo) ----
        dusaf_box = QGroupBox(
            "INPUT - Uso del suolo (DUSAF 7.0, dati analizzati)"
        )
        dusaf_layout = QVBoxLayout(dusaf_box)

        dusaf_purpose = QLabel(
            "Dataset <b>analizzato dal flusso di lavoro</b>: contiene le superfici "
            "classificate per uso del suolo (residenziale, agricolo, "
            "boschivo, infrastrutture, ecc.). Il plugin lo ritaglia sul "
            "Comune scelto e calcola le aree per ogni classe DUSAF."
        )
        dusaf_purpose.setWordWrap(True)
        dusaf_purpose.setTextFormat(TEXT_FORMAT_RICH)
        dusaf_layout.addWidget(dusaf_purpose)

        # Consiglio prominente: il path "layer di progetto" e' di gran
        # lunga il piu' robusto. Il REST e' un fallback per uso casuale.
        # IMPORTANTE: memorizzare il widget come attributo di istanza
        # (``self._dusaf_recommend``) e non come variabile locale, in
        # modo che ``_refresh_data_status`` possa chiamare setVisible
        # quando lo stato del progetto cambia.
        self._dusaf_recommend = QLabel(
            "<b>⚠ Consigliato</b>: caricare <b>DUSAF7 come layer di "
            "progetto</b> (offline, veloce, robusto). Usa il pulsante "
            "<b>1</b> per scaricare lo ZIP dal Geoportale RL la prima "
            "volta, oppure il pulsante <b>2</b> per caricare uno ZIP o "
            "uno shapefile <b>già presente nel tuo PC</b>.<br>"
            "<i>Alternativa</i>: se non carichi DUSAF7 il plugin userà "
            "il servizio REST live di Regione Lombardia, soggetto a "
            "interruzioni frequenti ('Failed to execute query..')."
        )
        self._dusaf_recommend.setWordWrap(True)
        self._dusaf_recommend.setTextFormat(TEXT_FORMAT_RICH)
        # Stile box-evidenziato in rosso/arancio: leggibile su entrambi
        # i temi (chiaro e scuro) perche' specifica COLORE testo +
        # SFONDO + BORDO, niente di lasciato al tema.
        self._dusaf_recommend.setStyleSheet(
            "QLabel { color:#7a0000; background-color:#fdecec; "
            "padding:6px; border:1px solid #cc3030; font-size:90%; }"
        )
        dusaf_layout.addWidget(self._dusaf_recommend)

        self._dusaf_status_label = QLabel("...")
        self._dusaf_status_label.setWordWrap(True)
        self._dusaf_status_label.setTextFormat(TEXT_FORMAT_RICH)
        dusaf_layout.addWidget(self._dusaf_status_label)

        # Mini-guida testuale a fianco dei bottoni: i tre passi della
        # procedura offline. Lasciamo i bottoni a fianco perche' il
        # workflow e' veramente solo 1) scarica, 2) carica, 3) Esegui.
        dusaf_guide = QLabel(
            "<i>Procedura consigliata (offline, indipendente da REST RL)</i>:<br>"
            "&nbsp;&nbsp;1. Scarica lo ZIP DUSAF 7.0 dal Geoportale (~321 MB)<br>"
            "&nbsp;&nbsp;2. Carica lo ZIP nel progetto con il pulsante qui sotto: "
            "il plugin estrae <code>DUSAF7.shp</code> automaticamente in una "
            "cartella <code>_estratto/</code> accanto allo ZIP"
        )
        dusaf_guide.setWordWrap(True)
        dusaf_guide.setTextFormat(TEXT_FORMAT_RICH)
        dusaf_guide.setStyleSheet(
            "QLabel { font-size:90%; padding:4px 0 0 0; }"
        )
        dusaf_layout.addWidget(dusaf_guide)

        dusaf_actions = QHBoxLayout()
        self._open_geoportale_btn = QPushButton(
            "1. Apri Geoportale RL"
        )
        self._open_geoportale_btn.setToolTip(
            "Apre nel browser la pagina ufficiale del Geoportale Regione "
            "Lombardia dove è scaricabile il pacchetto DUSAF 7.0 (~321 MB, "
            "Shapefile, CC BY 4.0)."
        )
        self._open_geoportale_btn.clicked.connect(self._on_open_geoportale_clicked)
        dusaf_actions.addWidget(self._open_geoportale_btn)

        self._load_dusaf_btn = QPushButton(
            "2. Carica DUSAF (ZIP o SHP) nel progetto..."
        )
        self._load_dusaf_btn.setToolTip(
            "Apre un selettore di file per caricare il DUSAF 7.0 nel "
            "progetto QGIS.\n\n"
            "Puoi selezionare direttamente:\n"
            "  - lo ZIP scaricato dal Geoportale RL (il plugin estrae "
            "DUSAF7.shp + sidecar in una sottocartella '<nome>_estratto/' "
            "accanto allo ZIP)\n"
            "  - oppure DUSAF7.shp se l'hai già estratto manualmente\n\n"
            "In entrambi i casi il plugin riconosce il layer caricato e "
            "lo usa al posto del servizio REST."
        )
        self._load_dusaf_btn.clicked.connect(self._on_load_dusaf_clicked)
        dusaf_actions.addWidget(self._load_dusaf_btn)

        dusaf_actions.addStretch(1)
        dusaf_layout.addLayout(dusaf_actions)

        root.addWidget(dusaf_box)

        # === Selezione Comune ===
        comune_box = QGroupBox("PROCESSING - Comune da analizzare")
        comune_layout = QVBoxLayout(comune_box)

        self._comune_input = QLineEdit()
        self._comune_input.setPlaceholderText(
            "Digita il nome del Comune lombardo e seleziona un valore dal menu..."
        )
        self._comune_completer_model = QStringListModel([], self)
        self._comune_completer = QCompleter(self._comune_completer_model, self._comune_input)
        self._comune_completer.setCaseSensitivity(CASE_INSENSITIVE)
        self._comune_completer.setFilterMode(MATCH_CONTAINS)
        self._comune_completer.setCompletionMode(COMPLETER_POPUP)
        self._comune_input.setCompleter(self._comune_completer)
        self._comune_input.textChanged.connect(self._on_comune_text_changed)
        comune_layout.addWidget(self._comune_input)

        self._comune_validation_label = QLabel("...")
        self._comune_validation_label.setWordWrap(True)
        self._comune_validation_label.setTextFormat(TEXT_FORMAT_RICH)
        comune_layout.addWidget(self._comune_validation_label)

        # Slider trasparenza del layer "DUSAF7 <Comune> - clip QC".
        # Permette di sovrapporre il DUSAF a un'ortofoto/basemap
        # sottostante senza dover passare dal pannello Stile Layer di
        # QGIS. Il valore viene applicato immediatamente quando il
        # layer clip QC e' gia' nel progetto (ad esempio dopo una
        # precedente esecuzione) ed e' anche ricordato per la prossima
        # esecuzione.
        opacity_row = QHBoxLayout()
        opacity_label = QLabel("Trasparenza layer <i>clip QC</i>:")
        opacity_label.setTextFormat(TEXT_FORMAT_RICH)
        opacity_row.addWidget(opacity_label)

        self._opacity_slider = QSlider(ORIENT_HORIZONTAL)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(100)
        self._opacity_slider.setToolTip(
            "Opacita' del layer 'DUSAF7 <Comune> - clip QC' (0% = "
            "totalmente trasparente, 100% = totalmente opaco). Utile "
            "per sovrapporre il DUSAF a un'ortofoto o a un tassello di "
            "sfondo."
        )
        opacity_row.addWidget(self._opacity_slider, stretch=1)

        self._opacity_spin = QSpinBox()
        self._opacity_spin.setRange(0, 100)
        self._opacity_spin.setValue(100)
        self._opacity_spin.setSuffix(" %")
        self._opacity_spin.setMinimumWidth(70)
        opacity_row.addWidget(self._opacity_spin)

        # Sincronizzazione bidirezionale slider <-> spinbox + apply
        # immediato sul layer del progetto se gia' presente.
        self._opacity_slider.valueChanged.connect(self._on_opacity_changed)
        self._opacity_spin.valueChanged.connect(self._on_opacity_changed)

        comune_layout.addLayout(opacity_row)

        root.addWidget(comune_box)

        # === Parametri ===
        params_box = QGroupBox("PROCESSING - Parametri")
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

        sliver_hint = QLabel(
            "<i>Default 1.0 m² è una soglia conservativa per intercettare "
            "i micro-frammenti generati dal clip (rumore numerico, non "
            "perdita di dato). Aumenta a 5-10 m² se vuoi un layer slivers "
            "più "
            "ricco per ispezione manuale; 0 disattiva il flag.</i>"
        )
        sliver_hint.setWordWrap(True)
        sliver_hint.setTextFormat(TEXT_FORMAT_RICH)
        sliver_hint.setStyleSheet("QLabel { font-size:90%; }")
        params_layout.addRow("", sliver_hint)

        root.addWidget(params_box)

        # === Cosa ottieni: riga compatta con link al README ===
        info_label = QLabel(
            "<b>Output</b>: 4 layer (superfici per classe, clip QC, confine, "
            "slivers) + 1 CSV in modalità file. Stili QML applicati. "
            f"<a href='{README_URL}'>Dettagli nel README →</a>"
        )
        info_label.setTextFormat(TEXT_FORMAT_RICH)
        info_label.setWordWrap(True)
        info_label.setOpenExternalLinks(True)
        info_label.setStyleSheet(
            "QLabel { color:#1a1a1a; background-color:#f6f8fa; padding:6px; "
            "border:1px solid #d0d7de; font-size:90%; }"
        )
        root.addWidget(info_label)

        # === Output: memory / project folder / custom folder ===
        output_box = QGroupBox("OUTPUT - Modalità di salvataggio")
        output_layout = QVBoxLayout(output_box)

        self._output_mode_group = QButtonGroup(self)

        self._mode_memory_radio = QRadioButton(
            "Solo layer in memoria nel progetto (nessun file su disco, "
            "no progetto QGIS richiesto)"
        )
        self._mode_memory_radio.setToolTip(
            "Modalità rapida per analisi esplorative. I 4 layer di output "
            "appaiono nel progetto come layer temporanei (Memory provider). "
            "Per renderli permanenti: tasto destro -> Rendi permanente."
        )
        self._output_mode_group.addButton(self._mode_memory_radio)
        output_layout.addWidget(self._mode_memory_radio)

        self._mode_project_radio = QRadioButton(
            "File GeoPackage + CSV nella cartella del progetto QGIS (richiede "
            "progetto salvato)"
        )
        self._output_mode_group.addButton(self._mode_project_radio)
        output_layout.addWidget(self._mode_project_radio)

        self._mode_custom_radio = QRadioButton(
            "File GeoPackage + CSV in cartella personalizzata"
        )
        self._output_mode_group.addButton(self._mode_custom_radio)
        output_layout.addWidget(self._mode_custom_radio)

        custom_row = QHBoxLayout()
        custom_row.addSpacing(22)
        self._custom_dir_edit = QLineEdit()
        self._custom_dir_edit.setPlaceholderText("Nessuna cartella selezionata...")
        custom_row.addWidget(self._custom_dir_edit, stretch=1)

        self._custom_dir_browse_btn = QPushButton("Sfoglia...")
        self._custom_dir_browse_btn.clicked.connect(self._on_browse_custom_dir)
        custom_row.addWidget(self._custom_dir_browse_btn)
        output_layout.addLayout(custom_row)

        self._mode_project_radio.setChecked(True)
        self._mode_memory_radio.toggled.connect(self._on_output_mode_changed)
        self._mode_project_radio.toggled.connect(self._on_output_mode_changed)
        self._mode_custom_radio.toggled.connect(self._on_output_mode_changed)

        self._output_mode_hint = QLabel("")
        self._output_mode_hint.setWordWrap(True)
        # Theme-safe orange that stays legible on both light and dark themes.
        self._output_mode_hint.setStyleSheet("color:#cc7700; font-size:90%;")
        output_layout.addWidget(self._output_mode_hint)

        root.addWidget(output_box)

        # === Esecuzione: log + progress ===
        run_box = QGroupBox("ESECUZIONE - Log e progresso")
        run_layout = QVBoxLayout(run_box)

        self._log_widget = _ClickablePathLog()
        self._log_widget.setReadOnly(True)
        self._log_widget.setMaximumBlockCount(2000)
        log_font = QFont("Consolas")
        log_font.setStyleHint(FONT_MONOSPACE)
        log_font.setPointSize(9)
        self._log_widget.setFont(log_font)
        self._log_widget.setPlaceholderText(
            "Il log dell'esecuzione apparirà qui dopo aver premuto Esegui. "
            "Doppio click su un path apre la cartella nel sistema."
        )
        self._log_widget.setToolTip(
            "Doppio click su una riga che contiene un percorso file/cartella "
            "valido apre la cartella corrispondente nell'esplora risorse."
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

        self._help_btn = QPushButton("?")
        self._help_btn.setToolTip(
            "Apre la documentazione del plugin (README su GitHub)."
        )
        self._help_btn.setFixedWidth(32)
        self._help_btn.clicked.connect(self._on_help_clicked)
        buttons.addWidget(self._help_btn)

        self._cancel_btn = QPushButton("Annulla esecuzione")
        self._cancel_btn.setEnabled(False)
        self._cancel_btn.clicked.connect(self._on_cancel_clicked)
        buttons.addWidget(self._cancel_btn)

        self._open_folder_btn = QPushButton("Apri cartella output")
        self._open_folder_btn.setEnabled(False)
        self._open_folder_btn.setToolTip(
            "Apre la cartella dei file di output nell'esplora risorse del "
            "sistema. Attivo solo dopo un'esecuzione che ha generato file."
        )
        self._open_folder_btn.clicked.connect(self._on_open_folder_clicked)
        buttons.addWidget(self._open_folder_btn)

        buttons.addSpacerItem(
            QSpacerItem(40, 1, SIZE_POLICY_EXPANDING, SIZE_POLICY_MINIMUM)
        )

        self._close_btn = QPushButton("Chiudi")
        self._close_btn.clicked.connect(self.reject)
        buttons.addWidget(self._close_btn)

        # "Esegui" è il bottone protagonista: enfatizzato visivamente
        # con bold + larghezza minima così l'utente lo identifica subito
        # come l'azione finale del flusso. setDefault attiva anche
        # l'invio da tastiera quando il dialog ha il focus.
        self._run_btn = QPushButton("▶ Esegui analisi")
        self._run_btn.setDefault(True)
        self._run_btn.setMinimumWidth(160)
        self._run_btn.setStyleSheet(
            "QPushButton { font-weight:bold; padding:6px 14px; }"
        )
        self._run_btn.clicked.connect(self._on_run_clicked)
        buttons.addWidget(self._run_btn)

        root.addLayout(buttons)

        # Enter inside the Comune field triggers Esegui (when valid)
        self._comune_input.returnPressed.connect(self._on_comune_return_pressed)

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
                "<b>Fonte attiva</b>: layer di progetto "
                f"<i>{comuni_layer.name()}</i> (offline).",
                STATUS_OK_STYLE,
            )
        elif get_istat_cached_shapefile_path() is not None:
            self._set_label(
                self._comuni_status_label,
                "<b>Fonte attiva</b>: cache ISTAT 2026 (configurata). "
                "Fonte autoritativa, offline, indipendente dal REST RL.",
                STATUS_OK_STYLE,
            )
        else:
            self._set_label(
                self._comuni_status_label,
                "<b>Fonte attiva</b>: servizio REST Regione Lombardia "
                "(scarica al volo il singolo Comune, ~10 KB). Lista "
                "autocomplete cacheata in profilo QGIS (TTL 30gg).",
                STATUS_INFO_STYLE,
            )

        dusaf_layer = _find_project_layer(
            self.DUSAF_LAYER_CANDIDATES, self.DUSAF_REQUIRED_FIELDS
        )

        if dusaf_layer is not None:
            self._set_label(
                self._dusaf_status_label,
                "<b>Fonte attiva</b>: layer di progetto "
                f"<i>{dusaf_layer.name()}</i> (offline, veloce).",
                STATUS_OK_STYLE,
            )
            # DUSAF e' nel progetto: l'utente ha gia' fatto la scelta
            # consigliata, il banner di richiamo diventa rumore visivo
            # inutile. Lo nascondiamo per tenere l'interfaccia pulita.
            if hasattr(self, "_dusaf_recommend"):
                self._dusaf_recommend.setVisible(False)
        else:
            self._set_label(
                self._dusaf_status_label,
                "<b>Fonte attiva</b>: servizio REST Regione Lombardia "
                "(online, limitato al Comune). "
                "<i>Per lavorare offline segui la procedura qui sotto.</i>",
                STATUS_INFO_STYLE,
            )
            # Nessun DUSAF nel progetto: rendiamo il banner di consiglio
            # visibile in modo che l'utente sappia perche' il workflow
            # cadrebbe sul REST e come evitarlo.
            if hasattr(self, "_dusaf_recommend"):
                self._dusaf_recommend.setVisible(True)

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
        had_error = False
        try:
            comuni_layer = _find_project_layer(
                self.COMUNI_LAYER_CANDIDATES, self.COMUNI_REQUIRED_FIELDS
            )
            if comuni_layer is not None and not force_refresh:
                self._populate_from_project_layer(comuni_layer)
            else:
                self._populate_from_rest(force_refresh=force_refresh)
        except Exception as exc:
            had_error = True
            self._valid_names = []
            self._valid_name_by_key = {}
            self._comuni_metadata_by_key = {}
            self._comune_completer_model.setStringList([])
            self._set_label(
                self._comune_validation_label,
                "<b>Errore caricamento lista Comuni</b>: " + str(exc)
                + "<br><i>Il servizio Regione Lombardia potrebbe essere "
                "temporaneamente non disponibile. Riprova tra qualche "
                "minuto, oppure configura la cache ISTAT ufficiale.</i>",
                STATUS_ERROR_STYLE,
            )
            try:
                self._log_widget.appendPlainText(
                    "[ERROR] Refresh lista Comuni fallito: {}".format(exc)
                )
            except Exception:
                pass

        # Only recompute the validation label from the input text if the
        # refresh succeeded; otherwise the explicit error message we set
        # above would be immediately overwritten by the generic "lista non
        # disponibile" placeholder.
        if not had_error:
            self._on_comune_text_changed(self._comune_input.text())
        else:
            self._update_run_state(False)

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
        request.setFlags(FEATURE_REQUEST_NO_GEOMETRY)
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
        QApplication.setOverrideCursor(CURSOR_WAIT)
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

        if source == "istat_cache":
            origin = "cache ISTAT ufficiale"
        elif source == "cache":
            origin = "cache locale (REST RL)"
        else:
            origin = "servizio REST Regione Lombardia"
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

    # ------------------------------------------------------------------
    # Output mode + settings persistence
    # ------------------------------------------------------------------

    def _selected_output_mode(self):
        if self._mode_memory_radio.isChecked():
            return OUTPUT_MODE_MEMORY
        if self._mode_custom_radio.isChecked():
            return OUTPUT_MODE_CUSTOM
        return OUTPUT_MODE_PROJECT

    def _on_output_mode_changed(self):
        is_custom = self._mode_custom_radio.isChecked()
        self._custom_dir_edit.setEnabled(
            is_custom and self._mode_custom_radio.isEnabled()
        )
        self._custom_dir_browse_btn.setEnabled(
            is_custom and self._mode_custom_radio.isEnabled()
        )

    def _project_is_saved(self):
        project = QgsProject.instance()
        if project.fileName():
            return True
        home = project.homePath()
        return bool(home and str(home).strip() not in ("", "."))

    def _refresh_output_mode_availability(self):
        """Disable the 'project folder' radio when the project is not saved.

        Also promote the selection to a valid alternative (memory) if the
        currently-checked radio is unavailable. This prevents the user from
        sitting on a mode that will fail at run time.
        """
        project_saved = self._project_is_saved()

        self._mode_project_radio.setEnabled(project_saved)

        if not project_saved:
            self._mode_project_radio.setToolTip(
                "Disponibile solo quando il progetto QGIS è stato salvato "
                "(gli output vanno nella sua cartella)."
            )
            if self._mode_project_radio.isChecked():
                # Promote to memory mode to avoid a guaranteed run-time error.
                self._mode_memory_radio.setChecked(True)
                self._output_mode_hint.setText(
                    "<b>Nota:</b> progetto QGIS non salvato. La modalità "
                    "'cartella del progetto' è disabilitata; impostata "
                    "automaticamente la modalità 'solo memoria'. Per scrivere "
                    "file su disco salva il progetto oppure scegli una "
                    "cartella personalizzata."
                )
            else:
                self._output_mode_hint.setText(
                    "<b>Nota:</b> progetto QGIS non salvato. La modalità "
                    "'cartella del progetto' è disabilitata."
                )
        else:
            self._mode_project_radio.setToolTip("")
            self._output_mode_hint.setText("")

        self._on_output_mode_changed()

    def _on_browse_custom_dir(self):
        start = self._custom_dir_edit.text().strip() or self._last_output_dir or ""
        chosen = QFileDialog.getExistingDirectory(
            self,
            "Seleziona cartella di output",
            start,
        )
        if chosen:
            self._custom_dir_edit.setText(chosen)
            self._last_output_dir = chosen

    def _load_settings(self):
        try:
            mode = self._settings.value(SETTINGS_OUTPUT_MODE, OUTPUT_MODE_PROJECT, type=str)
        except TypeError:
            mode = OUTPUT_MODE_PROJECT
        if mode == OUTPUT_MODE_MEMORY:
            self._mode_memory_radio.setChecked(True)
        elif mode == OUTPUT_MODE_CUSTOM:
            self._mode_custom_radio.setChecked(True)
        else:
            self._mode_project_radio.setChecked(True)

        last_dir = self._settings.value(SETTINGS_OUTPUT_DIR, "", type=str) or ""
        self._last_output_dir = last_dir
        if last_dir:
            self._custom_dir_edit.setText(last_dir)

        try:
            sliver = float(self._settings.value(SETTINGS_SLIVER_M2, 1.0, type=float))
            if sliver < 0:
                sliver = 1.0
            self._sliver_spin.setValue(sliver)
        except (TypeError, ValueError):
            pass

        # Opacita' del layer clip QC (default 100% = totalmente opaco).
        try:
            opacity = int(self._settings.value(
                SETTINGS_CLIP_QC_OPACITY_PCT, 100, type=int
            ))
        except (TypeError, ValueError):
            opacity = 100
        opacity = max(0, min(100, opacity))
        for widget in (self._opacity_slider, self._opacity_spin):
            widget.blockSignals(True)
            try:
                widget.setValue(opacity)
            finally:
                widget.blockSignals(False)

        self._refresh_output_mode_availability()

    def _save_settings(self):
        self._settings.setValue(SETTINGS_OUTPUT_MODE, self._selected_output_mode())
        self._settings.setValue(SETTINGS_OUTPUT_DIR, self._custom_dir_edit.text().strip())
        self._settings.setValue(SETTINGS_SLIVER_M2, float(self._sliver_spin.value()))
        self._settings.setValue(
            SETTINGS_CLIP_QC_OPACITY_PCT, int(self._opacity_slider.value())
        )

    # ------------------------------------------------------------------
    # Help / Open folder / Enter / log path clicks
    # ------------------------------------------------------------------

    def _on_help_clicked(self):
        QDesktopServices.openUrl(QUrl(README_URL))

    def _on_open_folder_clicked(self):
        target = self._last_output_dir
        if target and os.path.isdir(target):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))
        else:
            QMessageBox.information(
                self,
                "Cartella non disponibile",
                "Nessuna cartella di output disponibile. Esegui prima l'analisi "
                "in modalità file (su disco).",
            )

    def _on_opacity_changed(self, value):
        """Tiene allineati slider e spinbox, applica al layer clip QC.

        Se il layer di output "DUSAF7 <Comune> - clip QC" e' gia'
        presente nel progetto (es. da una run precedente), l'opacita'
        viene applicata immediatamente. Se non c'e' ancora, il valore
        resta memorizzato e verra' applicato dopo la prossima
        esecuzione del workflow (vedi
        ``_apply_clip_qc_opacity_to_project_layers``).

        Il valore e' persistito in QSettings, quindi sopravvive alla
        chiusura del dialog/QGIS.
        """
        try:
            value = int(value)
        except (TypeError, ValueError):
            return
        value = max(0, min(100, value))

        # Allinea slider <-> spinbox bloccando temporaneamente i signal
        # per evitare un loop infinito di valueChanged.
        for widget in (self._opacity_slider, self._opacity_spin):
            if widget.value() != value:
                widget.blockSignals(True)
                try:
                    widget.setValue(value)
                finally:
                    widget.blockSignals(False)

        self._settings.setValue(SETTINGS_CLIP_QC_OPACITY_PCT, value)
        self._apply_clip_qc_opacity_to_project_layers(value)

    def _apply_clip_qc_opacity_to_project_layers(self, opacity_pct):
        """Applica l'opacita' (in percentuale 0-100) a tutti i layer
        di progetto che assomigliano a 'DUSAF7 <Comune> - clip QC'.

        Cerchiamo per prefisso/suffisso anziche' nome esatto: cosi'
        gestiamo il caso di rerun su Comuni diversi (vecchio layer
        ancora in progetto) e il caso di doppia esecuzione (QGIS
        appende '(1)', '(2)', ...).
        """
        try:
            opacity_pct = int(opacity_pct)
        except (TypeError, ValueError):
            return
        opacity_value = max(0.0, min(1.0, opacity_pct / 100.0))

        project = QgsProject.instance()
        any_touched = False
        for layer in project.mapLayers().values():
            name = getattr(layer, "name", lambda: "")()
            if not name:
                continue
            # Riconosce 'DUSAF7 <Comune> - clip QC' indipendentemente
            # dal nome del Comune (e' la firma stabile degli output).
            if name.startswith("DUSAF7 ") and "- clip QC" in name:
                try:
                    layer.setOpacity(opacity_value)
                    layer.triggerRepaint()
                    any_touched = True
                except Exception:
                    pass

        if any_touched:
            try:
                # Rinfresca anche il canvas se l'iface e' disponibile,
                # in modo che il cambio sia visibile senza pan/zoom.
                if self.iface is not None and hasattr(self.iface, "mapCanvas"):
                    canvas = self.iface.mapCanvas()
                    if canvas is not None:
                        canvas.refresh()
            except Exception:
                pass

    def _zoom_canvas_to_processed_comune(self, comune_name):
        """Centra la mappa sull'estensione del Comune appena processato.

        Cerca nei layer di progetto quello chiamato ``Confine <Comune>
        fix`` (uno degli output del workflow) e imposta l'extent del
        canvas sul suo bounding box, con un piccolo margine perche'
        l'utente veda anche il contorno del Comune e qualche dettaglio
        oltre il confine. Se per qualche motivo il layer non si trova
        (caso teorico, non dovrebbe mai succedere su un run terminato
        con successo) l'operazione e' silenziosa: lo zoom e' una
        comodita', non un errore se manca.
        """
        if self.iface is None:
            return

        canvas = None
        try:
            canvas = self.iface.mapCanvas()
        except Exception:
            return
        if canvas is None:
            return

        # Il workflow crea sempre un layer 'Confine <Comune> fix'. Usiamo
        # Il layer generato dal flusso e' "Confine <NomeComune> fix",
        # ma <NomeComune> e' la versione canonica restituita dal
        # servizio REST (spesso UPPERCASE, es. "CREMONA") mentre nel
        # dialog l'utente lo digita in title case (es. "Cremona"). Per
        # evitare il mismatch facciamo un match case-insensitive sul
        # nome del Comune *dentro* il pattern "Confine ... fix". Se la
        # stessa sessione produce piu' "Confine X fix" (es. l'utente
        # processa Zibido, poi Varese, poi Cremona) prendiamo quello
        # giusto in base al nome, non il primo trovato (bug della
        # 0.3.10 che zoommava sempre sul primo Comune processato).
        target_key = comune_name.casefold().strip()
        project = QgsProject.instance()
        prefix_lower = "confine "
        suffix_lower = " fix"

        layers = []
        for candidate in project.mapLayers().values():
            name = getattr(candidate, "name", lambda: "")() or ""
            name_lower = name.lower()
            if not (
                name_lower.startswith(prefix_lower)
                and name_lower.endswith(suffix_lower)
            ):
                continue
            middle = name[len(prefix_lower):-len(suffix_lower)]
            if middle.casefold().strip() == target_key:
                layers.append(candidate)

        if not layers:
            # Nessun match esatto: non azzardiamo lo zoom su un layer a
            # caso (era la causa del bug). Meglio non zoommare che
            # zoommare sul Comune sbagliato.
            return

        # Se ci sono piu' match (l'utente ha rieseguito sullo stesso
        # Comune e QGIS ha aggiunto "(1)"/"(2)" al nome), prendiamo
        # l'ultimo aggiunto: e' deterministicamente quello dell'ultima
        # esecuzione del flusso.
        layer = layers[-1]
        if not getattr(layer, "isValid", lambda: False)():
            return

        try:
            extent = layer.extent()
        except Exception:
            return
        if extent is None or extent.isEmpty():
            return

        # Trasforma l'extent dal CRS del layer (EPSG:32632) al CRS del
        # canvas (di solito EPSG:3857 OpenStreetMap o quello del
        # progetto utente). Senza trasformazione il zoom finirebbe in
        # un punto sbagliato del mondo.
        try:
            from qgis.core import (
                QgsCoordinateTransform,
                QgsProject as _QgsProject,
            )

            layer_crs = layer.crs()
            canvas_crs = canvas.mapSettings().destinationCrs()
            if (
                layer_crs is not None and canvas_crs is not None
                and layer_crs.isValid() and canvas_crs.isValid()
                and layer_crs != canvas_crs
            ):
                transform = QgsCoordinateTransform(
                    layer_crs, canvas_crs, _QgsProject.instance().transformContext()
                )
                extent = transform.transformBoundingBox(extent)
        except Exception:
            # Se la trasformazione fallisce per qualunque motivo,
            # tentiamo lo zoom direttamente: in casi degeneri produrra'
            # un extent strano ma non rompe nulla.
            pass

        # Piccolo margine attorno al Comune (5% per lato) per
        # respirabilita' visiva: l'utente vede il contorno del Comune
        # con un po' di contesto, non incollato ai bordi del canvas.
        try:
            extent.scale(1.10)
        except Exception:
            pass

        try:
            canvas.setExtent(extent)
            canvas.refresh()
        except Exception:
            pass

    def _on_comune_return_pressed(self):
        if self._run_btn.isEnabled():
            self._on_run_clicked()

    def _on_refresh_comuni_clicked(self):
        # Immediate visible feedback at the top of the dialog: the log
        # widget lives below and may be off-screen on small displays.
        self._set_label(
            self._comune_validation_label,
            "<b>Aggiornamento lista Comuni in corso...</b> Contatto il "
            "servizio REST di Regione Lombardia.",
            STATUS_INFO_STYLE,
        )
        try:
            self._log_widget.appendPlainText(
                "[INFO] Forzato refresh della lista Comuni..."
            )
        except Exception:
            pass
        QApplication.processEvents()
        self._populate_comune_autocomplete(force_refresh=True)

    def _on_istat_setup_clicked(self):
        """Open the optional ISTAT setup dialog and refresh state on close."""
        from .istat_setup_dialog import IstatSetupDialog
        from ..compat import exec_dialog

        dlg = IstatSetupDialog(self)
        exec_dialog(dlg)

        if dlg.cache_changed():
            self._log_widget.appendPlainText(
                "[INFO] Stato cache ISTAT cambiato. Aggiorno stato dati e lista Comuni..."
            )
            self._refresh_data_status()
            self._populate_comune_autocomplete(force_refresh=True)

    def _on_open_geoportale_clicked(self):
        """Open the official RL Geoportale DUSAF 7.0 download page."""
        url = (
            "https://www.geoportale.regione.lombardia.it/download-pacchetti"
            "?p_p_id=dwnpackageportlet_WAR_gptdownloadportlet"
            "&p_p_lifecycle=0&p_p_state=normal&p_p_mode=view"
            "&_dwnpackageportlet_WAR_gptdownloadportlet_metadataid="
            "r_lombar%3A7cd05e9f-b693-4d7e-a8de-71b40b45f54e"
            "&_jsfBridgeRedirect=true"
        )
        QDesktopServices.openUrl(QUrl(url))
        try:
            self._log_widget.appendPlainText(
                "[INFO] Aperto il Geoportale RL nel browser. Scarica lo ZIP "
                "DUSAF 7.0, estrailo, poi clicca \"Carica DUSAF7.shp nel "
                "progetto...\" per aggiungerlo al progetto QGIS."
            )
        except Exception:
            pass

    def _on_load_dusaf_clicked(self):
        """Let the user pick the DUSAF ZIP (or already extracted .shp) and
        load DUSAF7 into the current project.

        Most users have just downloaded the Geoportale RL ZIP and would
        otherwise need to know that they have to extract it first. We
        accept both: if the picked file is a ZIP we transparently extract
        ``DUSAF7.*`` (skipping ``DUSAF7_FILARI.*``, which is a separate
        linear-features layer) to a sibling directory named
        ``<zipstem>_estratto/`` and then load the resulting shapefile.
        Extraction is one-shot per ZIP: a second click on the same ZIP
        reuses the existing extraction folder.

        Advanced users that have already extracted the shapefile can
        still pick ``DUSAF7.shp`` directly.
        """
        start_dir = self._settings.value(
            SETTINGS_PREFIX + "/last_dusaf_dir",
            os.path.expanduser("~"),
            type=str,
        ) or os.path.expanduser("~")

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Seleziona il pacchetto DUSAF (ZIP del Geoportale RL o .shp estratto)",
            start_dir,
            "Pacchetto DUSAF (*.zip *.shp);;"
            "Archivio ZIP Geoportale (*.zip);;"
            "Shapefile DUSAF7 (*.shp);;"
            "Tutti i file (*)",
        )
        if not filename:
            return

        # Persist the directory so subsequent loads start in the same place.
        self._settings.setValue(
            SETTINGS_PREFIX + "/last_dusaf_dir", os.path.dirname(filename)
        )

        # If the user picked a ZIP, extract DUSAF7 next to it.
        shp_path = filename
        if filename.lower().endswith(".zip"):
            try:
                QApplication.setOverrideCursor(CURSOR_WAIT)
                try:
                    shp_path = self._extract_dusaf_from_zip(filename)
                finally:
                    QApplication.restoreOverrideCursor()
            except Exception as exc:
                QMessageBox.critical(
                    self,
                    "Errore estrazione ZIP DUSAF",
                    "Non è stato possibile estrarre DUSAF7.shp dallo ZIP:\n"
                    "{}\n\n"
                    "Verifica di aver scaricato il pacchetto DUSAF 7.0 "
                    "completo dal Geoportale Regione Lombardia.".format(exc),
                )
                return

        # Default layer name = file stem, so the back-compat detector
        # ("DUSAF7"/"DUSAF 7"/etc.) recognises it without extra hints.
        layer_name = os.path.splitext(os.path.basename(shp_path))[0]

        layer = None
        if self.iface is not None and hasattr(self.iface, "addVectorLayer"):
            layer = self.iface.addVectorLayer(shp_path, layer_name, "ogr")

        if layer is None or not getattr(layer, "isValid", lambda: False)():
            # Fallback: try the lower-level path. addVectorLayer returns
            # None on failure in some QGIS builds.
            layer = QgsVectorLayer(shp_path, layer_name, "ogr")
            if layer.isValid():
                QgsProject.instance().addMapLayer(layer)
            else:
                QMessageBox.warning(
                    self,
                    "Layer DUSAF non valido",
                    "Lo shapefile estratto non è valido o mancano i file "
                    "sidecar (.dbf / .shx / .prj). Riprova selezionando lo "
                    "ZIP originale del Geoportale RL.",
                )
                return

        try:
            self._log_widget.appendPlainText(
                "[INFO] Layer DUSAF caricato: {} ({} feature)".format(
                    layer.name(), layer.featureCount()
                )
            )
        except Exception:
            pass

        # Refresh the status badge: the DUSAF source has just become
        # "project layer", so the user gets immediate visual confirmation.
        self._refresh_data_status()

    # Extensions of the Esri shapefile that compose the DUSAF7 dataset
    # inside the official Geoportale RL ZIP. The first four are required
    # by GDAL/OGR for a valid layer; the rest are optional but improve
    # performance (spatial index) or metadata.
    _DUSAF_SHAPEFILE_REQUIRED_EXTENSIONS = (".shp", ".dbf", ".shx", ".prj")
    _DUSAF_SHAPEFILE_OPTIONAL_EXTENSIONS = (
        ".cpg", ".sbn", ".sbx", ".shp.xml", ".qpj"
    )
    _DUSAF_TARGET_STEM = "DUSAF7"

    def _extract_dusaf_from_zip(self, zip_path):
        """Extract ``DUSAF7.*`` from a Regione Lombardia DUSAF ZIP.

        Returns the path of the extracted ``DUSAF7.shp``.

        Extraction directory is ``<zipdir>/<zipstem>_estratto/``. If that
        directory already contains a valid set of shapefile sidecars
        (.shp/.dbf/.shx/.prj), the function returns the existing path
        without re-extracting: large ZIPs (~321 MB compressed) take time
        to unpack and the user would otherwise pay that cost on every
        load.

        ``DUSAF7_FILARI.*`` (a separate linear-features layer published
        in the same ZIP) is deliberately skipped: it is not used by the
        plugin and would clutter the extracted directory.
        """
        zip_dir = os.path.dirname(os.path.abspath(zip_path))
        zip_stem = os.path.splitext(os.path.basename(zip_path))[0]
        extract_dir = os.path.join(zip_dir, "{}_estratto".format(zip_stem))

        expected_shp = os.path.join(
            extract_dir, self._DUSAF_TARGET_STEM + ".shp"
        )

        # Fast path: a previous extraction of the same ZIP is present
        # and complete. Reuse it.
        required_present = all(
            os.path.isfile(
                os.path.join(
                    extract_dir, self._DUSAF_TARGET_STEM + ext
                )
            )
            for ext in self._DUSAF_SHAPEFILE_REQUIRED_EXTENSIONS
        )
        if required_present:
            try:
                self._log_widget.appendPlainText(
                    "[INFO] Estrazione già presente: {}. Riuso i file "
                    "esistenti senza ri-estrarre.".format(extract_dir)
                )
            except Exception:
                pass
            return expected_shp

        # Inspect the ZIP first and collect the DUSAF7.* members
        # (case-insensitive). DUSAF7_FILARI.* members are excluded:
        # ``basename.startswith(stem + ".")`` matches ``DUSAF7.shp`` but
        # not ``DUSAF7_FILARI.shp`` because of the dot separator.
        stem_lower = self._DUSAF_TARGET_STEM.lower()
        all_extensions = (
            self._DUSAF_SHAPEFILE_REQUIRED_EXTENSIONS
            + self._DUSAF_SHAPEFILE_OPTIONAL_EXTENSIONS
        )

        with zipfile.ZipFile(zip_path, "r") as zf:
            zip_members = zf.namelist()

            members_by_ext = {}
            for member in zip_members:
                basename_lower = os.path.basename(member).lower()
                if not basename_lower.startswith(stem_lower + "."):
                    continue
                # Determine which target extension this member matches.
                # Prefer the longest matching extension (".shp.xml" wins
                # over ".xml") to avoid double-mapping.
                matched_ext = None
                for ext in sorted(all_extensions, key=len, reverse=True):
                    if basename_lower == (stem_lower + ext.lower()):
                        matched_ext = ext
                        break
                if matched_ext is not None:
                    members_by_ext[matched_ext] = member

            missing_required = [
                ext for ext in self._DUSAF_SHAPEFILE_REQUIRED_EXTENSIONS
                if ext not in members_by_ext
            ]
            if missing_required:
                raise ValueError(
                    "Lo ZIP selezionato non contiene tutti i file richiesti "
                    "dello shapefile DUSAF7. Mancanti: {}.".format(
                        ", ".join(missing_required)
                    )
                )

            # Extraction: write into a fresh directory atomically. If
            # something goes wrong mid-extraction we leave a clearly
            # named ``_estratto/`` folder so the user can delete it
            # manually if needed (we do NOT auto-clean: the ZIP itself
            # is 321 MB and the user might prefer to debug rather than
            # re-extract from scratch).
            os.makedirs(extract_dir, exist_ok=True)
            try:
                self._log_widget.appendPlainText(
                    "[INFO] Estrazione DUSAF7 da ZIP in: {}".format(
                        extract_dir
                    )
                )
            except Exception:
                pass

            for ext, member in members_by_ext.items():
                target_path = os.path.join(
                    extract_dir, self._DUSAF_TARGET_STEM + ext
                )
                with zf.open(member) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                try:
                    self._log_widget.appendPlainText(
                        "  estratto {}".format(
                            self._DUSAF_TARGET_STEM + ext
                        )
                    )
                except Exception:
                    pass
                QApplication.processEvents()

        return expected_shp

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
        output_mode = self._selected_output_mode()
        save_to_disk = output_mode != OUTPUT_MODE_MEMORY
        custom_dir = self._custom_dir_edit.text().strip()

        if output_mode == OUTPUT_MODE_CUSTOM and not custom_dir:
            QMessageBox.warning(
                self,
                "Cartella mancante",
                "La modalità 'Cartella personalizzata' è selezionata ma nessuna "
                "cartella è stata indicata. Premi 'Sfoglia...' per sceglierne una.",
            )
            return

        self._save_settings()

        self._log_widget.clear()
        self._progress_bar.setValue(0)
        self._set_running_ui(True)
        self._open_folder_btn.setEnabled(False)

        feedback = _DialogFeedback(self._log_widget, self._progress_bar)
        self._feedback = feedback

        params = {
            "COMUNE_NAME": canonical,
            "SLIVER_MIN_AREA_M2": sliver_threshold,
            "SAVE_TO_DISK": save_to_disk,
            "OUTPUT_DIR_OVERRIDE": custom_dir if output_mode == OUTPUT_MODE_CUSTOM else "",
        }

        try:
            mode_label = {
                OUTPUT_MODE_MEMORY: "Solo memoria",
                OUTPUT_MODE_PROJECT: "Cartella del progetto QGIS",
                OUTPUT_MODE_CUSTOM: f"Cartella personalizzata: {custom_dir}",
            }[output_mode]
            self._log_widget.appendPlainText(
                f"[INFO] Avvio algoritmo su {canonical} con soglia "
                f"slivers={sliver_threshold} m²."
            )
            self._log_widget.appendPlainText(f"[INFO] Modalità output: {mode_label}")
            QApplication.processEvents()

            result = processing.run(
                ALGORITHM_ID,
                params,
                feedback=feedback,
            )

            gpkg = result.get("OUTPUT_GPKG", "") or ""
            csv = result.get("OUTPUT_CSV", "") or ""
            self._log_widget.appendPlainText("")
            self._log_widget.appendPlainText("[OK] Esecuzione completata.")

            if save_to_disk and gpkg:
                self._last_output_dir = os.path.dirname(gpkg)
                self._open_folder_btn.setEnabled(True)
                self._log_widget.appendPlainText(f"     GeoPackage: {gpkg}")
                self._log_widget.appendPlainText(f"     CSV:        {csv}")
                self._log_widget.appendPlainText(
                    f"     Cartella:   {self._last_output_dir}"
                )
                summary = (
                    f"Il flusso di lavoro per {canonical} è terminato con successo.\n\n"
                    f"Output:\n- {gpkg}\n- {csv}"
                )
            else:
                summary = (
                    f"Il flusso di lavoro per {canonical} è terminato con successo.\n\n"
                    "Modalità memoria: i 4 layer di output sono nel progetto come "
                    "layer temporanei. Tasto destro -> 'Rendi permanente' per "
                    "salvarli su disco."
                )

            # Applica al volo la trasparenza scelta dall'utente al layer
            # clip QC appena creato dal workflow. In questo modo il valore
            # del slider conta anche per la prima run di sessione (non
            # solo per i refresh successivi).
            self._apply_clip_qc_opacity_to_project_layers(
                int(self._opacity_slider.value())
            )

            # Zoom automatico all'estensione del Comune appena processato:
            # appena il workflow finisce l'utente vede la mappa centrata
            # sul Comune analizzato, senza dover cercare manualmente.
            self._zoom_canvas_to_processed_comune(canonical)

            QMessageBox.information(
                self,
                "Analisi DUSAF completata",
                summary,
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
        self._istat_btn.setEnabled(not running)
        self._open_geoportale_btn.setEnabled(not running)
        self._load_dusaf_btn.setEnabled(not running)
        self._comune_input.setEnabled(not running)
        self._sliver_spin.setEnabled(not running)
        self._mode_memory_radio.setEnabled(not running)
        self._mode_project_radio.setEnabled(not running)
        self._mode_custom_radio.setEnabled(not running)
        is_custom = self._mode_custom_radio.isChecked()
        self._custom_dir_edit.setEnabled(not running and is_custom)
        self._custom_dir_browse_btn.setEnabled(not running and is_custom)
        self._help_btn.setEnabled(not running)
        self._cancel_btn.setEnabled(running)
        if running:
            QApplication.setOverrideCursor(CURSOR_WAIT)
        else:
            QApplication.restoreOverrideCursor()
            self._update_run_state()
