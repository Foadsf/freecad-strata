# SPDX-License-Identifier: LGPL-2.1-or-later
"""Semantic comparison of FreeCAD documents (Document.xml / GuiDocument.xml).

Turns XML differences into the language an engineer uses: "Edited Pad:
Length 7 mm -> 10 mm", "Added Fillet", "Changed appearance of Body".
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

UNITS = {
    "App::PropertyLength": "mm", "App::PropertyDistance": "mm", "App::PropertyPosition": "mm",
    "App::PropertyAngle": "°", "App::PropertyArea": "mm²", "App::PropertyVolume": "mm³",
    "App::PropertyForce": "N", "App::PropertyPressure": "MPa", "App::PropertySpeed": "mm/s",
}
VOLATILE_DOC_PROPS = {"LastModifiedDate", "LastModifiedBy", "FileName", "TransientDir", "CreationDate", "Uid"}
# Bookkeeping FreeCAD rewrites on its own; showing it would bury the real edit.
IGNORED_PROPS = {"_ElementMapVersion", "AttacherEngine", "AttacherType", "Proxy", "TipName", "_LinkTouched",
                 "_LinkOwner", "_Version", "Version"}
# Automatic helper objects: their own changes are never what the engineer did.
NOISE_TYPES = {"App::Origin", "App::Line", "App::Plane", "App::Point", "App::LocalCoordinateSystem"}
GUI_IGNORED_PROPS = {"SelectionStyle", "OnTopWhenSelected"}
# Consequences of other edits (adding a feature updates its Body's Group and Tip): kept in Compare, not in titles.
SUMMARY_QUIET = {"Group", "Tip", "BaseFeature", "Visibility", "ElementList", "_Body", "AddSubType"}


@dataclass
class PropChange:
    name: str
    old: str
    new: str


@dataclass
class ObjChange:
    name: str
    label: str
    type: str
    props: list[PropChange] = field(default_factory=list)
    geometry: bool = False
    appearance: list[str] = field(default_factory=list)
    renamed_from: str | None = None

    @property
    def empty(self) -> bool:
        return not (self.props or self.geometry or self.appearance or self.renamed_from)


@dataclass
class DocDiff:
    added: list[tuple[str, str, str]] = field(default_factory=list)  # (name, label, type)
    removed: list[tuple[str, str, str]] = field(default_factory=list)
    modified: list[ObjChange] = field(default_factory=list)
    document_props: list[PropChange] = field(default_factory=list)
    status: str = "modified"  # "added" | "removed" | "modified" | "unchanged"

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.modified or self.document_props)


# ---------------------------------------------------------------- parsing
def _fmt_num(v: str | None, unit: str = "") -> str:
    if v is None:
        return ""
    try:
        f = float(v)
    except ValueError:
        return v
    s = ("%.6g" % f) if abs(f) < 1e15 else v
    return (s + " " + unit).strip()


def _value(prop: ET.Element, ptype: str, file_hashes: dict | None) -> tuple[str, str]:
    """(comparison key, human-readable value)."""
    kids = list(prop)
    if not kids:
        return "", ""
    c = kids[0]
    tag = c.tag
    unit = UNITS.get(ptype, "")
    if tag == "Part" and c.get("file") is not None:  # geometry cache
        f = c.get("file")
        key = "geom:" + (file_hashes.get(f, f) if file_hashes else f)
        return key, "geometry"
    if tag in ("XLink", "XLinkSub"):  # external link: 'stamp' is a save time, not a change
        f = c.get("file", "")
        target = (f.rsplit("/", 1)[-1].rsplit(".", 1)[0] + " ▸ ") if f else ""
        subs = [s.get("value", "") for s in c]
        disp = target + c.get("name", "") + ("[%s]" % ", ".join(subs) if subs else "")
        return "xlink:%s|%s|%s" % (f, c.get("name", ""), ",".join(subs)), disp
    raw = b"".join(ET.tostring(k) for k in kids)
    key = hashlib.sha1(raw).hexdigest()
    if tag in ("Float", "Integer"):
        if ptype == "App::PropertyEnumeration" and c.get("CustomEnum"):
            return key, c.get("value", "")
        return key, _fmt_num(c.get("value"), unit)
    if tag in ("String", "Uuid", "Link", "Path"):
        return key, c.get("value", "")
    if tag == "Bool":
        return key, "yes" if c.get("value") == "true" else "no"
    if tag == "PropertyPlacement":
        pos = "(%s, %s, %s)" % tuple(_fmt_num(c.get(a)) for a in ("Px", "Py", "Pz"))
        ang = _fmt_num(str(float(c.get("A", "0")) * 57.29577951308232), "°")
        return key, "%s ∠%s" % (pos, ang)
    if tag == "PropertyVector":
        return key, "(%s, %s, %s)" % tuple(_fmt_num(c.get(a)) for a in ("valueX", "valueY", "valueZ"))
    if tag == "LinkSub":
        subs = [s.get("value", "") for s in c]
        return key, c.get("value", "") + ("[%s]" % ", ".join(subs) if subs else "")
    if tag in ("LinkList", "LinkSubList", "StringList", "FloatList"):
        items = [s.get("value") or s.get("obj") or "" for s in c]
        shown = ", ".join(items[:4]) + ("…" if len(items) > 4 else "")
        return key, "[%s]" % shown
    if tag in ("ConstraintList", "GeometryList"):
        return key, "%s %s" % (c.get("count", len(list(c))), "constraints" if tag == "ConstraintList" else "elements")
    if tag == "Map":
        return key, "%s entries" % c.get("count", len(list(c)))
    if tag == "PropertyColor":
        try:
            v = int(c.get("value", "0"))
            return key, "#%02X%02X%02X" % ((v >> 24) & 255, (v >> 16) & 255, (v >> 8) & 255)
        except ValueError:
            return key, c.get("value", "")
    return key, "…"


def _constraint_changes(old_el: ET.Element | None, new_el: ET.Element | None) -> list[PropChange]:
    if old_el is None or new_el is None:
        return []
    olds = old_el.findall(".//Constrain")
    news = new_el.findall(".//Constrain")
    if len(olds) != len(news):
        return [PropChange("Constraints", "%d" % len(olds), "%d" % len(news))]
    out = []
    for i, (a, b) in enumerate(zip(olds, news)):
        if a.get("Value") != b.get("Value"):
            label = b.get("Name") or "#%d" % (i + 1)
            unit = "°" if b.get("Type") in ("5", "7") else "mm"  # rough: Angle types -> degrees
            va, vb = a.get("Value"), b.get("Value")
            if unit == "°":
                try:
                    va, vb = str(float(va) * 57.29577951308232), str(float(vb) * 57.29577951308232)
                except (TypeError, ValueError):
                    pass
            out.append(PropChange("Constraint " + label, _fmt_num(va, unit), _fmt_num(vb, unit)))
    return out


@dataclass
class ParsedObject:
    name: str
    type: str
    label: str
    props: dict  # name -> (ptype, key, display, element)


def parse_document(xml: bytes | None, file_hashes: dict | None = None) -> tuple[dict, dict]:
    """Returns (objects: name -> ParsedObject, doc_props: name -> (key, display))."""
    if not xml:
        return {}, {}
    root = ET.fromstring(xml)
    types = {o.get("name"): o.get("type", "") for o in root.findall("./Objects/Object")}
    objects: dict[str, ParsedObject] = {}
    for o in root.findall("./ObjectData/Object"):
        name = o.get("name")
        if types and name not in types:  # <Objects> is authoritative
            continue
        props = {}
        for p in o.findall("./Properties/Property"):
            pname, ptype = p.get("name"), p.get("type", "")
            if pname in IGNORED_PROPS:
                continue
            key, disp = _value(p, ptype, file_hashes)
            props[pname] = (ptype, key, disp, p)
        label = props.get("Label", (None, None, name))[2] or name
        objects[name] = ParsedObject(name, types.get(name, ""), label, props)
    for name, t in types.items():  # objects without data block
        objects.setdefault(name, ParsedObject(name, t, name, {}))
    doc_props = {}
    for p in root.findall("./Properties/Property"):
        if p.get("name") in VOLATILE_DOC_PROPS:
            continue
        key, disp = _value(p, p.get("type", ""), None)
        doc_props[p.get("name")] = (key, disp)
    return objects, doc_props


def parse_gui(xml: bytes | None) -> dict[str, dict[str, tuple[str, str]]]:
    if not xml:
        return {}
    root = ET.fromstring(xml)
    out = {}
    for vp in root.findall("./ViewProviderData/ViewProvider"):
        props = {}
        for p in vp.findall("./Properties/Property"):
            if p.get("name") in GUI_IGNORED_PROPS:
                continue
            props[p.get("name")] = _value(p, p.get("type", ""), None)
        out[vp.get("name")] = props
    return out


# ---------------------------------------------------------------- diff
def short_type(t: str) -> str:
    return t.split("::")[-1] if t else "object"


def diff_documents(old_xml, new_xml, old_files=None, new_files=None, old_gui=None, new_gui=None) -> DocDiff:
    """old_files/new_files: archive entry name -> content hash (to detect geometry changes)."""
    d = DocDiff()
    if old_xml is None and new_xml is None:
        d.status = "unchanged"
        return d
    old, old_doc = parse_document(old_xml, old_files)
    new, new_doc = parse_document(new_xml, new_files)
    if old_xml is None:
        d.status = "added"
    elif new_xml is None:
        d.status = "removed"
    for name in new:
        if name not in old:
            o = new[name]
            d.added.append((name, o.label, o.type))
    for name in old:
        if name not in new:
            o = old[name]
            d.removed.append((name, o.label, o.type))
    gold, gnew = parse_gui(old_gui), parse_gui(new_gui)
    for name, n in new.items():
        o = old.get(name)
        if o is None or n.type in NOISE_TYPES:
            continue
        ch = ObjChange(name, n.label, n.type)
        mapmode = n.props.get("MapMode", (None, None, "0"))[2]
        attached = mapmode not in ("0", "", "Deactivated")  # placement follows the attachment
        if o.label != n.label:
            ch.renamed_from = o.label
        for pname, (ptype, key, disp, el) in n.props.items():
            if pname == "Label" or (attached and pname == "Placement"):
                continue
            prev = o.props.get(pname)
            if prev is not None and prev[1] == key:
                continue
            if disp == "geometry":
                ch.geometry = True
                continue
            if prev is not None and prev[2] == disp and disp != "…" and \
                    ptype != "Sketcher::PropertyConstraintList":
                continue  # same value at display precision: floating-point noise
            if ptype == "Sketcher::PropertyConstraintList":
                ch.props.extend(_constraint_changes(prev[3] if prev else None, el) or
                                [PropChange("Constraints", prev[2] if prev else "", disp)])
                continue
            ch.props.append(PropChange(pname, prev[2] if prev else "—", disp))
        for pname in o.props:
            if pname not in n.props and pname != "Label":
                ch.props.append(PropChange(pname, o.props[pname][2], "—"))
        gp_old, gp_new = gold.get(name, {}), gnew.get(name, {})
        for pname, (key, _disp) in gp_new.items():
            if gp_old.get(pname, (None,))[0] != key and gp_old:
                ch.appearance.append(pname)
        if not ch.empty:
            d.modified.append(ch)
    for pname, (key, disp) in new_doc.items():
        prev = old_doc.get(pname)
        if old_xml is not None and (prev is None or prev[0] != key):
            d.document_props.append(PropChange(pname, prev[1] if prev else "—", disp))
    if d.status == "modified" and d.empty:
        d.status = "unchanged"
    return d


def _has_unit(p: PropChange) -> bool:
    return any(p.new.endswith(" " + u) for u in set(UNITS.values()))


def _prop_phrase(p: PropChange) -> str:
    if p.old in ("", "—"):
        return "%s = %s" % (p.name, p.new)
    return "%s %s → %s" % (p.name, p.old, p.new)


def summarize(diff: DocDiff, max_items: int = 3) -> str:
    """One-line, engineer-readable description of a change."""
    if diff.status == "added":
        return "Created document"
    if diff.status == "removed":
        return "Deleted document"
    items = []

    def rank(ch):  # parameter edits first, then other edits, then rebuild-only and appearance-only
        if any(p.name.startswith("Constraint") or _has_unit(p) for p in ch.props):
            return 0
        return 1 if ch.props or ch.renamed_from else 2 if ch.geometry else 3
    consequences = []
    for ch in sorted(diff.modified, key=rank):
        if ch.renamed_from:
            items.append("Renamed %s → %s" % (ch.renamed_from, ch.label))
        params = [p for p in ch.props if p.name not in SUMMARY_QUIET]
        if params:
            items.append("Edited %s: %s" % (ch.label, "; ".join(_prop_phrase(p) for p in params[:2])))
        elif ch.geometry and not ch.renamed_from:
            consequences.append("Rebuilt %s" % ch.label)
        elif (ch.appearance or ch.props) and not ch.renamed_from:
            consequences.append(("Changed appearance of %s" if ch.appearance else "Updated %s") % ch.label)
    items = [("Added %s" % label) for _, label, _ in diff.added] + \
        [("Deleted %s" % label) for _, label, _ in diff.removed] + items
    if not items:  # knock-on effects only when nothing more meaningful happened
        items = consequences
    if not items:
        if diff.document_props:
            return "Edited document properties"
        return "Saved (no model changes)"
    text = " · ".join(items[:max_items])
    if len(items) > max_items:
        text += " · +%d more" % (len(items) - max_items)
    return text
