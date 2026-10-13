# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimal working example: version control for one part, from a script.

Run with FreeCAD's console (Strata installed):
    freecadcmd first_project.py [empty-folder]
"""
import os
import sys
import tempfile

import FreeCAD as App

from strata.project import Project

folder = next((a for a in sys.argv[1:] if not a.endswith(".py")), None) or tempfile.mkdtemp()
path = os.path.join(folder, "Plate.FCStd")

# A simple part, saved into the project folder
doc = App.newDocument("Plate")
plate = doc.addObject("Part::Box", "Plate")
plate.Length, plate.Width, plate.Height = 100, 60, 8
doc.recompute()
doc.saveAs(path)

# 1. Turn on version control for the folder (records "Start")
project = Project.create(folder, "Ada Lovelace", "ada@example.com")

# 2. Edit and save. In the FreeCAD window Strata records every save by itself;
#    from a script, call record() after saving.
plate.Height = 10
doc.recompute()
doc.save()
project.record()

# 3. Name the milestone
v1 = project.create_version("V1", "First prototype")

# 4. Keep working ...
plate.Height = 4
doc.recompute()
doc.save()
project.record()
print("Height now:", plate.Height)

# 5. ... and go back in time. Restore adds a new history entry; nothing is lost.
App.closeDocument(doc.Name)
project.restore(v1.sha)
doc = App.openDocument(path)
print("Height after restoring V1:", doc.getObject("Plate").Height)

print("History (newest first):")
for entry in project.history():
    marks = " [%s]" % ", ".join(entry.versions) if entry.versions else ""
    print("  %-13s %s%s" % (entry.author, entry.title, marks))
App.closeDocument(doc.Name)
project.repo.close()
