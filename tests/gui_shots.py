# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI smoke test + screenshots. Run inside FreeCAD GUI:  freecad.exe tests/gui_shots.py

Reads gui_cfg.json next to this file: {"demo": <Gearbox folder>, "out": <screenshot folder>}.
Exercises the panel, every dialog and a real workspace switch, then exits FreeCAD.
"""

import json
import os
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "gui_cfg.json"), encoding="utf-8"))
OUT, DEMO = CFG["out"], CFG["demo"]
os.makedirs(OUT, exist_ok=True)

import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide import QtCore, QtWidgets  # noqa: E402

LOG = open(os.path.join(OUT, "gui_log.txt"), "w", encoding="utf-8")


def log(msg):
    LOG.write(msg + "\n")
    LOG.flush()


def shot(widget, name):
    QtWidgets.QApplication.processEvents()
    ok = widget.grab().save(os.path.join(OUT, name + ".png"))
    log("shot %s %s %dx%d" % (name, "ok" if ok else "FAILED", widget.width(), widget.height()))


steps = []


def step(fn):
    steps.append(fn)
    return fn


def run_next():
    if not steps:
        log("DONE")
        LOG.close()
        os._exit(0)
    fn = steps.pop(0)
    try:
        fn()
        log("step %s ok" % fn.__name__)
    except Exception:
        log("STEP FAILED %s\n%s" % (fn.__name__, traceback.format_exc()))
    QtCore.QTimer.singleShot(int(CFG.get("delay_ms", 900)), run_next)


S = {}


@step
def boot():
    import strata.boot
    strata.boot.install()
    mw = Gui.getMainWindow()
    mw.showNormal()
    mw.resize(1680, 1000)
    S["mw"], S["panel"] = mw, strata.boot.panel()
    log("strata loaded from %s" % os.path.dirname(strata.boot.__file__))
    log("addWorkbenchManipulator: %s" % hasattr(Gui, "addWorkbenchManipulator"))
    Gui.activateWorkbench("PartDesignWorkbench")


@step
def toolbars():
    import strata.boot
    strata.boot._ensure_toolbar()  # raises here if broken, instead of silently inside a timer
    found = [(t.objectName(), t.isVisible(), [a.text() for a in t.actions() if "ersion" in a.text()])
             for t in S["mw"].findChildren(QtWidgets.QToolBar)]
    log("toolbars holding Strata buttons: %s" % [f for f in found if f[2]])


@step
def open_docs():
    d = App.openDocument(os.path.join(DEMO, "Parts", "Bracket.FCStd"))
    App.openDocument(os.path.join(DEMO, "Gearbox.FCStd"))
    mdi = S["mw"].findChild(QtWidgets.QMdiArea)
    for sw in mdi.subWindowList():
        if sw.windowTitle().startswith("Bracket"):
            mdi.setActiveSubWindow(sw)
    gd = Gui.getDocument(d.Name)
    gd.getObject("Body").Visibility = True
    gd.ActiveView.viewIsometric()
    gd.ActiveView.fitAll()
    S["panel"].show()
    S["panel"].refresh()


@step
def main_window():
    log("panel project: %s" % (S["panel"].project.root if S["panel"].project else None))
    shot(S["mw"], "01_main_window")
    shot(S["panel"], "02_panel")


@step
def history_card():
    h = S["panel"].history
    for r in range(h.count()):
        if h.item(r).data(QtCore.Qt.UserRole + 2) is not None and h.item(r).data(QtCore.Qt.UserRole + 2).versions:
            h.setCurrentRow(r)
            h.item(r).setSelected(True)
            break
    shot(S["panel"], "03_history_card")


@step
def version_card():
    g = S["panel"].graph
    node = next(n for n in g.nodes if n.versions and n.versions[0] == "LW-1")
    g.sel = node
    g.update()
    S["panel"]._node_selected(node)
    shot(S["panel"], "04_workspace_version_card")


def show_dialog(dlg, name, width=None):
    if width:
        dlg.resize(width, dlg.sizeHint().height())
    dlg.show()
    QtWidgets.QApplication.processEvents()
    shot(dlg, name)
    dlg.close()


@step
def create_version_dialog():
    from strata.ui import dialogs
    show_dialog(dialogs.CreateVersionDialog(S["panel"].project, S["panel"]), "05_create_version")


@step
def compare_dialog():
    from strata.ui import dialogs
    p = S["panel"].project
    v1 = next(v for v in p.versions() if v.name == "V1")
    choices = [("Main (latest)", p.head()), ("Version V1", v1.sha)]
    dlg = dialogs.CompareDialog(p, choices, v1.sha, p.head(), lambda *a: "", S["panel"])
    dlg.tree.expandAll()
    show_dialog(dlg, "06_compare", 760)


@step
def merge_dialog():
    from strata.ui import dialogs
    p = S["panel"].project
    plan = p.merge_plan("Lightweight")
    log("merge plan: take=%s conflicts=%s" % (plan.take_theirs, plan.conflicts))
    show_dialog(dialogs.MergeDialog(p, plan, S["panel"]), "07_merge")


@step
def release_dialog():
    from strata.ui import dialogs
    p = S["panel"].project
    v2 = next(v for v in p.versions() if v.name == "V2")
    show_dialog(dialogs.ReleaseDialog(p, v2, {"Parts/Bracket.FCStd": "BRK-100", "Parts/Shaft.FCStd": "SFT-200"},
                                      S["panel"]), "08_release", 700)
    show_dialog(dialogs.ReleasesDialog(S["panel"].project, None, S["panel"]), "09_releases", 860)


@step
def workspace_menu():
    m = S["panel"].ws_menu
    S["panel"]._fill_ws_menu()
    m.popup(S["panel"].ws_button.mapToGlobal(QtCore.QPoint(0, S["panel"].ws_button.height())))
    QtWidgets.QApplication.processEvents()
    shot(m, "10_workspace_menu")
    m.hide()


@step
def switch_to_lightweight():
    from strata import fc
    p = S["panel"].project
    mods = fc.unsaved(p)
    log("modified before switch: %s" % [d.Label for d in mods])
    S["accept_save"] = True  # the watchdog clicks "Save and continue" like a user would
    S["panel"].switch_workspace("Lightweight")
    log("modified after switch: %s" % [d.Label for d in fc.unsaved(p)])
    log("workspace now: %s" % p.current_workspace())
    d = App.ActiveDocument
    log("after switch active=%s pad=%s" % (d.Label if d else None,
                                            d.getObject("Pad").Length if d and d.getObject("Pad") else None))


@step
def after_switch():
    shot(S["mw"], "11_lightweight_workspace")


@step
def back_to_main():
    S["panel"].switch_workspace("main")


@step
def dark_panel():
    """Render a second panel under a dark palette, as FreeCAD's dark theme sets it."""
    from PySide import QtGui
    from strata.ui.panel import Panel
    app = QtWidgets.QApplication.instance()
    S["light_palette"] = QtGui.QPalette(app.palette())
    pal = QtGui.QPalette()
    for role, col in ((QtGui.QPalette.Window, "#2B2F36"), (QtGui.QPalette.WindowText, "#E6EAF0"),
                      (QtGui.QPalette.Base, "#1E2228"), (QtGui.QPalette.Text, "#E6EAF0"),
                      (QtGui.QPalette.Button, "#353B44"), (QtGui.QPalette.ButtonText, "#E6EAF0")):
        pal.setColor(role, QtGui.QColor(col))
    app.setPalette(pal)
    d = Panel(S["mw"])
    d.setFloating(True)
    d.resize(380, 900)
    d.show()
    d.project = S["panel"].project
    d.refresh()
    S["dark"] = d


