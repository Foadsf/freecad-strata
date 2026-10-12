# SPDX-License-Identifier: LGPL-2.1-or-later
"""End-to-end Addon Manager install test. Run inside the FreeCAD GUI, twice:

    freecad --user-cfg <tmp>/user.cfg tests/am_install_test.py      (phase from am_cfg.json)

am_cfg.json next to this file: {"phase": "install" | "verify", "out": <folder>, "repo": <url>, "branch": "main"}

install: adds the repository under Preferences > Addon Manager > Custom repositories (same preference
         the dialog writes), opens Tools > Addon manager, types "Strata" in the search box, clicks the
         list entry and the Install button with real mouse events, answers the dialogs like a user.
verify:  a fresh FreeCAD start; checks Strata was loaded from the installed folder and that a save
         inside a project is recorded in history.
Every modal dialog is logged, captured and answered. Results go to <out>/am_<phase>.log.
"""

import json
import os
import shutil
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(HERE, "am_cfg.json"), encoding="utf-8"))
OUT = CFG["out"]
os.makedirs(OUT, exist_ok=True)
PHASE = CFG["phase"]

import FreeCAD as App  # noqa: E402
import FreeCADGui as Gui  # noqa: E402
from PySide import QtCore, QtWidgets  # noqa: E402

LOG = open(os.path.join(OUT, "am_%s.log" % PHASE), "w", encoding="utf-8")
S = {"t0": time.time(), "modals": 0, "state": "start"}
# The Addon Manager names a custom repository after the last URL segment and installs it there.
NAME = CFG["repo"].rstrip("/").split("/")[-1].split(".git")[0]
MOD = os.path.join(App.getUserAppDataDir(), "Mod", NAME)


def log(msg):
    LOG.write("[%5.1fs] %s\n" % (time.time() - S["t0"], msg))
    LOG.flush()


def finish(ok, why):
    log(("RESULT PASS: " if ok else "RESULT FAIL: ") + why)
    LOG.close()
    os._exit(0 if ok else 3)


def shot(widget, name):
    try:
        widget.grab().save(os.path.join(OUT, name + ".png"))
        log("shot " + name)
    except Exception as exc:
        log("shot failed %s: %s" % (name, exc))


def click(widget, pos=None):
    from PySide6 import QtTest  # FreeCAD's PySide shim does not expose QtTest
    QtTest.QTest.mouseClick(widget, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                            pos if pos is not None else widget.rect().center())


# ------------------------------------------------------------------ dialogs
def watchdog():
    w = QtWidgets.QApplication.activeModalWidget()
    if w is None or w.objectName() == "AddonManager_Main_Window":
        return
    visible = [b.text().replace("&", "").lower() for b in w.findChildren(QtWidgets.QAbstractButton)
               if b.isVisible() and b.text()]
    if visible == ["cancel"]:  # a progress dialog: a user just waits
        if S.get("progress") is not w:
            S["progress"] = w
            log("progress dialog %r: waiting" % w.windowTitle())
        return
    S["modals"] += 1
    n = S["modals"]
    text = (w.text() if hasattr(w, "text") else "") or w.windowTitle()
    buttons = [b.text() for b in w.findChildren(QtWidgets.QAbstractButton) if b.isVisible() and b.text()]
    log("MODAL %d %s %r | %r | buttons=%s" % (n, type(w).__name__, w.windowTitle(), text[:200], buttons))
    shot(w, "am_%s_modal_%02d" % (PHASE, n))
    if "restart" in text.lower() and isinstance(w, QtWidgets.QMessageBox):
        later = [b for b in w.buttons() if w.buttonRole(b) in (QtWidgets.QMessageBox.RejectRole,
                                                                 QtWidgets.QMessageBox.NoRole)]
        if later:
            log("answer: %r (restart later; the test restarts FreeCAD itself)" % later[0].text())
            later[0].click()
            return
    for wanted in ("continue", "ok", "yes", "install"):
        for b in w.findChildren(QtWidgets.QAbstractButton):
            if b.isVisible() and b.isEnabled() and b.text().replace("&", "").lower().startswith(wanted):
                log("answer: %r" % b.text())
                b.click()
                return
    if hasattr(w, "accept"):
        log("answer: accept()")
        w.accept()


