# SPDX-License-Identifier: LGPL-2.1-or-later
"""Design tokens, stylesheet and icons. Follows FreeCAD's light or dark theme."""

from __future__ import annotations

from PySide import QtCore, QtGui, QtSvg, QtWidgets

LIGHT = {
    "bg": "#FFFFFF", "panel": "#F6F7F9", "surface": "#FFFFFF", "border": "#E2E6EB", "divider": "#EDF0F3",
    "text": "#1C2733", "muted": "#5F6B7A", "faint": "#98A2AE",
    "accent": "#1F6FEB", "accent_hover": "#185ACB", "accent_text": "#FFFFFF", "accent_soft": "#E9F1FE",
    "success": "#1A7F37", "success_soft": "#E5F4EA", "warning": "#9A6700", "warning_soft": "#FFF5DB",
    "danger": "#CF222E", "danger_soft": "#FDECEC", "node": "#2C3B4D", "hover": "#F0F3F7",
    "selected": "#E3EDFD", "shadow": "#1A000000",
}
DARK = {
    "bg": "#1E2228", "panel": "#252A31", "surface": "#2B3139", "border": "#3A414B", "divider": "#323943",
    "text": "#E6EAF0", "muted": "#A3ADBA", "faint": "#6F7A87",
    "accent": "#4C8DF6", "accent_hover": "#6AA1F8", "accent_text": "#FFFFFF", "accent_soft": "#253A5C",
    "success": "#3FB950", "success_soft": "#1F3527", "warning": "#D29922", "warning_soft": "#3A3020",
    "danger": "#F85149", "danger_soft": "#43272A", "node": "#D5DCE5", "hover": "#2F363F",
    "selected": "#2A3C58", "shadow": "#40000000",
}
LANES_LIGHT = ["#1F6FEB", "#8250DF", "#BF5B04", "#1A7F37", "#CF222E", "#0E7490", "#9A6700"]
LANES_DARK = ["#4C8DF6", "#A371F7", "#F0883E", "#3FB950", "#F85149", "#39C5CF", "#D29922"]


def set_overrides(light: dict | None = None, dark: dict | None = None,
                  lanes_light: list | None = None, lanes_dark: list | None = None):
    """Let a host add-on restyle Strata (e.g. its own accent colour) and restyle open widgets.

    light/dark: token -> colour, merged into LIGHT/DARK (unknown keys are rejected).
    lanes_*: replacement workspace lane colours for the version graph (the first is the Main lane).
    """
    for target, values in ((LIGHT, light), (DARK, dark)):
        if values:
            unknown = set(values) - set(target)
            if unknown:
                raise KeyError("unknown theme tokens: %s" % ", ".join(sorted(unknown)))
            target.update(values)
    if lanes_light:
        LANES_LIGHT[:] = list(lanes_light)
    if lanes_dark:
        LANES_DARK[:] = list(lanes_dark)
    _icon_cache.clear()
    sheet = stylesheet()
    # Only Strata's own registered widgets: walking QApplication.allWidgets() has crashed
    # FreeCAD 1.1.4 while a spreadsheet view was open (reported by the freecad-nxstyle integration).
    for w in list(_styled):
        if not _alive(w):
            _styled.remove(w)
            continue
        w.setStyleSheet(sheet)
        if hasattr(w, "schedule_refresh"):
            w.schedule_refresh()
        w.update()


_styled: list = []  # top-level Strata widgets that carry the stylesheet


def register(widget):
    """Called by Strata's panel and status pill so set_overrides can restyle them."""
    _styled.append(widget)


def _alive(widget) -> bool:
    try:
        import shiboken6
        return shiboken6.isValid(widget)
    except ImportError:
        try:
            widget.objectName()
            return True
        except RuntimeError:
            return False


def is_dark() -> bool:
    app = QtWidgets.QApplication.instance()
    if app is None:
        return False
    return app.palette().color(QtGui.QPalette.Window).lightness() < 128


def tokens() -> dict:
    return DARK if is_dark() else LIGHT


