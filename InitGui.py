# SPDX-License-Identifier: LGPL-2.1-or-later
# Strata: Onshape-style versions, workspaces and releases for FreeCAD.
# Not a workbench: the panel and toolbar are available in every workbench.


def _strata_install():
    import FreeCAD
    try:
        import strata.boot
        strata.boot.install()
    except Exception:
        import traceback
        FreeCAD.Console.PrintError("Strata failed to start:\n%s\n" % traceback.format_exc())


def _strata_defer():
    from PySide import QtCore
    QtCore.QTimer.singleShot(0, _strata_install)


_strata_defer()
