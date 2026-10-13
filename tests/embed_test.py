# SPDX-License-Identifier: LGPL-2.1-or-later
"""GUI test of the embedding API (strata.boot.configure/on_ready/actions, theme.set_overrides).

    freecad --user-cfg <tmp>/user.cfg tests/embed_test.py     (twice: phase "live", then "prestart")

embed_cfg.json next to this file: {"phase": "live" | "prestart", "out": <folder>}.
Strata must have started on its own; the test never calls install(). Use a throwaway --user-cfg:
phase "live" stores preferences that phase "prestart" then checks at startup.
"""

import json
import os
import shutil
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "embed_cfg.json"), encoding="utf-8"))
PHASE = CFG["phase"]

import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide import QtCore  # noqa: E402

LOG = open(os.path.join(CFG["out"], "embed_%s.log" % PHASE), "w", encoding="utf-8")
FAILS = []


def check(cond, msg):
    LOG.write(("PASS " if cond else "FAIL ") + msg + "\n")
    LOG.flush()
    if not cond:
        FAILS.append(msg)


def finish():
    App.saveParameter()  # os._exit skips FreeCAD's own save of user.cfg
    LOG.write("RESULT %s (%d failed)\n" % ("PASS" if not FAILS else "FAIL", len(FAILS)))
    LOG.close()
    os._exit(0 if not FAILS else 3)


def later(ms, fn):
    def run():
        try:
            fn()
        except Exception:
            check(False, "exception:\n" + traceback.format_exc())
            finish()
    QtCore.QTimer.singleShot(ms, run)


def area():
    import strata.boot as sb
    return {QtCore.Qt.LeftDockWidgetArea: "left", QtCore.Qt.RightDockWidgetArea: "right"}.get(
        Gui.getMainWindow().dockWidgetArea(sb.panel()), "other")


def fallback_visible():
    import strata.boot as sb
    tb = sb._state.get("fallback_toolbar")
    return tb is not None and tb.isVisible()


# ------------------------------------------------------------------ phase live
def live_start():
    import strata.boot as sb
    from strata.ui import theme
    check(sb.is_ready() and sb.panel() is not None, "Strata started on its own")
    called = []
    sb.on_ready(lambda: called.append(1))
    check(called == [1], "on_ready runs at once when Strata is already up")
    acts = sb.actions()
    check(sorted(acts) == sorted(sb.COMMAND_NAMES), "actions() returns the five commands")
    check(acts["Strata_Panel"].isEnabled(), "Versions and history action enabled without a project")
    check(not acts["Strata_CreateVersion"].isEnabled(), "Create version disabled without a project")
    check(fallback_visible(), "default mode provides a toolbar")
    sb.configure(host_toolbar=True, panel_area="left")
    check(not fallback_visible(), "host_toolbar=True hides Strata's toolbar")
    check(not sb._state["toolbar_timer"].isActive(), "host_toolbar=True stops the toolbar timer")
    check(area() == "left", "panel_area='left' docks the panel left")
    from PySide import QtWidgets
    real_all = QtWidgets.QApplication.allWidgets
    walked = []
    QtWidgets.QApplication.allWidgets = staticmethod(lambda: walked.append(1) or real_all())
    try:
        theme.set_overrides(light={"accent": "#00838F"})
        calls_by_strata = len(walked)
        QtWidgets.QApplication.instance().allWidgets()  # positive control: the detector must see this
        check(len(walked) == calls_by_strata + 1, "allWidgets() detector works (positive control)")
        del walked[calls_by_strata:]
    finally:
        QtWidgets.QApplication.allWidgets = real_all
    check("#00838F" in sb.panel().styleSheet(), "set_overrides restyles the open panel")
    check("#00838F" in sb.panel().status.styleSheet(), "set_overrides restyles the status pill")
    check(not walked, "set_overrides does not walk QApplication.allWidgets()")
    try:
        theme.set_overrides(light={"no_such_token": "#000"})
        check(False, "set_overrides rejects unknown tokens")
    except KeyError:
        check(True, "set_overrides rejects unknown tokens")
    # a project makes Create version available
    root = tempfile.mkdtemp(prefix="strata-embed-")
    from strata.project import Project
    doc = App.newDocument("Embed")
    doc.addObject("Part::Box", "Box")
    doc.recompute()
    doc.saveAs(os.path.join(root, "Embed.FCStd"))
    Project.create(root, "Tester", "t@example.com")
    sb.panel().refresh()
    check(sb.actions()["Strata_CreateVersion"].isEnabled(), "Create version enabled once a project is active")
    App.closeDocument(doc.Name)
    # tabified behind another dock: raising the other tab must not count as "user hid the panel"
    from PySide import QtWidgets as QW
    mw = Gui.getMainWindow()
    p = sb.panel()
    params = App.ParamGet(sb.PARAMS)
    params.SetBool("PanelVisible", True)
    other = QW.QDockWidget("Other", mw)
    other.setObjectName("EmbedTestOtherDock")
    other.setWidget(QW.QLabel("other"))
    mw.addDockWidget(QtCore.Qt.LeftDockWidgetArea, other)
    p.show()
    mw.tabifyDockWidget(p, other)
    QW.QApplication.processEvents()
    p.raise_()  # Strata's tab in front first, so raising the other one is a real change
    QW.QApplication.processEvents()
    params.SetBool("PanelVisible", True)
    seen = []
    p.visibilityChanged.connect(lambda v: seen.append(v))
    other.raise_()
    QW.QApplication.processEvents()
    check(bool(mw.tabifiedDockWidgets(p)), "panel is tabified with another dock")
    check(False in seen, "raising another tab emits visibilityChanged(False) (the trigger is reproduced)")
    check(params.GetBool("PanelVisible", True), "PanelVisible stays true when another tab is raised")
    p.raise_()
    p.hide()  # a host or Qt hiding the dock is not a user decision
    check(params.GetBool("PanelVisible", True), "programmatic hide() is not remembered")
    p.show()
    p.titlebar.close.click()  # the panel's own close button
    check(not params.GetBool("PanelVisible", True), "closing the panel with its button is remembered")
    sb._toggle_panel()  # the toolbar / menu command
    check(params.GetBool("PanelVisible", False) and p.isVisible(), "re-opening it with the command is remembered")
    mw.removeDockWidget(other)
    other.deleteLater()
    later(4500, lambda: live_end(root))