def lane_color(i: int) -> str:
    lanes = LANES_DARK if is_dark() else LANES_LIGHT
    return lanes[i % len(lanes)]


def stylesheet(t: dict | None = None) -> str:
    t = t or tokens()
    return """
#StrataPanel, #StrataPanel QWidget#StrataBody { background: %(panel)s; }
#StrataPanel QLabel { color: %(text)s; background: transparent; }
#StrataPanel QLabel[role="caption"] { color: %(muted)s; font-size: 8pt; }
#StrataPanel QLabel[role="section"] { color: %(muted)s; font-size: 7.5pt; font-weight: 600; letter-spacing: 0.6px; }
#StrataPanel QLabel[role="project"] { color: %(muted)s; font-size: 8pt; }
#StrataPanel QLabel[role="title"] { color: %(text)s; font-size: 10.5pt; font-weight: 600; }
#StrataPanel QFrame[role="card"], QDialog#StrataDialog QFrame[role="card"] {
    background: %(surface)s; border: 1px solid %(border)s; border-radius: 8px; }
#StrataPanel QFrame[role="divider"] { background: %(divider)s; max-height: 1px; min-height: 1px; border: none; }
#StrataPanel QFrame[role="banner"] { background: %(accent_soft)s; border: 1px solid %(accent_soft)s; border-radius: 8px; }
#StrataPanel QFrame[role="banner-warn"] { background: %(warning_soft)s; border-radius: 8px; }
QPushButton[kind="primary"] {
    background: %(accent)s; color: %(accent_text)s; border: none; border-radius: 6px;
    padding: 6px 14px; font-weight: 600; }
QPushButton[kind="primary"]:hover { background: %(accent_hover)s; }
QPushButton[kind="primary"]:disabled { background: %(border)s; color: %(faint)s; }
QPushButton[kind="secondary"] {
    background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 6px; padding: 5px 12px; }
QPushButton[kind="secondary"]:hover { background: %(hover)s; }
QPushButton[kind="danger"] {
    background: %(surface)s; color: %(danger)s; border: 1px solid %(border)s; border-radius: 6px; padding: 5px 12px; }
QPushButton[kind="danger"]:hover { background: %(danger_soft)s; }
QToolButton[kind="icon"] { background: transparent; border: none; border-radius: 6px; padding: 4px; }
QToolButton[kind="icon"]:hover { background: %(hover)s; }
QToolButton[kind="icon"]:checked { background: %(selected)s; }
QToolButton[kind="pill"] {
    background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 13px;
    padding: 3px 10px 3px 8px; font-weight: 600; }
QToolButton[kind="pill"]:hover { background: %(hover)s; }
QToolButton[kind="pill"]::menu-indicator { image: none; width: 0; }
QToolButton[kind="link"] { background: transparent; border: none; color: %(accent)s; font-weight: 600; padding: 2px 4px; }
QToolButton[kind="link"]:hover { text-decoration: underline; }
#StrataPanel QLineEdit, QDialog#StrataDialog QLineEdit, QDialog#StrataDialog QPlainTextEdit,
QDialog#StrataDialog QComboBox, #StrataPanel QComboBox {
    background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 6px; padding: 5px 8px;
    selection-background-color: %(accent)s; }
#StrataPanel QLineEdit:focus, QDialog#StrataDialog QLineEdit:focus, QDialog#StrataDialog QPlainTextEdit:focus,
QDialog#StrataDialog QComboBox:focus { border: 1px solid %(accent)s; }
#StrataPanel QScrollArea, #StrataPanel QListView { background: transparent; border: none; }
#StrataPanel QScrollBar:vertical, QDialog#StrataDialog QScrollBar:vertical { background: transparent; width: 8px; margin: 2px; }
#StrataPanel QScrollBar::handle:vertical, QDialog#StrataDialog QScrollBar::handle:vertical {
    background: %(border)s; border-radius: 3px; min-height: 24px; }
#StrataPanel QScrollBar::add-line, #StrataPanel QScrollBar::sub-line,
QDialog#StrataDialog QScrollBar::add-line, QDialog#StrataDialog QScrollBar::sub-line { height: 0; }
#StrataPanel QSplitter::handle { background: transparent; height: 6px; }
QDialog#StrataDialog { background: %(panel)s; }
QDialog#StrataDialog QLabel { color: %(text)s; }
QDialog#StrataDialog QLabel[role="caption"] { color: %(muted)s; font-size: 8pt; }
QDialog#StrataDialog QLabel[role="title"] { font-size: 12pt; font-weight: 600; }
QDialog#StrataDialog QLabel[role="field"] { color: %(muted)s; font-size: 8pt; font-weight: 600; }
QDialog#StrataDialog QTreeWidget, QDialog#StrataDialog QTableWidget {
    background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 8px;
    alternate-background-color: %(panel)s; gridline-color: %(divider)s; }
QDialog#StrataDialog QHeaderView::section {
    background: %(surface)s; color: %(muted)s; border: none; border-bottom: 1px solid %(border)s;
    padding: 6px 8px; font-weight: 600; font-size: 8pt; }
QDialog#StrataDialog QCheckBox, QDialog#StrataDialog QRadioButton { color: %(text)s; }
QMenu#StrataMenu { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 8px; padding: 5px; }
QMenu#StrataMenu::item { padding: 6px 22px 6px 10px; border-radius: 5px; }
QMenu#StrataMenu::item:selected { background: %(selected)s; color: %(text)s; }
QMenu#StrataMenu::item:disabled { color: %(faint)s; }
QMenu#StrataMenu::separator { height: 1px; background: %(divider)s; margin: 4px 6px; }
QMenu#StrataMenu::section { color: %(muted)s; font-size: 7.5pt; font-weight: 600; padding: 6px 10px 2px 10px; }
QToolButton#StrataStatus { background: transparent; border: none; border-radius: 4px; padding: 1px 8px; color: %(text)s; }
QToolButton#StrataStatus:hover { background: %(hover)s; }
""" % t


