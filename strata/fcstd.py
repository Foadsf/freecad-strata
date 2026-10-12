# SPDX-License-Identifier: LGPL-2.1-or-later
"""Explode a FreeCAD .FCStd archive into tracked text entries and rebuild it.

An .FCStd is a zip. FreeCAD rewrites the whole archive on every save and drops
entries it does not know, so history cannot live inside the file; Strata keeps
it beside the files instead and rebuilds archives on restore.

By default only textual entries (Document.xml, GuiDocument.xml, ASCII BREP,
element maps, ...) go into the shared history. Binary entries (thumbnails,
colour tables, binary BREP) are kept in a local content-addressed store and
recorded in the manifest, so restores on this machine are exact.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
import zlib

MANIFEST = ".strata.json"


def is_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def read_entries(path: str) -> list[tuple[str, bytes]]:
    with zipfile.ZipFile(path) as z:
        return [(i.filename, z.read(i.filename)) for i in z.infolist() if not i.is_dir()]


def explode(path: str, track_binary: bool = False):
    """Returns (tracked: name->bytes, binaries: name->bytes, manifest dict)."""
    tracked, binaries, order, binary_index = {}, {}, [], {}
    for name, data in read_entries(path):
        order.append(name)
        if track_binary or is_text(data):
            tracked[name] = data
        else:
            binaries[name] = data
            binary_index[name] = blob_sha(data)
    manifest = {"format": 1, "order": order, "binary": binary_index}
    return tracked, binaries, manifest


def manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, indent=1, sort_keys=True).encode("utf-8")


def rebuild(dest: str, tracked: dict[str, bytes], manifest: dict, binary_lookup=None) -> list[str]:
    """Write an .FCStd atomically. Returns names of binary entries that could not be restored."""
    current = {}
    if os.path.isfile(dest):
        try:
            current = dict(read_entries(dest))
        except (zipfile.BadZipFile, OSError):
            current = {}
    missing = []
    order = list(manifest.get("order") or [])
    for name in tracked:
        if name not in order:
            order.append(name)
    if "Document.xml" in order:  # FreeCAD expects Document.xml first
        order.remove("Document.xml")
        order.insert(0, "Document.xml")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in order:
            if name in tracked:
                data = tracked[name]
            else:
                sha = manifest.get("binary", {}).get(name)
                data = binary_lookup(sha) if (binary_lookup and sha) else None
                if data is None and name in current:
                    data = current[name]  # best effort: same-named entry from the file on disk
                if data is None:
                    missing.append(name)
                    continue
            z.writestr(name, data)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    tmp = dest + ".strata-tmp"
    with open(tmp, "wb") as fh:
        fh.write(buf.getvalue())
    os.replace(tmp, dest)
    return missing


class BlobStore:
    """Local content-addressed store for binary entries that are not shared."""

    def __init__(self, root: str):
        self.root = root

    def _path(self, sha: str) -> str:
        return os.path.join(self.root, sha[:2], sha[2:])

    def put(self, data: bytes) -> str:
        sha = blob_sha(data)
        p = self._path(sha)
        if not os.path.exists(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p + ".tmp", "wb") as fh:
                fh.write(zlib.compress(data, 6))
            os.replace(p + ".tmp", p)
        return sha

    def get(self, sha: str) -> bytes | None:
        p = self._path(sha)
        if not os.path.exists(p):
            return None
        with open(p, "rb") as fh:
            return zlib.decompress(fh.read())
