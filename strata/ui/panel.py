# SPDX-License-Identifier: LGPL-2.1-or-later
"""The "Versions and history" panel."""

from __future__ import annotations

import os
import traceback

import FreeCAD as App
from PySide import QtCore, QtGui, QtWidgets

from .. import fc
from ..gitcore import GitError, GitMissing
from ..project import Project, StrataError, default_identity, display_name, friendly_time
from . import dialogs, theme
from .widgets import HistoryList, VersionGraph

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None


def _stem(rel: str) -> str:
    return os.path.splitext(rel.rsplit("/", 1)[-1])[0]


class Toast(QtWidgets.QLabel):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAlignment(QtCore.Qt.AlignCenter)
        self.setWordWrap(True)
        self.hide()
        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def show_text(self, text: str, kind: str = "info"):
        t = theme.tokens()
        bg = {"info": t["node"], "success": t["success"], "error": t["danger"]}[kind]
        fg = t["panel"] if kind == "info" else "#FFFFFF"
        self.setStyleSheet("background:%s; color:%s; border-radius:8px; padding:8px 14px; font-weight:600;" % (bg, fg))
        self.setText(text)
        w = min(self.parent().width() - 24, 420)
        self.setFixedWidth(w)
        self.adjustSize()
        self.move((self.parent().width() - w) // 2, self.parent().height() - self.height() - 52)
        self.show()
        self.raise_()
        self.timer.start(3800)


class TitleBar(QtWidgets.QWidget):
    def __init__(self, panel):
        super().__init__(panel)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 6, 4)
        lay.setSpacing(2)
        ic = QtWidgets.QLabel()
        ic.setPixmap(theme.icon_pixmap("history", theme.tokens()["accent"], 18))
        lay.addWidget(ic)
        lay.addSpacing(6)
        title = theme.label("Versions and history", "title")
        lay.addWidget(title)
        lay.addStretch(1)
        self.compare = theme.make_icon_button("compare", "Compare two points in time")
        self.releases = theme.make_icon_button("release", "Releases")
        self.sync = theme.make_icon_button("sync", "Sync with your team")
        self.more = theme.make_icon_button("more", "More")
        self.close = theme.make_icon_button("close", "Hide panel", 16)
        for b in (self.compare, self.releases, self.sync, self.more, self.close):
            lay.addWidget(b)
        self.close.clicked.connect(panel.hide)


