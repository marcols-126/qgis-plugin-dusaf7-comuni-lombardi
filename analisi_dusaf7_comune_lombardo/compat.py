# -*- coding: utf-8 -*-

"""Compatibility shim for Qt5/Qt6 and QGIS 3.34 -> 4.x.

The plugin targets a broad range of QGIS versions and both Qt bindings. This
module centralises the small but breaking differences so the rest of the code
can stay clean and version-agnostic.

Key differences handled here:

- ``QDialog.exec_`` (Qt5) vs ``QDialog.exec`` (Qt6).
- ``Qt.RichText`` flat access still works on Qt6 but some IDEs and future
  releases prefer ``Qt.TextFormat.RichText``: we expose stable aliases.
- ``QVariant`` typing for ``QgsField`` is still accepted in current bindings
  but ``QMetaType`` is the forward-looking path on Qt6.
- ``QgsVectorFileWriter.NoError`` enum scope.
- Optional features that only exist on newer QGIS releases.
"""

from qgis.PyQt import QtCore
from qgis.PyQt.QtCore import Qt, QVariant


# ----------------------------------------------------------------------------
# Qt binding detection
# ----------------------------------------------------------------------------

QT_VERSION_STR = getattr(QtCore, "QT_VERSION_STR", "0.0.0")
QT_MAJOR = int(QT_VERSION_STR.split(".", 1)[0]) if QT_VERSION_STR else 5
IS_QT6 = QT_MAJOR >= 6


# ----------------------------------------------------------------------------
# QGIS version detection
# ----------------------------------------------------------------------------

try:
    from qgis.core import Qgis

    QGIS_VERSION_INT = int(getattr(Qgis, "QGIS_VERSION_INT", 0))
    QGIS_VERSION_STR = str(getattr(Qgis, "QGIS_VERSION", ""))
except Exception:
    QGIS_VERSION_INT = 0
    QGIS_VERSION_STR = ""

QGIS_AT_LEAST_3_34 = QGIS_VERSION_INT >= 33400
QGIS_AT_LEAST_3_40 = QGIS_VERSION_INT >= 34000
QGIS_AT_LEAST_4_0 = QGIS_VERSION_INT >= 40000


# ----------------------------------------------------------------------------
# QDialog.exec compatibility
# ----------------------------------------------------------------------------

def exec_dialog(dialog):
    """Execute a Qt dialog regardless of Qt5/Qt6 binding.

    Qt6 dropped the trailing-underscore variant. Qt5 keeps both. Prefer the
    keyword-safe ``exec`` and fall back to ``exec_`` for older bindings.
    """
    runner = getattr(dialog, "exec", None)
    if callable(runner):
        return runner()

    legacy_runner = getattr(dialog, "exec_", None)
    if callable(legacy_runner):
        return legacy_runner()

    raise RuntimeError("Qt dialog has neither exec() nor exec_() callable.")


# ----------------------------------------------------------------------------
# Qt enum aliases that may live in different scopes between Qt5 and Qt6
# ----------------------------------------------------------------------------

# Text format
TEXT_FORMAT_RICH = getattr(getattr(Qt, "TextFormat", Qt), "RichText", Qt.RichText)

# Case sensitivity
CASE_INSENSITIVE = getattr(
    getattr(Qt, "CaseSensitivity", Qt),
    "CaseInsensitive",
    Qt.CaseInsensitive,
)

# Match flags (used by QCompleter)
MATCH_STARTS_WITH = getattr(
    getattr(Qt, "MatchFlag", Qt),
    "MatchStartsWith",
    Qt.MatchStartsWith,
)

# Cursor / wait indicator
CURSOR_WAIT = getattr(getattr(Qt, "CursorShape", Qt), "WaitCursor", Qt.WaitCursor)

# Window modality
MODAL_APPLICATION = getattr(
    getattr(Qt, "WindowModality", Qt),
    "ApplicationModal",
    Qt.ApplicationModal,
)


# ----------------------------------------------------------------------------
# QVariant -> QgsField typing helper
# ----------------------------------------------------------------------------

def qfield_type_double():
    """Return the type identifier expected by ``QgsField`` for a double value.

    Both Qt5 and current Qt6 bindings still accept ``QVariant.Double`` here.
    This helper exists so future QGIS releases can switch to ``QMetaType``
    by editing one place only.
    """
    return QVariant.Double


def qfield_type_int():
    """Return the type identifier expected by ``QgsField`` for an int value."""
    return QVariant.Int


def qfield_type_string():
    """Return the type identifier expected by ``QgsField`` for a string."""
    return QVariant.String


# ----------------------------------------------------------------------------
# Public summary helper for diagnostics
# ----------------------------------------------------------------------------

def runtime_summary():
    """Return a short, human-readable runtime summary used in logs."""
    return (
        "QGIS={qgis} (int={qgis_int}) | Qt={qt} (major={qt_major})".format(
            qgis=QGIS_VERSION_STR or "unknown",
            qgis_int=QGIS_VERSION_INT,
            qt=QT_VERSION_STR,
            qt_major=QT_MAJOR,
        )
    )
