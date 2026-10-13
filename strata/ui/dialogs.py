# SPDX-License-Identifier: LGPL-2.1-or-later
"""Dialogs. Plain engineering language; no version-control jargon."""

from __future__ import annotations

import os
import time

from PySide import QtCore, QtGui, QtWidgets

from ..project import display_name, friendly_time
from . import theme


def _stem(doc: str) -> str:
    return os.path.splitext(doc.rsplit("/", 1)[-1])[0]


class StrataDialog(QtWidgets.QDialog):
    def __init__(self, title: str, subtitle: str = "", parent=None, width: int = 460):
        super().__init__(parent)
        self.setObjectName("StrataDialog")
        self.setWindowTitle(title)
        self.setStyleSheet(theme.stylesheet())
        self.setMinimumWidth(width)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(14)
        head = QtWidgets.QVBoxLayout()
        head.setSpacing(3)
        head.addWidget(theme.label(title, "title"))
        if subtitle:
            head.addWidget(theme.label(subtitle, "caption", wrap=True))
        outer.addLayout(head)
        self.body = QtWidgets.QVBoxLayout()
        self.body.setSpacing(10)
        outer.addLayout(self.body, 1)
        self.footer = QtWidgets.QHBoxLayout()
        self.footer.setSpacing(8)
        self.footer_left = QtWidgets.QHBoxLayout()
        self.footer.addLayout(self.footer_left)
        self.footer.addStretch(1)
        outer.addLayout(self.footer)
        self.cancel_btn = theme.make_button("Cancel", "secondary")
        self.cancel_btn.clicked.connect(self.reject)
        self.footer.addWidget(self.cancel_btn)
        self.primary = None

    def set_primary(self, text: str, icon_name: str | None = None) -> QtWidgets.QPushButton:
        self.primary = theme.make_button(text, "primary", icon_name)
        self.primary.setDefault(True)
        self.primary.clicked.connect(self.accept)
        self.footer.addWidget(self.primary)
        return self.primary

    def field(self, caption: str, widget: QtWidgets.QWidget, hint: str = "") -> QtWidgets.QWidget:
        box = QtWidgets.QVBoxLayout()
        box.setSpacing(4)
        box.addWidget(theme.label(caption.upper(), "field"))
        box.addWidget(widget)
        if hint:
            box.addWidget(theme.label(hint, "caption", wrap=True))
        self.body.addLayout(box)
        return widget

    def error(self, text: str):
        QtWidgets.QMessageBox.warning(self, self.windowTitle(), text)


# ------------------------------------------------------------------ simple forms
class CreateVersionDialog(StrataDialog):
    def __init__(self, project, parent=None):
        docs = project.documents_on_disk()
        super().__init__("Create version",
                         "A version is a permanent, named snapshot of every document in %s. "
                         "You can view it, compare with it, branch from it or release it at any time." % project.name,
                         parent)
        self.name = self.field("Version name", QtWidgets.QLineEdit(project.suggest_version_name()))
        self.desc = self.field("Description", QtWidgets.QPlainTextEdit())
        self.desc.setPlaceholderText("What is in this version? e.g. 'Prototype sent to supplier'")
        self.desc.setFixedHeight(80)
        chips = theme.label("Includes %d document%s: %s" % (
            len(docs), "" if len(docs) == 1 else "s", ", ".join(_stem(d) for d in docs[:6]) +
            (" …" if len(docs) > 6 else "")), "caption", wrap=True)
        self.body.addWidget(chips)
        self.set_primary("Create version", "version")
        self.name.selectAll()


class NewWorkspaceDialog(StrataDialog):
    def __init__(self, project, base_label: str, parent=None):
        super().__init__("Create workspace",
                         "A workspace is a separate place to explore a design change. Main stays untouched "
                         "until you merge your work back.", parent)
        self.name = self.field("Workspace name", QtWidgets.QLineEdit())
        self.name.setPlaceholderText("e.g. Lighter bracket")
        self.desc = self.field("Description", QtWidgets.QLineEdit())
        self.desc.setPlaceholderText("Optional")
        self.body.addWidget(theme.label("Starts from: %s" % base_label, "caption"))
        self.set_primary("Create workspace", "branch")


