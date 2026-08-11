"""主窗口 GUI 辅助逻辑测试。"""

from PySide6 import QtCore, QtWidgets

from gisdo.gui.app import MainWindow


class _AnimationHost:
    """只提供动画清理方法所需状态，避免测试构造完整主窗口。"""

    def __init__(self, animation, page, effect):
        self._page_animation = animation
        self._page_animation_page = page
        self._page_animation_effect = effect


def test_clear_page_animation_stops_and_detaches_effect(qtbot):
    page = QtWidgets.QWidget()
    qtbot.addWidget(page)
    effect = QtWidgets.QGraphicsOpacityEffect(page)
    page.setGraphicsEffect(effect)
    animation = QtCore.QPropertyAnimation(effect, b"opacity", page)
    animation.setDuration(1_000)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.start()
    host = _AnimationHost(animation, page, effect)

    MainWindow._clear_page_animation(host)

    assert animation.state() == QtCore.QAbstractAnimation.State.Stopped
    assert animation.targetObject() is None
    assert page.graphicsEffect() is None
    assert host._page_animation is None
    assert host._page_animation_page is None
    assert host._page_animation_effect is None


def test_clear_page_animation_ignores_stale_callback(qtbot):
    page = QtWidgets.QWidget()
    qtbot.addWidget(page)
    effect = QtWidgets.QGraphicsOpacityEffect(page)
    page.setGraphicsEffect(effect)
    current = QtCore.QPropertyAnimation(effect, b"opacity", page)
    stale = QtCore.QPropertyAnimation(page, b"windowOpacity", page)
    host = _AnimationHost(current, page, effect)

    MainWindow._clear_page_animation(host, stale)

    assert host._page_animation is current
    assert page.graphicsEffect() is effect
