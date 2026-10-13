# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI smoke test that also produces the README screenshots.

    freecad tests/gui_shots.py

Reads gui_cfg.json next to this file: {"demo_parent": <folder>, "out": <screenshot folder>}.
Builds the demo project inside the GUI, drives the panel, every dialog, a 3D compare, a read-only
preview and a real workspace switch, logs to <out>/gui_log.txt, then exits FreeCAD.
Any modal dialog is logged, captured and dismissed so an unattended run never hangs.
"""

import json
import os
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "gui_cfg.json"), encoding="utf-8"))
OUT = CFG["out"]
os.makedirs(OUT, exist_ok=True)

import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide import QtCore, QtGui, QtWidgets  # noqa: E402

LOG = open(os.path.join(OUT, "gui_log.txt"), "w", encoding="utf-8")
S = {}
steps = []


def log(msg):
    LOG.write(msg + "\n")
    LOG.flush()


def shot(widget, name):
    if "panel" in S:
        S["panel"].toast.hide()
    for dock in S["mw"].findChildren(QtWidgets.QDockWidget) if "mw" in S else []:
        if dock.windowTitle() == "Tasks":
            dock.hide()  # a fresh --user-cfg leaves FreeCAD's Tasks panel overlaid on the 3D view
    QtWidgets.QApplication.processEvents()
    ok = widget.grab().save(os.path.join(OUT, name + ".png"))
    log("shot %s %s %dx%d" % (name, "ok" if ok else "FAILED", widget.width(), widget.height()))


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


def activate(doc_name, iso=True):
    mdi = S["mw"].findChild(QtWidgets.QMdiArea)
    for sw in mdi.subWindowList():
        if sw.windowTitle().startswith(doc_name):
            mdi.setActiveSubWindow(sw)
    gd = Gui.getDocument(App.getDocument(doc_name).Name) if doc_name in App.listDocuments() else None
    view = gd.ActiveView if gd else None
    if view is not None and iso:
        view.viewIsometric()
        view.fitAll()


def doc_by_label(prefix):
    return next((d for d in App.listDocuments().values() if d.Label.startswith(prefix)), None)


def show_dialog(dlg, name, width=None):
    if width:
        dlg.resize(width, dlg.sizeHint().height())
    dlg.show()
    QtWidgets.QApplication.processEvents()
    shot(dlg, name)
    dlg.close()


def version(name):
    return next(v for v in S["p"].versions() if v.name == name)


# ---------------------------------------------------------------------------- steps
@step
def boot():
    import strata.boot
    # Strata must have started on its own from InitGui.py; never start it here, or a broken
    # startup would go unnoticed (it did once: InitGui raised NameError and every run still passed).
    if strata.boot.panel() is None:
        log("STARTUP FAILED: Strata did not start from InitGui.py")
        LOG.close()
        os._exit(4)
    mw = Gui.getMainWindow()
    mw.showNormal()
    mw.resize(1680, 1000)
    S["mw"], S["panel"] = mw, strata.boot.panel()
    Gui.activateWorkbench("PartDesignWorkbench")


@step
def build_demo():
    import sys
    sys.path.insert(0, HERE)
    from make_demo import build
    S["root"] = build(CFG["demo_parent"])
    log("demo at %s" % S["root"])


@step
def open_project():
    root = S["root"]
    b = App.openDocument(os.path.join(root, "Parts", "Bracket.FCStd"))
    log("bracket volume %.1f" % b.getObject("Body").Shape.Volume)
    App.openDocument(os.path.join(root, "Gearbox.FCStd"))
    activate("Gearbox")
    S["panel"].show()
    S["panel"].refresh()
    S["p"] = S["panel"].project


@step
def overview():
    shot(S["mw"], "01_overview")


@step
def history_entry():
    h = S["panel"].history
    for r in range(h.count()):
        e = h.item(r).data(QtCore.Qt.UserRole + 2)
        if e is not None and e.versions == ["V2"]:
            h.setCurrentRow(r)
            h.item(r).setSelected(True)
    shot(S["panel"], "02_history_entry")
    S["panel"]._close_card()


@step
def workspace_menu():
    m = S["panel"].ws_menu
    S["panel"]._fill_ws_menu()
    m.popup(S["panel"].ws_button.mapToGlobal(QtCore.QPoint(0, S["panel"].ws_button.height())))
    QtWidgets.QApplication.processEvents()
    shot(m, "03_workspace_menu")
    m.hide()


@step
def create_version_dialog():
    from strata.ui import dialogs
    show_dialog(dialogs.CreateVersionDialog(S["p"], S["panel"]), "04_create_version")


@step
def compare_dialog():
    from strata.ui import dialogs
    p = S["p"]
    choices = [("Main (latest)", p.head()), ("Version V1", version("V1").sha), ("Version LW-1", version("LW-1").sha)]
    dlg = dialogs.CompareDialog(p, choices, version("V1").sha, p.head(), lambda *a: "", S["panel"])
    dlg.tree.expandAll()
    show_dialog(dlg, "05_compare", 760)


@step
def compare_3d():
    from strata import fc
    stats = fc.compare_overlay(S["p"], version("V2").sha, version("LW-1").sha, "Parts/Bracket.FCStd", "V2", "LW-1")
    log("overlay: " + stats)
    gd = Gui.ActiveDocument
    gd.ActiveView.viewIsometric()
    gd.ActiveView.fitAll()


@step
def compare_3d_shot():
    shot(S["mw"], "06_compare_3d")
    App.closeDocument(App.ActiveDocument.Name)
    activate("Gearbox")


@step
def preview_v1():
    S["panel"].view(version("V1").sha, "V1", ["Gearbox.FCStd"])
    gd = Gui.ActiveDocument
    gd.ActiveView.viewIsometric()
    gd.ActiveView.fitAll()


@step
def preview_shot():
    shot(S["mw"], "07_view_old_version")
    d = App.ActiveDocument
    log("preview label: %s" % d.Label)
    App.closeDocument(d.Name)
    activate("Gearbox")


@step
def switch_to_lightweight():
    S["accept_save"] = True
    S["panel"].switch_workspace("Lightweight")
    log("workspace now: %s" % S["p"].current_workspace())
    activate("Bracket")


@step
def lightweight_shot():
    graph = S["panel"].graph
    node = next(n for n in graph.nodes if n.versions == ["LW-1"])
    graph.sel = node
    S["panel"]._node_selected(node)
    shot(S["mw"], "08_workspace")
    S["panel"]._close_card()


@step
def back_to_main_and_merge_dialog():
    from strata.ui import dialogs
    S["panel"].switch_workspace("main")
    plan = S["p"].merge_plan("Lightweight")
    show_dialog(dialogs.MergeDialog(S["p"], plan, S["panel"]), "09_merge")


@step
def release_dialogs():
    from strata.ui import dialogs
    show_dialog(dialogs.ReleaseDialog(S["p"], version("V2"), {"Parts/Bracket.FCStd": "BRK-100",
                                                               "Parts/Shaft.FCStd": "SFT-200"}, S["panel"]),
                "10_release", 700)
    show_dialog(dialogs.ReleasesDialog(S["p"], None, S["panel"]), "11_releases", 860)


@step
def properties_flow():
    """Onshape tutorial: How to keep your product designs organized (Workspace properties)."""
    from strata import fc
    from strata.ui import dialogs
    p = S["p"]
    fc.set_document_properties(p, {
        "Parts/Bracket.FCStd": {"Part number": "BRK-100", "Description": "Mounting bracket", "Material": "S235"},
        "Parts/Shaft.FCStd": {"Part number": "SFT-200", "Description": "Drive shaft", "Material": "42CrMo4"},
        "Gearbox.FCStd": {"Part number": "GBX-001", "Description": "Gearbox assembly"}})
    log("properties entry: %s" % p.history()[0].title)
    log("bracket properties: %s" % p.document_properties("Parts/Bracket.FCStd"))
    S["panel"].refresh()
    show_dialog(dialogs.PropertiesDialog(p, S["panel"]), "19_properties", 820)


@step
def restore_dialog():
    """Onshape: Restore with a per-tab strategy and a preview of the changes."""
    from strata.ui import dialogs
    p = S["p"]
    show_dialog(dialogs.RestoreDialog(p, version("V1").sha, "V1", None, S["panel"], compare_cb=lambda: None),
                "20_restore", 620)


@step
def merge_flow():
    """Onshape: Merging (strategy, per-tab choice, compare with base, revert link)."""
    from strata import fc
    from strata.ui import dialogs
    p = S["p"]
    plan = p.merge_plan("Lightweight")
    log("merge plan: theirs=%s ours_only=%s conflicts=%s combine=%s" % (
        plan.take_theirs, plan.ours_only, plan.conflicts, plan.auto_merge))
    log("unsaved before merge: %s" % [d.Label for d in fc.unsaved(p)])
    show_dialog(dialogs.MergeDialog(p, plan, S["panel"], compare_cb=lambda: None), "09_merge", 680)
    changed = p.merge(plan, strategy="merge")
    fc.reload(p, changed)
    panel = S["panel"]
    panel._sticky = ("merge", "Merged Lightweight into Main.", "Undo merge", panel.undo_merge)
    panel.refresh()
    log("after merge: %s; can undo: %s" % (p.history()[0].title, p.can_undo_merge()))


@step
def merge_banner_shot():
    if not S["p"].can_undo_merge():
        raise RuntimeError("no merge to undo")
    shot(S["panel"], "21_undo_merge")
    S["panel"].undo_merge()
    log("after undo: latest entry %r; banner visible: %s" % (S["p"].history()[0].title,
                                                              S["panel"].banner.isVisible()))


@step
def revision_flow():
    """Onshape: Release management, Revision history and obsoleting a revision."""
    from strata.ui import dialogs
    p = S["p"]
    v3 = p.create_version("V3", "Production release")
    rel_b = p.release(v3, "Parts/Bracket.FCStd", "BRK-100", description="Production", approval=True)
    log("submitted: %s rev %s %s" % (rel_b.part_number, rel_b.revision, rel_b.state))
    p.set_release_state(rel_b, "Released", "Checked against drawing D-100")
    rel_a = next(r for r in p.revision_history("BRK-100") if r.revision == "A")
    show_dialog(dialogs.ObsoleteDialog(rel_a, S["panel"]), "23_obsolete", 520)
    p.set_release_state(rel_a, "Obsolete", "Replaced by rev B")
    log("revision history: %s" % [(r.revision, r.state, r.comment) for r in p.revision_history("BRK-100")])
    S["panel"].refresh()
    show_dialog(dialogs.ReleasesDialog(p, None, S["panel"], on_export=lambda *a: None, part_number="BRK-100"),
                "22_revision_history", 900)


@step
def export_flow():
    from strata import fc
    p = S["p"]
    rel_b = next(r for r in p.revision_history("BRK-100") if r.revision == "B")
    folder = os.path.join(CFG["demo_parent"], "Released")
    files = fc.export_revision(p, rel_b.sha, rel_b.document, folder, "BRK-100_revB")
    log("exported: %s" % [os.path.basename(f) for f in files])


@step
def share_dialog():
    from strata.ui import dialogs
    show_dialog(dialogs.ShareDialog(S["p"], S["panel"]), "12_share")


@step
def tools_menu():
    mw = S["mw"]
    tools = next(a for a in mw.menuBar().actions() if a.text().replace("&", "") == "Tools")
    m = tools.menu()
    m.popup(mw.mapToGlobal(QtCore.QPoint(200, 30)))
    QtWidgets.QApplication.processEvents()
    shot(m, "16_tools_menu")
    m.hide()


@step
def settings_and_join():
    from strata.ui import dialogs
    show_dialog(dialogs.SettingsDialog(S["p"], S["panel"]), "17_settings", 520)
    show_dialog(dialogs.JoinDialog(S["panel"]), "18_join", 560)


@step
def dark_panel():
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
    d.resize(400, 860)
    d.show()
    d.project = S["p"]
    d.refresh()
    S["dark"] = d


@step
def dark_shot():
    shot(S["dark"], "13_dark_theme")
    S["dark"].close()
    QtWidgets.QApplication.instance().setPalette(S["light_palette"])


@step
def untracked_doc():
    folder = os.path.join(CFG["demo_parent"], "Sandbox")
    os.makedirs(folder, exist_ok=True)
    d = App.newDocument("Housing")
    d.addObject("Part::Box", "Box")
    d.recompute()
    d.saveAs(os.path.join(folder, "Housing.FCStd"))
    S["panel"].refresh()


@step
def onboarding():
    from strata.ui import dialogs
    shot(S["panel"], "14_turn_on")
    show_dialog(dialogs.StartDialog(os.path.join(CFG["demo_parent"], "Sandbox"), "Ada Lovelace", S["panel"]),
                "15_turn_on_dialog", 560)


def watchdog():
    """An unattended run cannot answer dialogs: record any modal, then answer or dismiss it."""
    w = QtWidgets.QApplication.activeModalWidget()
    if w is None:
        return
    n = S["modals"] = S.get("modals", 0) + 1
    text = w.text() if hasattr(w, "text") else w.windowTitle()
    log("MODAL %d: %s | %s" % (n, type(w).__name__, text))
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
