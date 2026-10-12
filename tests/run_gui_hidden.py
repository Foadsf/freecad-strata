# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run tests/gui_shots.py in the FreeCAD GUI and print its log.

    python tests/run_gui_hidden.py <work-folder> <screenshot-folder> [--freecad PATH] [--visible]

A throwaway --user-cfg keeps your FreeCAD preferences untouched. On Windows, if the optional
`surfacekit` helper is importable (STRATA_SURFACEKIT=<dir>), FreeCAD runs on a hidden desktop
inside a job object with a 6 GB memory ceiling; otherwise it runs as a normal window.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))


def default_freecad():
    if os.name == "nt":
        return os.path.expandvars(r"%LOCALAPPDATA%\Programs\FreeCAD 1.1\bin\freecad.exe")
    return shutil.which("freecad") or shutil.which("FreeCAD") or "freecad"


def main():
    args = [a for a in sys.argv[1:]]
    freecad = default_freecad()
    if "--freecad" in args:
        i = args.index("--freecad")
        freecad = args[i + 1]
        del args[i:i + 2]
    visible = "--visible" in args
    args = [a for a in args if a != "--visible"]
    work, out = os.path.abspath(args[0]), os.path.abspath(args[1])
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(HERE, "gui_cfg.json"), "w", encoding="utf-8") as fh:
        json.dump({"demo_parent": work, "out": out}, fh)
    cfg_dir = tempfile.mkdtemp(prefix="strata-cfg-")
    argv = [freecad, "--user-cfg", os.path.join(cfg_dir, "user.cfg"), os.path.join(HERE, "gui_shots.py")]
    kit = None
    if os.name == "nt" and not visible and os.environ.get("STRATA_SURFACEKIT"):
        sys.path.insert(0, os.environ["STRATA_SURFACEKIT"])
        try:
            import surfacekit as kit
        except ImportError:
            kit = None
    try:
        if kit is not None:
            desk = kit.HiddenDesktop()
            try:
                code, _, _ = kit.run_on_desktop(argv, desk.path, timeout=420, max_memory_gb=6)
            finally:
                desk.close()
        else:
            code = subprocess.run(argv, timeout=420).returncode
    finally:
        shutil.rmtree(cfg_dir, ignore_errors=True)
    print("exit:", code)
    log = os.path.join(out, "gui_log.txt")
    print(open(log, encoding="utf-8").read() if os.path.exists(log) else "no gui_log.txt")


if __name__ == "__main__":
    main()
