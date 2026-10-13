# SPDX-License-Identifier: LGPL-2.1-or-later
"""Custom-painted widgets: the version graph and the history list."""

from __future__ import annotations

import datetime as _dt
import time

from PySide import QtCore, QtGui, QtWidgets

from ..project import display_name, friendly_time
from . import theme

ROW_H = 48
LANE_W = 16
LANE_X0 = 20


def _font(base: QtGui.QFont, pt: float | None = None, weight=None) -> QtGui.QFont:
    f = QtGui.QFont(base)
    if pt:
        f.setPointSizeF(pt)
    if weight is not None:
        f.setWeight(weight)
    return f


def draw_chip(p: QtGui.QPainter, x: float, y: float, text: str, fg: str, bg: str, font: QtGui.QFont,
              icon_name: str | None = None) -> float:
    """Draw a rounded chip with its left edge at x, vertically centred on y. Returns its width."""
    fm = QtGui.QFontMetrics(font)
    pad, ih = 7, (12 if icon_name else 0)
    w = fm.horizontalAdvance(text) + pad * 2 + (ih + 3 if icon_name else 0)
    h = fm.height() + 4
    r = QtCore.QRectF(x, y - h / 2, w, h)
    p.setPen(QtCore.Qt.NoPen)
    p.setBrush(QtGui.QColor(bg))
    p.drawRoundedRect(r, h / 2, h / 2)
    tx = x + pad
    if icon_name:
        p.drawPixmap(QtCore.QPointF(tx, y - ih / 2), theme.icon_pixmap(icon_name, fg, ih))
        tx += ih + 3
    p.setPen(QtGui.QColor(fg))
    p.setFont(font)
    p.drawText(QtCore.QRectF(tx, r.top(), w, h), QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft, text)
    return w