@step
def dark_shot():
    S["dark"].graph.sel = S["dark"].graph.nodes[1]
    S["dark"]._node_selected(S["dark"].graph.nodes[1])
    shot(S["dark"], "13_dark_panel")
    S["dark"].close()
    QtWidgets.QApplication.instance().setPalette(S["light_palette"])


@step
def empty_state():
    App.newDocument("Unsaved")
    S["panel"].refresh()


@step
def empty_shot():
    shot(S["panel"], "12_empty_state")


def watchdog():
    """An unattended run cannot answer dialogs: record any modal, then dismiss it."""
    w = QtWidgets.QApplication.activeModalWidget()
    if w is not None:
        n = S.setdefault("modals", 0) + 1
        S["modals"] = n
        text = w.text() if hasattr(w, "text") else w.windowTitle()
        info = w.informativeText() if hasattr(w, "informativeText") else ""
        log("MODAL %d: %s | %s | %s" % (n, type(w).__name__, text, info))
        shot(w, "modal_%02d" % n)
        if S.get("accept_save") and isinstance(w, QtWidgets.QMessageBox) and "Save changes" in text:
            btn = next(b for b in w.buttons() if w.buttonRole(b) == QtWidgets.QMessageBox.AcceptRole)
            log("watchdog: clicking %r" % btn.text())
            btn.click()
        else:
            w.reject() if hasattr(w, "reject") else w.close()


_wd = QtCore.QTimer()
_wd.timeout.connect(watchdog)
_wd.start(1500)
QtCore.QTimer.singleShot(2500, run_next)