class ShareDialog(StrataDialog):
    def __init__(self, project, parent=None):
        super().__init__("Share project",
                         "Choose a shared location your team can reach: a network folder, a synced drive "
                         "(OneDrive, Nextcloud, Dropbox) or a company server address. Everyone who opens the "
                         "project from there gets all workspaces and versions, and Sync keeps you up to date.",
                         parent, 520)
        row = QtWidgets.QHBoxLayout()
        self.location = QtWidgets.QLineEdit(project.remote_url() or "")
        self.location.setPlaceholderText(r"\\server\cad\%s   or   https://git.example.com/cad/%s.git"
                                         % (project.name, project.name))
        browse = theme.make_button("Browse…", "secondary", "folder")
        browse.clicked.connect(self._browse)
        row.addWidget(self.location, 1)
        row.addWidget(browse)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.field("Shared location", w, "Works offline: you can keep working and sync whenever the location is "
                                         "reachable.")
        self.set_primary("Share and sync", "share")

    def _browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Shared location")
        if d:
            self.location.setText(d)


class JoinDialog(StrataDialog):
    def __init__(self, parent=None):
        super().__init__("Open shared project",
                         "Get a copy of a project a colleague shared, with its full version history.", parent, 520)
        self.location = self._path_row("Shared location", directory=True)
        self.dest = self._path_row("Save to folder", directory=True)
        self.set_primary("Open project", "share")

    def _path_row(self, caption, directory=True):
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        edit = QtWidgets.QLineEdit()
        b = theme.make_button("Browse…", "secondary", "folder")
        b.clicked.connect(lambda: edit.setText(QtWidgets.QFileDialog.getExistingDirectory(self, caption) or edit.text()))
        row.addWidget(edit, 1)
        row.addWidget(b)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        self.field(caption, w)
        return edit


class StartDialog(StrataDialog):
    def __init__(self, folder: str, user: str, parent=None):
        super().__init__("Turn on version control",
                         "Every time you save, a history entry is recorded automatically. Create versions for "
                         "milestones, try ideas in workspaces and go back in time whenever you need to.", parent, 520)
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.folder = QtWidgets.QLineEdit(folder)
        b = theme.make_button("Browse…", "secondary", "folder")
        b.clicked.connect(self._browse)
        row.addWidget(self.folder, 1)
        row.addWidget(b)
        w = QtWidgets.QWidget()
        w.setLayout(row)
        self.field("Project folder", w, "All FreeCAD documents in this folder and its sub-folders are versioned "
                                        "together, so assemblies and their parts always match.")
        self.docs_label = theme.label("", "caption", wrap=True)
        self.body.addWidget(self.docs_label)
        self.user = self.field("Your name", QtWidgets.QLineEdit(user), "Shown next to your changes.")
        self.folder.textChanged.connect(self._count)
        self._count()
        self.set_primary("Turn on", "check")

    def _browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "Project folder", self.folder.text())
        if d:
            self.folder.setText(d)

    def _count(self):
        n = 0
        for dp, dn, fn in os.walk(self.folder.text()):
            dn[:] = [d for d in dn if not d.startswith(".")]
            n += sum(1 for f in fn if f.lower().endswith(".fcstd"))
            if n > 999:
                break
        self.docs_label.setText("%d FreeCAD document%s found." % (n, "" if n == 1 else "s"))


class SettingsDialog(StrataDialog):
    def __init__(self, project, parent=None):
        super().__init__("Project settings", project.root, parent, 500)
        name, email = project.repo.identity()
        self.user = self.field("Your name", QtWidgets.QLineEdit(name))
        self.email = self.field("Email", QtWidgets.QLineEdit(email))
        s = project.settings()
        self.record = QtWidgets.QCheckBox("Record a history entry every time a document is saved")
        self.record.setChecked(s.get("record_on_save", True))
        self.binary = QtWidgets.QCheckBox("Also share binary attachments (thumbnails, colour tables, images)")
        self.binary.setChecked(s.get("track_binary", False))
        self.body.addWidget(self.record)
        self.body.addWidget(self.binary)
        self.body.addWidget(theme.label(
            "By default only the text content of documents (model definition and geometry) is shared, which keeps "
            "history small. Binary attachments are still restored exactly on this computer.", "caption", wrap=True))
        from ..project import REVISION_SCHEMES
        self.scheme = QtWidgets.QComboBox()
        for key, text in REVISION_SCHEMES.items():
            self.scheme.addItem(text, key)
        self.scheme.setCurrentIndex(max(0, self.scheme.findData(s.get("revision_scheme", "letters"))))
        self.field("Revision scheme", self.scheme, "Used for the next revision of every part number in this project.")
        self.set_primary("Save")


