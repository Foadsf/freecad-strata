# SPDX-License-Identifier: LGPL-2.1-or-later
"""Installs Strata into the running FreeCAD GUI: panel, commands, toolbar, status pill, observers."""

from __future__ import annotations

import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from . import fc

ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "icons")
_state: dict = {}
PARAMS = "User parameter:BaseApp/Preferences/Mod/Strata"


def panel():
    return _state.get("panel")


class _Command:
    def __init__(self, text, tip, icon, fn, needs_project=True, checkable=False):
        self.text, self.tip, self.icon, self.fn = text, tip, icon, fn
        self.needs_project, self.checkable = needs_project, checkable

    def GetResources(self):
        res = {"Pixmap": os.path.join(ICONS, self.icon + ".svg"), "MenuText": self.text, "ToolTip": self.tip}
        return res

    def Activated(self, *_):
        self.fn()

    def IsActive(self):
        p = panel()
        return p is not None and (not self.needs_project or p.project is not None)


class _Manipulator:
    """Puts the two everyday buttons next to Save, in every workbench (same pattern as FreeCAD's BIM)."""
    COMMANDS = ["Strata_Panel", "Strata_CreateVersion"]
    ALL = COMMANDS + ["Strata_Compare", "Strata_Releases", "Strata_Sync"]

    def modifyToolBars(self):
        return [{"append": c, "toolBar": "File"} for c in self.COMMANDS]


def _toggle_panel():
    p = panel()
    p.setVisible(not p.isVisible())
    if p.isVisible():
        p.raise_()
        p.refresh()


def _ensure_toolbar(*_):
    """Keep a Strata toolbar in every workbench: the manipulator's one, else our own Qt toolbar."""
    mw = Gui.getMainWindow()
    fallback = _state.get("fallback_toolbar")
    label = _state["commands"]["Strata_Panel"].text
    managed = [t for t in mw.findChildren(QtWidgets.QToolBar)
               if t is not fallback and t.isVisible() and any(a.text() == label for a in t.actions())]
    if managed:
        if fallback is not None:
            fallback.hide()
        return
    if fallback is None:
        from PySide import QtGui
        fallback = QtWidgets.QToolBar("Strata", mw)
        fallback.setObjectName("StrataTools")
        for name in _Manipulator.ALL:
            cmd = _state["commands"][name]
            act = QtGui.QAction(QtGui.QIcon(os.path.join(ICONS, cmd.icon + ".svg")), cmd.text, fallback)
            act.setToolTip(cmd.tip)
            act.triggered.connect(lambda _=False, c=cmd: c.fn())
            fallback.addAction(act)
        mw.addToolBar(fallback)
        _state["fallback_toolbar"] = fallback
    fallback.show()


def safe_install():
    """Startup entry point used by InitGui.py: never lets an error escape into FreeCAD's startup."""
    try:
        install()
    except Exception:
        import traceback
        App.Console.PrintError("Strata failed to start:\n%s\n" % traceback.format_exc())


def install():
    if "panel" in _state:
        return
    from .ui.panel import Panel, StatusPill
    mw = Gui.getMainWindow()
    p = Panel(mw)
    mw.addDockWidget(QtCore.Qt.RightDockWidgetArea, p)
    p.setMinimumWidth(300)
    _state["panel"] = p
    params = App.ParamGet(PARAMS)
    p.setVisible(params.GetBool("PanelVisible", True))
    p.visibilityChanged.connect(lambda v: params.SetBool("PanelVisible", bool(v)))

    pill = StatusPill(p)
    mw.statusBar().addPermanentWidget(pill)
    p.status = pill

    obs = fc.SaveObserver(p.on_project_change)
    App.addDocumentObserver(obs)
    _state["observer"] = obs
    mdi = mw.findChild(QtWidgets.QMdiArea)
    if mdi is not None:
        mdi.subWindowActivated.connect(p.schedule_refresh)

    cmds = {
        "Strata_Panel": _Command("Versions and history", "Show or hide versions, workspaces and history", "history",
                                 _toggle_panel, needs_project=False),
        "Strata_CreateVersion": _Command("Create version", "Save a named, permanent snapshot of the project", "version",
                                         lambda: panel().create_version()),
        "Strata_Compare": _Command("Compare", "Compare two versions or points in history", "compare",
                                   lambda: panel().compare()),
        "Strata_Releases": _Command("Releases", "Release management: part numbers, revisions, approvals", "release",
                                    lambda: panel().show_releases()),
        "Strata_Sync": _Command("Sync", "Share and sync the project with your team", "sync",
                                lambda: panel().sync()),
    }
    for name, cmd in cmds.items():
        Gui.addCommand(name, cmd)
    _state["commands"] = cmds
    if hasattr(Gui, "addWorkbenchManipulator"):
        m = _Manipulator()
        Gui.addWorkbenchManipulator(m)
        _state["manipulator"] = m
        try:  # rebuild the toolbars of the workbench that is already active
            wb = Gui.activeWorkbench()
            if wb is not None:
                Gui.activateWorkbench(wb.name())
        except Exception:
            pass
    # FreeCAD's toolbar manager rebuilds toolbars on every workbench switch, and Gui::MainWindow's
    # workbenchActivated signal is not exposed to Python, so check after switches with a light timer.
    tb_timer = QtCore.QTimer(mw)
    tb_timer.timeout.connect(_ensure_toolbar)
    tb_timer.start(2000)
    _state["toolbar_timer"] = tb_timer
    QtCore.QTimer.singleShot(0, _ensure_toolbar)
    p.refresh()
    App.Console.PrintLog("Strata: ready\n")