# ------------------------------------------------------------------ icons
# 24x24 stroke icons, drawn for Strata. "C" is replaced by the requested colour.
_ICONS = {
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
    "version": '<circle cx="12" cy="12" r="4" fill="C"/><path d="M12 2v6M12 16v6"/>',
    "workspace": '<path d="M4 20h4l10.5-10.5a2.8 2.8 0 0 0-4-4L4 16v4z"/><path d="M13.5 6.5l4 4"/>',
    "branch": '<circle cx="6" cy="5" r="2.2"/><circle cx="6" cy="19" r="2.2"/><circle cx="18" cy="7" r="2.2"/>'
              '<path d="M6 7.2v9.6"/><path d="M18 9.2c0 5-12 3-12 7.6"/>',
    "merge": '<circle cx="6" cy="5" r="2.2"/><circle cx="6" cy="19" r="2.2"/><circle cx="18" cy="12" r="2.2"/>'
             '<path d="M6 7.2v9.6"/><path d="M6 7.2c0 4 4 4.8 9.8 4.8"/>',
    "compare": '<rect x="3" y="4" width="8" height="16" rx="2"/><rect x="13" y="4" width="8" height="16" rx="2" '
               'stroke-dasharray="2.5 2.5"/>',
    "release": '<path d="M12 2l2.4 2.1 3.1-.4.9 3 2.8 1.5-1 3 1 3-2.8 1.5-.9 3-3.1-.4L12 22l-2.4-2.1-3.1.4-.9-3'
               '-2.8-1.5 1-3-1-3 2.8-1.5.9-3 3.1.4z"/><path d="M8.5 12.2l2.3 2.3 4.7-4.8"/>',
    "sync": '<path d="M20 11a8 8 0 0 0-14.3-4.6L4 8"/><path d="M4 3v5h5"/><path d="M4 13a8 8 0 0 0 14.3 4.6L20 16"/>'
            '<path d="M20 21v-5h-5"/>',
    "restore": '<path d="M4 12a8 8 0 1 0 2.4-5.7L4 8.5"/><path d="M4 4v4.5h4.5"/>',
    "eye": '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "more": '<circle cx="5" cy="12" r="1.3" fill="C"/><circle cx="12" cy="12" r="1.3" fill="C"/>'
            '<circle cx="19" cy="12" r="1.3" fill="C"/>',
    "check": '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
    "warning": '<path d="M12 3L2 20h20L12 3z"/><path d="M12 10v4.5M12 17.3v.2"/>',
    "share": '<path d="M7 18a4.5 4.5 0 0 1-.5-9A6 6 0 0 1 18 9a4 4 0 0 1-.5 9"/><path d="M12 12v8M9 15l3-3 3 3"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "chevron": '<path d="M7 10l5 5 5-5"/>',
    "save": '<circle cx="12" cy="12" r="3" fill="C"/>',
    "start": '<path d="M6 21V4"/><path d="M6 4h11l-2.5 4L17 12H6"/>',
    "doc": '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
    "cube": '<path d="M12 2.8l8 4.6v9.2l-8 4.6-8-4.6V7.4z"/><path d="M4 7.4l8 4.6 8-4.6M12 12v9.2"/>',
    "settings": '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
}

