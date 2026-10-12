# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run tests/gui_shots.py in FreeCAD on a hidden desktop with a memory ceiling (Windows).

    python run_gui_hidden.py <demo Gearbox folder> <screenshot folder> [--visible]

Uses C:/dev/scripts/surfacekit (HiddenDesktop, run_on_desktop with a job-object memory cap).
A throwaway --user-cfg keeps the user's FreeCAD preferences untouched.
"""

import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, r"C:\dev\scripts")
import surfacekit  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FREECAD = os.path.expandvars(r"%LOCALAPPDATA%\Programs\FreeCAD 1.1\bin\freecad.exe")

demo, out = sys.argv[1], sys.argv[2]
visible = "--visible" in sys.argv
os.makedirs(out, exist_ok=True)
with open(os.path.join(HERE, "gui_cfg.json"), "w", encoding="utf-8") as fh:
    json.dump({"demo": demo, "out": out}, fh)
cfg_dir = tempfile.mkdtemp(prefix="strata-cfg-")
user_cfg = os.path.join(cfg_dir, "user.cfg")
argv = [FREECAD, "--user-cfg", user_cfg, os.path.join(HERE, "gui_shots.py")]
desk = None if visible else surfacekit.HiddenDesktop()
try:
    code, so, se = surfacekit.run_on_desktop(argv, desk.path if desk else None, timeout=300, max_memory_gb=6)
finally:
    if desk:
        desk.close()
    shutil.rmtree(cfg_dir, ignore_errors=True)
print("exit:", code)
log = os.path.join(out, "gui_log.txt")
print(open(log, encoding="utf-8").read() if os.path.exists(log) else "no gui_log.txt")
