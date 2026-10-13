# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimal working example: properties, merge strategies, combining a properties edit, undo merge.

Run with FreeCAD's console (Strata installed):
    freecadcmd merging.py [empty-folder]
"""
import os
import sys
import tempfile

import FreeCAD as App

from strata import fc
from strata.project import Project

folder = next((a for a in sys.argv[1:] if not a.endswith(".py")), None) or tempfile.mkdtemp()
path = os.path.join(folder, "Bracket.FCStd")

doc = App.newDocument("Bracket")
body = doc.addObject("Part::Box", "Body")
body.Length, body.Width, body.Height = 60, 40, 12
doc.recompute()
doc.saveAs(path)
App.closeDocument(doc.Name)

project = Project.create(folder, "Ada Lovelace", "ada@example.com")
project.create_version("V1", "Baseline")

# Lightweight changes the model ...
project.create_workspace("Lightweight")
doc = App.openDocument(path)
doc.getObject("Body").Height = 6
doc.recompute()
doc.save()
project.record()
App.closeDocument(doc.Name)

# ... while Main only gives the document a part number and properties (one history entry)
project.switch_workspace("main")
fc.set_document_properties(project, {"Bracket.FCStd": {
    "Part number": "BRK-100", "Description": "Mounting bracket", "Material": "S235"}})
print("Latest entry:", project.history()[0].title)

# Both sides changed the bracket, but one side only its properties: no conflict
plan = project.merge_plan("Lightweight")
print("Conflicts:", plan.conflicts, "| combined automatically:", plan.auto_merge)
project.merge(plan)


def state():
    d = App.openDocument(path)
    out = "Height %s, part number %s, material %s" % (d.getObject("Body").Height, d.Id, dict(d.Meta).get("Material"))
    App.closeDocument(d.Name)
    return out


print("After Merge changes:", state())

# Undo is possible right after a merge; then try another strategy
project.undo_merge()
print("After undo:", state(), "| latest entry:", project.history()[0].title)
project.merge(project.merge_plan("Lightweight"), strategy="keep")
print("After Keep Main:", state(), "| latest entry:", project.history()[0].title)
project.repo.close()
