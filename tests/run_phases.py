# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run a multi-phase GUI test (embed_test.py, am_install_test.py) in FreeCAD, one start per phase.

    python tests/run_phases.py embed_test.py <out-folder> live prestart keys
    python tests/run_phases.py am_install_test.py <out-folder> install verify uninstall \
        --set repo=https://github.com/Foadsf/freecad-strata --set branch=main

All phases share one throwaway --user-cfg in <out-folder>, so preferences written by one phase are
seen by the next. On Windows, with STRATA_SURFACEKIT=<dir of surfacekit.py>, FreeCAD runs on a hidden
desktop inside a job object with a 6 GB memory ceiling; otherwise as a normal window.
Each phase's log (<out>/<prefix>_<phase>.log) is printed; the run stops at the first failing phase.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    args = sys.argv[1:]
    extra = {}
    while "--set" in args:
        i = args.index("--set")
        k, v = args[i + 1].split("=", 1)
        extra[k] = v
        del args[i:i + 2]
    freecad = os.path.expandvars(r"%LOCALAPPDATA%\Programs\FreeCAD 1.1\bin\freecad.exe") if os.name == "nt" else "freecad"
    if "--freecad" in args:
        i = args.index("--freecad")
        freecad = args[i + 1]
        del args[i:i + 2]
    script, out, phases = args[0], os.path.abspath(args[1]), args[2:]
    prefix = os.path.basename(script).replace("_test.py", "").replace("_install", "")
    cfg_name = {"embed": "embed_cfg.json", "am": "am_cfg.json"}[prefix]
    log_prefix = {"embed": "embed", "am": "am"}[prefix]
    os.makedirs(out, exist_ok=True)
    kit = None
    if os.name == "nt" and os.environ.get("STRATA_SURFACEKIT"):
        sys.path.insert(0, os.environ["STRATA_SURFACEKIT"])
        import surfacekit as kit
    for phase in phases:
        cfg = {"phase": phase, "out": out}
        cfg.update(extra)
        with open(os.path.join(HERE, cfg_name), "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        argv = [freecad, "--user-cfg", os.path.join(out, "user.cfg"),
                "--log-file", os.path.join(out, "fc_%s.log" % phase), os.path.join(HERE, os.path.basename(script))]
        if kit is not None:
            desk = kit.HiddenDesktop()
            try:
                code, _, _ = kit.run_on_desktop(argv, desk.path, timeout=660, max_memory_gb=6)
            finally:
                desk.close()
        else:
            code = subprocess.run(argv, timeout=660).returncode
        print("== phase %s exit %s" % (phase, code))
        log = os.path.join(out, "%s_%s.log" % (log_prefix, phase))
        print(open(log, encoding="utf-8").read() if os.path.exists(log) else "(no log)")
        if code != 0:
            sys.exit(1)


if __name__ == "__main__":
    main()