_icon_cache: dict = {}


def icon_pixmap(name: str, color: str, size: int = 18) -> QtGui.QPixmap:
    app = QtWidgets.QApplication.instance()
    ratio = app.devicePixelRatio() if app and hasattr(app, "devicePixelRatio") else 1.0
    key = (name, color, size, ratio)
    if key in _icon_cache:
        return _icon_cache[key]
    body = _ICONS.get(name, _ICONS["doc"]).replace('"C"', '"%s"' % color)
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="%s" '
           'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">%s</svg>') % (color, body)
    renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(svg.encode()))
    px = QtGui.QPixmap(int(size * ratio), int(size * ratio))
    px.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(px)
    p.setRenderHint(QtGui.QPainter.Antialiasing)
    renderer.render(p)
    p.end()
    px.setDevicePixelRatio(ratio)
    _icon_cache[key] = px
    return px


def icon(name: str, color: str | None = None, size: int = 18) -> QtGui.QIcon:
    t = tokens()
    ic = QtGui.QIcon()
    ic.addPixmap(icon_pixmap(name, color or t["muted"], size), QtGui.QIcon.Normal)
    ic.addPixmap(icon_pixmap(name, t["faint"], size), QtGui.QIcon.Disabled)
    return ic


def app_icon_svg_path() -> str:
    import os
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", "strata.svg")


def initials(name: str) -> str:
    parts = [p for p in name.replace(".", " ").replace("_", " ").split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def avatar_color(name: str) -> str:
    palette = LANES_DARK if is_dark() else LANES_LIGHT
    return palette[sum(map(ord, name)) % len(palette)]


def make_button(text: str, kind: str = "secondary", icon_name: str | None = None) -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    b.setProperty("kind", kind)
    b.setCursor(QtCore.Qt.PointingHandCursor)
    if icon_name:
        t = tokens()
        b.setIcon(icon(icon_name, t["accent_text"] if kind == "primary" else t["text"], 16))
    return b


def make_icon_button(name: str, tip: str, size: int = 18) -> QtWidgets.QToolButton:
    b = QtWidgets.QToolButton()
    b.setProperty("kind", "icon")
    b.setIcon(icon(name, None, size))
    b.setIconSize(QtCore.QSize(size, size))
    b.setToolTip(tip)
    b.setCursor(QtCore.Qt.PointingHandCursor)
    b.setAutoRaise(True)
    return b


def label(text: str, role: str | None = None, wrap: bool = False) -> QtWidgets.QLabel:
    lb = QtWidgets.QLabel(text)
    if role:
        lb.setProperty("role", role)
    lb.setWordWrap(wrap)
    return lb


def menu(parent=None) -> QtWidgets.QMenu:
    m = QtWidgets.QMenu(parent)
    m.setObjectName("StrataMenu")
    m.setStyleSheet(stylesheet())
    m.setWindowFlags(m.windowFlags() | QtCore.Qt.NoDropShadowWindowHint)
    return m
