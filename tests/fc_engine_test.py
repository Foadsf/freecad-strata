# SPDX-License-Identifier: LGPL-2.1-or-later
"""End-to-end engine test inside FreeCAD (console mode).

    freecadcmd tests/fc_engine_test.py      -> writes tests/fc_engine_result.txt, exit code 0 on success
"""

import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

from strata import fc  # noqa: E402
from strata.project import Project  # noqa: E402

RESULT = os.path.join(HERE, "fc_engine_result.txt")
lines = []


def check(cond, msg):
    lines.append(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        raise AssertionError(msg)


def make_part(path):
    doc = App.newDocument("Bracket")
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    pts = [App.Vector(0, 0, 0), App.Vector(10, 0, 0), App.Vector(10, 10, 0), App.Vector(0, 10, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Profile = sk
    pad.Length = 5
    doc.recompute()
    doc.saveAs(path)
    return doc


def make_assembly(path, part_doc):
    doc = App.newDocument("Assembly")
    doc.saveAs(path)  # external links need a saved owner
    link = doc.addObject("App::Link", "BracketLink")
    link.LinkedObject = part_doc.getObject("Body")
    link.Placement.Base = App.Vector(50, 0, 0)
    doc.recompute()
    doc.saveAs(path)
    return doc


def reopen(path):
    for d in list(App.listDocuments().values()):
        if os.path.normcase(d.FileName) == os.path.normcase(path):
            App.closeDocument(d.Name)
    return App.openDocument(path)


def close_all():
    for name in list(App.listDocuments()):
        App.closeDocument(name)


def main():
    tmp = tempfile.mkdtemp(prefix="strata-fc-")
    root = os.path.join(tmp, "Gearbox")
    os.makedirs(os.path.join(root, "Parts"))
    part_path = os.path.join(root, "Parts", "Bracket.FCStd")
    asm_path = os.path.join(root, "Gearbox.FCStd")
    try:
        part = make_part(part_path)
        make_assembly(asm_path, part)
        close_all()

        p = Project.create(root, "Ada", "ada@example.com")
        check(p.documents() == ["Gearbox.FCStd", "Parts/Bracket.FCStd"], "start snapshot has both documents")

        # edit a parameter, save, record (what the save observer does)
        part = App.openDocument(part_path)
        part.getObject("Pad").Length = 12
        part.recompute()
        part.save()
        t0 = time.time()
        p.record(["Parts/Bracket.FCStd"])
        dt = time.time() - t0
        title = p.history()[0].title
        lines.append("INFO record took %.0f ms, title: %s" % (dt * 1000, title))
        check(title.startswith("Edited Pad: Length 5 mm → 12 mm"), "history title leads with the parameter change")
        check("XZ-plane" not in title and "AttacherEngine" not in title, "no FreeCAD bookkeeping noise in title")
        v1 = p.create_version("V1", "12 mm pad")

        part.getObject("Pad").Length = 20
        part.recompute()
        part.save()
        p.record(["Parts/Bracket.FCStd"])
        close_all()

        # restore V1 and check FreeCAD reads the rebuilt archive, geometry included
        changed = p.restore(v1.sha)
        check(changed == ["Parts/Bracket.FCStd"], "restore rewrites only the changed document")
        part = App.openDocument(part_path)
        pad = part.getObject("Pad")
        check(abs(pad.Length.Value - 12) < 1e-9, "restored parameter Length = 12")
        check(abs(pad.Shape.Volume - 1200) < 1e-6, "restored BREP volume 1200 without recompute (got %.3f)"
              % pad.Shape.Volume)
        part.recompute()
        check(abs(pad.Shape.Volume - 1200) < 1e-6, "volume after recompute still 1200")
        asm = App.openDocument(asm_path)
        link = asm.getObject("BracketLink")
        check(link.LinkedObject is not None and link.LinkedObject.Document.Name == part.Name,
              "assembly link resolves to the restored part")
        check(abs(link.Shape.Volume - 1200) < 1e-6, "linked shape volume follows the part (got %.3f)" % link.Shape.Volume)
        close_all()

        # workspaces: an alternative design that leaves Main untouched
        p.create_workspace("Thin wall", v1.sha)
        part = App.openDocument(part_path)
        part.getObject("Pad").Length = 3
        part.recompute()
        part.save()
        p.record(["Parts/Bracket.FCStd"])
        close_all()
        p.switch_workspace("main")
        part = App.openDocument(part_path)
        check(abs(part.getObject("Pad").Shape.Volume - 1200) < 1e-6, "Main still has the 12 mm design")
        close_all()
        p.switch_workspace("Thin_wall")
        part = App.openDocument(part_path)
        check(abs(part.getObject("Pad").Shape.Volume - 300) < 1e-6, "Thin wall workspace has the 3 mm design")
        close_all()

        # preview a version without touching the project
        prev = fc.open_preview(p, v1.sha, "Parts/Bracket.FCStd", "V1")
        check(fc.is_preview(prev) and prev.Label == "Bracket @ V1", "preview opens read-only copy labelled @ V1")
        check(abs(prev.getObject("Pad").Shape.Volume - 1200) < 1e-6, "preview geometry is V1")
        close_all()

        # 3D compare overlay
        stats = fc.compare_overlay(p, v1.sha, p.head(), "Parts/Bracket.FCStd", "V1", "Thin wall")
        lines.append("INFO overlay: " + stats)
        check("1200.0 mm³ → 300.0 mm³" in stats, "overlay reports volume change")
        cmp = App.ActiveDocument
        check(len(cmp.Objects) == 2, "overlay document has before and after")
        close_all()

        # merge Thin wall into Main
        p.switch_workspace("main")
        plan = p.merge_plan("Thin_wall")
        check(plan.take_theirs == ["Parts/Bracket.FCStd"] and not plan.conflicts, "merge is conflict-free")
        p.merge(plan)
        part = App.openDocument(part_path)
        check(abs(part.getObject("Pad").Shape.Volume - 300) < 1e-6, "merged design opens in FreeCAD")
        close_all()

        # properties (Onshape: Workspace properties), written into closed documents, one history entry
        n_before = len(p.history())
        fc.set_document_properties(p, {
            "Parts/Bracket.FCStd": {"Part number": "BRK-100", "Description": "Bracket", "Material": "S235"},
            "Gearbox.FCStd": {"Part number": "GBX-001", "Description": "Gearbox assembly"}})
        props = p.document_properties("Parts/Bracket.FCStd")
        check((props["Part number"], props["Description"], props.get("Material")) == ("BRK-100", "Bracket", "S235"),
              "properties read back from the file: %s" % props)
        check(len(p.history()) == n_before + 1, "two documents' properties recorded as one entry")
        lines.append("INFO properties entry: " + p.history()[0].title)
        fc.set_document_properties(p, {"Parts/Bracket.FCStd": {"Material": ""}})
        check("Material" not in p.document_properties("Parts/Bracket.FCStd"), "empty value removes a custom property")
        lines.append("INFO single-document entry: " + p.history()[0].title)

        # export a released revision (Onshape: Revision history > Export)
        rel = p.release(p.create_version("V2", "for export"), "Parts/Bracket.FCStd", "BRK-100")
        out_dir = os.path.join(tmp, "export")
        files = fc.export_revision(p, rel.sha, rel.document, out_dir, "BRK-100_A")
        check(sorted(os.path.basename(f) for f in files) == ["BRK-100_A.FCStd", "BRK-100_A.step"],
              "export writes FCStd and STEP: %s" % files)
        step_shape = Part.read(os.path.join(out_dir, "BRK-100_A.step"))
        check(abs(step_shape.Volume - 300) < 1e-3, "exported STEP has the released geometry (volume %.3f)"
              % step_shape.Volume)
        close_all()
        lines.append("ALL PASSED")
        return 0
    except Exception:
        lines.append("ERROR " + traceback.format_exc())
        return 1
    finally:
        for proj in Project._instances.values():
            proj.repo.close()
        Project._instances.clear()
        with open(RESULT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
        shutil.rmtree(tmp, ignore_errors=True)


code = main()
if code:
    sys.exit(code)
