# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimal, dependency-free git plumbing used by Strata.

Objects are written directly as loose objects (fast, no process per blob);
reads go through one persistent ``git cat-file --batch`` process so packed
objects fetched from teammates work too. Ref updates, history walks, fetch
and push use the git executable, which must be installed.

Nothing in this module knows about FreeCAD.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import time
import zlib
from dataclasses import dataclass, field

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW: no console flash inside FreeCAD

ZERO = "0" * 40


class GitError(RuntimeError):
    pass


class GitMissing(GitError):
    pass


def find_git() -> str | None:
    exe = shutil.which("git")
    if exe:
        return exe
    if os.name == "nt":
        candidates = [
            r"C:\Program Files\Git\cmd\git.exe",
            r"C:\Program Files (x86)\Git\cmd\git.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe"),
        ]
        gh = os.path.expandvars(r"%LOCALAPPDATA%\GitHubDesktop")
        if os.path.isdir(gh):
            for d in sorted(os.listdir(gh), reverse=True):
                candidates.append(os.path.join(gh, d, "resources", "app", "git", "cmd", "git.exe"))
        for c in candidates:
            if os.path.isfile(c):
                return c
    return None


@dataclass
class Commit:
    sha: str
    tree: str
    parents: list[str]
    author: str
    email: str
    time: int
    message: str
    trailers: dict[str, str] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.message.split("\n", 1)[0]

    @property
    def body(self) -> str:
        parts = self.message.split("\n\n")
        body = [p for p in parts[1:] if not _is_trailer_block(p)]
        return "\n\n".join(body).strip()


def _is_trailer_block(block: str) -> bool:
    lines = [ln for ln in block.strip().splitlines() if ln.strip()]
    return bool(lines) and all(ln.startswith("Strata-") and ":" in ln for ln in lines)


