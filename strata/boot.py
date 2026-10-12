# SPDX-License-Identifier: LGPL-2.1-or-later
"""Installs Strata into the running FreeCAD GUI: panel, commands, toolbar, status pill, observers.

Embedding API for host add-ons (e.g. a custom UI that provides its own ribbon or toolbar):

    import strata.boot as sb
    sb.configure(host_toolbar=True, panel_area="left")  # before or after start; stored as preferences
    sb.on_ready(lambda: my_ribbon.add(sb.actions()))     # called once Strata is up (or at once if it is)
    from strata.ui import theme
    theme.set_overrides(light={"accent": "#00838F"})     # restyles open panels

With host_toolbar=True Strata adds no toolbar of its own, installs no workbench manipulator and does
not re-activate the current workbench (which would reset the host's shortcuts).
"""

from __future__ import annotations

import os

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from . import fc

ICONS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", "icons")
_state: dict = {}
PARAMS = "User parameter:BaseApp/Preferences/Mod/Strata"
COMMAND_NAMES = ["Strata_Panel", "Strata_CreateVersion", "Strata_Compare", "Strata_Releases", "Strata_Sync"]
AREAS = {"left": QtCore.Qt.LeftDockWidgetArea, "right": QtCore.Qt.RightDockWidgetArea}


def panel():
    return _state.get("panel")


def is_ready() -> bool:
    return "panel" in _state


# ------------------------------------------------------------------ embedding API
def on_ready(callback):
    """Call `callback()` once Strata is installed; immediately if it already is."""
    if is_ready():
        callback()
    else:
        _state.setdefault("ready_callbacks", []).append(callback)


def actions() -> dict:
    """Strata's commands as QActions owned by Strata: {command name: QAction}. Created at startup,
    enabled/disabled with the active project. Hosts can put them on their own ribbons or menus."""
    return dict(_state.get("actions", {}))


def configure(host_toolbar: bool | None = None, panel_area: str | None = None):
    """Adapt Strata to a host UI. Persisted as preferences, so it also applies at the next start."""
    params = App.ParamGet(PARAMS)
    if host_toolbar is not None:
        params.SetBool("HostProvidesToolbar", bool(host_toolbar))
        if is_ready():
            _apply_toolbar_mode(bool(host_toolbar))
    if panel_area is not None:
        if panel_area not in AREAS:
            raise ValueError("panel_area must be 'left' or 'right'")
        params.SetString("PanelArea", panel_area)
        p = panel()
        if p is not None:
            mw = Gui.getMainWindow()
            if mw.dockWidgetArea(p) != AREAS[panel_area]:
                mw.addDockWidget(AREAS[panel_area], p)


def host_provides_toolbar() -> bool:
    return App.ParamGet(PARAMS).GetBool("HostProvidesToolbar", False)


# ------------------------------------------------------------------ commands
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
    ALL = COMMAND_NAMES

    def modifyToolBars(self):
        return [{"append": c, "toolBar": "File"} for c in self.COMMANDS]


def _toggle_panel():
    p = panel()
    p.user_set_visible(not p.isVisible())


def _build_actions(parent):
    from PySide import QtGui
    acts = {}
    for name in COMMAND_NAMES:
        cmd = _state["commands"][name]
        act = QtGui.QAction(QtGui.QIcon(os.path.join(ICONS, cmd.icon + ".svg")), cmd.text, parent)
        act.setObjectName(name)
        act.setToolTip(cmd.tip)
        act.setStatusTip(cmd.tip)
        act.triggered.connect(lambda _=False, c=cmd: c.fn())
        acts[name] = act
    _state["actions"] = acts
    _update_actions()


def _update_actions():
    for name, act in _state.get("actions", {}).items():
        act.setEnabled(_state["commands"][name].IsActive())


def _ensure_toolbar(*_):
    """Keep a Strata toolbar in every workbench: the manipulator's one, else our own Qt toolbar."""
    if host_provides_toolbar():
        return
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
        fallback = QtWidgets.QToolBar("Strata", mw)
        fallback.setObjectName("StrataTools")
        for name in COMMAND_NAMES:
            fallback.addAction(_state["actions"][name])
        mw.addToolBar(fallback)
        _state["fallback_toolbar"] = fallback
    fallback.show()


def _apply_toolbar_mode(host: bool):
    mw = Gui.getMainWindow()
    timer = _state.get("toolbar_timer")
    if host:
        if timer is not None:
            timer.stop()
        if _state.get("fallback_toolbar") is not None:
            _state["fallback_toolbar"].hide()
        m = _state.pop("manipulator", None)
        if m is not None and hasattr(Gui, "removeWorkbenchManipulator"):
            Gui.removeWorkbenchManipulator(m)
        return
    if hasattr(Gui, "addWorkbenchManipulator") and "manipulator" not in _state:
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
    if timer is None:
        timer = QtCore.QTimer(mw)
        timer.timeout.connect(_ensure_toolbar)
        _state["toolbar_timer"] = timer
    timer.start(2000)
    QtCore.QTimer.singleShot(0, _ensure_toolbar)


# ------------------------------------------------------------------ startup
def safe_install():
    """Startup entry point used by InitGui.py: never lets an error escape into FreeCAD's startup."""
    try:
        install()
    except Exception:
        import traceback
        App.Console.PrintError("Strata failed to start:\n%s\n" % traceback.format_exc())


def install():
    if is_ready():
        return
    from .ui.panel import Panel, StatusPill
    mw = Gui.getMainWindow()
    params = App.ParamGet(PARAMS)
    p = Panel(mw)
    area = params.GetString("PanelArea", "right")
    mw.addDockWidget(AREAS.get(area, QtCore.Qt.RightDockWidgetArea), p)
    p.setMinimumWidth(300)
    p.setVisible(params.GetBool("PanelVisible", True))
    # Remember only what the user does on purpose. Qt signals are not enough: visibilityChanged(False)
    # fires when the dock becomes a background tab, and both it and the toggle-view action fire
    # while the main window closes (both reported by freecad-nxstyle).
    p.user_visibility.connect(lambda on: params.SetBool("PanelVisible", bool(on)))

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
    _state["panel"] = p
    _build_actions(mw)
    p.refreshed.connect(_update_actions)
    _apply_toolbar_mode(params.GetBool("HostProvidesToolbar", False))
    p.refresh()
    App.Console.PrintLog("Strata: ready\n")
    for cb in _state.pop("ready_callbacks", []):
        try:
            cb()
        except Exception:
            import traceback
            App.Console.PrintError("Strata: on_ready callback failed:\n%s\n" % traceback.format_exc())