# ------------------------------------------------------------------ compare
COLORS = {"added": "success", "removed": "danger", "modified": "warning"}


def fill_changes(tree: QtWidgets.QTreeWidget, diffs: dict):
    t = theme.tokens()
    tree.clear()
    for doc, d in diffs.items():
        top = QtWidgets.QTreeWidgetItem([_stem(doc), {"added": "New document", "removed": "Deleted document",
                                                       "unchanged": "No model changes"}.get(d.status, "Modified")])
        top.setIcon(0, theme.icon("cube", t["text"], 16))
        f = top.font(0)
        f.setBold(True)
        top.setFont(0, f)
        tree.addTopLevelItem(top)

        def add(text, detail, kind, top=top):
            it = QtWidgets.QTreeWidgetItem([text, detail])
            it.setForeground(0, QtGui.QBrush(QtGui.QColor(t[COLORS[kind]])))
            mark = {"added": "plus", "removed": "close", "modified": "workspace"}[kind]
            it.setIcon(0, theme.icon(mark, t[COLORS[kind]], 14))
            top.addChild(it)
            return it
        for name, label, typ in d.added:
            add(label, "Added · %s" % typ.split("::")[-1], "added")
        for name, label, typ in d.removed:
            add(label, "Deleted · %s" % typ.split("::")[-1], "removed")
        rebuilt = [ch for ch in d.modified if not (ch.props or ch.renamed_from or ch.appearance)]
        for ch in d.modified:
            if ch in rebuilt:
                continue
            parts = []
            if ch.renamed_from:
                parts.append("renamed from %s" % ch.renamed_from)
            if ch.props:
                parts.append("%d parameter%s" % (len(ch.props), "" if len(ch.props) == 1 else "s"))
            if ch.geometry:
                parts.append("geometry")
            if ch.appearance:
                parts.append("appearance")
            it = add(ch.label, ", ".join(parts), "modified")
            for p in ch.props:
                sub = QtWidgets.QTreeWidgetItem([p.name, "%s  →  %s" % (p.old or "—", p.new or "—")])
                sub.setForeground(0, QtGui.QBrush(QtGui.QColor(t["muted"])))
                it.addChild(sub)
        if rebuilt:
            grp = QtWidgets.QTreeWidgetItem(["Rebuilt", "%d feature%s recomputed as a result" % (
                len(rebuilt), "" if len(rebuilt) == 1 else "s")])
            grp.setForeground(0, QtGui.QBrush(QtGui.QColor(t["muted"])))
            grp.setForeground(1, QtGui.QBrush(QtGui.QColor(t["muted"])))
            for ch in rebuilt:
                sub = QtWidgets.QTreeWidgetItem([ch.label, "geometry updated"])
                sub.setForeground(0, QtGui.QBrush(QtGui.QColor(t["muted"])))
                sub.setForeground(1, QtGui.QBrush(QtGui.QColor(t["muted"])))
                grp.addChild(sub)
            top.addChild(grp)
        for p in d.document_props:
            add("Document · %s" % p.name, "%s → %s" % (p.old, p.new), "modified")
        top.setExpanded(True)
    if not diffs:
        tree.addTopLevelItem(QtWidgets.QTreeWidgetItem(["No differences", ""]))


