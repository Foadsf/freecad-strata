# SPDX-License-Identifier: LGPL-2.1-or-later
"""Regenerate strata/resources/icons/*.svg (toolbar icons) from the icon set in strata/ui/theme.py.

Pure Python: reads the _ICONS table without importing Qt.
"""

import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
THEME = os.path.join(ROOT, "strata", "ui", "theme.py")
OUT = os.path.join(ROOT, "strata", "resources", "icons")
COLOR = "#2F7CF6"
NAMES = ["history", "version", "compare", "release", "sync", "properties", "workspace", "branch", "merge"]


def load_icons():
    tree = ast.parse(open(THEME, encoding="utf-8").read())
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "_ICONS":
            return ast.literal_eval(node.value)
    raise SystemExit("_ICONS not found")


def main():
    icons = load_icons()
    os.makedirs(OUT, exist_ok=True)
    for name in NAMES:
        body = icons[name].replace('"C"', '"%s"' % COLOR)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 24 24" fill="none" '
               'stroke="%s" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">%s</svg>\n') % (COLOR, body)
        with open(os.path.join(OUT, name + ".svg"), "w", encoding="utf-8") as fh:
            fh.write(svg)
    print("wrote %d icons to %s" % (len(NAMES), OUT))


if __name__ == "__main__":
    main()
