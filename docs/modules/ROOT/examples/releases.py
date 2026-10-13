# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimal working example: release a version, revise it, obsolete the old revision.

Run with FreeCAD's console (Strata installed):
    freecadcmd releases.py [empty-folder]
"""
import os
import sys
import tempfile

import FreeCAD as App

from strata.project import Project

folder = next((a for a in sys.argv[1:] if not a.endswith(".py")), None) or tempfile.mkdtemp()
path = os.path.join(folder, "Shaft.FCStd")

doc = App.newDocument("Shaft")
shaft = doc.addObject("Part::Cylinder", "Shaft")
shaft.Radius, shaft.Height = 6, 80
doc.recompute()
doc.saveAs(path)

project = Project.create(folder, "Ada Lovelace", "ada@example.com")
v1 = project.create_version("V1", "Prototype")

# Release V1 as part number SFT-200; the revision letter is chosen automatically
rev_a = project.release(v1, "Shaft.FCStd", "SFT-200", description="Prototype")
print("Released %s rev %s (%s)" % (rev_a.part_number, rev_a.revision, rev_a.state))

# A design change, a new version, released through an approval step
shaft.Height = 95
doc.recompute()
doc.save()
project.record()
v2 = project.create_version("V2", "Longer shaft")
rev_b = project.release(v2, "Shaft.FCStd", "SFT-200", description="Longer shaft", approval=True)
print("Submitted %s rev %s (%s)" % (rev_b.part_number, rev_b.revision, rev_b.state))

project.set_release_state(rev_b, "Released")   # approve
project.set_release_state(rev_a, "Obsolete")   # retire the old revision

print("Release register:")
for r in project.releases():
    print("  %-8s rev %-2s %-9s from %s (%s)" % (r.part_number, r.revision, r.state, r.version, r.description))
print("Next revision would be:", project.next_revision("SFT-200"))
App.closeDocument(doc.Name)
project.repo.close()
