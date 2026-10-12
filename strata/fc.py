# SPDX-License-Identifier: LGPL-2.1-or-later
"""FreeCAD glue: which project is active, saving, reloading and previews."""

from __future__ import annotations

import os

import FreeCAD as App

from .project import Project, StrataError

Gui = None
if App.GuiUp:  # FreeCADGui imports in console mode too, but is not functional there
    import FreeCADGui as Gui


def log(msg: str):
    App.Console.PrintLog("Strata: %s\n" % msg)


def project_for(doc) -> Project | None:
    if doc is None or not getattr(doc, "FileName", ""):
        return None
    try:
        p = Project.locate(doc.FileName)
    except Exception as exc:
        log("cannot open project: %s" % exc)
        return None
    return p if p and p.contains(doc.FileName) else None


def active_project() -> Project | None:
    return project_for(App.ActiveDocument)


def open_docs(project: Project) -> list[tuple[object, str]]:
    out = []
    for doc in App.listDocuments().values():
        fn = getattr(doc, "FileName", "")
        if fn and project.contains(fn):
            out.append((doc, project.rel(fn)))
    return out


def is_modified(doc) -> bool:
    if Gui is not None:
        gd = Gui.getDocument(doc.Name)
        if gd is not None:
            return bool(gd.Modified)
    return doc.isTouched() if hasattr(doc, "isTouched") else False


def unsaved(project: Project) -> list:
    return [doc for doc, _ in open_docs(project) if is_modified(doc)]


def save(docs):
    for d in docs:
        d.save()


def reload(project: Project, rels: list[str]):
    """Re-open the open documents whose files changed on disk (workspace switch, restore, merge, sync)."""
    rels = set(rels)
    active = App.ActiveDocument.FileName if App.ActiveDocument else None
    clean = {d.FileName for d, _ in open_docs(project) if not is_modified(d)}
    reopen = []
    for doc, rel in open_docs(project):
        if rel in rels:
            reopen.append(doc.FileName)
            App.closeDocument(doc.Name)
    new_active = None
    for path in reopen:
        if os.path.isfile(path):
            nd = App.openDocument(path)
            if os.path.normcase(path) == os.path.normcase(active or ""):
                new_active = nd
    if new_active is not None and Gui is not None:
        App.setActiveDocument(new_active.Name)
        gd = Gui.getDocument(new_active.Name)
        if gd is not None and gd.ActiveView is not None:
            Gui.ActiveDocument = gd
    if Gui is not None:
        # Documents now match the workspace on disk. An assembly whose linked part was reloaded gets
        # flagged modified by FreeCAD although nothing was edited; clear that so the user is not nagged.
        from PySide import QtWidgets
        QtWidgets.QApplication.processEvents()
        for d, _ in open_docs(project):
            if d.FileName in clean or d.FileName in reopen:
                gd = Gui.getDocument(d.Name)
                if gd is not None:
                    gd.Modified = False


def open_preview(project: Project, rev: str, rel: str, label: str):
    """Open a document as it was at rev. All documents of that point are exported so links resolve."""
    dest = os.path.join(project.dir, "previews", rev[:12])
    for doc in project.documents(rev):
        project.export_document(rev, doc, dest)
    path = os.path.join(dest, *rel.split("/"))
    if not os.path.isfile(path):
        raise StrataError("%s did not exist at that point" % rel)
    for d in App.listDocuments().values():
        if os.path.normcase(getattr(d, "FileName", "")) == os.path.normcase(path):
            App.closeDocument(d.Name)
    doc = App.openDocument(path)
    # label the preview and every linked document it pulled in from the same point in time
    prefix = os.path.normcase(dest)
    for d in App.listDocuments().values():
        fn = getattr(d, "FileName", "") or ""
        if os.path.normcase(fn).startswith(prefix) and " @ " not in d.Label:
            d.Label = "%s @ %s" % (os.path.splitext(os.path.basename(fn))[0], label)
            if Gui is not None and Gui.getDocument(d.Name) is not None:
                Gui.getDocument(d.Name).Modified = False
    if Gui is not None:
        gd = Gui.getDocument(doc.Name)
        if gd is not None and gd.ActiveView is not None and hasattr(gd.ActiveView, "fitAll"):
            gd.ActiveView.fitAll()
    return doc


