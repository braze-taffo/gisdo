"""QPainter 自绘几何图标，无第三方依赖。

``get_icon(name, color)`` 返回 64px 透明底 QIcon，线条宽度统一，
风格为描边线性图标。支持的 name 见 ``_DRAWERS``。
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui

_SIZE = 64


def _painter(pixmap: QtGui.QPixmap, color: str, width: float = 4.5) -> QtGui.QPainter:
    p = QtGui.QPainter(pixmap)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    pen = QtGui.QPen(QtGui.QColor(color))
    pen.setWidthF(width)
    pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
    return p


def _draw_chat(p: QtGui.QPainter) -> None:
    p.drawRoundedRect(QtCore.QRectF(8, 10, 48, 36), 10, 10)
    path = QtGui.QPainterPath()
    path.moveTo(20, 46)
    path.lineTo(20, 56)
    path.lineTo(32, 46)
    p.drawPath(path)
    for x in (22, 32, 42):
        p.drawPoint(QtCore.QPointF(x, 28))


def _draw_folder(p: QtGui.QPainter) -> None:
    path = QtGui.QPainterPath()
    path.moveTo(10, 16)
    path.lineTo(26, 16)
    path.lineTo(31, 22)
    path.lineTo(54, 22)
    path.lineTo(54, 50)
    path.lineTo(10, 50)
    path.closeSubpath()
    p.drawPath(path)


def _draw_cpu(p: QtGui.QPainter) -> None:
    p.drawRect(QtCore.QRectF(18, 18, 28, 28))
    p.drawRect(QtCore.QRectF(27, 27, 10, 10))
    for i in range(4):
        off = 22 + i * 7
        p.drawLine(QtCore.QPointF(off, 18), QtCore.QPointF(off, 10))
        p.drawLine(QtCore.QPointF(off, 46), QtCore.QPointF(off, 54))
        p.drawLine(QtCore.QPointF(18, off), QtCore.QPointF(10, off))
        p.drawLine(QtCore.QPointF(46, off), QtCore.QPointF(54, off))


def _draw_search(p: QtGui.QPainter) -> None:
    p.drawEllipse(QtCore.QRectF(10, 10, 30, 30))
    p.drawLine(QtCore.QPointF(36, 40), QtCore.QPointF(54, 56))


def _draw_package(p: QtGui.QPainter) -> None:
    p.drawRect(QtCore.QRectF(10, 22, 44, 32))
    p.drawLine(QtCore.QPointF(10, 32), QtCore.QPointF(54, 32))
    p.drawLine(QtCore.QPointF(26, 22), QtCore.QPointF(26, 32))
    p.drawLine(QtCore.QPointF(38, 22), QtCore.QPointF(38, 32))


def _draw_image(p: QtGui.QPainter) -> None:
    p.drawRoundedRect(QtCore.QRectF(8, 12, 48, 40), 6, 6)
    p.drawEllipse(QtCore.QRectF(16, 20, 8, 8))
    path = QtGui.QPainterPath()
    path.moveTo(10, 50)
    path.lineTo(26, 34)
    path.lineTo(34, 42)
    path.lineTo(44, 32)
    path.lineTo(54, 50)
    p.drawPath(path)


def _draw_gear(p: QtGui.QPainter) -> None:
    cx, cy, r1, r2 = 32, 32, 12, 20
    teeth = QtGui.QPainterPath()
    for i in range(8):
        ang = math.radians(i * 45)
        x1 = cx + r2 * math.cos(ang)
        y1 = cy + r2 * math.sin(ang)
        x2 = cx + (r2 + 6) * math.cos(ang)
        y2 = cy + (r2 + 6) * math.sin(ang)
        teeth.moveTo(x1, y1)
        teeth.lineTo(x2, y2)
    p.drawPath(teeth)
    p.drawEllipse(QtCore.QPointF(cx, cy), r1, r1)
    p.drawEllipse(QtCore.QPointF(cx, cy), 5, 5)


def _draw_send(p: QtGui.QPainter) -> None:
    path = QtGui.QPainterPath()
    path.moveTo(10, 32)
    path.lineTo(54, 12)
    path.lineTo(38, 54)
    path.lineTo(30, 38)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QtCore.QPointF(30, 38), QtCore.QPointF(54, 12))


def _draw_stop(p: QtGui.QPainter) -> None:
    p.setBrush(QtGui.QColor(p.pen().color()))
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.drawRoundedRect(QtCore.QRectF(16, 16, 32, 32), 5, 5)


def _draw_refresh(p: QtGui.QPainter) -> None:
    p.drawArc(QtCore.QRectF(12, 12, 40, 40), 40 * 16, 290 * 16)
    path = QtGui.QPainterPath()
    path.moveTo(50, 10)
    path.lineTo(52, 24)
    path.lineTo(38, 20)
    p.drawPath(path)


def _draw_plus(p: QtGui.QPainter) -> None:
    p.drawLine(QtCore.QPointF(32, 14), QtCore.QPointF(32, 50))
    p.drawLine(QtCore.QPointF(14, 32), QtCore.QPointF(50, 32))


def _draw_trash(p: QtGui.QPainter) -> None:
    p.drawLine(QtCore.QPointF(12, 18), QtCore.QPointF(52, 18))
    p.drawLine(QtCore.QPointF(26, 18), QtCore.QPointF(26, 12))
    p.drawLine(QtCore.QPointF(26, 12), QtCore.QPointF(38, 12))
    p.drawLine(QtCore.QPointF(38, 12), QtCore.QPointF(38, 18))
    path = QtGui.QPainterPath()
    path.moveTo(17, 18)
    path.lineTo(20, 54)
    path.lineTo(44, 54)
    path.lineTo(47, 18)
    p.drawPath(path)
    p.drawLine(QtCore.QPointF(27, 26), QtCore.QPointF(28, 46))
    p.drawLine(QtCore.QPointF(37, 26), QtCore.QPointF(36, 46))


def _draw_check(p: QtGui.QPainter) -> None:
    path = QtGui.QPainterPath()
    path.moveTo(14, 34)
    path.lineTo(27, 47)
    path.lineTo(50, 18)
    p.drawPath(path)


def _draw_warning(p: QtGui.QPainter) -> None:
    path = QtGui.QPainterPath()
    path.moveTo(32, 8)
    path.lineTo(58, 52)
    path.lineTo(6, 52)
    path.closeSubpath()
    p.drawPath(path)
    p.drawLine(QtCore.QPointF(32, 24), QtCore.QPointF(32, 38))
    p.drawPoint(QtCore.QPointF(32, 46))


def _draw_clear(p: QtGui.QPainter) -> None:
    p.drawLine(QtCore.QPointF(18, 18), QtCore.QPointF(46, 46))
    p.drawLine(QtCore.QPointF(46, 18), QtCore.QPointF(18, 46))


def _draw_scroll(p: QtGui.QPainter) -> None:
    p.drawLine(QtCore.QPointF(32, 12), QtCore.QPointF(32, 46))
    path = QtGui.QPainterPath()
    path.moveTo(20, 36)
    path.lineTo(32, 50)
    path.lineTo(44, 36)
    p.drawPath(path)


_DRAWERS = {
    "chat": _draw_chat,
    "folder": _draw_folder,
    "cpu": _draw_cpu,
    "search": _draw_search,
    "package": _draw_package,
    "image": _draw_image,
    "gear": _draw_gear,
    "send": _draw_send,
    "stop": _draw_stop,
    "refresh": _draw_refresh,
    "plus": _draw_plus,
    "trash": _draw_trash,
    "check": _draw_check,
    "warning": _draw_warning,
    "clear": _draw_clear,
    "scroll": _draw_scroll,
}


def get_icon(name: str, color: str = "#24292F") -> QtGui.QIcon:
    """按名称取图标；未知名称返回空 QIcon。"""
    pixmap = QtGui.QPixmap(_SIZE, _SIZE)
    pixmap.fill(QtCore.Qt.GlobalColor.transparent)
    drawer = _DRAWERS.get(name)
    if drawer is not None:
        painter = _painter(pixmap, color)
        drawer(painter)
        painter.end()
    return QtGui.QIcon(pixmap)


def available_icons() -> list[str]:
    return sorted(_DRAWERS)


__all__ = ["available_icons", "get_icon"]