def live_end(root):
    check(not fallback_visible(), "Strata's toolbar stays hidden after 4.5 s (no flicker)")
    from strata.project import Project
    for p in Project._instances.values():
        p.repo.close()
    shutil.rmtree(root, ignore_errors=True)
    if FAILS:
        finish()
    # a host chooses its own keys; they apply from the next start (checked in phase prestart)
    import strata.boot as sb
    sb.configure(shortcuts={"Strata_Compare": None, "Strata_Sync": "Ctrl+Alt+Shift+Y"})
    try:
        sb.configure(shortcuts={"Strata_NoSuchCommand": "Ctrl+K"})
        check(False, "configure(shortcuts=) rejects unknown commands")
    except KeyError:
        check(True, "configure(shortcuts=) rejects unknown commands")
    # End with a real shutdown (not os._exit) so FreeCAD saves user.cfg itself; record what the dock
    # signals do meanwhile, so the next phase can tell the trigger was reproduced.
    import strata.boot as sb
    p = sb.panel()
    events = open(os.path.join(CFG["out"], "shutdown_events.txt"), "w", encoding="utf-8")

    def rec(kind, v):
        events.write("%s %s\n" % (kind, v))
        events.flush()
    p.toggleViewAction().toggled.connect(lambda v: rec("toggled", v))
    p.visibilityChanged.connect(lambda v: rec("visibilityChanged", v))
    check(App.ParamGet(sb.PARAMS).GetBool("PanelVisible", False), "PanelVisible true just before shutdown")
    LOG.write("RESULT PASS (0 failed) - closing the main window\n")
    LOG.close()
    Gui.getMainWindow().close()


# ------------------------------------------------------------------ phase prestart
def prestart():
    import strata.boot as sb
    check(sb.host_provides_toolbar(), "preference HostProvidesToolbar persisted")
    check(sb.is_ready(), "Strata started on its own")
    check("fallback_toolbar" not in sb._state, "no Strata toolbar was created at startup in host mode")
    check(area() == "left", "panel created in the left dock area")
    ev_path = os.path.join(CFG["out"], "shutdown_events.txt")
    events = open(ev_path, encoding="utf-8").read().split() if os.path.exists(ev_path) else []
    check("False" in events, "dock signals fired False during the previous shutdown (trigger reproduced): %s"
          % " ".join(events))
    check(App.ParamGet(sb.PARAMS).GetBool("PanelVisible", False), "PanelVisible still true after a normal shutdown")
    check(sb.panel().isVisible(), "panel visible at startup after a normal shutdown")
    # host-chosen keys (set in phase live): the host builds the key bindings when it wants
    sb.activate_commands()
    from PySide import QtWidgets as QW
    QW.QApplication.processEvents()
    get = lambda n: Gui.Command.get(n).getShortcut()  # noqa: E731
    check(get("Strata_Panel") == "Ctrl+Shift+H", "keys are bound after activate_commands (positive control): %r"
          % get("Strata_Panel"))
    check(get("Strata_Compare") == "", "host left Compare without a key: %r" % get("Strata_Compare"))
    check(Gui.Command.listByShortcut("Ctrl+Shift+D") == [], "Ctrl+Shift+D is free for the host: %s"
          % Gui.Command.listByShortcut("Ctrl+Shift+D"))
    check(get("Strata_Sync") == "Ctrl+Alt+Shift+Y", "host moved Sync to Ctrl+Alt+Shift+Y: %r" % get("Strata_Sync"))
    check(Gui.Command.listByShortcut("Ctrl+Shift+Y") == [], "Ctrl+Shift+Y is free for the host")
    sb.configure(shortcuts={})
    check(sb.effective_shortcuts() == sb.SHORTCUTS, "configure(shortcuts={}) returns to the defaults")
    sb.configure(host_toolbar=False, panel_area="right")
    later(2500, prestart_end)