# ------------------------------------------------------------------ install phase
def am():
    import AddonManager
    return getattr(AddonManager, "INSTANCE", None)


def tick_install():
    try:
        st = S["state"]
        inst = am()
        if st == "start":
            if PHASE == "install" and os.path.exists(MOD):
                finish(False, "%s already exists before the test" % MOD)
            if PHASE == "uninstall" and not os.path.isdir(MOD):
                finish(False, "%s is not installed, nothing to uninstall" % MOD)
            pref = App.ParamGet("User parameter:BaseApp/Preferences/Addons")
            pref.SetString("CustomRepositories", "%s %s\n" % (CFG["repo"], CFG["branch"]))
            log("custom repository set: %r" % pref.GetString("CustomRepositories"))
            S["state"] = "open"
            QtCore.QTimer.singleShot(0, lambda: Gui.runCommand("Std_AddonMgr"))
            log("Tools > Addon manager")
        elif st == "open":
            if inst is not None and inst.item_model is not None and inst.dialog is not None:
                names = [r.name for r in inst.item_model.repos]
                # the list ignores input until loading has finished
                if NAME in names and inst.composite_view.package_list.isEnabled():
                    log("list loaded: %d addons, %s present" % (len(names), NAME))
                    S["state"] = "search"
                elif time.time() - S["t0"] > 240:
                    finish(False, "%s not listed after 240 s (%d addons loaded)" % (NAME, len(names)))
        elif st == "search":
            pl = inst.composite_view.package_list
            edit = pl.ui.view_bar.search.filter_line_edit
            edit.setFocus()
            edit.clear()
            from PySide6 import QtTest  # FreeCAD's PySide shim does not expose QtTest
            QtTest.QTest.keyClicks(edit, "strata")
            log("search box text: %r" % edit.text())
            S["state"] = "select"
        elif st == "select":
            pl = inst.composite_view.package_list
            view = pl.ui.listPackages
            model = view.model()
            rows = [model.index(r, 0) for r in range(model.rowCount())]
            hit = None
            for idx in rows:
                repo = pl.item_model.repos[pl.item_filter.mapToSource(idx).row()]
                if repo.name == NAME:
                    hit, addon = idx, repo
            if hit is None:
                log("visible rows after search: %d, Strata not among them" % len(rows))
                return
            log("addon: name=%s url=%s branch=%s status=%s" % (addon.name, addon.url, addon.branch,
                                                                 addon.status()))
            meta = addon.metadata
            if meta is not None:
                log("metadata: version=%s license=%s content=%s" % (
                    meta.version, [lic.name for lic in meta.license], list(meta.content)))
            view.scrollTo(hit)
            click(view.viewport(), view.visualRect(hit).center())
            S["addon"] = addon
            S["state"] = "details"
        elif st == "details":
            bar = inst.composite_view.package_details_controller.ui.button_bar
            btn = bar.uninstall if PHASE == "uninstall" else bar.install
            log("%s button visible=%s enabled=%s" % (btn.text(), btn.isVisible(), btn.isEnabled()))
            shot(inst.dialog, "am_details_before_%s" % PHASE)
            if btn.isVisible() and btn.isEnabled():
                click(btn)
                S["installing_since"] = time.time()
                S["state"] = "uninstalling" if PHASE == "uninstall" else "installing"
            elif S.setdefault("details_tries", 0) > 10:
                finish(False, "Install button never became visible after selecting the addon")
            else:
                S["details_tries"] += 1
        elif st == "installing":
            done = inst.installer_gui is None and os.path.isdir(MOD)
            if done:
                log("installed folder: %s" % MOD)
                log("contents: %s" % sorted(os.listdir(MOD))[:20])
                log("is git checkout: %s" % os.path.isdir(os.path.join(MOD, ".git")))
                log("addon status now: %s" % S["addon"].status())
                shot(inst.dialog, "am_details_after_install")
                S["state"] = "close"
            elif time.time() - S["installing_since"] > 240:
                finish(False, "install did not finish in 240 s")
        elif st == "uninstalling":
            if inst.installer_gui is None and not os.path.exists(MOD):
                manifest = os.path.join(os.path.dirname(MOD), "manifest.json")
                listed = os.path.isfile(manifest) and ('"%s"' % NAME) in open(manifest, encoding="utf-8").read()
                log("folder removed; still in Addon Manager manifest: %s" % listed)
                shot(inst.dialog, "am_details_after_uninstall")
                inst.dialog.reject()
                QtCore.QTimer.singleShot(2000, lambda: finish(not listed, "Addon Manager removed %s" % NAME
                                                              if not listed else "manifest still lists it"))
                S["state"] = "done"
            elif time.time() - S["installing_since"] > 120:
                finish(False, "uninstall did not finish in 120 s")
        elif st == "close":
            inst.dialog.reject()
            S["state"] = "closed"
            S["closed_at"] = time.time()
        elif st == "closed" and time.time() - S["closed_at"] > 3:
            ok = os.path.isfile(os.path.join(MOD, "InitGui.py")) and os.path.isfile(os.path.join(MOD, "package.xml"))
            finish(ok, "Addon Manager installed Strata into %s" % MOD if ok else "installed folder incomplete")
    except Exception:
        finish(False, "exception in state %s:\n%s" % (S["state"], traceback.format_exc()))