_overlays: set = set()  # names of comparison documents created by compare_overlay


def is_preview(doc) -> bool:
    """Read-only views that belong to a project: version previews and 3D comparisons."""
    if doc is not None and doc.Name in _overlays:
        return True
    fn = os.path.normcase(getattr(doc, "FileName", "") or "")
    return (os.sep + ".strata" + os.sep + "previews" + os.sep) in fn


def _visible_solids(doc):
    import Part
    shapes = []
    for o in doc.Objects:
        if o.InList or not hasattr(o, "Shape"):
            continue
        if o.TypeId.startswith("App::") and o.TypeId not in ("App::Part", "App::Link"):
            continue
        if not getattr(o, "Visibility", True):
            continue
        try:
            s = o.Shape
        except Exception:
            continue
        if s is None or s.isNull() or not s.Solids:
            continue
        shapes.append(s.copy())
    return Part.makeCompound(shapes) if shapes else None


def compare_overlay(project: Project, rev_a: str, rev_b: str, rel: str, label_a: str, label_b: str) -> str:
    """Show both states of a document in one 3D view: before in translucent red, after in blue."""
    shapes = []
    for rev in (rev_a, rev_b):
        dest = os.path.join(project.dir, "previews", rev[:12])
        for doc in project.documents(rev):
            project.export_document(rev, doc, dest)
        path = os.path.join(dest, *rel.split("/"))
        if not os.path.isfile(path):
            shapes.append(None)
            continue
        d = App.openDocument(path, True)  # hidden
        try:
            d.recompute()
            shapes.append(_visible_solids(d))
        finally:
            App.closeDocument(d.Name)
    stem = os.path.splitext(os.path.basename(rel))[0]
    cmp = App.newDocument("Compare")
    _overlays.add(cmp.Name)
    cmp.Label = "Compare %s: %s → %s" % (stem, label_a, label_b)
    styles = [("Before", label_a, (0.85, 0.25, 0.22), 70), ("After", label_b, (0.12, 0.44, 0.92), 15)]
    vols = []
    for shape, (name, lbl, color, transp) in zip(shapes, styles):
        if shape is None:
            vols.append(None)
            continue
        f = cmp.addObject("Part::Feature", name)
        f.Label = "%s (%s)" % (name, lbl)
        f.Shape = shape
        vols.append(shape.Volume)
        if Gui is not None and f.ViewObject is not None:
            f.ViewObject.ShapeColor = color
            f.ViewObject.Transparency = transp
            f.ViewObject.LineColor = color
    cmp.recompute()
    if Gui is not None:
        gd = Gui.getDocument(cmp.Name)
        if gd and gd.ActiveView:
            gd.ActiveView.viewIsometric()
            gd.ActiveView.fitAll()
        gd.Modified = False
    if None in vols:
        return "Overlay shown. One side has no solid geometry."
    a, b = vols
    pct = ((b - a) / a * 100.0) if a else 0.0
    return "Volume %s: %.1f mm³ → %.1f mm³ (%+.1f%%). Before is red and translucent, after is blue." % (
        stem, a, b, pct)


class SaveObserver:
    """Records a history entry after every save of a document inside a project."""

    def __init__(self, on_change):
        self.on_change = on_change

    def slotFinishSaveDocument(self, doc, filename):
        from PySide import QtCore
        QtCore.QTimer.singleShot(0, lambda: self._record(filename))

    def _record(self, filename):
        if is_preview_path(filename):
            return
        try:
            p = Project.locate(filename)
            if p is None or not p.contains(filename) or not p.settings().get("record_on_save", True):
                return
            sha = p.record([p.rel(filename)])
            self.on_change(p, sha)
        except Exception as exc:
            App.Console.PrintWarning("Strata: could not record history: %s\n" % exc)

    def slotActivateDocument(self, doc):
        self.on_change(None, None)

    def slotCreatedDocument(self, doc):
        self.on_change(None, None)

    def slotDeletedDocument(self, doc):
        self.on_change(None, None)


def is_preview_path(path: str) -> bool:
    return (os.sep + ".strata" + os.sep) in os.path.normcase(os.path.abspath(path))