def parse_trailers(message: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for ln in message.splitlines():
        if ln.startswith("Strata-") and ":" in ln:
            k, v = ln.split(":", 1)
            out[k[len("Strata-"):].strip().lower()] = v.strip()
    return out


class Repo:
    """A bare repository. Strata never uses a git working tree."""

    def __init__(self, git_dir: str, git_exe: str | None = None):
        self.git_dir = os.path.abspath(git_dir)
        self.git_exe = git_exe or find_git()
        if not self.git_exe:
            raise GitMissing("Git is not installed")
        self._cat = None
        self._cat_lock = threading.Lock()
        self._cache: dict[str, tuple[str, bytes]] = {}

    # ------------------------------------------------------------ processes
    def git(self, *args: str, input: bytes | None = None, check: bool = True, env: dict | None = None) -> str:
        full_env = dict(os.environ)
        full_env.update({"GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        if env:
            full_env.update(env)
        proc = subprocess.run(
            [self.git_exe, "--git-dir", self.git_dir, *args],
            input=input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
            creationflags=_NO_WINDOW,
        )
        if check and proc.returncode != 0:
            raise GitError(proc.stderr.decode("utf-8", "replace").strip() or "git %s failed" % args[0])
        return proc.stdout.decode("utf-8", "replace")

    def close(self):
        with self._cat_lock:
            if self._cat is not None:
                try:
                    self._cat.stdin.close()
                    self._cat.wait(timeout=2)
                except Exception:
                    self._cat.kill()
                finally:
                    self._cat.stdout.close()
                self._cat = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @classmethod
    def init(cls, git_dir: str, user: str, email: str) -> "Repo":
        exe = find_git()
        if not exe:
            raise GitMissing("Git is not installed")
        os.makedirs(git_dir, exist_ok=True)
        subprocess.run([exe, "init", "--bare", "-q", git_dir], check=True, creationflags=_NO_WINDOW,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        repo = cls(git_dir, exe)
        repo.set_head("refs/heads/main")

        def q(v: str) -> str:
            return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
        with open(os.path.join(git_dir, "config"), "a", encoding="utf-8", newline="\n") as fh:
            fh.write("[user]\n\tname = %s\n\temail = %s\n[core]\n\tautocrlf = false\n[gc]\n\tautoDetach = false\n"
                     % (q(user), q(email)))
        repo._identity = (user, email)
        return repo

    def set_identity(self, user: str, email: str):
        self.git("config", "user.name", user)
        self.git("config", "user.email", email)
        self._identity = (user, email)

    def identity(self) -> tuple[str, str]:
        if getattr(self, "_identity", None) is None:
            out = self.git("config", "--get-regexp", r"^user\.(name|email)$", check=False)
            vals = dict(ln.split(" ", 1) for ln in out.splitlines() if " " in ln)
            self._identity = (vals.get("user.name", "Unknown"), vals.get("user.email", "unknown@localhost"))
        return self._identity

    # ------------------------------------------------------------ objects
    def write_object(self, kind: str, data: bytes) -> str:
        raw = b"%s %d\x00" % (kind.encode(), len(data)) + data
        sha = hashlib.sha1(raw).hexdigest()
        path = os.path.join(self.git_dir, "objects", sha[:2], sha[2:])
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = "%s.tmp%d" % (path, os.getpid())
            with open(tmp, "wb") as fh:
                fh.write(zlib.compress(raw, 1))
            try:
                os.replace(tmp, path)
            except OSError:
                if os.path.exists(tmp):
                    os.remove(tmp)
        self._cache[sha] = (kind, data)
        return sha

    def read_object(self, sha: str) -> tuple[str, bytes]:
        hit = self._cache.get(sha)
        if hit:
            return hit
        path = os.path.join(self.git_dir, "objects", sha[:2], sha[2:])
        if os.path.isfile(path):  # loose object: no process needed
            with open(path, "rb") as fh:
                raw = zlib.decompress(fh.read())
            nul = raw.index(b"\x00")
            kind = raw[:nul].split(b" ")[0].decode()
            result = (kind, raw[nul + 1:])
            self._cache[sha] = result
            return result
        with self._cat_lock:
            if self._cat is None or self._cat.poll() is not None:
                self._cat = subprocess.Popen(
                    [self.git_exe, "--git-dir", self.git_dir, "cat-file", "--batch"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    creationflags=_NO_WINDOW,
                )
            self._cat.stdin.write(sha.encode() + b"\n")
            self._cat.stdin.flush()
            header = self._cat.stdout.readline().decode().split()
            if len(header) < 3 or header[1] == "missing":
                raise GitError("object %s is missing" % sha)
            kind, size = header[1], int(header[2])
            data = self._read_exact(size)
            self._cat.stdout.read(1)  # trailing LF
        if len(self._cache) > 4096:
            self._cache.clear()
        self._cache[sha] = (kind, data)
        return kind, data

    def _read_exact(self, n: int) -> bytes:
        chunks, left = [], n
        while left:
            c = self._cat.stdout.read(left)
            if not c:
                raise GitError("git cat-file ended early")
            chunks.append(c)
            left -= len(c)
        return b"".join(chunks)

    def blob(self, sha: str) -> bytes:
        return self.read_object(sha)[1]

    # ------------------------------------------------------------ trees
    def write_tree(self, entries: dict[str, tuple[str, str]]) -> str:
        """entries: name -> (mode, sha). Mode '40000' for trees, '100644' for blobs."""
        def key(item):
            name, (mode, _) = item
            return name.encode() + (b"/" if mode == "40000" else b"")
        body = b"".join(
            b"%s %s\x00" % (mode.encode(), name.encode()) + bytes.fromhex(sha)
            for name, (mode, sha) in sorted(entries.items(), key=key)
        )
        return self.write_object("tree", body)

    def read_tree(self, sha: str) -> dict[str, tuple[str, str]]:
        kind, data = self.read_object(sha)
        if kind != "tree":
            raise GitError("%s is a %s, not a tree" % (sha, kind))
        out, i = {}, 0
        while i < len(data):
            sp = data.index(b" ", i)
            nul = data.index(b"\x00", sp)
            mode = data[i:sp].decode()
            name = data[sp + 1:nul].decode()
            out[name] = (mode, data[nul + 1:nul + 21].hex())
            i = nul + 21
        return out

    def flatten(self, tree: str, prefix: str = "") -> dict[str, str]:
        out = {}
        for name, (mode, sha) in self.read_tree(tree).items():
            if mode == "40000":
                out.update(self.flatten(sha, prefix + name + "/"))
            else:
                out[prefix + name] = sha
        return out

    def build_tree(self, files: dict[str, str]) -> str:
        """files: 'a/b/c.txt' -> blob sha. Returns root tree sha."""
        root: dict = {}
        for path, sha in files.items():
            node = root
            parts = path.split("/")
            for p in parts[:-1]:
                node = node.setdefault(p, {})
            node[parts[-1]] = sha

        def emit(node: dict) -> str:
            entries = {}
            for name, val in node.items():
                if isinstance(val, dict):
                    entries[name] = ("40000", emit(val))
                else:
                    entries[name] = ("100644", val)
            return self.write_tree(entries)
        return emit(root)

    def empty_tree(self) -> str:
        return self.write_tree({})

    # ------------------------------------------------------------ commits
    def write_commit(self, tree: str, parents: list[str], message: str,
                     author: tuple[str, str] | None = None, when: int | None = None) -> str:
        name, email = author or self.identity()
        when = int(when if when is not None else time.time())
        offset = -time.timezone if not time.localtime(when).tm_isdst else -time.altzone
        tz = "%s%02d%02d" % ("+" if offset >= 0 else "-", abs(offset) // 3600, abs(offset) % 3600 // 60)
        lines = ["tree " + tree] + ["parent " + p for p in parents]
        sig = "%s <%s> %d %s" % (name, email, when, tz)
        lines += ["author " + sig, "committer " + sig, "", message.rstrip() + "\n"]
        return self.write_object("commit", "\n".join(lines).encode("utf-8"))

    def read_commit(self, sha: str) -> Commit:
        kind, data = self.read_object(sha)
        if kind == "tag":
            return self.read_commit(self.peel(sha))
        text = data.decode("utf-8", "replace")
        head, _, message = text.partition("\n\n")
        tree, parents, author, email, when = "", [], "", "", 0
        for ln in head.splitlines():
            if ln.startswith("tree "):
                tree = ln[5:]
            elif ln.startswith("parent "):
                parents.append(ln[7:])
            elif ln.startswith("author "):
                rest = ln[7:]
                lt, gt = rest.find("<"), rest.find(">")
                author, email = rest[:lt].strip(), rest[lt + 1:gt]
                when = int(rest[gt + 1:].split()[0])
        return Commit(sha, tree, parents, author, email, when, message.strip(), parse_trailers(message))

    def peel(self, sha: str) -> str:
        kind, data = self.read_object(sha)
        while kind == "tag":
            sha = data.split(b"\n", 1)[0].split()[1].decode()
            kind, data = self.read_object(sha)
        return sha

    # ------------------------------------------------------------ refs
    # The "files" ref backend is read and written directly: spawning git costs
    # ~0.1 s per call on Windows, which would make every save feel sluggish.
    def _packed_refs(self) -> dict[str, str]:
        out, path = {}, os.path.join(self.git_dir, "packed-refs")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                for ln in fh:
                    if ln[:1] in ("#", "^") or not ln.strip():
                        continue
                    sha, ref = ln.strip().split(" ", 1)
                    out[ref] = sha
        return out

    def _read_ref(self, ref: str) -> str | None:
        path = os.path.join(self.git_dir, *ref.split("/"))
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                val = fh.read().strip()
            if val.startswith("ref: "):
                return self._read_ref(val[5:])
            return val or None
        return self._packed_refs().get(ref)

    def rev_parse(self, rev: str) -> str | None:
        if rev == "HEAD" or rev.startswith("refs/"):
            sha = self._read_ref(rev)
        elif len(rev) == 40:
            sha = rev
        else:
            sha = self._read_ref("refs/heads/" + rev) or self._read_ref("refs/tags/" + rev)
        if not sha:
            return None
        try:
            return self.peel(sha)
        except GitError:
            return None

    def update_ref(self, ref: str, new: str, old: str | None = None, reason: str = "strata"):
        path = os.path.join(self.git_dir, *ref.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        lock = path + ".lock"
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            raise GitError("%s is being updated by another process" % ref)
        try:
            if old is not None:
                cur = self._read_ref(ref)
                if (cur or ZERO) != old:
                    raise GitError("%s changed meanwhile (expected %s, found %s)" % (ref, old[:8], (cur or ZERO)[:8]))
            os.write(fd, (new + "\n").encode())
            os.close(fd)
            fd = None
            os.replace(lock, path)
        finally:
            if fd is not None:
                os.close(fd)
            if os.path.exists(lock):
                os.remove(lock)

    def delete_ref(self, ref: str):
        path = os.path.join(self.git_dir, *ref.split("/"))
        if os.path.isfile(path):
            os.remove(path)
        if ref in self._packed_refs():
            self.git("update-ref", "-d", ref)

    def head_ref(self) -> str:
        with open(os.path.join(self.git_dir, "HEAD"), encoding="utf-8") as fh:
            val = fh.read().strip()
        return val[5:] if val.startswith("ref: ") else "refs/heads/main"

    def set_head(self, ref: str):
        with open(os.path.join(self.git_dir, "HEAD"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write("ref: %s\n" % ref)

    def refs(self, prefix: str) -> list[tuple[str, str, str]]:
        """Refs under prefix ('refs/heads/'): (refname, object sha, peeled commit sha)."""
        found = {r: s for r, s in self._packed_refs().items() if r.startswith(prefix)}
        base = os.path.join(self.git_dir, *prefix.rstrip("/").split("/"))
        for dirpath, _, files in os.walk(base):
            for f in files:
                if f.endswith(".lock"):
                    continue
                full = os.path.join(dirpath, f)
                ref = os.path.relpath(full, self.git_dir).replace(os.sep, "/")
                with open(full, encoding="utf-8") as fh:
                    val = fh.read().strip()
                if len(val) == 40:
                    found[ref] = val
        rows = []
        for ref in sorted(found):
            try:
                rows.append((ref, found[ref], self.peel(found[ref])))
            except GitError:
                continue
        return rows

    def tag(self, ref_name: str, target: str, message: str):
        name, email = self.identity()
        when = int(time.time())
        body = "object %s\ntype commit\ntag %s\ntagger %s <%s> %d +0000\n\n%s\n" % (
            target, ref_name.rsplit("/", 1)[-1], name, email, when, message.rstrip())
        sha = self.write_object("tag", body.encode("utf-8"))
        self.update_ref(ref_name, sha, ZERO)
        return sha

    def read_tag(self, sha: str) -> dict:
        kind, data = self.read_object(sha)
        if kind != "tag":
            return {"message": "", "tagger": "", "time": 0, "object": sha}
        head, _, msg = data.decode("utf-8", "replace").partition("\n\n")
        info = {"message": msg.strip(), "tagger": "", "time": 0}
        for ln in head.splitlines():
            if ln.startswith("object "):
                info["object"] = ln[7:]
            elif ln.startswith("tagger "):
                rest = ln[7:]
                lt, gt = rest.find("<"), rest.find(">")
                info["tagger"] = rest[:lt].strip()
                info["time"] = int(rest[gt + 1:].split()[0])
        return info

    def parents_of(self, sha: str) -> list[str]:
        kind, data = self.read_object(sha)
        out = []
        for ln in data.split(b"\n\n", 1)[0].split(b"\n"):
            if ln.startswith(b"parent "):
                out.append(ln[7:].decode())
        return out

    def topo(self, tips: list[str]) -> list[tuple[str, list[str]]]:
        """All commits reachable from tips, children before parents, newest first: (sha, parents)."""
        parents: dict[str, list[str]] = {}
        stack = [self.rev_parse(t) or t for t in tips]
        while stack:
            s = stack.pop()
            if s in parents:
                continue
            parents[s] = self.parents_of(s)
            stack.extend(parents[s])
        times = {s: self.read_commit(s).time for s in parents}
        children: dict[str, int] = {s: 0 for s in parents}
        for s, ps in parents.items():
            for p in ps:
                children[p] += 1
        import heapq
        ready = [(-times[s], s) for s, n in children.items() if n == 0]
        heapq.heapify(ready)
        out = []
        while ready:
            _, s = heapq.heappop(ready)
            out.append((s, parents[s]))
            for p in parents[s]:
                children[p] -= 1
                if children[p] == 0:
                    heapq.heappush(ready, (-times[p], p))
        return out

    def log(self, revs: list[str], limit: int = 0, first_parent: bool = False) -> list[str]:
        if not revs:
            return []
        if first_parent and len(revs) == 1:
            out, sha = [], self.rev_parse(revs[0])
            while sha and (not limit or len(out) < limit):
                out.append(sha)
                ps = self.parents_of(sha)
                sha = ps[0] if ps else None
            return out
        shas = [s for s, _ in self.topo(revs)]
        return shas[:limit] if limit else shas

    def ancestors(self, sha: str) -> set[str]:
        seen, stack = set(), [sha]
        while stack:
            s = stack.pop()
            if s not in seen:
                seen.add(s)
                stack.extend(self.parents_of(s))
        return seen

    def merge_base(self, a: str, b: str) -> str | None:
        common = self.ancestors(a) & self.ancestors(b)
        if not common:
            return None
        # best common ancestor: one that is not an ancestor of another common ancestor
        for s, _ in self.topo(list(common)):
            return s
        return None

    def is_ancestor(self, a: str, b: str) -> bool:
        return a in self.ancestors(b)