class CompareDialog(StrataDialog):
    def __init__(self, project, choices: list[tuple[str, str]], a: str, b: str, overlay_cb=None, parent=None):
        super().__init__("Compare", "See what changed between two points in time: added, deleted and edited "
                                    "features with their old and new values.", parent, 680)
        self.project = project
        self.overlay_cb = overlay_cb
        row = QtWidgets.QHBoxLayout()
        self.a, self.b = QtWidgets.QComboBox(), QtWidgets.QComboBox()
        for combo, sel in ((self.a, a), (self.b, b)):
            for text, sha in choices:
                combo.addItem(text, sha)
            i = combo.findData(sel)
            combo.setCurrentIndex(max(0, i))
            combo.currentIndexChanged.connect(self.refresh)
        arrow = QtWidgets.QLabel()
        arrow.setPixmap(theme.icon_pixmap("chevron", theme.tokens()["muted"], 16).transformed(QtGui.QTransform().rotate(-90)))
        row.addWidget(self._labeled("FROM", self.a), 1)
        row.addWidget(arrow, 0, QtCore.Qt.AlignBottom)
        row.addWidget(self._labeled("TO", self.b), 1)
        self.body.addLayout(row)
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Feature", "Change"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setMinimumHeight(320)
        self.tree.setAlternatingRowColors(False)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.body.addWidget(self.tree, 1)
        self.stats = theme.label("", "caption", wrap=True)
        self.body.addWidget(self.stats)
        if overlay_cb:
            self.overlay_btn = theme.make_button("Show in 3D", "secondary", "eye")
            self.overlay_btn.setToolTip("Overlay both states of the selected document in a new 3D view")
            self.overlay_btn.clicked.connect(self._overlay)
            self.footer_left.addWidget(self.overlay_btn)
        self.cancel_btn.setText("Close")
        self.refresh()

    def _labeled(self, cap, w):
        box = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(theme.label(cap, "field"))
        lay.addWidget(w)
        return box

    def refresh(self):
        self.diffs = self.project.diff(self.a.currentData(), self.b.currentData())
        fill_changes(self.tree, self.diffs)
        n = sum(len(d.added) + len(d.removed) + len(d.modified) for d in self.diffs.values())
        self.stats.setText("%d document%s changed · %d feature change%s" % (
            len(self.diffs), "" if len(self.diffs) == 1 else "s", n, "" if n == 1 else "s"))

    def selected_doc(self) -> str | None:
        it = self.tree.currentItem()
        while it is not None and it.parent() is not None:
            it = it.parent()
        docs = list(self.diffs)
        if it is not None:
            idx = self.tree.indexOfTopLevelItem(it)
            if 0 <= idx < len(docs):
                return docs[idx]
        return docs[0] if docs else None

    def _overlay(self):
        doc = self.selected_doc()
        if not doc:
            return
        try:
            stats = self.overlay_cb(self.a.currentData(), self.b.currentData(), doc,
                                    self.a.currentText(), self.b.currentText())
        except Exception as exc:  # shown to the user, never fatal
            self.error(str(exc))
            return
        if stats:
            self.stats.setText(stats)


# ------------------------------------------------------------------ merge
def _decision_combo(target: str, source: str, allow_both: bool, combine: bool = False) -> QtWidgets.QComboBox:
    combo = QtWidgets.QComboBox()
    if combine:
        combo.addItem("Combine both changes", "merged")
    combo.addItem("Keep %s" % target, "ours")
    combo.addItem("Take %s" % source, "theirs")
    if allow_both:
        combo.addItem("Keep both (copy)", "both")
    return combo


