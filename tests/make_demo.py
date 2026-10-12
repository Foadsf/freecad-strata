# SPDX-License-Identifier: LGPL-2.1-or-later
"""Build a demo project with realistic history (run with freecadcmd).

    freecadcmd tests/make_demo.py <target-folder>
Creates <target>/Gearbox with two parts and an assembly, two engineers, versions,
a workspace with an alternative design and a release.
"""

import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import FreeCAD as App  # noqa: E402
import Part  # noqa: E402

from strata.project import Project  # noqa: E402

target = next((a for a in sys.argv[1:] if not a.endswith(".py")), os.path.join(HERE, "demo"))
root = os.path.join(target, "Gearbox")
shutil.rmtree(root, ignore_errors=True)
os.makedirs(os.path.join(root, "Parts"))
paths = {"Bracket": os.path.join(root, "Parts", "Bracket.FCStd"),
         "Shaft": os.path.join(root, "Parts", "Shaft.FCStd"),
         "Gearbox": os.path.join(root, "Gearbox.FCStd")}
clock = [time.time() - 3 * 86400]


def tick(minutes):
    clock[0] += minutes * 60


def bracket():
    doc = App.newDocument("Bracket")
    body = doc.addObject("PartDesign::Body", "Body")
    sk = body.newObject("Sketcher::SketchObject", "Sketch")
    pts = [App.Vector(0, 0, 0), App.Vector(60, 0, 0), App.Vector(60, 40, 0), App.Vector(0, 40, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]))
    pad = body.newObject("PartDesign::Pad", "Pad")
    pad.Label = "Base plate"
    pad.Profile = sk
    pad.Length = 8
    doc.recompute()
    body.Visibility = True
    doc.Id = "BRK-100"
    doc.saveAs(paths["Bracket"])
    return doc


def shaft():
    doc = App.newDocument("Shaft")
    cyl = doc.addObject("Part::Cylinder", "Shaft")
    cyl.Radius = 6
    cyl.Height = 80
    doc.recompute()
    doc.Id = "SFT-200"
    doc.saveAs(paths["Shaft"])
    return doc


def assembly(b, s):
    doc = App.newDocument("Gearbox")
    doc.saveAs(paths["Gearbox"])
    l1 = doc.addObject("App::Link", "Bracket")
    l1.LinkedObject = b.getObject("Body")
    l2 = doc.addObject("App::Link", "Shaft")
    l2.LinkedObject = s.getObject("Shaft")
    l2.Placement.Base = App.Vector(30, 20, 8)
    doc.recompute()
    doc.save()
    return doc


def as_user(p, name):
    p.repo.set_identity(name, name.lower().replace(" ", ".") + "@example.com")


time.time = lambda: clock[0]  # the demo's own clock: three days of work in a few seconds


def record(p, doc, title=None):
    tick(23)
    p.record([p.rel(doc.FileName)], title=title)


b, s = bracket(), shaft()
a = assembly(b, s)
p = Project.create(root, "Ada Lovelace", "ada@example.com")

as_user(p, "Ada Lovelace")
b.getObject("Pad").Length = 10
b.recompute()
b.save()
record(p, b)
hole = b.getObject("Body").newObject("PartDesign::Pocket", "Pocket")
hole.Label = "Bore"
sk2 = b.getObject("Body").newObject("Sketcher::SketchObject", "Sketch001")
sk2.addGeometry(Part.Circle(App.Vector(30, 20, 0), App.Vector(0, 0, 1), 6.2))
hole.Profile = sk2
hole.Type = 1
b.recompute()
b.save()
record(p, b)
v1 = p.create_version("V1", "Initial concept")

tick(60 * 22)  # next day
as_user(p, "Bob Smith")
s.getObject("Shaft").Height = 95
s.recompute()
s.save()
record(p, s)
b.getObject("Pad").Length = 12
b.recompute()
b.save()
record(p, b)
v2 = p.create_version("V2", "Prototype sent to supplier")
p.release(v2, "Parts/Bracket.FCStd", "BRK-100", description="Prototype")

as_user(p, "Ada Lovelace")
p.create_workspace("Lightweight", description="Thinner plate, smaller bore")
b.getObject("Pad").Length = 6
b.recompute()
b.save()
record(p, b)
b.getObject("Pad").Label = "Base plate (thin)"
b.save()
record(p, b)
p.create_version("LW-1", "Lightweight option for review")

tick(60 * 20)  # next day
p.switch_workspace("main")
for name in list(App.listDocuments()):
    App.closeDocument(name)
s = App.openDocument(paths["Shaft"])
s.getObject("Shaft").Radius = 6.5
s.recompute()
s.save()
as_user(p, "Bob Smith")
record(p, s)
as_user(p, "Ada Lovelace")
for proj in Project._instances.values():
    proj.repo.close()
print("demo ready:", root)
