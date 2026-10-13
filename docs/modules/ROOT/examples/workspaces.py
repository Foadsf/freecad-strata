# SPDX-License-Identifier: LGPL-2.1-or-later
"""Minimal working example: try a design alternative in a workspace, compare it, merge it.

Run with FreeCAD's console (Strata installed):
    freecadcmd workspaces.py [empty-folder]
"""
import os
import sys
import tempfile

import FreeCAD as App

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
v1 = project.create_version("V1", "Baseline")

# A workspace is a separate line of work. Creating one switches to it.
project.create_workspace("Lightweight", description="thinner plate")
doc = App.openDocument(path)
doc.getObject("Body").Height = 6
doc.recompute()
doc.save()
project.record()
App.closeDocument(doc.Name)
print("Workspaces:", ", ".join(w.display for w in project.workspaces()))

# Main is untouched while you work elsewhere
project.switch_workspace("main")
doc = App.openDocument(path)
print("Height in Main:", doc.getObject("Body").Height)
App.closeDocument(doc.Name)

# What would change? A semantic comparison, feature by feature
for document, diff in project.diff(v1.sha, project.repo.rev_parse("refs/heads/Lightweight")).items():
    for change in diff.modified:
        for p in change.props:
            print("Compare %s: %s.%s %s -> %s" % (document, change.label, p.name, p.old, p.new))

# Bring the alternative into Main
plan = project.merge_plan("Lightweight")
print("Taken automatically:", plan.take_theirs, "| conflicts:", plan.conflicts)
project.merge(plan)
doc = App.openDocument(path)
print("Height in Main after merge:", doc.getObject("Body").Height)
print("Latest entry:", project.history()[0].title)
App.closeDocument(doc.Name)
project.repo.close()