class MergeDialog(StrataDialog):
    """Onshape-style merge: an overall strategy, a choice per document, and Compare with base."""

    STRATEGIES = [("merge", "Merge changes"), ("keep", "Keep {target}"), ("replace", "Replace with {source}")]

    def __init__(self, project, plan, parent=None, compare_cb=None):
        target = display_name(project.current_workspace())
        super().__init__("Merge %s into %s" % (plan.source_label, target),
                         "Brings the changes made in %s since the two workspaces split into %s. You can undo the merge "
                         "right afterwards." % (plan.source_label, target), parent, 680)
        self.project, self.plan, self.target = project, plan, target
        self.strategy = QtWidgets.QComboBox()
        for key, text in self.STRATEGIES:
            self.strategy.addItem(text.format(target=target, source=plan.source_label), key)
        self.field("Overall merge strategy", self.strategy,
                   "Merge changes takes what only %s changed, keeps what only %s changed, and asks about documents "
                   "changed in both. A choice below overrides it for one document." % (plan.source_label, target))
        self.combos = {}
        rows = [(d, "theirs-only") for d in plan.take_theirs] + [(d, "both") for d in plan.conflicts] + \
               [(d, "combine") for d in plan.auto_merge] + [(d, "ours-only") for d in plan.ours_only]
        if rows:
            self.body.addWidget(theme.label("DOCUMENTS", "field"))
            base = project.group_docs(project.files(plan.base))
            ours = project.group_docs(project.files(plan.ours))
            theirs = project.group_docs(project.files(plan.theirs))
            from ..docdiff import summarize
            t = theme.tokens()
            for doc, kind in rows:
                parts = []
                if kind in ("theirs-only", "both", "combine"):
                    parts.append("%s: %s" % (plan.source_label, summarize(project.diff_doc(base.get(doc), theirs.get(doc)))))
                if kind in ("ours-only", "both", "combine"):
                    parts.append("%s: %s" % (target, summarize(project.diff_doc(base.get(doc), ours.get(doc)))))
                if kind == "combine":
                    parts.append("Only the properties changed on one side, so both changes can be kept.")
                icon, color = {"both": ("warning", t["warning"]), "theirs-only": ("merge", t["accent"]),
                               "combine": ("merge", t["success"]), "ours-only": ("workspace", t["muted"])}[kind]
                card = self._row(doc, icon, color, "\n".join(parts))
                combo = _decision_combo(target, plan.source_label, kind == "both", kind == "combine")
                card.layout().addWidget(combo)
                self.combos[doc] = combo
                self.body.addWidget(card)
        else:
            self.body.addWidget(theme.label("No document changes; only history will be combined.", "caption"))
        self.strategy.currentIndexChanged.connect(self._apply_strategy)
        self._apply_strategy()
        if compare_cb:
            b = theme.make_button("Compare with base", "secondary", "compare")
            b.setToolTip("Compare the common starting point with %s" % plan.source_label)
            b.clicked.connect(compare_cb)
            self.footer_left.addWidget(b)
        self.set_primary("Merge", "merge")

    def _apply_strategy(self):
        decisions = self.project.merge_decisions(self.plan, self.strategy.currentData())
        for doc, combo in self.combos.items():
            choice = decisions.get(doc) or "theirs"
            combo.setCurrentIndex(max(0, combo.findData(choice)))

    def _row(self, doc, icon_name, color, detail):
        card = QtWidgets.QFrame()
        card.setProperty("role", "card")
        lay = QtWidgets.QHBoxLayout(card)
        lay.setContentsMargins(12, 10, 12, 10)
        ic = QtWidgets.QLabel()
        ic.setPixmap(theme.icon_pixmap(icon_name, color, 18))
        lay.addWidget(ic, 0, QtCore.Qt.AlignTop)
        txt = QtWidgets.QVBoxLayout()
        txt.setSpacing(2)
        name = theme.label(_stem(doc))
        f = name.font()
        f.setBold(True)
        name.setFont(f)
        txt.addWidget(name)
        txt.addWidget(theme.label(detail, "caption", wrap=True))
        lay.addLayout(txt, 1)
        return card

    def chosen_strategy(self) -> str:
        return self.strategy.currentData()

    def resolutions(self) -> dict:
        return {doc: combo.currentData() for doc, combo in self.combos.items()}


# ------------------------------------------------------------------ restore
class RestoreDialog(StrataDialog):
    """Onshape-style restore: what changes per document, keep or restore each, compare before confirming."""

    def __init__(self, project, rev, label, only=None, parent=None, compare_cb=None):
        cur = display_name(project.current_workspace())
        super().__init__("Restore %s to %s" % (cur, label),
                         "Brings documents back to how they were at %s. This adds a new history entry; nothing is "
                         "lost, and the current state stays in history." % label, parent, 620)
        from ..docdiff import summarize
        self.checks = {}
        diffs = project.diff(project.head(), rev)
        if not diffs:
            self.body.addWidget(theme.label("Nothing to restore: the documents already match %s." % label, "caption"))
        else:
            self.body.addWidget(theme.label("RESTORE THESE DOCUMENTS", "field"))
        for doc, diff in diffs.items():
            box = QtWidgets.QCheckBox("%s  ·  %s" % (_stem(doc), summarize(diff)))
            box.setChecked(only is None or doc in only)
            self.checks[doc] = box
            self.body.addWidget(box)
        if compare_cb:
            b = theme.make_button("Compare changes", "secondary", "compare")
            b.clicked.connect(compare_cb)
            self.footer_left.addWidget(b)
        self.set_primary("Restore", "restore")
        self.primary.setEnabled(bool(diffs))

    def documents(self) -> list[str] | None:
        """None means all documents (a full restore)."""
        chosen = [d for d, b in self.checks.items() if b.isChecked()]
        return None if len(chosen) == len(self.checks) else chosen


