# -*- coding: utf-8 -*-

"""Optional ISTAT 2026 boundaries setup dialog.

Opens from the main dialog button "Usa ISTAT ufficiale (download)". The
dialog guides the user through the one-off setup:

1. Open the official ISTAT page in a browser to download the ZIP archive.
2. Pick the downloaded ZIP via a file dialog.
3. Validate + extract it into the plugin cache (using
   ``IstatBoundariesClient.prepare_local_package``).

Once the cache is in place the workflow's data resolver can use ISTAT as
the authoritative boundary source and fall back to the Regione Lombardia
ArcGIS REST service when the cache is missing or stale.
"""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QDesktopServices, QFont
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QVBoxLayout,
)

from ..data_sources import CacheManager, IstatBoundariesClient


STATUS_OK_STYLE = "color:#006100; background-color:#eefbea; padding:4px; border:1px solid #3caa3c;"
STATUS_INFO_STYLE = "color:#1a4170; background-color:#e8f1fb; padding:4px; border:1px solid #5c8fce;"
STATUS_WARN_STYLE = "color:#7a4a00; background-color:#fff5e1; padding:4px; border:1px solid #e0a040;"
STATUS_ERROR_STYLE = "color:#7a0000; background-color:#fdecec; padding:4px; border:1px solid #cc3030;"


