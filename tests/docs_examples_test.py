# SPDX-License-Identifier: LGPL-2.1-or-later
"""Run every documentation example inside FreeCAD and compare its output with the .out file the docs show.

    freecadcmd tests/docs_examples_test.py            -> tests/docs_examples_result.txt
    freecadcmd tests/docs_examples_test.py --update   -> rewrites the .out files (review the diff!)
"""
import contextlib
import io
import os
import runpy
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXAMPLES = os.path.join(ROOT, "docs", "modules", "ROOT", "examples")
sys.path.insert(0, ROOT)
UPDATE = "--update" in sys.argv
lines, failed = [], 0

for name in sorted(f for f in os.listdir(EXAMPLES) if f.endswith(".py")):
    script = os.path.join(EXAMPLES, name)
    expected_file = script[:-3] + ".out"
    work = tempfile.mkdtemp(prefix="strata-example-")
    buf = io.StringIO()
    argv = sys.argv
    try:
        sys.argv = [script, work]
        with contextlib.redirect_stdout(buf):
            runpy.run_path(script, run_name="__main__")
        out = buf.getvalue()
        if UPDATE or not os.path.exists(expected_file):
            with open(expected_file, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(out)
            lines.append("WROTE %s" % os.path.basename(expected_file))
        else:
            expected = open(expected_file, encoding="utf-8").read()
            if out == expected:
                lines.append("PASS %s" % name)
            else:
                failed += 1
                lines.append("FAIL %s\n--- expected\n%s--- got\n%s" % (name, expected, out))
    except Exception:
        failed += 1
        lines.append("ERROR %s\n%s\n%s" % (name, buf.getvalue(), traceback.format_exc()))
    finally:
        sys.argv = argv
        from strata.project import Project
        for p in Project._instances.values():
            p.repo.close()
        Project._instances.clear()
        shutil.rmtree(work, ignore_errors=True)

lines.append("RESULT %s" % ("PASS" if not failed else "FAIL (%d)" % failed))
with open(os.path.join(HERE, "docs_examples_result.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
if failed:
    sys.exit(1)