def prestart_end():
    check(area() == "right", "panel_area='right' moves it back")
    check(fallback_visible(), "toolbar back after host_toolbar=False")
    finish()


# ------------------------------------------------------------------ phase keys
def keys_start():
    import strata.boot as sb
    from PySide import QtWidgets as QW
    from PySide6 import QtTest
    for name, accel in sb.SHORTCUTS.items():
        got = Gui.Command.get(name).getShortcut()
        check(got == accel, "%s has default shortcut %s (got %r)" % (name, accel, got))
        if hasattr(Gui.Command, "listByShortcut"):
            owners = Gui.Command.listByShortcut(accel)
            check(owners == [name], "%s is used by %s only (owners: %s)" % (accel, name, owners))
    mw = Gui.getMainWindow()
    tools = [a for a in mw.menuBar().actions() if a.text().replace("&", "") == "Tools"]
    entries = [a.text() for a in tools[0].menu().actions() if a.text()] if tools else []
    want = [sb._state["commands"][n].text for n in sb.COMMAND_NAMES]
    check(entries[-len(want):] == want, "Tools menu ends with Strata's commands: %s" % entries[-len(want):])
    mw.activateWindow()
    QW.QApplication.processEvents()
    S_active = QW.QApplication.activeWindow() is mw
    p = sb.panel()
    p.user_set_visible(True)
    if not S_active:
        check(True, "COULD NOT TELL: main window not active on this desktop, key presses not tested")
        finish()
    QtTest.QTest.keyClick(mw, QtCore.Qt.Key_H, QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier)
    QW.QApplication.processEvents()
    check(not p.isVisible(), "Ctrl+Shift+H hides the panel")
    QtTest.QTest.keyClick(mw, QtCore.Qt.Key_H, QtCore.Qt.ControlModifier | QtCore.Qt.ShiftModifier)
    QW.QApplication.processEvents()
    check(p.isVisible(), "Ctrl+Shift+H shows it again")
    # panel keys need a project with history
    root = tempfile.mkdtemp(prefix="strata-keys-")
    from strata.project import Project
    doc = App.newDocument("Keys")
    box = doc.addObject("Part::Box", "Box")
    doc.recompute()
    doc.saveAs(os.path.join(root, "Keys.FCStd"))
    Project.create(root, "Tester", "t@example.com")
    box.Length = 20
    doc.recompute()
    doc.save()
    later(2500, lambda: keys_panel(root, doc))


def keys_panel(root, doc):
    import strata.boot as sb
    from PySide import QtWidgets as QW
    from PySide6 import QtTest
    p = sb.panel()
    p.refresh()
    h = p.history
    rows = [r for r in range(h.count()) if h.item(r).data(QtCore.Qt.UserRole + 2) is not None]
    check(len(rows) >= 2, "history has entries (%d)" % len(rows))
    h.setFocus()
    h.setCurrentRow(rows[0])
    QtTest.QTest.keyClick(h, QtCore.Qt.Key_Down)
    check(h.currentRow() == rows[1], "Down arrow moves to the next history entry")
    activated = []
    h.entry_activated.disconnect()
    h.entry_activated.connect(lambda e: activated.append(e.title))
    QtTest.QTest.keyClick(h, QtCore.Qt.Key_Return)
    check(len(activated) == 1, "Enter on a history entry views it (%s)" % activated)
    QtTest.QTest.keyClick(h, QtCore.Qt.Key_F, QtCore.Qt.ControlModifier)
    QW.QApplication.processEvents()
    check(p.search.isVisible() and p.search.hasFocus(), "Ctrl+F opens the history search")
    QtTest.QTest.keyClicks(p.search, "Length")
    QtTest.QTest.keyClick(p.search, QtCore.Qt.Key_Escape)
    check(p.search.isVisible() and p.search.text() == "", "Esc clears the search")
    QtTest.QTest.keyClick(p.search, QtCore.Qt.Key_Escape)
    QW.QApplication.processEvents()
    check(not p.search.isVisible(), "Esc again closes the search")
    from strata.project import Project
    for proj in Project._instances.values():
        proj.repo.close()
    App.closeDocument(doc.Name)
    shutil.rmtree(root, ignore_errors=True)
    finish()


later(4000, {"live": live_start, "prestart": prestart, "keys": keys_start}[PHASE])
later(120 * 1000, lambda: (check(False, "global timeout"), finish()))