class IstatSetupDialog(QDialog):
    """Setup dialog for the optional ISTAT 2026 boundaries cache."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurazione confini ISTAT 2026 (opzionale)")
        self.setMinimumSize(640, 540)

        self._client = IstatBoundariesClient()
        self._cache_manager = CacheManager()
        self._selected_zip_path = ""
        self._cache_changed = False

        self._build_ui()
        self._refresh_status()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)

        header = QLabel("<h2 style='margin:0;'>Confini ISTAT 2026 - Configurazione</h2>")
        header.setTextFormat(Qt.RichText)
        root.addWidget(header)

        intro = QLabel(
            "ISTAT pubblica i confini amministrativi ufficiali come archivio "
            "ZIP. Procedura una tantum: scarica lo ZIP dal sito ufficiale "
            "e poi seleziona il file. Il plugin estrae e salva i confini "
            "nel profilo QGIS. Da quel momento il flusso di lavoro userà "
            "ISTAT come fonte primaria al posto del servizio REST RL."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#555;")
        root.addWidget(intro)

        # === Step 1 - Open ISTAT page ===
        step1 = QGroupBox("Passo 1 · Scarica lo ZIP ufficiale ISTAT")
        s1_layout = QVBoxLayout(step1)

        url_label = QLabel(
            f'Apri la pagina ISTAT (URL: '
            f'<a href="{self._client.landing_page_url}">'
            f'{self._client.landing_page_url}</a>) e scarica '
            f'lo ZIP del dataset <b>Confini amministrativi {self._client.reference_year}</b> '
            f'(versione <i>non generalizzato</i>, CRS {self._client.expected_crs_label}).'
        )
        url_label.setTextFormat(Qt.RichText)
        url_label.setWordWrap(True)
        url_label.setOpenExternalLinks(True)
        s1_layout.addWidget(url_label)

        open_btn = QPushButton("Apri pagina ISTAT nel browser")
        open_btn.clicked.connect(self._on_open_istat_page)
        s1_layout.addWidget(open_btn, alignment=Qt.AlignLeft)

        root.addWidget(step1)

        # === Step 2 - Pick ZIP ===
        step2 = QGroupBox("Passo 2 · Seleziona lo ZIP scaricato")
        s2_layout = QVBoxLayout(step2)

        path_row = QHBoxLayout()
        self._zip_path_edit = QLineEdit()
        self._zip_path_edit.setReadOnly(True)
        self._zip_path_edit.setPlaceholderText("Nessun file selezionato...")
        path_row.addWidget(self._zip_path_edit, stretch=1)

        browse_btn = QPushButton("Sfoglia ZIP...")
        browse_btn.clicked.connect(self._on_browse_clicked)
        path_row.addWidget(browse_btn)
        s2_layout.addLayout(path_row)

        root.addWidget(step2)

        # === Step 3 - Prepare cache ===
        step3 = QGroupBox("Passo 3 · Estrai e prepara la cache locale")
        s3_layout = QVBoxLayout(step3)

        self._prepare_btn = QPushButton("Estrai e prepara cache locale")
        self._prepare_btn.setEnabled(False)
        self._prepare_btn.clicked.connect(self._on_prepare_clicked)
        s3_layout.addWidget(self._prepare_btn, alignment=Qt.AlignLeft)

        self._log_widget = QPlainTextEdit()
        self._log_widget.setReadOnly(True)
        self._log_widget.setMaximumBlockCount(500)
        log_font = QFont("Consolas")
        log_font.setStyleHint(QFont.Monospace)
        log_font.setPointSize(9)
        self._log_widget.setFont(log_font)
        self._log_widget.setPlaceholderText(
            "Il log di estrazione apparirà qui dopo aver cliccato 'Estrai e prepara'."
        )
        self._log_widget.setMinimumHeight(120)
        s3_layout.addWidget(self._log_widget)

        root.addWidget(step3, stretch=1)

        # === Status + cache management ===
        status_box = QGroupBox("Stato cache ISTAT")
        status_layout = QVBoxLayout(status_box)

        self._status_label = QLabel("...")
        self._status_label.setWordWrap(True)
        self._status_label.setTextFormat(Qt.RichText)
        status_layout.addWidget(self._status_label)

        actions_row = QHBoxLayout()
        self._clear_btn = QPushButton("Rimuovi cache ISTAT")
        self._clear_btn.setToolTip(
            "Elimina la cartella di estrazione ISTAT e l'entry corrispondente "
            "nel manifest. Lo ZIP scaricato non viene eliminato."
        )
        self._clear_btn.clicked.connect(self._on_clear_clicked)
        actions_row.addWidget(self._clear_btn)
        actions_row.addStretch(1)
        status_layout.addLayout(actions_row)

        root.addWidget(status_box)

        # === Bottom buttons ===
        buttons = QHBoxLayout()
        buttons.addSpacerItem(QSpacerItem(40, 1, QSizePolicy.Expanding, QSizePolicy.Minimum))
        close_btn = QPushButton("Chiudi")
        close_btn.clicked.connect(self.accept)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def cache_changed(self):
        """Return True when the cache was modified during this dialog session.

        The caller (main dialog) uses this to decide whether to refresh its
        autocomplete and status badges after the dialog closes.
        """
        return self._cache_changed

    def _refresh_status(self):
        shp_path = self._client.cached_shapefile_path(self._cache_manager)
        if shp_path:
            size_kb = os.path.getsize(shp_path) // 1024
            text = (
                f"<b>Cache ISTAT configurata.</b><br>"
                f"Shapefile: <i>{shp_path}</i> ({size_kb} KB)"
            )
            self._set_label(self._status_label, text, STATUS_OK_STYLE)
            self._clear_btn.setEnabled(True)
        else:
            self._set_label(
                self._status_label,
                "<b>Cache ISTAT non configurata.</b> Il flusso di lavoro userà "
                "il servizio REST Regione Lombardia (default).",
                STATUS_INFO_STYLE,
            )
            self._clear_btn.setEnabled(False)

    @staticmethod
    def _set_label(label, html, style):
        label.setStyleSheet(f"QLabel {{ {style} }}")
        label.setText(html)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _on_open_istat_page(self):
        QDesktopServices.openUrl(QUrl(self._client.landing_page_url))

    def _on_browse_clicked(self):
        start_dir = os.path.dirname(self._selected_zip_path) if self._selected_zip_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Seleziona ZIP confini ISTAT",
            start_dir,
            "Archivi ZIP (*.zip);;Tutti i file (*)",
        )
        if not path:
            return

        self._selected_zip_path = path
        self._zip_path_edit.setText(path)
        self._prepare_btn.setEnabled(True)
        self._log_widget.appendPlainText(f"[INFO] Selezionato ZIP: {path}")

    def _on_prepare_clicked(self):
        zip_path = self._selected_zip_path
        if not zip_path or not os.path.isfile(zip_path):
            QMessageBox.warning(
                self,
                "ZIP non valido",
                "Seleziona uno ZIP esistente prima di procedere.",
            )
            return

        self._log_widget.appendPlainText("")
        self._log_widget.appendPlainText("[INFO] Validazione contenuto ZIP...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        QApplication.processEvents()

        try:
            components = self._client.validate_required_shapefile_components(zip_path)
            present = components.get("present", []) if isinstance(components, dict) else []
            present_names = sorted(os.path.basename(p) for p in present)
            self._log_widget.appendPlainText(
                f"[OK] Componenti shapefile trovati ({len(present_names)} file): "
                + ", ".join(present_names)
            )

            self._log_widget.appendPlainText(
                "[INFO] Estrazione nella cache locale del profilo QGIS..."
            )
            QApplication.processEvents()

            entry = self._client.prepare_local_package(
                archive_path=zip_path,
                cache_manager=self._cache_manager,
                overwrite=True,
            )

            shp_path = entry.get("shapefile_path", "(percorso non disponibile)")
            self._log_widget.appendPlainText(f"[OK] Cache ISTAT pronta.")
            self._log_widget.appendPlainText(f"     Shapefile: {shp_path}")

            self._cache_changed = True
            QMessageBox.information(
                self,
                "Cache ISTAT pronta",
                "I confini ISTAT 2026 sono stati estratti nella cache del profilo "
                "QGIS. Il flusso di lavoro li userà come fonte primaria al posto "
                "del REST RL.",
            )
        except FileExistsError as exc:
            self._log_widget.appendPlainText(f"[ERROR] {exc}")
            QMessageBox.warning(
                self,
                "Cache già presente",
                f"La cache ISTAT esiste già. Rimuoverla prima di ri-importare.\n\n{exc}",
            )
        except Exception as exc:
            self._log_widget.appendPlainText(f"[ERROR] {type(exc).__name__}: {exc}")
            QMessageBox.critical(
                self,
                "Errore durante la preparazione",
                f"La preparazione della cache è fallita.\n\nDettaglio: {exc}",
            )
        finally:
            QApplication.restoreOverrideCursor()
            self._refresh_status()

    def _on_clear_clicked(self):
        confirm = QMessageBox.question(
            self,
            "Conferma rimozione cache ISTAT",
            "Eliminare la cache ISTAT? Il flusso di lavoro tornerà a usare "
            "il servizio REST Regione Lombardia come default.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        try:
            removed = self._client.clear_cache(self._cache_manager)
            if removed:
                self._log_widget.appendPlainText("[OK] Cache ISTAT rimossa.")
                self._cache_changed = True
            else:
                self._log_widget.appendPlainText("[INFO] Nessuna cache ISTAT da rimuovere.")
        except Exception as exc:
            self._log_widget.appendPlainText(f"[ERROR] {exc}")
            QMessageBox.critical(
                self,
                "Errore durante la rimozione",
                f"Non sono riuscito a rimuovere la cache ISTAT.\n\nDettaglio: {exc}",
            )
        finally:
            self._refresh_status()