# ------------------------------------------------------------------ verify phase
def tick_verify():
    try:
        import strata
        import strata.boot
        from strata.project import Project
        loaded_from = os.path.dirname(os.path.dirname(os.path.abspath(strata.__file__)))
        log("strata %s loaded from %s" % (strata.__version__, loaded_from))
        if os.path.normcase(os.path.realpath(loaded_from)) != os.path.normcase(os.path.realpath(MOD)):
            finish(False, "Strata was not loaded from the Addon Manager folder")
        p = strata.boot.panel()
        log("panel present=%s visible=%s" % (p is not None, p.isVisible() if p else None))
        import sys
        log("workbenches with Strata: %s" % [w for w in Gui.listWorkbenches() if "trata" in w])
        log("boot state keys: %s; InitGui-ish modules: %s" % (
            sorted(strata.boot._state), [m for m in sys.modules if "strata" in m.lower()]))
        if p is None:
            finish(False, "panel not installed at startup")
        root = tempfile.mkdtemp(prefix="strata-am-verify-")
        doc = App.newDocument("Probe")
        box = doc.addObject("Part::Box", "Box")
        doc.recompute()
        path = os.path.join(root, "Probe.FCStd")
        doc.saveAs(path)
        proj = Project.create(root, "Tester", "tester@example.com")
        box.Length = 42
        doc.recompute()
        doc.save()  # the save observer records this asynchronously
        S["verify"] = (proj, root, p)
        QtCore.QTimer.singleShot(2500, verify_after_save)
    except Exception:
        finish(False, "exception:\n" + traceback.format_exc())


def verify_after_save():
    proj, root, panel = S["verify"]
    titles = [e.title for e in proj.history()]
    log("history: %s" % titles)
    panel.show()
    panel.refresh()
    QtWidgets.QApplication.processEvents()
    shot(Gui.getMainWindow(), "am_verify_main")
    ok = bool(titles) and titles[0].startswith("Edited Box: Length 10 mm → 42 mm")
    proj.repo.close()
    shutil.rmtree(root, ignore_errors=True)
    finish(ok, "installed Strata records saves" if ok else "save was not recorded as expected")


_wd = QtCore.QTimer()
_wd.timeout.connect(watchdog)
_wd.start(700)
if PHASE in ("install", "uninstall"):
    _t = QtCore.QTimer()
    _t.timeout.connect(tick_install)
    QtCore.QTimer.singleShot(3000, lambda: _t.start(1000))
else:
    QtCore.QTimer.singleShot(4000, tick_verify)
QtCore.QTimer.singleShot(600 * 1000, lambda: finish(False, "global timeout"))
