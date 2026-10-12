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
    check(fallback_visible() or "manipulator" in sb._state, "default mode provides a toolbar")
    sb.configure(host_toolbar=True, panel_area="left")
    check(not fallback_visible(), "host_toolbar=True hides Strata's toolbar")
    check("manipulator" not in sb._state, "host_toolbar=True removes the workbench manipulator")
    check(not sb._state["toolbar_timer"].isActive(), "host_toolbar=True stops the toolbar timer")
    check(area() == "left", "panel_area='left' docks the panel left")
    theme.set_overrides(light={"accent": "#00838F"})
    check("#00838F" in sb.panel().styleSheet(), "set_overrides restyles the open panel")
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
    later(4500, lambda: live_end(root))


def live_end(root):
    check(not fallback_visible(), "Strata's toolbar stays hidden after 4.5 s (no flicker)")
    from strata.project import Project
    for p in Project._instances.values():
        p.repo.close()
    shutil.rmtree(root, ignore_errors=True)
    finish()


# ------------------------------------------------------------------ phase prestart
def prestart():
    import strata.boot as sb
    check(sb.host_provides_toolbar(), "preference HostProvidesToolbar persisted")
    check(sb.is_ready(), "Strata started on its own")
    check("fallback_toolbar" not in sb._state, "no Strata toolbar was created at startup in host mode")
    check("manipulator" not in sb._state, "no workbench manipulator registered in host mode")
    check(area() == "left", "panel created in the left dock area")
    sb.configure(host_toolbar=False, panel_area="right")
    later(2500, prestart_end)


def prestart_end():
    import strata.boot as sb
    check(area() == "right", "panel_area='right' moves it back")
    check(fallback_visible() or "manipulator" in sb._state, "toolbar back after host_toolbar=False")
    finish()


later(4000, live_start if PHASE == "live" else prestart)
later(120 * 1000, lambda: (check(False, "global timeout"), finish()))
