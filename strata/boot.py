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
# Default shortcuts. Chosen so that none clashes with FreeCAD 1.1's own defaults (all 185 were
# listed from a running FreeCAD); users can change them under Tools > Customize > Keyboard.
SHORTCUTS = {
    "Strata_Panel": "Ctrl+Shift+H",          # History
    "Strata_CreateVersion": "Ctrl+Shift+V",  # Version
    "Strata_Compare": "Ctrl+Shift+D",        # Diff
    "Strata_Releases": "Ctrl+Shift+R",       # Releases
    "Strata_Sync": "Ctrl+Shift+Y",           # sYnc
}


class _Command:
    def __init__(self, text, tip, icon, fn, needs_project=True, checkable=False, accel=""):
        self.text, self.tip, self.icon, self.fn = text, tip, icon, fn
        self.needs_project, self.checkable, self.accel = needs_project, checkable, accel

    def GetResources(self):
        res = {"Pixmap": os.path.join(ICONS, self.icon + ".svg"), "MenuText": self.text, "ToolTip": self.tip}
        if self.accel:
            res["Accel"] = self.accel
        return res

    def Activated(self, *_):
        self.fn()

    def IsActive(self):
        p = panel()
        return p is not None and (not self.needs_project or p.project is not None)


class _Manipulator:
    """Adds Strata's commands to the Tools menu of every workbench.

    Besides making them easy to find, this is what makes the keyboard shortcuts work: FreeCAD only
    creates a command's action, and with it the key binding, once a menu or toolbar holds it
    (measured: no action and an empty shortcut before, Ctrl+Shift+H working after).
    """

    def modifyMenuBar(self):
        # Tools > ... Customize, ---, Versions and history, Create version, Compare, Releases, Sync
        items = [{"append": COMMAND_NAMES[0], "menuItem": "Std_DlgCustomize"}]
        items += [{"append": COMMAND_NAMES[i], "menuItem": COMMAND_NAMES[i - 1]}
                  for i in range(1, len(COMMAND_NAMES))]
        items.append({"insert": "Separator", "menuItem": COMMAND_NAMES[0]})
        return items


def activate_commands():
    """Re-activate the current workbench so FreeCAD builds Strata's menu entries and key bindings now
    rather than at the next workbench switch. Called automatically unless a host provides the toolbar
    (re-activation can reset a host's own shortcuts, so hosts choose the moment)."""
    wb = Gui.activeWorkbench()
    if wb is None:
        return
    try:
        wb.reloadActive()  # rebuilds menus/toolbars of the active workbench, manipulators included
    except Exception:
        try:  # older FreeCAD: activating the same workbench again is a no-op, so switch away and back
            name = wb.name()
            Gui.activateWorkbench("NoneWorkbench")
            Gui.activateWorkbench(name)
        except Exception:
            pass


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
        if cmd.accel:
            # shown in menus/tooltips only: FreeCAD's own command owns the key binding
            act.setToolTip("%s (%s)" % (cmd.tip, cmd.accel))
        else:
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
        return
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
        cmd.accel = SHORTCUTS.get(name, "")
        Gui.addCommand(name, cmd)
    _state["commands"] = cmds
    _state["panel"] = p
    _build_actions(mw)
    p.refreshed.connect(_update_actions)
    host = params.GetBool("HostProvidesToolbar", False)
    if hasattr(Gui, "addWorkbenchManipulator"):
        m = _Manipulator()
        Gui.addWorkbenchManipulator(m)
        _state["manipulator"] = m
        if not host:
            activate_commands()  # menu entries and shortcuts now, not at the next workbench switch
    _apply_toolbar_mode(host)
    p.refresh()
    App.Console.PrintLog("Strata: ready\n")
    for cb in _state.pop("ready_callbacks", []):
        try:
            cb()
        except Exception:
            import traceback
            App.Console.PrintError("Strata: on_ready callback failed:\n%s\n" % traceback.format_exc())
