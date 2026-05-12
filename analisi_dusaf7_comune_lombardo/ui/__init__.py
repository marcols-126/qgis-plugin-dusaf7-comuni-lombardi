# -*- coding: utf-8 -*-

"""User-facing dialogs for the DUSAF 7 Lombardia plugin.

The package exposes a single primary dialog (``DusafMainDialog``) that
replaces the bare Processing form when the user clicks the plugin's
toolbar action. The Processing algorithm itself stays available in the
Toolbox so existing models and scripts keep working.
"""

from .main_dialog import DusafMainDialog

__all__ = ["DusafMainDialog"]
