# SPDX-License-Identifier: LGPL-2.1-or-later
"""The Strata project model, in the vocabulary engineers use.

    Project    a folder of FreeCAD documents that are versioned together
    Workspace  an independent line of work (git branch); "Main" by default
    History    every save, automatically recorded (commit)
    Version    a named, immutable snapshot of the whole project (annotated tag)
    Release    a version formally released under a part number and revision
    Merge      bring the changes of one workspace into another
    Sync       exchange workspaces and versions with a shared location

The model has no FreeCAD or Qt dependency.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from . import docdiff
from .fcstd import MANIFEST, BlobStore, explode, manifest_bytes, rebuild
from .gitcore import ZERO, Repo

STRATA_DIR = ".strata"
HEADS = "refs/heads/"
VERSION_REFS = "refs/tags/v/"
RELEASE_REFS = "refs/tags/release/"
PENDING_REFS = "refs/tags/pending/"
REJECTED_REFS = "refs/tags/rejected/"
OBSOLETE_REFS = "refs/tags/obsolete/"
REMOTE = "origin"
# ASME Y14.35: revision letters I, O, Q, S, X, Z are not used
REVISION_LETTERS = [c for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if c not in "IOQSXZ"]


class StrataError(RuntimeError):
    pass


# ---------------------------------------------------------------- records
@dataclass
class Workspace:
    name: str
    head: str | None
    current: bool = False
    remote_only: bool = False

    @property
    def display(self) -> str:
        return display_name(self.name)


@dataclass
class HistoryEntry:
    sha: str
    title: str
    body: str
    kind: str
    author: str
    time: int
    docs: list[str]
    parents: list[str]
    versions: list[str] = field(default_factory=list)


@dataclass
class Version:
    name: str
    description: str
    sha: str
    ref: str
    author: str
    time: int
    workspace: str = ""


@dataclass
class Release:
    part_number: str
    revision: str
    description: str
    state: str  # Pending | Released | Rejected | Obsolete
    sha: str
    version: str
    document: str
    author: str
    time: int
    approver: str = ""


@dataclass
class GraphNode:
    sha: str
    lane: int
    row: int
    time: int
    versions: list[str] = field(default_factory=list)
    workspaces: list[str] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)
    kind: str = "commit"  # start | version | workspace | merge
    current: bool = False


@dataclass
class MergePlan:
    source: str
    source_label: str
    ours: str
    theirs: str
    base: str | None
    take_theirs: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    unchanged: bool = False
    fast_forward: bool = False


@dataclass
class SyncResult:
    pushed: list[str] = field(default_factory=list)
    pulled: list[str] = field(default_factory=list)
    merged: list[str] = field(default_factory=list)
    needs_merge: list[str] = field(default_factory=list)
    changed_docs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- names
_BAD_REF = re.compile(r"[\x00-\x20~^:?*\[\\\x7f]|\.\.|@\{|//")


def ref_slug(name: str) -> str:
    s = name.strip().replace(" ", "_")
    s = _BAD_REF.sub("-", s).strip("./-")
    if s.endswith(".lock"):
        s = s[:-5]
    if not s:
        raise StrataError("Please enter a name")
    return s


def display_name(slug: str) -> str:
    return "Main" if slug == "main" else slug.replace("_", " ")


def next_revision(existing: list[str]) -> str:
    def index(rev: str) -> int:
        n = 0
        for ch in rev:
            if ch not in REVISION_LETTERS:
                return -1
            n = n * len(REVISION_LETTERS) + REVISION_LETTERS.index(ch) + 1
        return n
    top = max([index(r) for r in existing] + [0])
    n, out = top + 1, ""
    while n:
        n, r = divmod(n - 1, len(REVISION_LETTERS))
        out = REVISION_LETTERS[r] + out
    return out


def split_doc(path: str) -> tuple[str, str] | None:
    """'Parts/Bracket.FCStd/Document.xml' -> ('Parts/Bracket.FCStd', 'Document.xml')."""
    parts = path.split("/")
    for i, p in enumerate(parts):
        if p.lower().endswith(".fcstd"):
            return "/".join(parts[:i + 1]), "/".join(parts[i + 1:])
    return None


def default_identity() -> tuple[str, str]:
    try:
        user = getpass.getuser()
    except Exception:
        user = "engineer"
    return user, "%s@%s" % (user, os.environ.get("COMPUTERNAME", "localhost").lower())


def friendly_time(ts: int, now: float | None = None) -> str:
    now = now or time.time()
    delta = now - ts
    dt = _dt.datetime.fromtimestamp(ts)
    if delta < 60:
        return "just now"
    if delta < 3600:
        return "%d min ago" % (delta // 60)
    today = _dt.date.fromtimestamp(now)
    if dt.date() == today:
        return dt.strftime("%H:%M")
    if dt.date() == today - _dt.timedelta(days=1):
        return "Yesterday " + dt.strftime("%H:%M")
    if dt.year == today.year:
        return dt.strftime("%d %b %H:%M")
    return dt.strftime("%d %b %Y")


def _hide(path: str):
    if os.name == "nt":
        try:
            subprocess.run(["attrib", "+h", path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=0x08000000)
        except OSError:
            pass


# ---------------------------------------------------------------- project
class Project:
    _instances: dict[str, "Project"] = {}

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        self.dir = os.path.join(self.root, STRATA_DIR)
        self.repo = Repo(os.path.join(self.dir, "repo.git"))
        self.blobs = BlobStore(os.path.join(self.dir, "blobs"))
        self._settings_path = os.path.join(self.dir, "settings.json")

    # ------------------------------------------------ discovery / creation
    @classmethod
    def locate(cls, path: str) -> "Project | None":
        d = os.path.abspath(path)
        if os.path.isfile(d) or not os.path.isdir(d):
            d = os.path.dirname(d)
        while True:
            if os.path.isdir(os.path.join(d, STRATA_DIR, "repo.git")):
                return cls.open(d)
            parent = os.path.dirname(d)
            if parent == d:
                return None
            d = parent

    @classmethod
    def open(cls, root: str) -> "Project":
        key = os.path.normcase(os.path.abspath(root))
        if key not in cls._instances:
            cls._instances[key] = cls(root)
        return cls._instances[key]

    @classmethod
    def create(cls, root: str, user: str | None = None, email: str | None = None) -> "Project":
        root = os.path.abspath(root)
        sdir = os.path.join(root, STRATA_DIR)
        if os.path.isdir(os.path.join(sdir, "repo.git")):
            return cls.open(root)
        du, de = default_identity()
        Repo.init(os.path.join(sdir, "repo.git"), user or du, email or de)
        _hide(sdir)
        with open(os.path.join(sdir, "README.txt"), "w", encoding="utf-8") as fh:
            fh.write("This folder holds the version history of the FreeCAD documents next to it.\n"
                     "It is managed by the Strata add-on. Do not edit or delete it.\n")
        proj = cls.open(root)
        proj.save_settings({"track_binary": False, "record_on_save": True})
        proj.record(kind="start", title="Start")
        return proj

    @classmethod
    def join(cls, url: str, root: str, user: str | None = None, email: str | None = None) -> "Project":
        """Get a project that a colleague shared, into an empty or new folder."""
        root = os.path.abspath(root)
        if os.path.isdir(os.path.join(root, STRATA_DIR, "repo.git")):
            raise StrataError("This folder already has a project")
        os.makedirs(root, exist_ok=True)
        du, de = default_identity()
        Repo.init(os.path.join(root, STRATA_DIR, "repo.git"), user or du, email or de)
        _hide(os.path.join(root, STRATA_DIR))
        proj = cls.open(root)
        proj.save_settings({"track_binary": False, "record_on_save": True})
        proj.set_remote(url)
        proj.repo.git("fetch", "--tags", REMOTE)
        main = proj.repo.rev_parse("refs/remotes/%s/main" % REMOTE)
        if not main:
            raise StrataError("The shared location has no project yet")
        proj.repo.update_ref(HEADS + "main", main, ZERO, "strata: join")
        proj.materialize(None, main)
        return proj

    # ------------------------------------------------ settings
    def settings(self) -> dict:
        try:
            with open(self._settings_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"track_binary": False, "record_on_save": True}

    def save_settings(self, values: dict):
        s = self.settings()
        s.update(values)
        with open(self._settings_path, "w", encoding="utf-8") as fh:
            json.dump(s, fh, indent=1)

    @property
    def name(self) -> str:
        return os.path.basename(self.root)

    # ------------------------------------------------ paths
    def rel(self, path: str) -> str:
        return os.path.relpath(os.path.abspath(path), self.root).replace(os.sep, "/")

    def abspath(self, rel: str) -> str:
        return os.path.join(self.root, *rel.split("/"))

    def contains(self, path: str) -> bool:
        try:
            r = self.rel(path)
        except ValueError:
            return False
        return not r.startswith("..") and not r.startswith(STRATA_DIR + "/")

    def documents_on_disk(self) -> list[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for f in filenames:
                if f.lower().endswith(".fcstd"):
                    out.append(self.rel(os.path.join(dirpath, f)))
        return sorted(out)

    # ------------------------------------------------ refs
    def current_workspace(self) -> str:
        return self.repo.head_ref()[len(HEADS):]

    def head(self) -> str | None:
        return self.repo.rev_parse(self.repo.head_ref())

    def workspaces(self) -> list[Workspace]:
        cur = self.current_workspace()
        out = [Workspace(ref[len(HEADS):], sha, ref[len(HEADS):] == cur) for ref, sha, _ in self.repo.refs(HEADS)]
        names = {w.name for w in out}
        if cur not in names:
            out.append(Workspace(cur, None, True))
        prefix = "refs/remotes/%s/" % REMOTE
        for ref, sha, _ in self.repo.refs(prefix):
            n = ref[len(prefix):]
            if n != "HEAD" and n not in names and n != cur:
                out.append(Workspace(n, sha, False, remote_only=True))
        out.sort(key=lambda w: (w.name != "main", w.name.lower()))
        return out

    # ------------------------------------------------ trees
    def files(self, rev: str | None) -> dict[str, str]:
        if not rev:
            return {}
        return self.repo.flatten(self.repo.read_commit(rev).tree)

    @staticmethod
    def group_docs(files: dict[str, str]) -> dict[str, dict[str, str]]:
        docs: dict[str, dict[str, str]] = {}
        for path, sha in files.items():
            sp = split_doc(path)
            if sp:
                docs.setdefault(sp[0], {})[sp[1]] = sha
        return docs

    def documents(self, rev: str | None = None) -> list[str]:
        return sorted(self.group_docs(self.files(rev or self.head())))

    def _snapshot_doc(self, doc: str) -> dict[str, str]:
        tracked, binaries, manifest = explode(self.abspath(doc), self.settings().get("track_binary", False))
        out = {}
        for name, data in tracked.items():
            out[doc + "/" + name] = self.repo.write_object("blob", data)
        for data in binaries.values():
            self.blobs.put(data)
        out[doc + "/" + MANIFEST] = self.repo.write_object("blob", manifest_bytes(manifest))
        return out

    def snapshot(self, docs: list[str] | None = None) -> dict[str, str]:
        """Tree contents for HEAD with the given documents (default: all on disk) re-read from disk."""
        base = self.files(self.head())
        if docs is None:
            docs = self.documents_on_disk()
            keep = {}
        else:
            keep = {p: s for p, s in base.items() if (split_doc(p) or ("",))[0] not in docs}
        files = dict(keep)
        for doc in docs:
            if os.path.isfile(self.abspath(doc)):
                try:
                    files.update(self._snapshot_doc(doc))
                except Exception as exc:  # a corrupt or half-written archive must not lose the rest
                    raise StrataError("Could not read %s: %s" % (doc, exc)) from exc
        return files

    # ------------------------------------------------ history
    @staticmethod
    def _message(title: str, body: str, kind: str, docs: list[str], extra: dict | None = None) -> str:
        trailers = ["Strata-Kind: %s" % kind]
        if docs:
            trailers.append("Strata-Docs: %s" % "; ".join(docs))
        for k, v in (extra or {}).items():
            trailers.append("Strata-%s: %s" % (k, v))
        parts = [title.strip() or "Saved"]
        if body.strip():
            parts.append(body.strip())
        parts.append("\n".join(trailers))
        return "\n\n".join(parts)

    def describe(self, old: dict[str, str], new: dict[str, str]) -> tuple[str, list[str], str]:
        """(title, changed docs, detail body) for a tree change."""
        a, b = self.group_docs(old), self.group_docs(new)
        changed = sorted(d for d in set(a) | set(b) if a.get(d) != b.get(d))
        lines = []
        for doc in changed:
            diff = self.diff_doc(a.get(doc), b.get(doc))
            label = os.path.splitext(os.path.basename(doc))[0]
            lines.append((label, docdiff.summarize(diff)))
        if not lines:
            return "Saved (no model changes)", changed, ""
        if len(lines) == 1:
            return lines[0][1], changed, ""
        title = " · ".join("%s: %s" % (lbl, s) for lbl, s in lines[:2])
        if len(lines) > 2:
            title += " · +%d documents" % (len(lines) - 2)
        return title, changed, "\n".join("%s — %s" % ln for ln in lines)

    def record(self, docs: list[str] | None = None, kind: str = "save", title: str | None = None,
               body: str = "") -> str | None:
        """Record the on-disk state of the given documents (default all) into the current workspace."""
        parent = self.head()
        files = self.snapshot(docs)
        tree = self.repo.build_tree(files)
        if parent and self.repo.read_commit(parent).tree == tree and kind != "start":
            return None
        old = self.files(parent)
        auto_title, changed, auto_body = self.describe(old, files)
        message = self._message(title or auto_title, body or auto_body, kind, changed)
        sha = self.repo.write_commit(tree, [parent] if parent else [], message)
        self.repo.update_ref(self.repo.head_ref(), sha, parent or ZERO, "strata: " + kind)
        return sha

    def history(self, workspace: str | None = None, limit: int = 500) -> list[HistoryEntry]:
        ref = HEADS + (workspace or self.current_workspace())
        if not self.repo.rev_parse(ref):
            return []
        vmap: dict[str, list[str]] = {}
        for v in self.versions():
            vmap.setdefault(v.sha, []).append(v.name)
        out = []
        for sha in self.repo.log([ref], limit=limit, first_parent=True):
            c = self.repo.read_commit(sha)
            docs = [d for d in c.trailers.get("docs", "").split("; ") if d]
            out.append(HistoryEntry(sha, c.title, c.body, c.trailers.get("kind", "save"), c.author, c.time,
                                    docs, c.parents, vmap.get(sha, [])))
        return out

    # ------------------------------------------------ comparison
    def diff_doc(self, a: dict[str, str] | None, b: dict[str, str] | None) -> docdiff.DocDiff:
        def xml(entries, name):
            return self.repo.blob(entries[name]) if entries and name in entries else None
        return docdiff.diff_documents(
            xml(a, "Document.xml") if a else None, xml(b, "Document.xml") if b else None,
            a, b, xml(a, "GuiDocument.xml"), xml(b, "GuiDocument.xml"))

    def diff(self, rev_a: str | None, rev_b: str | None) -> dict[str, docdiff.DocDiff]:
        a, b = self.group_docs(self.files(rev_a)), self.group_docs(self.files(rev_b))
        out = {}
        for doc in sorted(set(a) | set(b)):
            if a.get(doc) != b.get(doc):
                out[doc] = self.diff_doc(a.get(doc), b.get(doc))
        return out

    # ------------------------------------------------ materialize
    def _write_doc(self, doc: str, entries: dict[str, str], dest: str | None = None) -> list[str]:
        manifest = json.loads(self.repo.blob(entries[MANIFEST])) if MANIFEST in entries else {}
        tracked = {n: self.repo.blob(s) for n, s in entries.items() if n != MANIFEST}
        return rebuild(dest or self.abspath(doc), tracked, manifest, self.blobs.get)

    def materialize(self, old_rev: str | None, new_rev: str | None, docs: list[str] | None = None) -> list[str]:
        """Make the files on disk match new_rev for documents that differ from old_rev."""
        a, b = self.group_docs(self.files(old_rev)), self.group_docs(self.files(new_rev))
        changed = []
        for doc in sorted(set(a) | set(b)):
            if docs is not None and doc not in docs:
                continue
            path = self.abspath(doc)
            if a.get(doc) == b.get(doc) and os.path.isfile(path):
                continue
            if doc in b:
                self._write_doc(doc, b[doc])
            elif os.path.isfile(path):
                trash = os.path.join(self.dir, "trash", time.strftime("%Y%m%d-%H%M%S"))
                os.makedirs(trash, exist_ok=True)
                shutil.move(path, os.path.join(trash, os.path.basename(path)))
            changed.append(doc)
        return changed

    def export_document(self, rev: str, doc: str, dest_dir: str | None = None) -> str:
        """Write a document as it was at rev into a preview folder; returns the file path."""
        entries = self.group_docs(self.files(rev)).get(doc)
        if entries is None:
            raise StrataError("%s does not exist in that version" % doc)
        dest_dir = dest_dir or os.path.join(self.dir, "previews", rev[:10])
        dest = os.path.join(dest_dir, *doc.split("/"))
        self._write_doc(doc, entries, dest)
        return dest

    # ------------------------------------------------ workspaces
    def create_workspace(self, name: str, from_rev: str | None = None, description: str = "",
                         switch: bool = True) -> tuple[str, list[str]]:
        slug = ref_slug(name)
        ref = HEADS + slug
        if self.repo.rev_parse(ref):
            raise StrataError("A workspace named '%s' already exists" % display_name(slug))
        base = from_rev or self.head()
        if not base:
            raise StrataError("Nothing has been recorded yet")
        src = self._label_for(base)
        marker = self.repo.write_commit(
            self.repo.read_commit(base).tree, [base],
            self._message("Created workspace %s from %s" % (display_name(slug), src), description, "workspace", []))
        self.repo.update_ref(ref, marker, ZERO, "strata: workspace")
        changed = self.switch_workspace(slug) if switch else []
        return slug, changed

    def switch_workspace(self, name: str) -> list[str]:
        ref = HEADS + name
        if not self.repo.rev_parse(ref):
            remote = self.repo.rev_parse("refs/remotes/%s/%s" % (REMOTE, name))
            if not remote:
                raise StrataError("Workspace '%s' does not exist" % display_name(name))
            self.repo.update_ref(ref, remote, ZERO, "strata: track")
        self.record()
        old = self.head()
        self.repo.set_head(ref)
        return self.materialize(old, self.head())

    def delete_workspace(self, name: str):
        if name == self.current_workspace():
            raise StrataError("Switch to another workspace before deleting this one")
        if name == "main":
            raise StrataError("The Main workspace cannot be deleted")
        self.repo.delete_ref(HEADS + name)

    # ------------------------------------------------ versions
    def versions(self) -> list[Version]:
        out = []
        for ref, obj, commit in self.repo.refs(VERSION_REFS):
            info = self.repo.read_tag(obj)
            try:
                meta = json.loads(info["message"])
            except ValueError:
                meta = {"name": ref[len(VERSION_REFS):], "description": info["message"]}
            out.append(Version(meta.get("name", ref[len(VERSION_REFS):]), meta.get("description", ""), commit, ref,
                               info["tagger"], info["time"], meta.get("workspace", "")))
        out.sort(key=lambda v: v.time, reverse=True)
        return out

    def suggest_version_name(self) -> str:
        n = 0
        for v in self.versions():
            m = re.fullmatch(r"V(\d+)", v.name)
            if m:
                n = max(n, int(m.group(1)))
        return "V%d" % (n + 1)

    def create_version(self, name: str, description: str = "", rev: str | None = None) -> Version:
        if rev is None:
            self.record()
        target = rev or self.head()
        if not target:
            raise StrataError("Nothing has been recorded yet")
        slug = ref_slug(name)
        ref = VERSION_REFS + slug
        if self.repo.rev_parse(ref):
            raise StrataError("A version named '%s' already exists" % name)
        meta = {"name": name.strip(), "description": description.strip(), "workspace": self.current_workspace()}
        self.repo.tag(ref, target, json.dumps(meta, ensure_ascii=False))
        return next(v for v in self.versions() if v.ref == ref)

    def _label_for(self, sha: str) -> str:
        for v in self.versions():
            if v.sha == sha:
                return v.name
        c = self.repo.read_commit(sha)
        return "'%s' (%s)" % (c.title[:40], friendly_time(c.time))

    # ------------------------------------------------ restore
    def restore(self, rev: str, docs: list[str] | None = None) -> list[str]:
        """Bring the current workspace back to rev (whole project or some documents), as a new history entry."""
        self.record()
        head = self.head()
        target = self.files(rev)
        if docs is None:
            files = target
        else:
            files = {p: s for p, s in self.files(head).items() if (split_doc(p) or ("",))[0] not in docs}
            files.update({p: s for p, s in target.items() if (split_doc(p) or ("",))[0] in docs})
        tree = self.repo.build_tree(files)
        if head and self.repo.read_commit(head).tree == tree:
            return []
        label = self._label_for(rev)
        what = ", ".join(os.path.splitext(os.path.basename(d))[0] for d in docs) if docs else ""
        title = ("Restored %s to %s" % (what, label)) if what else "Restored to %s" % label
        _, changed_docs, _ = self.describe(self.files(head), files)
        sha = self.repo.write_commit(tree, [head] if head else [],
                                     self._message(title, "", "restore", changed_docs, {"From": rev}))
        self.repo.update_ref(self.repo.head_ref(), sha, head or ZERO, "strata: restore")
        return self.materialize(head, sha)

    # ------------------------------------------------ merge
    def merge_plan(self, source: str) -> MergePlan:
        """source: workspace name or a full ref."""
        ref = source if source.startswith("refs/") else HEADS + source
        theirs = self.repo.rev_parse(ref)
        if not theirs:
            raise StrataError("Nothing to merge from %s" % display_name(source))
        ours = self.head()
        label = display_name(ref.rsplit("/", 1)[-1])
        plan = MergePlan(ref, label, ours, theirs, None)
        if self.repo.is_ancestor(theirs, ours):
            plan.unchanged = True
            return plan
        plan.base = self.repo.merge_base(ours, theirs)
        plan.fast_forward = plan.base == ours
        b = self.group_docs(self.files(plan.base))
        o = self.group_docs(self.files(ours))
        t = self.group_docs(self.files(theirs))
        for doc in sorted(set(b) | set(o) | set(t)):
            bo, oo, to = b.get(doc), o.get(doc), t.get(doc)
            if oo == to or to == bo:
                continue
            if oo == bo:
                plan.take_theirs.append(doc)
            else:
                plan.conflicts.append(doc)
        return plan

    def merge(self, plan: MergePlan, resolutions: dict[str, str] | None = None) -> list[str]:
        """resolutions: doc -> 'ours' | 'theirs' | 'both'. Returns documents changed on disk."""
        if plan.unchanged:
            return []
        resolutions = resolutions or {}
        missing = [d for d in plan.conflicts if d not in resolutions]
        if missing:
            raise StrataError("Choose how to resolve: %s" % ", ".join(missing))
        files = self.files(plan.ours)
        theirs = self.files(plan.theirs)

        def take(doc, as_doc=None):
            nonlocal files
            if as_doc is None:
                files = {p: s for p, s in files.items() if (split_doc(p) or ("",))[0] != doc}
            for p, s in theirs.items():
                sp = split_doc(p)
                if sp and sp[0] == doc:
                    files[(as_doc or doc) + "/" + sp[1]] = s

        for doc in plan.take_theirs:
            take(doc)
        for doc in plan.conflicts:
            choice = resolutions[doc]
            if choice == "theirs":
                take(doc)
            elif choice == "both":
                stem, ext = os.path.splitext(doc)
                take(doc, "%s (%s)%s" % (stem, ref_slug(plan.source_label), ext))
        tree = self.repo.build_tree(files)
        title = "Merged %s into %s" % (plan.source_label, display_name(self.current_workspace()))
        _, changed_docs, body = self.describe(self.files(plan.ours), files)
        sha = self.repo.write_commit(tree, [plan.ours, plan.theirs],
                                     self._message(title, body, "merge", changed_docs))
        self.repo.update_ref(self.repo.head_ref(), sha, plan.ours, "strata: merge")
        return self.materialize(plan.ours, sha)

    # ------------------------------------------------ releases
    def releases(self) -> list[Release]:
        # Each state change is an immutable tag; the record's state is the furthest one reached.
        rank = {"Pending": 0, "Rejected": 1, "Released": 2, "Obsolete": 3}
        records: dict[tuple[str, str], Release] = {}
        for prefix, state in ((PENDING_REFS, "Pending"), (RELEASE_REFS, "Released"),
                              (REJECTED_REFS, "Rejected"), (OBSOLETE_REFS, "Obsolete")):
            for ref, obj, commit in self.repo.refs(prefix):
                pn, _, rev = ref[len(prefix):].rpartition("/")
                info = self.repo.read_tag(obj)
                try:
                    meta = json.loads(info["message"])
                except ValueError:
                    meta = {}
                key = (meta.get("part_number", pn), meta.get("revision", rev))
                rec = records.get(key)
                if rec is None:
                    rec = records[key] = Release(key[0], key[1], meta.get("description", ""), state, commit,
                                                 meta.get("version", ""), meta.get("document", ""),
                                                 meta.get("requested_by", info["tagger"]), info["time"])
                if rank[state] >= rank[rec.state]:
                    rec.state = state
                if state in ("Released", "Rejected"):
                    rec.approver = info["tagger"]
        return sorted(records.values(), key=lambda r: (r.part_number, len(r.revision), r.revision))

    def next_revision(self, part_number: str) -> str:
        return next_revision([r.revision for r in self.releases()
                              if r.part_number == part_number and r.state != "Rejected"])

    def release(self, version: Version, document: str, part_number: str, revision: str | None = None,
                description: str = "", approval: bool = False) -> Release:
        """Release one document of a version. With approval=True it is submitted as Pending."""
        pn = ref_slug(part_number)
        revision = revision or self.next_revision(part_number)
        for r in self.releases():
            if r.part_number == part_number and r.revision == revision and r.state != "Rejected":
                raise StrataError("%s rev %s already exists" % (part_number, revision))
        meta = {"part_number": part_number, "revision": revision, "description": description,
                "version": version.name, "document": document, "requested_by": self.repo.identity()[0]}
        prefix = PENDING_REFS if approval else RELEASE_REFS
        self.repo.tag("%s%s/%s" % (prefix, pn, ref_slug(revision)), version.sha, json.dumps(meta, ensure_ascii=False))
        return next(r for r in self.releases() if r.part_number == part_number and r.revision == revision)

    def set_release_state(self, rel: Release, state: str, comment: str = ""):
        prefix = {"Released": RELEASE_REFS, "Rejected": REJECTED_REFS, "Obsolete": OBSOLETE_REFS}[state]
        meta = {"part_number": rel.part_number, "revision": rel.revision, "description": rel.description,
                "version": rel.version, "document": rel.document, "requested_by": rel.author, "comment": comment}
        self.repo.tag("%s%s/%s" % (prefix, ref_slug(rel.part_number), ref_slug(rel.revision)), rel.sha,
                      json.dumps(meta, ensure_ascii=False))

    # ------------------------------------------------ graph
    def graph(self) -> list[GraphNode]:
        """Versions, workspace tips, merges and the start, with lanes per workspace (newest first)."""
        heads = {ref[len(HEADS):]: sha for ref, sha, _ in self.repo.refs(HEADS)}
        versions = self.versions()
        tips = list(heads.values()) + [v.sha for v in versions]
        if not tips:
            return []
        parents: dict[str, list[str]] = {}
        order: list[str] = []
        for sha, ps in self.repo.topo(sorted(set(tips))):
            parents[sha] = ps
            order.append(sha)
        lane_of: dict[str, int] = {}
        cur = self.current_workspace()
        names = sorted(heads, key=lambda n: (n != "main", n != cur, n.lower()))
        lane_names = {}
        for i, n in enumerate(names):
            lane_names[i] = n
            sha = heads[n]
            while sha and sha not in lane_of:
                lane_of[sha] = i
                ps = parents.get(sha) or []
                sha = ps[0] if ps else None
        nextlane = len(names)
        for sha in order:
            if sha not in lane_of:
                lane_of[sha] = nextlane
                nextlane += 1
        vmap: dict[str, list[str]] = {}
        for v in versions:
            vmap.setdefault(v.sha, []).append(v.name)
        wmap: dict[str, list[str]] = {}
        for n, sha in heads.items():
            wmap.setdefault(sha, []).append(n)
        keep = set(vmap) | set(wmap)
        for sha in order:
            if len(parents[sha]) > 1 or not parents[sha]:
                keep.add(sha)
        # fork points: a commit with children in two lanes
        child_lanes: dict[str, set[int]] = {}
        for sha in order:
            for p in parents[sha]:
                child_lanes.setdefault(p, set()).add(lane_of[sha])
        keep |= {sha for sha, lanes in child_lanes.items() if len(lanes) > 1}

        def reduced_parents(sha):
            res, stack, seen = [], list(parents[sha]), set()
            while stack:
                p = stack.pop(0)
                if p in seen:
                    continue
                seen.add(p)
                if p in keep:
                    res.append(p)
                else:
                    stack.extend(parents.get(p, []))
            return res

        head = self.head()
        nodes, row = [], 0
        times = {}
        for sha in order:
            if sha not in keep:
                continue
            c = self.repo.read_commit(sha)
            times[sha] = c.time
            kind = "version" if sha in vmap else "workspace" if sha in wmap else \
                "start" if not parents[sha] else "merge" if len(parents[sha]) > 1 else "commit"
            nodes.append(GraphNode(sha, lane_of[sha], row, c.time, vmap.get(sha, []),
                                   wmap.get(sha, []), reduced_parents(sha), kind, sha == head))
            row += 1
        return nodes

    # ------------------------------------------------ sync
    def remote_url(self) -> str | None:
        return self.repo.git("config", "--get", "remote.%s.url" % REMOTE, check=False).strip() or None

    def set_remote(self, url: str):
        url = url.strip()
        if not url:
            raise StrataError("Enter a shared folder or server address")
        local_like = os.path.isabs(url) or url.startswith("\\\\") or re.match(r"^[A-Za-z]:", url)
        if local_like and not os.path.exists(os.path.join(url, "HEAD")):
            os.makedirs(url, exist_ok=True)
            if os.listdir(url):
                url = os.path.join(url, self.name + ".strata")
            Repo.init(url, *self.repo.identity())
        if self.remote_url():
            self.repo.git("remote", "set-url", REMOTE, url)
        else:
            self.repo.git("remote", "add", REMOTE, url)
        self.repo.git("config", "remote.%s.fetch" % REMOTE, "+refs/heads/*:refs/remotes/%s/*" % REMOTE)

    def sync(self) -> SyncResult:
        if not self.remote_url():
            raise StrataError("No shared location configured")
        self.record()
        res = SyncResult()
        self.repo.git("fetch", "--prune", "--tags", REMOTE)
        cur = self.current_workspace()
        prefix = "refs/remotes/%s/" % REMOTE
        remote = {ref[len(prefix):]: sha for ref, sha, _ in self.repo.refs(prefix) if not ref.endswith("/HEAD")}
        local = {ref[len(HEADS):]: sha for ref, sha, _ in self.repo.refs(HEADS)}
        to_push = []
        for name, lsha in local.items():
            rsha = remote.get(name)
            if rsha is None or rsha == lsha:
                if rsha is None:
                    to_push.append(name)
                continue
            if self.repo.is_ancestor(rsha, lsha):
                to_push.append(name)
            elif self.repo.is_ancestor(lsha, rsha):
                self.repo.update_ref(HEADS + name, rsha, lsha, "strata: sync")
                res.pulled.append(name)
                if name == cur:
                    res.changed_docs += self.materialize(lsha, rsha)
            elif name == cur:
                plan = self.merge_plan(prefix + name)
                if plan.conflicts:
                    res.needs_merge.append(name)
                else:
                    res.changed_docs += self.merge(plan)
                    res.merged.append(name)
                    to_push.append(name)
            else:
                res.needs_merge.append(name)
        for name, rsha in remote.items():
            if name not in local:
                self.repo.update_ref(HEADS + name, rsha, ZERO, "strata: sync")
                res.pulled.append(name)
        if to_push:
            self.repo.git("push", REMOTE, *["%s%s:%s%s" % (HEADS, n, HEADS, n) for n in to_push])
            res.pushed = to_push
        self.repo.git("push", "--tags", REMOTE)
        return res
