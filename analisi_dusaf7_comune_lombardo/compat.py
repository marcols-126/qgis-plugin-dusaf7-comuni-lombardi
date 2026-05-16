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
from qgis.PyQt.QtGui import QFont, QTextCursor
from qgis.PyQt.QtWidgets import (
    QCompleter,
    QFrame,
    QMessageBox,
    QSizePolicy,
)


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

def _qt_enum(scope_name, member_name):
    """Resolve a Qt enum that may live either in a nested scope (Qt6 strict,
    e.g. ``Qt.TextFormat.RichText``) or flat on ``Qt`` (Qt5 / legacy Qt6).

    On QGIS 4.0 with Qt6 strict bindings the flat alias (``Qt.RichText``) was
    removed entirely, so a naive ``getattr(..., default=Qt.RichText)`` blows up
    because the default is evaluated eagerly. This helper resolves lazily.
    """
    scope = getattr(Qt, scope_name, None)
    if scope is not None:
        value = getattr(scope, member_name, None)
        if value is not None:
            return value
    return getattr(Qt, member_name)


# Text format
TEXT_FORMAT_RICH = _qt_enum("TextFormat", "RichText")

# Case sensitivity
CASE_INSENSITIVE = _qt_enum("CaseSensitivity", "CaseInsensitive")

# Match flags (used by QCompleter)
MATCH_STARTS_WITH = _qt_enum("MatchFlag", "MatchStartsWith")
MATCH_CONTAINS = _qt_enum("MatchFlag", "MatchContains")

# Alignment
ALIGN_LEFT = _qt_enum("AlignmentFlag", "AlignLeft")

# Orientations
ORIENT_HORIZONTAL = _qt_enum("Orientation", "Horizontal")


# ----------------------------------------------------------------------------
# Scoped-enum resolver for non-Qt widget classes
# ----------------------------------------------------------------------------

def _class_enum(cls, scope_name, member_name):
    """Resolve a scoped enum on an arbitrary Qt class.

    On Qt6 strict bindings (QGIS 4.0 Norrkoping) the flat aliases of nested
    enums were removed from many widget classes too: ``QCompleter.PopupCompletion``
    now lives only as ``QCompleter.CompletionMode.PopupCompletion``,
    ``QFont.Monospace`` as ``QFont.StyleHint.Monospace``, etc.

    The same eager-default-evaluation gotcha that bit us on ``Qt.RichText``
    applies here, so we resolve lazily.
    """
    scope = getattr(cls, scope_name, None)
    if scope is not None:
        value = getattr(scope, member_name, None)
        if value is not None:
            return value
    return getattr(cls, member_name)


# QCompleter
COMPLETER_POPUP = _class_enum(QCompleter, "CompletionMode", "PopupCompletion")

# QFont style hints
FONT_MONOSPACE = _class_enum(QFont, "StyleHint", "Monospace")

# QSizePolicy
SIZE_POLICY_EXPANDING = _class_enum(QSizePolicy, "Policy", "Expanding")
SIZE_POLICY_MINIMUM = _class_enum(QSizePolicy, "Policy", "Minimum")

# QMessageBox standard buttons
MSGBOX_YES = _class_enum(QMessageBox, "StandardButton", "Yes")
MSGBOX_NO = _class_enum(QMessageBox, "StandardButton", "No")

# QTextCursor selection types
TEXTCURSOR_LINE_UNDER_CURSOR = _class_enum(
    QTextCursor, "SelectionType", "LineUnderCursor"
)

# QFrame shapes
FRAME_NO_FRAME = _class_enum(QFrame, "Shape", "NoFrame")


# ----------------------------------------------------------------------------
# QgsFeatureRequest scoped enums (qgis.core)
# ----------------------------------------------------------------------------

try:
    from qgis.core import QgsFeatureRequest

    FEATURE_REQUEST_NO_GEOMETRY = _class_enum(
        QgsFeatureRequest, "Flag", "NoGeometry"
    )
    FEATURE_REQUEST_GEOMETRY_SKIP_INVALID = _class_enum(
        QgsFeatureRequest, "InvalidGeometryCheck", "GeometrySkipInvalid"
    )
    FEATURE_REQUEST_GEOMETRY_NO_CHECK = _class_enum(
        QgsFeatureRequest, "InvalidGeometryCheck", "GeometryNoCheck"
    )
except Exception:
    # If QgsFeatureRequest is missing entirely (extremely unusual: would
    # mean qgis.core failed to import), fall through to None so callers
    # crash with a clear AttributeError instead of import-time failure.
    FEATURE_REQUEST_NO_GEOMETRY = None
    FEATURE_REQUEST_GEOMETRY_SKIP_INVALID = None
    FEATURE_REQUEST_GEOMETRY_NO_CHECK = None


# ----------------------------------------------------------------------------
# QgsProcessingParameterNumber scoped enums
# ----------------------------------------------------------------------------

try:
    from qgis.core import QgsProcessingParameterNumber

    PROC_NUM_DOUBLE = _class_enum(QgsProcessingParameterNumber, "Type", "Double")
    PROC_NUM_INTEGER = _class_enum(QgsProcessingParameterNumber, "Type", "Integer")
except Exception:
    PROC_NUM_DOUBLE = None
    PROC_NUM_INTEGER = None


# ----------------------------------------------------------------------------
# QgsVectorFileWriter scoped enums
# ----------------------------------------------------------------------------

try:
    from qgis.core import QgsVectorFileWriter

    VFW_NO_ERROR = _class_enum(QgsVectorFileWriter, "WriterError", "NoError")
    VFW_CREATE_OR_OVERWRITE_FILE = _class_enum(
        QgsVectorFileWriter, "ActionOnExistingFile", "CreateOrOverwriteFile"
    )
    VFW_CREATE_OR_OVERWRITE_LAYER = _class_enum(
        QgsVectorFileWriter, "ActionOnExistingFile", "CreateOrOverwriteLayer"
    )
except Exception:
    VFW_NO_ERROR = None
    VFW_CREATE_OR_OVERWRITE_FILE = None
    VFW_CREATE_OR_OVERWRITE_LAYER = None

# Cursor / wait indicator
CURSOR_WAIT = _qt_enum("CursorShape", "WaitCursor")

# Window modality
MODAL_APPLICATION = _qt_enum("WindowModality", "ApplicationModal")


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