class Panel(QtWidgets.QDockWidget):
    refreshed = QtCore.Signal()  # after every refresh: the active project may have changed

    def __init__(self, mw):
        super().__init__("Versions and history", mw)
        self.setObjectName("StrataPanel")
        self.project: Project | None = None
        self.busy = False
        self._last_sync = None
        self.setStyleSheet(theme.stylesheet())
        self.titlebar = TitleBar(self)
        self.setTitleBarWidget(self.titlebar)
        self.titlebar.compare.clicked.connect(lambda: self.compare())
        self.titlebar.releases.clicked.connect(self.show_releases)
        self.titlebar.sync.clicked.connect(self.sync)
        self.titlebar.more.clicked.connect(self._more_menu)

        body = QtWidgets.QWidget()
        body.setObjectName("StrataBody")
        self.setWidget(body)
        self.stack = QtWidgets.QStackedLayout(body)
        self.stack.addWidget(self._build_empty())
        self.stack.addWidget(self._build_main())
        self.toast = Toast(body)

        self.timer = QtCore.QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.refresh)
        self.status = None

    def changeEvent(self, ev):
        # follow FreeCAD's light/dark theme when it changes the application palette
        if ev.type() == QtCore.QEvent.PaletteChange and not getattr(self, "_restyling", False):
            self._restyling = True
            theme._icon_cache.clear()
            self.setStyleSheet(theme.stylesheet())
            self._restyling = False
            self.schedule_refresh()
        super().changeEvent(ev)

    # ================================================================ build
    def _build_empty(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(28, 40, 28, 28)
        lay.setSpacing(12)
        lay.addStretch(1)
        badge = QtWidgets.QLabel()
        t = theme.tokens()
        px = QtGui.QPixmap(72 * 2, 72 * 2)
        px.setDevicePixelRatio(2)
        px.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(px)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        p.setPen(QtCore.Qt.NoPen)
        p.setBrush(QtGui.QColor(t["accent_soft"]))
        p.drawEllipse(QtCore.QRectF(0, 0, 72, 72))
        p.drawPixmap(QtCore.QPointF(20, 20), theme.icon_pixmap("history", t["accent"], 32))
        p.end()
        badge.setPixmap(px)
        lay.addWidget(badge, 0, QtCore.Qt.AlignHCenter)
        self.empty_title = theme.label("", "title")
        self.empty_title.setAlignment(QtCore.Qt.AlignCenter)
        self.empty_title.setWordWrap(True)
        lay.addWidget(self.empty_title)
        self.empty_text = theme.label("", "caption", wrap=True)
        self.empty_text.setAlignment(QtCore.Qt.AlignCenter)
        lay.addWidget(self.empty_text)
        lay.addSpacing(6)
        self.empty_btn = theme.make_button("Turn on version control", "primary", "check")
        self.empty_btn.clicked.connect(self.start_tracking)
        lay.addWidget(self.empty_btn, 0, QtCore.Qt.AlignHCenter)
        join = QtWidgets.QToolButton()
        join.setProperty("kind", "link")
        join.setText("Open a shared project…")
        join.setCursor(QtCore.Qt.PointingHandCursor)
        join.clicked.connect(self.join_project)
        lay.addWidget(join, 0, QtCore.Qt.AlignHCenter)
        lay.addStretch(2)
        return w

    def _section(self, text: str, extra: QtWidgets.QWidget | None = None):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(14, 6, 10, 2)
        lbl = theme.label(text, "section")
        row.addWidget(lbl)
        row.addStretch(1)
        if extra is not None:
            row.addWidget(extra)
        return row, lbl

    def _build_main(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        head = QtWidgets.QVBoxLayout()
        head.setContentsMargins(14, 4, 12, 10)
        head.setSpacing(8)
        proj_row = QtWidgets.QHBoxLayout()
        proj_row.setSpacing(6)
        self.proj_icon = QtWidgets.QLabel()
        self.proj_icon.setPixmap(theme.icon_pixmap("folder", theme.tokens()["muted"], 14))
        proj_row.addWidget(self.proj_icon)
        self.proj_label = theme.label("", "project")
        proj_row.addWidget(self.proj_label, 1)
        head.addLayout(proj_row)
        row = QtWidgets.QHBoxLayout()
        self.ws_button = QtWidgets.QToolButton()
        self.ws_button.setProperty("kind", "pill")
        self.ws_button.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.ws_button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self.ws_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.ws_button.setToolTip("Workspace: switch, create or merge")
        self.ws_menu = theme.menu(self.ws_button)
        self.ws_menu.aboutToShow.connect(self._fill_ws_menu)
        self.ws_button.setMenu(self.ws_menu)
        row.addWidget(self.ws_button)
        row.addStretch(1)
        self.create_btn = theme.make_button("Create version", "primary", "version")
        self.create_btn.clicked.connect(lambda: self.create_version())
        row.addWidget(self.create_btn)
        head.addLayout(row)
        lay.addLayout(head)

        self.banner = QtWidgets.QFrame()
        self.banner.setProperty("role", "banner")
        bl = QtWidgets.QHBoxLayout(self.banner)
        bl.setContentsMargins(10, 8, 8, 8)
        self.banner_icon = QtWidgets.QLabel()
        bl.addWidget(self.banner_icon, 0, QtCore.Qt.AlignTop)
        self.banner_text = theme.label("", None, wrap=True)
        bl.addWidget(self.banner_text, 1)
        self.banner_btn = QtWidgets.QToolButton()
        self.banner_btn.setProperty("kind", "link")
        self.banner_btn.setCursor(QtCore.Qt.PointingHandCursor)
        bl.addWidget(self.banner_btn, 0, QtCore.Qt.AlignVCenter)
        wrap = QtWidgets.QHBoxLayout()
        wrap.setContentsMargins(12, 0, 12, 8)
        wrap.addWidget(self.banner)
        lay.addLayout(wrap)
        self.banner.hide()
        self._banner_action = None
        self.banner_btn.clicked.connect(lambda: self._banner_action and self._banner_action())

        div = QtWidgets.QFrame()
        div.setProperty("role", "divider")
        lay.addWidget(div)

        self.split = split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(6)
        # versions
        vbox = QtWidgets.QWidget()
        vl = QtWidgets.QVBoxLayout(vbox)
        vl.setContentsMargins(0, 4, 0, 0)
        vl.setSpacing(2)
        self.ver_count = theme.label("", "caption")
        r, self.ver_label = self._section("VERSIONS", self.ver_count)
        vl.addLayout(r)
        self.graph = VersionGraph()
        self.graph_scroll = scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setWidget(self.graph)
        vl.addWidget(scroll, 1)
        split.addWidget(vbox)
        # history
        hbox = QtWidgets.QWidget()
        hl = QtWidgets.QVBoxLayout(hbox)
        hl.setContentsMargins(0, 4, 0, 0)
        hl.setSpacing(2)
        self.search_btn = theme.make_icon_button("search", "Search history", 15)
        self.search_btn.setCheckable(True)
        r, self.hist_label = self._section("HISTORY", self.search_btn)
        hl.addLayout(r)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Filter by feature, person or document")
        self.search.setClearButtonEnabled(True)
        sw = QtWidgets.QHBoxLayout()
        sw.setContentsMargins(12, 2, 12, 4)
        sw.addWidget(self.search)
        hl.addLayout(sw)
        self.search.hide()
        self.search_btn.toggled.connect(self._toggle_search)
        self.history = HistoryList()
        hl.addWidget(self.history, 1)
        split.addWidget(hbox)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        lay.addWidget(split, 1)
        self.search.textChanged.connect(self.history.set_filter)

        # detail card
        self.card = QtWidgets.QFrame()
        self.card.setProperty("role", "card")
        cl = QtWidgets.QVBoxLayout(self.card)
        cl.setContentsMargins(12, 10, 10, 10)
        cl.setSpacing(6)
        top = QtWidgets.QHBoxLayout()
        self.card_title = theme.label("", None, wrap=True)
        f = self.card_title.font()
        f.setBold(True)
        self.card_title.setFont(f)
        top.addWidget(self.card_title, 1)
        close = theme.make_icon_button("close", "Close", 14)
        close.clicked.connect(self._close_card)
        top.addWidget(close, 0, QtCore.Qt.AlignTop)
        cl.addLayout(top)
        self.card_meta = theme.label("", "caption", wrap=True)
        cl.addWidget(self.card_meta)
        self.card_changes = theme.label("", None, wrap=True)
        self.card_changes.setTextFormat(QtCore.Qt.RichText)
        cl.addWidget(self.card_changes)
        self.card_actions = QtWidgets.QHBoxLayout()
        self.card_actions.setSpacing(6)
        cl.addLayout(self.card_actions)
        cw = QtWidgets.QHBoxLayout()
        cw.setContentsMargins(10, 6, 10, 6)
        cw.addWidget(self.card)
        lay.addLayout(cw)
        self.card.hide()

        # footer
        div2 = QtWidgets.QFrame()
        div2.setProperty("role", "divider")
        lay.addWidget(div2)
        foot = QtWidgets.QHBoxLayout()
        foot.setContentsMargins(14, 6, 10, 8)
        self.foot_icon = QtWidgets.QLabel()
        foot.addWidget(self.foot_icon)
        self.foot_text = theme.label("", "caption")
        foot.addWidget(self.foot_text, 1)
        self.foot_link = QtWidgets.QToolButton()
        self.foot_link.setProperty("kind", "link")
        self.foot_link.setCursor(QtCore.Qt.PointingHandCursor)
        self.foot_link.clicked.connect(self.sync)
        foot.addWidget(self.foot_link)
        lay.addLayout(foot)

        self.graph.selected.connect(self._node_selected)
        self.graph.activated.connect(lambda n: self.view(n.sha, self._node_label(n)))
        self.graph.context.connect(self._node_menu)
        self.history.entry_selected.connect(self._entry_selected)
        self.history.entry_activated.connect(lambda e: self.view(e.sha, self._entry_label(e), e.docs))
        self.history.entry_action.connect(self._entry_action)
        self.history.entry_context.connect(self._entry_menu)
        return w

    def _toggle_search(self, on):
        self.search.setVisible(on)
        if on:
            self.search.setFocus()
        else:
            self.search.clear()

    # ================================================================ refresh
    def schedule_refresh(self, *_):
        self.timer.start(120)

    def on_project_change(self, project, sha):
        if sha and project is not None and self.isVisible():
            title = project.history(limit=1)[0].title if project.head() else ""
            self.toast.show_text("Saved to history · " + title[:70], "info")
        self.schedule_refresh()

    def refresh(self):
        if self.busy:
            return
        try:
            self._refresh()
        finally:
            self.refreshed.emit()

    def _refresh(self):
        doc = App.ActiveDocument
        if doc is not None and fc.is_preview(doc):
            proj = self.project  # keep showing the project the preview came from
        else:
            proj = fc.project_for(doc)
        self.project = proj
        self._update_status()
        if proj is None:
            self._show_empty(doc)
            return
        self.stack.setCurrentIndex(1)
        try:
            self._populate(proj, doc)
        except (GitError, StrataError) as exc:
            self._show_banner("warning", "Version history could not be read: %s" % exc, None, None)

    def _show_empty(self, doc):
        self.stack.setCurrentIndex(0)
        if doc is None:
            self.empty_title.setText("Versions and history")
            self.empty_text.setText("Open or save a FreeCAD document to see its versions, workspaces and history.")
            self.empty_btn.hide()
        elif not doc.FileName:
            self.empty_title.setText("Save to get started")
            self.empty_text.setText("Save “%s” into a project folder, then turn on version control for that "
                                    "folder." % doc.Label)
            self.empty_btn.hide()
        else:
            folder = os.path.dirname(doc.FileName)
            self.empty_title.setText("Version control is off")
            self.empty_text.setText("Turn it on for %s and every save is recorded. Create versions for milestones, "
                                    "explore ideas in workspaces and go back in time whenever you need."
                                    % os.path.basename(folder))
            self.empty_btn.show()

    def _populate(self, proj: Project, doc):
        t = theme.tokens()
        cur = proj.current_workspace()
        n_docs = len(proj.documents())
        self.proj_label.setText("%s  ·  %d document%s" % (proj.name, n_docs, "" if n_docs == 1 else "s"))
        self.ws_button.setText(" " + display_name(cur) + "  ▾")
        self.ws_button.setIcon(theme.icon("workspace", theme.lane_color(0 if cur == "main" else 1), 15))
        versions = proj.versions()
        nodes = proj.graph()
        self.graph.set_data(nodes, versions, proj.releases(), cur)
        # show up to five graph rows without scrolling; history gets the rest
        want = min(max(len(nodes), 2), 5) * 48 + 12
        if self.graph_scroll.minimumHeight() != want:
            self.graph_scroll.setMinimumHeight(want)
            total = sum(self.split.sizes()) or 600
            self.split.setSizes([want + 40, max(200, total - want - 40)])
        self.ver_count.setText(str(len(versions)) if versions else "")
        self.hist_label.setText("HISTORY · %s" % display_name(cur).upper())
        self.history.set_entries(proj.history())
        if doc is not None and fc.is_preview(doc):
            what = ("%s. Red is before, blue is after." % doc.Label) if doc.Name in fc._overlays else \
                ("%s. It is read-only: changes here are not part of the project." % doc.Label)
            self._show_banner("eye", "You are viewing " + what, "Close", lambda: self._close_preview(doc))
        else:
            mods = fc.unsaved(proj)
            if mods:
                self._show_banner("save", "Unsaved changes in %s. Save to record them in history."
                                  % ", ".join(d.Label for d in mods[:3]), "Save", lambda: self._save(mods))
            else:
                self.banner.hide()
        url = proj.remote_url()
        if url:
            self.foot_icon.setPixmap(theme.icon_pixmap("share", t["success"], 14))
            when = (" · synced " + friendly_time(self._last_sync)) if self._last_sync else ""
            self.foot_text.setText("Shared%s" % when)
            self.foot_text.setToolTip(url)
            self.foot_link.setText("Sync now")
        else:
            self.foot_icon.setPixmap(theme.icon_pixmap("lock", t["muted"], 14))
            self.foot_text.setText("Only on this computer")
            self.foot_text.setToolTip("")
            self.foot_link.setText("Share…")

    def _show_banner(self, icon_name, text, action_text, action):
        t = theme.tokens()
        self.banner_icon.setPixmap(theme.icon_pixmap(icon_name, t["accent"], 16))
        self.banner_text.setText(text)
        self.banner_btn.setVisible(bool(action_text))
        self.banner_btn.setText(action_text or "")
        self._banner_action = action
        self.banner.show()

    def _update_status(self):
        if self.status is None:
            return
        p = self.project
        if p is None:
            self.status.hide()
            return
        self.status.setText("%s  ·  %s" % (p.name, display_name(p.current_workspace())))
        self.status.setIcon(theme.icon("workspace", theme.tokens()["accent"], 14))
        self.status.show()

    # ================================================================ helpers
    def _run(self, fn, *args, success: str | None = None):
        self.busy = True
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            result = fn(*args)
        except GitMissing:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Strata", "Strata needs Git to store history. Install Git from "
                                                          "https://git-scm.com and restart FreeCAD.")
            return None
        except (StrataError, GitError) as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            QtWidgets.QMessageBox.warning(self, "Strata", str(exc))
            return None
        except Exception as exc:
            QtWidgets.QApplication.restoreOverrideCursor()
            App.Console.PrintError("Strata: %s\n" % traceback.format_exc())
            QtWidgets.QMessageBox.critical(self, "Strata", "Something went wrong: %s\nDetails are in the Report view."
                                           % exc)
            return None
        finally:
            self.busy = False
            if QtWidgets.QApplication.overrideCursor() is not None:
                QtWidgets.QApplication.restoreOverrideCursor()
        if success:
            self.toast.show_text(success, "success")
        self.refresh()
        return result

    def ensure_saved(self) -> bool:
        if self.project is None:
            return False
        mods = fc.unsaved(self.project)
        if not mods:
            return True
        box = QtWidgets.QMessageBox(self)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowTitle("Unsaved changes")
        box.setText("Save changes to %s first?" % ", ".join(d.Label for d in mods))
        box.setInformativeText("Your changes are recorded in history before continuing.")
        save = box.addButton("Save and continue", QtWidgets.QMessageBox.AcceptRole)
        box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not save:
            return False
        fc.save(mods)
        # Saving one document of an assembly can flag its linked partners as modified again;
        # the files on disk are what gets recorded, so one more pass is enough.
        again = fc.unsaved(self.project)
        if again:
            fc.save(again)
        return True

    def _save(self, docs):
        fc.save(docs)
        self.schedule_refresh()

    def _close_preview(self, doc):
        App.closeDocument(doc.Name)
        self.schedule_refresh()

    def _node_label(self, n) -> str:
        if n.versions:
            return n.versions[0]
        if n.workspaces:
            return display_name(n.workspaces[0])
        return "Start" if n.kind == "start" else n.sha[:7]

    def _entry_label(self, e) -> str:
        return e.versions[0] if e.versions else friendly_time(e.time)

    def _active_rel(self) -> str | None:
        doc = App.ActiveDocument
        if doc is not None and self.project and doc.FileName and self.project.contains(doc.FileName):
            return self.project.rel(doc.FileName)
        return None

    def _pick_doc(self, rev: str, prefer: list[str] | None = None) -> str | None:
        docs = self.project.documents(rev)
        if not docs:
            return None
        active = self._active_rel()
        for cand in ([active] if active else []) + list(prefer or []):
            if cand in docs:
                return cand
        if len(docs) == 1:
            return docs[0]
        m = theme.menu(self)
        m.addSection("Which document?")
        for d in docs:
            a = m.addAction(theme.icon("cube"), _stem(d))
            a.setData(d)
        a = m.exec(QtGui.QCursor.pos())
        return a.data() if a else None

    # ================================================================ actions
    def start_tracking(self):
        doc = App.ActiveDocument
        folder = os.path.dirname(doc.FileName) if doc is not None and doc.FileName else os.path.expanduser("~")
        dlg = dialogs.StartDialog(folder, default_identity()[0], self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        if doc is not None and fc.is_modified(doc):
            doc.save()
        user = dlg.user.text().strip() or default_identity()[0]
        self._run(lambda: Project.create(dlg.folder.text().strip(), user, default_identity()[1]),
                  success="Version control is on. Every save is now recorded.")

    def join_project(self):
        dlg = dialogs.JoinDialog(self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        proj = self._run(Project.join, dlg.location.text().strip(), dlg.dest.text().strip())
        if proj:
            for rel in proj.documents()[:5]:
                App.openDocument(proj.abspath(rel))
            self.toast.show_text("Project ready in %s" % proj.root, "success")

    def create_version(self, rev: str | None = None):
        if self.project is None or not self.ensure_saved():
            return
        dlg = dialogs.CreateVersionDialog(self.project, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        name = dlg.name.text().strip()
        self._run(self.project.create_version, name, dlg.desc.toPlainText(), rev,
                  success="Version %s created" % name)

    def new_workspace(self, from_rev: str | None = None, label: str | None = None):
        if self.project is None or not self.ensure_saved():
            return
        base = label or "the latest state of %s" % display_name(self.project.current_workspace())
        dlg = dialogs.NewWorkspaceDialog(self.project, base, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        res = self._run(self.project.create_workspace, dlg.name.text(), from_rev, dlg.desc.text())
        if res:
            fc.reload(self.project, res[1])
            self.toast.show_text("You are now in workspace %s" % display_name(res[0]), "success")
            self.refresh()

    def switch_workspace(self, name: str):
        if self.project is None or name == self.project.current_workspace() or not self.ensure_saved():
            return
        changed = self._run(self.project.switch_workspace, name)
        if changed is not None:
            fc.reload(self.project, changed)
            self.toast.show_text("Switched to %s" % display_name(name), "success")
            self.refresh()

    def view(self, rev: str, label: str, prefer: list[str] | None = None):
        if self.project is None:
            return
        rel = self._pick_doc(rev, prefer)
        if rel:
            proj = self.project
            self._run(fc.open_preview, proj, rev, rel, label)
            self.project = proj
            self.schedule_refresh()

    def restore(self, rev: str, label: str, docs: list[str] | None = None):
        if self.project is None:
            return
        what = ", ".join(_stem(d) for d in docs) if docs else display_name(self.project.current_workspace())
        if not dialogs.ask_restore(self, what, label) or not self.ensure_saved():
            return
        changed = self._run(self.project.restore, rev, docs)
        if changed is not None:
            fc.reload(self.project, changed)
            self.toast.show_text("Restored %s to %s" % (what, label), "success")
            self.refresh()

    def compare(self, a: str | None = None, b: str | None = None):
        if self.project is None:
            return
        p = self.project
        choices = []
        head = p.head()
        cur = display_name(p.current_workspace())
        choices.append(("%s (latest)" % cur, head))
        for w in p.workspaces():
            if not w.current and w.head:
                choices.append(("Workspace %s" % w.display, w.head))
        for v in p.versions():
            choices.append(("Version %s" % v.name, v.sha))
        for e in p.history(limit=60)[1:]:
            choices.append(("%s · %s" % (friendly_time(e.time), e.title[:48]), e.sha))
        if a is None:
            vs = p.versions()
            a = vs[0].sha if vs else (p.history()[-1].sha if p.history() else head)
        dlg = dialogs.CompareDialog(p, choices, a, b or head,
                                    lambda ra, rb, doc, la, lb: fc.compare_overlay(p, ra, rb, doc, la, lb), self)
        dlg.exec()

    def merge(self, source: str):
        if self.project is None or not self.ensure_saved():
            return
        p = self.project
        try:
            p.record()
            plan = p.merge_plan(source)
        except (StrataError, GitError) as exc:
            QtWidgets.QMessageBox.warning(self, "Merge", str(exc))
            return
        if plan.unchanged:
            self.toast.show_text("Nothing to merge: %s has no new changes" % plan.source_label, "info")
            return
        dlg = dialogs.MergeDialog(p, plan, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        changed = self._run(p.merge, plan, dlg.resolutions())
        if changed is not None:
            fc.reload(p, changed)
            self.toast.show_text("Merged %s" % plan.source_label, "success")
            self.refresh()

    def release(self, version_name: str):
        p = self.project
        v = next((v for v in p.versions() if v.name == version_name), None)
        if v is None:
            return
        pns = {}
        for doc, rel in fc.open_docs(p):
            if getattr(doc, "Id", ""):
                pns[rel] = doc.Id
        for r in p.releases():
            pns.setdefault(r.document, r.part_number)
        dlg = dialogs.ReleaseDialog(p, v, pns, self)
        if dlg.exec() != QtWidgets.QDialog.Accepted:
            return
        items = dlg.items()
        approval = dlg.approval.isChecked()

        def go():
            return [p.release(v, it["document"], it["part_number"], it["revision"], it["description"], approval)
                    for it in items]
        rels = self._run(go)
        if rels:
            self.toast.show_text("%s %s" % ("Submitted" if approval else "Released",
                                            ", ".join("%s %s" % (r.part_number, r.revision) for r in rels)), "success")

    def show_releases(self):
        if self.project is None:
            return
        p = self.project
        dialogs.ReleasesDialog(p, lambda rel: self.view(rel.sha, "%s rev %s" % (rel.part_number, rel.revision),
                                                        [rel.document]), self).exec()
        self.refresh()

    def sync(self):
        p = self.project
        if p is None or not self.ensure_saved():
            return
        if not p.remote_url():
            dlg = dialogs.ShareDialog(p, self)
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return
            if self._run(p.set_remote, dlg.location.text()) is None and not p.remote_url():
                return
        res = self._run(p.sync)
        if res is None:
            return
        import time as _t
        self._last_sync = int(_t.time())
        if res.changed_docs:
            fc.reload(p, res.changed_docs)
        parts = []
        if res.pulled or res.merged:
            parts.append("received %s" % ", ".join(display_name(n) for n in res.pulled + res.merged))
        if res.pushed:
            parts.append("sent %s" % ", ".join(display_name(n) for n in res.pushed))
        self.toast.show_text(("Synced: " + "; ".join(parts)) if parts else "Up to date", "success")
        cur = p.current_workspace()
        if cur in res.needs_merge:
            ok = QtWidgets.QMessageBox.question(
                self, "Sync", "A teammate changed the same documents in %s. Review and merge their changes now?"
                % display_name(cur))
            if ok == QtWidgets.QMessageBox.Yes:
                self.merge("refs/remotes/origin/" + cur)
        self.refresh()

    def settings(self):
        p = self.project
        if p is None:
            return
        dlg = dialogs.SettingsDialog(p, self)
        if dlg.exec() == QtWidgets.QDialog.Accepted:
            p.repo.set_identity(dlg.user.text().strip() or "engineer", dlg.email.text().strip() or "unknown@localhost")
            p.save_settings({"record_on_save": dlg.record.isChecked(), "track_binary": dlg.binary.isChecked()})
            self.toast.show_text("Settings saved", "success")

    # ================================================================ menus
    def _fill_ws_menu(self):
        m = self.ws_menu
        m.clear()
        p = self.project
        if p is None:
            return
        m.addSection("Workspaces")
        cur = p.current_workspace()
        for w in p.workspaces():
            a = m.addAction(theme.icon("check" if w.current else "workspace",
                                       theme.tokens()["accent"] if w.current else None, 15),
                            w.display + ("   (from team)" if w.remote_only else ""))
            a.triggered.connect(lambda _=False, n=w.name: self.switch_workspace(n))
        m.addSeparator()
        m.addAction(theme.icon("plus", None, 15), "New workspace…", lambda: self.new_workspace())
        others = [w for w in p.workspaces() if w.name != cur and not w.remote_only]
        if others:
            sub = m.addMenu(theme.icon("merge", None, 15), "Merge into %s" % display_name(cur))
            sub.setObjectName("StrataMenu")
            sub.setStyleSheet(theme.stylesheet())
            for w in others:
                sub.addAction(w.display, lambda n=w.name: self.merge(n))

    def _more_menu(self):
        m = theme.menu(self)
        p = self.project
        if p is not None:
            m.addAction(theme.icon("version"), "Create version…", lambda: self.create_version())
            m.addAction(theme.icon("branch"), "New workspace…", lambda: self.new_workspace())
            m.addAction(theme.icon("compare"), "Compare…", lambda: self.compare())
            m.addAction(theme.icon("release"), "Releases…", self.show_releases)
            m.addSeparator()
            m.addAction(theme.icon("share"), "Share project…" if not p.remote_url() else "Sync now", self.sync)
            m.addAction(theme.icon("settings"), "Project settings…", self.settings)
            m.addSeparator()
        m.addAction(theme.icon("folder"), "Open a shared project…", self.join_project)
        if p is None and App.ActiveDocument is not None and App.ActiveDocument.FileName:
            m.addAction(theme.icon("check"), "Turn on version control…", self.start_tracking)
        m.exec(QtGui.QCursor.pos())

    def _node_menu(self, n, pos):
        m = theme.menu(self)
        label = self._node_label(n)
        cur = self.project.current_workspace()
        if n.workspaces and cur not in n.workspaces:
            ws = n.workspaces[0]
            m.addAction(theme.icon("workspace"), "Switch to %s" % display_name(ws), lambda: self.switch_workspace(ws))
            m.addAction(theme.icon("merge"), "Merge %s into %s" % (display_name(ws), display_name(cur)),
                        lambda: self.merge(ws))
            m.addSeparator()
        m.addAction(theme.icon("eye"), "View %s" % label, lambda: self.view(n.sha, label))
        m.addAction(theme.icon("compare"), "Compare with latest", lambda: self.compare(n.sha, self.project.head()))
        m.addAction(theme.icon("branch"), "Branch to create workspace…", lambda: self.new_workspace(n.sha, label))
        if n.versions:
            m.addAction(theme.icon("release"), "Release %s…" % label, lambda: self.release(n.versions[0]))
        else:
            m.addAction(theme.icon("version"), "Create version here…", lambda: self.create_version(n.sha))
        m.addSeparator()
        m.addAction(theme.icon("restore"), "Restore %s to %s…" % (display_name(cur), label),
                    lambda: self.restore(n.sha, label))
        if n.workspaces and cur not in n.workspaces and n.workspaces[0] != "main":
            ws = n.workspaces[0]
            m.addSeparator()
            m.addAction(theme.icon("close", theme.tokens()["danger"]), "Delete workspace %s…" % display_name(ws),
                        lambda: self._delete_ws(ws))
        m.exec(pos)

    def _delete_ws(self, ws):
        ok = QtWidgets.QMessageBox.question(self, "Delete workspace",
                                            "Delete workspace %s? Its versions are kept." % display_name(ws))
        if ok == QtWidgets.QMessageBox.Yes:
            self._run(self.project.delete_workspace, ws, success="Workspace deleted")

    def _entry_menu(self, e, pos):
        m = theme.menu(self)
        label = self._entry_label(e)
        m.addAction(theme.icon("eye"), "View this point", lambda: self.view(e.sha, label, e.docs))
        m.addAction(theme.icon("compare"), "Compare with latest", lambda: self.compare(e.sha, self.project.head()))
        if e.parents:
            m.addAction(theme.icon("compare"), "Show what changed here", lambda: self.compare(e.parents[0], e.sha))
        m.addSeparator()
        m.addAction(theme.icon("restore"), "Restore to this point…", lambda: self.restore(e.sha, label))
        for d in e.docs[:4]:
            m.addAction(theme.icon("restore"), "Restore only %s…" % _stem(d), lambda d=d: self.restore(e.sha, label, [d]))
        m.addSeparator()
        m.addAction(theme.icon("version"), "Create version here…", lambda: self.create_version(e.sha))
        m.addAction(theme.icon("branch"), "Branch from here…", lambda: self.new_workspace(e.sha, label))
        m.exec(pos)

    def _entry_action(self, name, e):
        if name == "view":
            self.view(e.sha, self._entry_label(e), e.docs)
        else:
            self.restore(e.sha, self._entry_label(e))

    # ================================================================ detail card
    def _clear_actions(self):
        while self.card_actions.count():
            it = self.card_actions.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()  # deleteLater alone leaves it painted until the next event loop pass
                w.setParent(None)
                w.deleteLater()

    def _card_button(self, text, icon_name, cb, kind="secondary"):
        b = theme.make_button(text, kind, icon_name)
        b.clicked.connect(cb)
        self.card_actions.addWidget(b)

    def _close_card(self):
        self.card.hide()
        self.graph.clear_selection()
        self.history.clearSelection()

    def _changes_html(self, rev_a, rev_b, limit=5) -> str:
        t = theme.tokens()
        rows = []
        for doc, d in self.project.diff(rev_a, rev_b).items():
            stem = _stem(doc)
            if d.status in ("added", "removed"):
                rows.append(("success" if d.status == "added" else "danger", "+" if d.status == "added" else "−",
                             "%s (document %s)" % (stem, d.status)))
                continue
            for _, lbl, typ in d.added:
                rows.append(("success", "+", "%s <span style='color:%s'>· %s</span>" % (lbl, t["muted"], stem)))
            for _, lbl, typ in d.removed:
                rows.append(("danger", "−", "%s <span style='color:%s'>· %s</span>" % (lbl, t["muted"], stem)))
            rebuilt = []
            for ch in d.modified:
                bits = ["%s %s → %s" % (p.name, p.old, p.new) for p in ch.props[:2]]
                if ch.renamed_from:
                    bits.insert(0, "renamed from %s" % ch.renamed_from)
                if not bits:  # knock-on rebuilds: summarised in one muted line
                    rebuilt.append(ch.label)
                    continue
                rows.append(("warning", "~", "%s <span style='color:%s'>%s</span>" % (
                    ch.label, t["muted"], "; ".join(bits))))
            if rebuilt:
                rows.append(("faint", "·", "<span style='color:%s'>Rebuilt %s</span>" % (
                    t["muted"], ", ".join(rebuilt[:4]) + (" …" if len(rebuilt) > 4 else ""))))
        if not rows:
            return "<span style='color:%s'>No model changes</span>" % t["muted"]
        rows.sort(key=lambda r: r[0] == "faint")
        html = "".join("<div style='margin:1px 0'><b style='color:%s'>%s</b>&nbsp; %s</div>" % (t[c], m, txt)
                       for c, m, txt in rows[:limit])
        if len(rows) > limit:
            html += "<div style='color:%s'>+%d more…</div>" % (t["muted"], len(rows) - limit)
        return html

    def _entry_selected(self, e):
        self.graph.clear_selection()
        self.card_title.setText(e.title)
        self.card_meta.setText("%s · %s" % (e.author, dialogs.format_time(e.time)))
        self.card_changes.setText(self._changes_html(e.parents[0] if e.parents else None, e.sha))
        self._clear_actions()
        label = self._entry_label(e)
        self._card_button("View", "eye", lambda: self.view(e.sha, label, e.docs))
        if e.sha != self.project.head():
            self._card_button("Restore", "restore", lambda: self.restore(e.sha, label))
        self._card_button("Compare", "compare", lambda: self.compare(e.sha, self.project.head()))
        self.card_actions.addStretch(1)
        self.card.show()

    def _node_selected(self, n):
        self.history.clearSelection()
        p = self.project
        label = self._node_label(n)
        cur = p.current_workspace()
        self._clear_actions()
        meta = friendly_time(n.time)
        if n.versions:
            v = next((v for v in p.versions() if v.name == n.versions[0]), None)
            self.card_title.setText("Version %s" % label)
            if v:
                meta = "%s · %s%s" % (v.author, dialogs.format_time(v.time),
                                      ("\n" + v.description) if v.description else "")
            rels = [r for r in p.releases() if r.version == label]
            if rels:
                meta += "\nReleased: " + ", ".join("%s rev %s (%s)" % (r.part_number, r.revision, r.state)
                                                    for r in rels)
            self._card_button("View", "eye", lambda: self.view(n.sha, label))
            self._card_button("Branch", "branch", lambda: self.new_workspace(n.sha, label))
            self._card_button("Release", "release", lambda: self.release(label))
        elif n.workspaces:
            ws = n.workspaces[0]
            self.card_title.setText("Workspace %s" % display_name(ws))
            meta = "Last change %s" % dialogs.format_time(n.time)
            if ws != cur:
                self._card_button("Switch", "workspace", lambda: self.switch_workspace(ws), "primary")
                self._card_button("Merge into %s" % display_name(cur), "merge", lambda: self.merge(ws))
            else:
                meta += "\nThis is the workspace you are working in."
                self._card_button("Create version", "version", lambda: self.create_version())
        else:
            self.card_title.setText(label)
            self._card_button("View", "eye", lambda: self.view(n.sha, label))
            self._card_button("Branch", "branch", lambda: self.new_workspace(n.sha, label))
        self.card_meta.setText(meta)
        prev = n.parents[0] if n.parents else None
        self.card_changes.setText(self._changes_html(prev, n.sha) if prev else "")
        self.card_changes.setVisible(bool(prev))
        self.card_actions.addStretch(1)
        self.card.show()


class StatusPill(QtWidgets.QToolButton):
    """Workspace indicator in FreeCAD's status bar; click to toggle the panel."""

    def __init__(self, panel):
        super().__init__()
        self.setObjectName("StrataStatus")
        self.setStyleSheet(theme.stylesheet())
        self.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setToolTip("Versions and history")
        self.clicked.connect(lambda: panel.setVisible(not panel.isVisible()))
        self.hide()