# =================================================================== graph
class VersionGraph(QtWidgets.QWidget):
    """Onshape-style version graph: versions are solid nodes, workspaces are open nodes."""

    selected = QtCore.Signal(object)
    activated = QtCore.Signal(object)
    context = QtCore.Signal(object, QtCore.QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nodes = []
        self.version_info = {}
        self.release_info = {}
        self.current_ws = "main"
        self.sel = None
        self.hover = None
        self.setMouseTracking(True)
        self.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

    def set_data(self, nodes, versions, releases, current_ws):
        self.nodes = nodes
        self.version_info = {v.name: v for v in versions}
        self.release_info = {}
        for r in releases:
            self.release_info.setdefault(r.version, []).append(r)
        self.current_ws = current_ws
        keep = self.sel.sha if self.sel else None
        self.sel = next((n for n in nodes if n.sha == keep), None)
        self.setFixedHeight(max(1, len(nodes)) * ROW_H + 8)
        self.update()

    def lanes(self) -> int:
        return max([n.lane for n in self.nodes] + [0]) + 1

    def _node_at(self, pos) -> object | None:
        row = int((pos.y() - 4) // ROW_H)
        return self.nodes[row] if 0 <= row < len(self.nodes) else None

    def _xy(self, n) -> QtCore.QPointF:
        return QtCore.QPointF(LANE_X0 + n.lane * LANE_W, 4 + n.row * ROW_H + ROW_H / 2)

    # ---------------------------------------------------------------- paint
    def paintEvent(self, _ev):
        t = theme.tokens()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        base = self.font()
        if not self.nodes:
            p.setPen(QtGui.QColor(t["muted"]))
            p.drawText(self.rect(), QtCore.Qt.AlignCenter, "No versions yet")
            return
        by_sha = {n.sha: n for n in self.nodes}
        # row backgrounds
        for n in self.nodes:
            if n is self.sel or n is self.hover:
                r = QtCore.QRectF(4, 4 + n.row * ROW_H + 2, self.width() - 8, ROW_H - 4)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QColor(t["selected"] if n is self.sel else t["hover"]))
                p.drawRoundedRect(r, 7, 7)
        # edges
        for n in self.nodes:
            a = self._xy(n)
            for i, ps in enumerate(n.parents):
                m = by_sha.get(ps)
                if m is None:
                    continue
                b = self._xy(m)
                color = QtGui.QColor(theme.lane_color(n.lane if i == 0 else m.lane))
                pen = QtGui.QPen(color, 2)
                pen.setCapStyle(QtCore.Qt.RoundCap)
                p.setPen(pen)
                p.setBrush(QtCore.Qt.NoBrush)
                path = QtGui.QPainterPath(a)
                if a.x() == b.x():
                    path.lineTo(b)
                elif i == 0:  # branch: run down own lane, bend into the parent near it
                    bend = b.y() - ROW_H * 0.75
                    path.lineTo(a.x(), bend)
                    path.cubicTo(a.x(), bend + ROW_H * 0.45, b.x(), b.y() - ROW_H * 0.3, b.x(), b.y())
                else:  # merge: leave towards the other lane straight away
                    bend = a.y() + ROW_H * 0.75
                    path.cubicTo(a.x(), a.y() + ROW_H * 0.3, b.x(), bend - ROW_H * 0.45, b.x(), bend)
                    path.lineTo(b)
                p.drawPath(path)
        # nodes + labels
        text_x = LANE_X0 + self.lanes() * LANE_W + 6
        f_title = _font(base, base.pointSizeF() * 1.0, QtGui.QFont.DemiBold)
        f_cap = _font(base, base.pointSizeF() * 0.86)
        f_chip = _font(base, base.pointSizeF() * 0.8, QtGui.QFont.DemiBold)
        for n in self.nodes:
            c = self._xy(n)
            lane_c = QtGui.QColor(theme.lane_color(n.lane))
            is_ws = bool(n.workspaces)
            current = self.current_ws in n.workspaces
            if current:
                p.setPen(QtCore.Qt.NoPen)
                halo = QtGui.QColor(lane_c)
                halo.setAlpha(55)
                p.setBrush(halo)
                p.drawEllipse(c, 10.5, 10.5)
            if n.versions:
                p.setPen(QtGui.QPen(QtGui.QColor(t["panel"]), 2))
                p.setBrush(QtGui.QColor(t["node"]))
                p.drawEllipse(c, 6.5, 6.5)
                if is_ws:
                    p.setPen(QtGui.QPen(lane_c, 2))
                    p.setBrush(QtCore.Qt.NoBrush)
                    p.drawEllipse(c, 9, 9)
            elif is_ws:
                p.setPen(QtGui.QPen(lane_c, 2.2))
                p.setBrush(QtGui.QColor(t["panel"]))
                p.drawEllipse(c, 6.5, 6.5)
            elif n.kind == "start":
                p.setPen(QtGui.QPen(QtGui.QColor(t["node"]), 2))
                p.setBrush(QtGui.QColor(t["panel"]))
                p.drawRect(QtCore.QRectF(c.x() - 5, c.y() - 5, 10, 10))
            else:
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(lane_c)
                p.drawEllipse(c, 3.5, 3.5)
            # text
            y0 = 4 + n.row * ROW_H
            x = text_x
            if n.versions:
                title = ", ".join(n.versions)
            elif is_ws:
                title = display_name(n.workspaces[0])
            elif n.kind == "start":
                title = "Start"
            elif n.kind == "merge":
                title = "Merge"
            else:
                title = "Branch point"
            p.setFont(f_title)
            p.setPen(QtGui.QColor(t["text"] if (n.versions or n.kind == "start") else
                                  (lane_c.name() if is_ws else t["muted"])))
            fm = QtGui.QFontMetrics(f_title)
            p.drawText(QtCore.QPointF(x, y0 + 20), title)
            cx = x + fm.horizontalAdvance(title) + 8
            if n.versions:
                for ws in n.workspaces:
                    cx += draw_chip(p, cx, y0 + 15, display_name(ws), lane_c.name(), t["accent_soft"], f_chip,
                                    "workspace") + 4
            for vname in n.versions:
                for rel in self.release_info.get(vname, []):
                    fg, bg = {"Released": (t["success"], t["success_soft"]),
                              "Pending": (t["warning"], t["warning_soft"]),
                              "Obsolete": (t["faint"], t["hover"]),
                              "Rejected": (t["danger"], t["danger_soft"])}.get(rel.state, (t["muted"], t["hover"]))
                    if cx < self.width() - 40:
                        cx += draw_chip(p, cx, y0 + 15, "%s %s" % (rel.part_number, rel.revision), fg, bg, f_chip,
                                        "release") + 4
            if current and not n.versions:
                draw_chip(p, cx, y0 + 15, "Current", t["accent_text"], lane_c.name(), f_chip)
            # caption
            v = self.version_info.get(n.versions[0]) if n.versions else None
            if v:
                cap = "%s · %s" % (v.author, friendly_time(v.time))
                if v.description:
                    cap += " · " + v.description.splitlines()[0]
            elif is_ws:
                cap = ("Workspace · edited %s" % friendly_time(n.time)) if n.kind != "start" else "Workspace"
            else:
                cap = friendly_time(n.time)
            p.setFont(f_cap)
            p.setPen(QtGui.QColor(t["muted"]))
            fm = QtGui.QFontMetrics(f_cap)
            p.drawText(QtCore.QPointF(x, y0 + 36), fm.elidedText(cap, QtCore.Qt.ElideRight, self.width() - x - 12))
        p.end()

    # ---------------------------------------------------------------- input
    def mouseMoveEvent(self, ev):
        n = self._node_at(ev.position() if hasattr(ev, "position") else ev.pos())
        if n is not self.hover:
            self.hover = n
            self.setCursor(QtCore.Qt.PointingHandCursor if n else QtCore.Qt.ArrowCursor)
            self.update()

    def leaveEvent(self, _ev):
        self.hover = None
        self.update()

    def mousePressEvent(self, ev):
        n = self._node_at(ev.position() if hasattr(ev, "position") else ev.pos())
        if n is not None:
            self.sel = n
            self.update()
            self.selected.emit(n)
            if ev.button() == QtCore.Qt.RightButton:
                self.context.emit(n, ev.globalPosition().toPoint() if hasattr(ev, "globalPosition") else ev.globalPos())

    def mouseDoubleClickEvent(self, ev):
        n = self._node_at(ev.position() if hasattr(ev, "position") else ev.pos())
        if n is not None:
            self.activated.emit(n)

    def clear_selection(self):
        self.sel = None
        self.update()


# =================================================================== history
HEADER_ROLE = QtCore.Qt.UserRole + 1
ENTRY_ROLE = QtCore.Qt.UserRole + 2
KIND_ICON = {"version": "version", "restore": "restore", "merge": "merge", "workspace": "branch",
             "start": "start", "sync": "sync"}


class HistoryDelegate(QtWidgets.QStyledItemDelegate):
    action = QtCore.Signal(str, object)  # ("view" | "restore", entry)

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.hover_row = -1

    def sizeHint(self, option, index):
        if index.data(HEADER_ROLE):
            return QtCore.QSize(10, 28)
        return QtCore.QSize(10, 50)

    def _buttons(self, rect: QtCore.QRect):
        s, gap = 26, 4
        r2 = QtCore.QRectF(rect.right() - 8 - s, rect.center().y() - s / 2, s, s)
        r1 = QtCore.QRectF(r2.left() - gap - s, r2.top(), s, s)
        return {"view": r1, "restore": r2}

    def paint(self, p, option, index):
        t = theme.tokens()
        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = option.rect
        base = option.font
        header = index.data(HEADER_ROLE)
        if header:
            p.setFont(_font(base, base.pointSizeF() * 0.8, QtGui.QFont.DemiBold))
            p.setPen(QtGui.QColor(t["muted"]))
            p.drawText(rect.adjusted(12, 6, -8, 0), QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, header.upper())
            p.restore()
            return
        e = index.data(ENTRY_ROLE)
        hovered = index.row() == self.hover_row
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        if selected or hovered:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(t["selected"] if selected else t["hover"]))
            p.drawRoundedRect(QtCore.QRectF(rect).adjusted(4, 2, -4, -2), 7, 7)
        # leading badge: author avatar for ordinary saves, an icon for milestones
        cx, cy, rad = rect.left() + 26, rect.center().y(), 13
        kind = e.kind if not e.versions else "version"
        if kind in KIND_ICON:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(t["accent_soft"]))
            p.drawEllipse(QtCore.QPointF(cx, cy), rad, rad)
            p.drawPixmap(QtCore.QPointF(cx - 8, cy - 8), theme.icon_pixmap(KIND_ICON[kind], t["accent"], 16))
        else:
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(theme.avatar_color(e.author)))
            p.drawEllipse(QtCore.QPointF(cx, cy), rad, rad)
            p.setPen(QtGui.QColor("#FFFFFF"))
            p.setFont(_font(base, base.pointSizeF() * 0.78, QtGui.QFont.Bold))
            p.drawText(QtCore.QRectF(cx - rad, cy - rad, rad * 2, rad * 2), QtCore.Qt.AlignCenter,
                       theme.initials(e.author))
        x = rect.left() + 48
        right = rect.right() - (70 if hovered else 12)
        f_title = _font(base, base.pointSizeF() * 0.95, QtGui.QFont.Medium)
        f_cap = _font(base, base.pointSizeF() * 0.82)
        f_chip = _font(base, base.pointSizeF() * 0.78, QtGui.QFont.DemiBold)
        ty = rect.top() + 17
        for v in e.versions:
            x += draw_chip(p, x, ty, v, t["accent_text"], t["accent"], f_chip) + 5
        if index.row() == self._first_entry_row() and not hovered:
            cw = QtGui.QFontMetrics(f_chip).horizontalAdvance("Latest") + 14
            draw_chip(p, rect.right() - 12 - cw, ty, "Latest", t["muted"], t["hover"], f_chip)
            right = rect.right() - 20 - cw
        p.setFont(f_title)
        p.setPen(QtGui.QColor(t["text"]))
        fm = QtGui.QFontMetrics(f_title)
        p.drawText(QtCore.QPointF(x, ty + fm.ascent() / 2 - 1), fm.elidedText(e.title, QtCore.Qt.ElideRight, right - x))
        docs = ", ".join(d.rsplit("/", 1)[-1].rsplit(".", 1)[0] for d in e.docs[:2])
        if len(e.docs) > 2:
            docs += " +%d" % (len(e.docs) - 2)
        cap = " · ".join(s for s in (e.author, friendly_time(e.time), docs) if s)
        p.setFont(f_cap)
        p.setPen(QtGui.QColor(t["muted"]))
        fm2 = QtGui.QFontMetrics(f_cap)
        p.drawText(QtCore.QPointF(rect.left() + 48, rect.top() + 37),
                   fm2.elidedText(cap, QtCore.Qt.ElideRight, right - rect.left() - 48))
        if hovered:
            for name, r in self._buttons(rect).items():
                p.setPen(QtGui.QPen(QtGui.QColor(t["border"]), 1))
                p.setBrush(QtGui.QColor(t["surface"]))
                p.drawRoundedRect(r, 6, 6)
                p.drawPixmap(r.topLeft() + QtCore.QPointF(5, 5),
                             theme.icon_pixmap("eye" if name == "view" else "restore", t["text"], 16))
        p.restore()

    def _first_entry_row(self) -> int:
        m = self.view.model()
        for r in range(m.rowCount()):
            if m.index(r, 0).data(ENTRY_ROLE) is not None:
                return r
        return -1

    def editorEvent(self, event, model, option, index):
        if event.type() == QtCore.QEvent.MouseButtonRelease and not index.data(HEADER_ROLE):
            pos = event.position() if hasattr(event, "position") else event.pos()
            for name, r in self._buttons(option.rect).items():
                if r.contains(pos):
                    self.action.emit(name, index.data(ENTRY_ROLE))
                    return True
        return False