# ------------------------------------------------------------------ properties
class PropertiesDialog(StrataDialog):
    """Onshape: Workspace properties. Part number, description, company and custom properties per document."""

    FIXED = ["Part number", "Description", "Company"]

    def __init__(self, project, parent=None):
        super().__init__("Properties",
                         "Part numbers, descriptions and your own properties for the documents in %s. They are "
                         "stored in the documents, versioned with them, and used by releases." % project.name,
                         parent, 820)
        self.project = project
        self.docs = project.documents_on_disk()
        self.values = {d: project.document_properties(d) for d in self.docs}
        custom = sorted({k for v in self.values.values() for k in v} - set(self.FIXED))
        self.columns = self.FIXED + custom
        self.table = QtWidgets.QTableWidget(len(self.docs), len(self.columns) + 1)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.table.horizontalHeader().setStretchLastSection(True)
        self._fill()
        self.table.setMinimumHeight(min(380, 70 + 34 * len(self.docs)))
        self.body.addWidget(self.table)
        add = theme.make_button("Add property…", "secondary", "plus")
        add.clicked.connect(self._add_column)
        self.footer_left.addWidget(add)
        self.body.addWidget(theme.label("Tip: select several cells in one column and type to give them the same "
                                        "value. An empty custom value removes that property.", "caption", wrap=True))
        self.set_primary("Save", "check")

    def _fill(self):
        self.table.setColumnCount(len(self.columns) + 1)
        self.table.setHorizontalHeaderLabels(["Document"] + self.columns)
        for r, doc in enumerate(self.docs):
            it = QtWidgets.QTableWidgetItem(doc)
            it.setFlags(QtCore.Qt.ItemIsEnabled)
            self.table.setItem(r, 0, it)
            for c, col in enumerate(self.columns, start=1):
                if self.table.item(r, c) is None:
                    self.table.setItem(r, c, QtWidgets.QTableWidgetItem(self.values[doc].get(col, "")))
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, max(160, self.table.columnWidth(0)))

    def _add_column(self):
        name, ok = QtWidgets.QInputDialog.getText(self, "Add property", "Property name, e.g. Material or Vendor:")
        name = (name or "").strip()
        if ok and name and name not in self.columns and name != "Document":
            self.columns.append(name)
            self._fill()

    def keyPressEvent(self, ev):
        # typing into several selected cells of a column sets them all (like Onshape's multi-select)
        items = self.table.selectedItems()
        if len(items) > 1 and ev.text() and ev.text().isprintable() and not self.table.state():
            text, ok = QtWidgets.QInputDialog.getText(self, "Set value", "Value for %d cells:" % len(items),
                                                      text=ev.text())
            if ok:
                for it in items:
                    if it.column() > 0:
                        it.setText(text)
            return
        super().keyPressEvent(ev)

    def changes(self) -> dict:
        out = {}
        for r, doc in enumerate(self.docs):
            for c, col in enumerate(self.columns, start=1):
                new = self.table.item(r, c).text().strip()
                if new != self.values[doc].get(col, ""):
                    out.setdefault(doc, {})[col] = new
        return out


