# SPDX-License-Identifier: LGPL-2.1-or-later
# Strata: Onshape-style versions, workspaces and releases for FreeCAD.
#
# The "Versions and history" panel, the toolbar and the status-bar indicator are installed at
# startup and stay available in every workbench. FreeCAD only runs this file for add-ons whose
# package.xml declares a workbench, so Strata also registers a small workbench of its own.

import FreeCADGui


class StrataWorkbench(FreeCADGui.Workbench):
    MenuText = "Strata"
    ToolTip = "Versions, workspaces, history and releases"

    def __init__(self):
        import os
        import strata
        self.__class__.Icon = os.path.join(os.path.dirname(strata.__file__), "resources", "strata.svg")

    def Initialize(self):
        commands = ["Strata_Panel", "Strata_CreateVersion", "Strata_Compare", "Strata_Releases", "Strata_Sync",
                    "Strata_Properties"]
        self.appendToolbar("Strata", commands)
        self.appendMenu("&Strata", commands)

    def Activated(self):
        import strata.boot
        p = strata.boot.panel()
        if p is not None:
            p.user_set_visible(True)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


FreeCADGui.addWorkbench(StrataWorkbench())

# FreeCAD executes this file with separate globals and locals, so top-level functions cannot call
# each other here. Install once the main window is up; errors are reported, never raised.
import strata.boot  # noqa: E402
from PySide import QtCore  # noqa: E402

QtCore.QTimer.singleShot(0, strata.boot.safe_install)
