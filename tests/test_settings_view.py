"""设置页的模型思考强度选择测试。"""

from gisdo.config import Settings
from gisdo.gui.views.settings import SettingsView


class _State:
    def __init__(self, thinking_level="auto"):
        self.settings = Settings(ai_thinking_level=thinking_level)
        self.saved = {}

    def update_settings(self, **kwargs):
        self.saved = kwargs
        for key, value in kwargs.items():
            setattr(self.settings, key, value)


def test_settings_view_loads_thinking_level(qtbot):
    state = _State("disabled")
    view = SettingsView(state, None)
    qtbot.addWidget(view)
    assert view.thinking_combo.currentData() == "disabled"


def test_settings_view_saves_thinking_level(qtbot):
    state = _State()
    view = SettingsView(state, None)
    qtbot.addWidget(view)
    view.thinking_combo.setCurrentIndex(view.thinking_combo.findData("high"))
    view._save()
    assert state.saved["ai_thinking_level"] == "high"


def test_settings_view_off_option_is_available(qtbot):
    state = _State()
    view = SettingsView(state, None)
    qtbot.addWidget(view)
    idx = view.thinking_combo.findData("disabled")
    assert idx >= 0
    assert "关闭" in view.thinking_combo.itemText(idx)