# ------------------------------------------------------------------ release
class ReleaseDialog(StrataDialog):
    def __init__(self, project, version, part_numbers: dict, parent=None):
        super().__init__("Release %s" % version.name,
                         "Releasing makes a version official for manufacturing. Each released document gets a part "
                         "number and the next revision.", parent, 640)
        self.project = project
        self.table = QtWidgets.QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Document", "Part number", "Revision", "Description"])
        self.table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        docs = project.documents(version.sha)
        for doc in docs:
            props = project.document_properties(doc, version.sha)
            r = self.table.rowCount()
            self.table.insertRow(r)
            it = QtWidgets.QTableWidgetItem(_stem(doc))
            it.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
            pn = props.get("Part number") or part_numbers.get(doc) or ""
            it.setCheckState(QtCore.Qt.Checked if pn or len(docs) == 1 else QtCore.Qt.Unchecked)
            it.setData(QtCore.Qt.UserRole, doc)
            self.table.setItem(r, 0, it)
            pn = pn or _stem(doc).upper().replace(" ", "-")
            self.table.setItem(r, 1, QtWidgets.QTableWidgetItem(pn))
            self.table.setItem(r, 2, QtWidgets.QTableWidgetItem(project.next_revision(pn)))
            desc = props.get("Description") or (version.description.splitlines()[0] if version.description else "")
            self.table.setItem(r, 3, QtWidgets.QTableWidgetItem(desc))
        self.table.itemChanged.connect(self._pn_changed)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(0, max(150, self.table.columnWidth(0)))
        self.table.setColumnWidth(1, 140)
        self.table.setMinimumHeight(min(320, 60 + 34 * len(docs)))
        self.body.addWidget(self.table)
        from ..project import REVISION_SCHEMES
        self.body.addWidget(theme.label("Revision scheme: %s. Part numbers and descriptions come from the documents' "
                                        "properties." % REVISION_SCHEMES[project.settings().get("revision_scheme",
                                                                                                 "letters")],
                                        "caption", wrap=True))
        self.approval = QtWidgets.QCheckBox("Require approval before it is released")
        self.body.addWidget(self.approval)
        self.approval.toggled.connect(lambda on: self.primary.setText("Submit for approval" if on else "Release"))
        self.set_primary("Release", "release")

    def _pn_changed(self, item):
        if item.column() == 1:
            self.table.blockSignals(True)
            self.table.item(item.row(), 2).setText(self.project.next_revision(item.text().strip()))
            self.table.blockSignals(False)

    def items(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it.checkState() == QtCore.Qt.Checked:
                out.append({"document": it.data(QtCore.Qt.UserRole),
                            "part_number": self.table.item(r, 1).text().strip(),
                            "revision": self.table.item(r, 2).text().strip(),
                            "description": self.table.item(r, 3).text().strip()})
        return out


class ObsoleteDialog(StrataDialog):
    def __init__(self, rel, parent=None):
        super().__init__("Obsolete %s rev %s" % (rel.part_number, rel.revision),
                         "An obsolete revision stays in the record but is no longer valid for manufacturing.",
                         parent, 520)
        self.reusable = QtWidgets.QCheckBox("Mark revision as re-releasable")
        self.body.addWidget(self.reusable)
        self.body.addWidget(theme.label("When checked, the next release of %s may use revision %s again."
                                        % (rel.part_number, rel.revision), "caption", wrap=True))
        self.comment = self.field("Reason", QtWidgets.QLineEdit())
        self.comment.setPlaceholderText("e.g. replaced by rev B, supplier change")
        self.set_primary("Obsolete", "lock")


class ReleasesDialog(StrataDialog):
    STATE_STYLE = {"Released": ("success", "success_soft"), "Pending": ("warning", "warning_soft"),
                   "Rejected": ("danger", "danger_soft"), "Obsolete": ("faint", "hover")}
    COLUMNS = ["Part number", "Rev", "State", "Document", "Version", "Requested by", "Approved by", "Comment"]

    def __init__(self, project, on_view=None, parent=None, on_export=None, part_number=None):
        super().__init__("Releases", "Every release in %s: revisions, their state, who requested and approved them."
                         % project.name, parent, 900)
        self.project, self.on_view, self.on_export = project, on_view, on_export
        self.part = QtWidgets.QComboBox()
        self.part.addItem("All part numbers", None)
        for pn in sorted({r.part_number for r in project.releases()}):
            self.part.addItem("%s  (revision history)" % pn, pn)
        if part_number:
            self.part.setCurrentIndex(max(0, self.part.findData(part_number)))
        self.field("Show", self.part)
        self.part.currentIndexChanged.connect(self.refresh)
        self.table = QtWidgets.QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.horizontalHeader().setDefaultAlignment(QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter)
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(300)
        self.body.addWidget(self.table, 1)
        self.approve = theme.make_button("Approve", "secondary", "check")
        self.reject_btn = theme.make_button("Reject", "danger", "close")
        self.obsolete = theme.make_button("Obsolete…", "secondary", "lock")
        self.view = theme.make_button("View", "secondary", "eye")
        self.export = theme.make_button("Export…", "secondary", "share")
        for b in (self.approve, self.reject_btn, self.obsolete, self.view, self.export):
            self.footer_left.addWidget(b)
        self.approve.clicked.connect(lambda: self._state("Released"))
        self.reject_btn.clicked.connect(lambda: self._state("Rejected"))
        self.obsolete.clicked.connect(lambda: self._state("Obsolete"))
        self.view.clicked.connect(self._view)
        self.export.clicked.connect(self._export)
        self.table.itemSelectionChanged.connect(self._buttons)
        self.cancel_btn.setText("Close")
        self.refresh()

    def refresh(self):
        t = theme.tokens()
        pn = self.part.currentData()
        self.rels = self.project.revision_history(pn) if pn else self.project.releases()
        self.table.setRowCount(0)
        for rel in self.rels:
            r = self.table.rowCount()
            self.table.insertRow(r)
            rev = rel.revision + (" (%d)" % rel.attempt if rel.attempt > 1 else "")
            state = rel.state + (", re-releasable" if rel.reusable else "")
            vals = [rel.part_number, rev, state, _stem(rel.document), rel.version, rel.author,
                    rel.approver if rel.state != "Pending" else "—", rel.comment]
            for c, v in enumerate(vals):
                it = QtWidgets.QTableWidgetItem(v)
                if c == 2:
                    fg, bg = self.STATE_STYLE.get(rel.state, ("muted", "hover"))
                    it.setForeground(QtGui.QBrush(QtGui.QColor(t[fg])))
                    it.setBackground(QtGui.QBrush(QtGui.QColor(t[bg])))
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()
        if not self.rels:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QtWidgets.QTableWidgetItem("Nothing released yet. Right-click a version → Release."))
            self.table.setSpan(0, 0, 1, len(self.COLUMNS))
        self._buttons()

    def current(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self.rels:
            return None
        return self.rels[rows[0].row()] if rows[0].row() < len(self.rels) else None

    def _buttons(self):
        rel = self.current()
        self.approve.setEnabled(bool(rel and rel.state == "Pending"))
        self.reject_btn.setEnabled(bool(rel and rel.state == "Pending"))
        self.obsolete.setEnabled(bool(rel and rel.state == "Released"))
        self.view.setEnabled(bool(rel and self.on_view))
        self.export.setEnabled(bool(rel and self.on_export and rel.state in ("Released", "Obsolete")))

    def _state(self, state):
        rel = self.current()
        if not rel:
            return
        comment, reusable = "", False
        if state == "Obsolete":
            dlg = ObsoleteDialog(rel, self)
            if dlg.exec() != QtWidgets.QDialog.Accepted:
                return
            comment, reusable = dlg.comment.text().strip(), dlg.reusable.isChecked()
        elif state == "Rejected":
            comment, ok = QtWidgets.QInputDialog.getText(self, "Reject", "Reason for rejecting %s rev %s:"
                                                         % (rel.part_number, rel.revision))
            if not ok:
                return
        self.project.set_release_state(rel, state, comment.strip(), reusable)
        self.refresh()

    def _view(self):
        rel = self.current()
        if rel and self.on_view:
            self.on_view(rel)

    def _export(self):
        rel = self.current()
        if rel and self.on_export:
            folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Export %s rev %s to"
                                                                % (rel.part_number, rel.revision))
            if folder:
                self.on_export(rel, folder)


def ask_restore(parent, what: str, target: str) -> bool:
    box = QtWidgets.QMessageBox(parent)
    box.setIcon(QtWidgets.QMessageBox.Question)
    box.setWindowTitle("Restore")
    box.setText("Restore %s to %s?" % (what, target))
    box.setInformativeText("This adds a new history entry. Nothing is lost: the current state stays in history and "
                           "you can return to it at any time.")
    yes = box.addButton("Restore", QtWidgets.QMessageBox.AcceptRole)
    box.addButton("Cancel", QtWidgets.QMessageBox.RejectRole)
    box.exec()
    return box.clickedButton() is yes


def format_time(ts: int) -> str:
    return "%s (%s)" % (time.strftime("%d %b %Y %H:%M", time.localtime(ts)), friendly_time(ts))