class HistoryList(QtWidgets.QListWidget):
    entry_selected = QtCore.Signal(object)
    entry_activated = QtCore.Signal(object)
    entry_action = QtCore.Signal(str, object)
    entry_context = QtCore.Signal(object, QtCore.QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)  # arrow keys move through history, Enter views
        self.delegate = HistoryDelegate(self)
        self.setItemDelegate(self.delegate)
        self.delegate.action.connect(self.entry_action)
        self.itemSelectionChanged.connect(self._sel)
        # itemActivated covers double-click and Enter (platform-dependent), so double-click is not connected
        self.itemActivated.connect(lambda it: it.data(ENTRY_ROLE) and self.entry_activated.emit(it.data(ENTRY_ROLE)))
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._ctx)
        self.entries = []
        self.filter = ""

    def set_entries(self, entries):
        self.entries = entries
        self._fill()

    def set_filter(self, text: str):
        self.filter = text.strip().lower()
        self._fill()

    def _fill(self):
        keep = self.current_entry()
        keep = keep.sha if keep else None
        self.blockSignals(True)
        self.clear()
        today = _dt.date.today()
        group = None
        for e in self.entries:
            if self.filter and self.filter not in (e.title + " " + e.author + " " + " ".join(e.docs + e.versions)).lower():
                continue
            d = _dt.date.fromtimestamp(e.time)
            g = "Today" if d == today else "Yesterday" if d == today - _dt.timedelta(days=1) else \
                ("This week" if (today - d).days < 7 else d.strftime("%B %Y"))
            if g != group:
                group = g
                h = QtWidgets.QListWidgetItem()
                h.setData(HEADER_ROLE, g)
                h.setFlags(QtCore.Qt.NoItemFlags)
                self.addItem(h)
            it = QtWidgets.QListWidgetItem()
            it.setData(ENTRY_ROLE, e)
            it.setToolTip("%s\n%s · %s" % (e.title, e.author,
                                          time.strftime("%Y-%m-%d %H:%M", time.localtime(e.time))))
            self.addItem(it)
            if e.sha == keep:
                it.setSelected(True)
        self.blockSignals(False)
        self.viewport().update()

    def current_entry(self):
        items = self.selectedItems()
        return items[0].data(ENTRY_ROLE) if items else None

    def _sel(self):
        e = self.current_entry()
        if e is not None:
            self.entry_selected.emit(e)

    def _ctx(self, pos):
        it = self.itemAt(pos)
        if it and it.data(ENTRY_ROLE):
            it.setSelected(True)
            self.entry_context.emit(it.data(ENTRY_ROLE), self.viewport().mapToGlobal(pos))

    def mouseMoveEvent(self, ev):
        idx = self.indexAt(ev.position().toPoint() if hasattr(ev, "position") else ev.pos())
        row = idx.row() if idx.isValid() else -1
        if row != self.delegate.hover_row:
            self.delegate.hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(ev)

    def leaveEvent(self, ev):
        self.delegate.hover_row = -1
        self.viewport().update()
        super().leaveEvent(ev)
