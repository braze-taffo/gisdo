"""设置视图：运行时路径、输出根目录、LLM 配置（OpenAI 兼容）、自主程度。"""

from __future__ import annotations

from PySide6 import QtWidgets

from gisdo.agent.loop import (
    AUTONOMY_AUTONOMOUS,
    AUTONOMY_CONFIRM_EVERY_STEP,
    AUTONOMY_CONFIRM_WRITES,
)

# 预设端点：选一项自动填 base_url（与一个建议模型，仅当模型为空时）。
_PRESETS = [
    ("（自定义）", "", ""),
    ("DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat"),
    ("通义千问 Qwen", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    ("Moonshot Kimi", "https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    ("OpenAI", "https://api.openai.com/v1", "gpt-4o-mini"),
    ("本地 Ollama", "http://localhost:11434/v1", "qwen2.5"),
]

_AUTONOMY = [
    ("仅写操作确认（推荐）", AUTONOMY_CONFIRM_WRITES),
    ("全程自主", AUTONOMY_AUTONOMOUS),
    ("每步都确认", AUTONOMY_CONFIRM_EVERY_STEP),
]


class SettingsView(QtWidgets.QWidget):
    def __init__(self, state, log):
        super().__init__()
        self.state = state
        self.log = log
        self._build()
        self._load()

    def _build(self) -> None:
        layout = QtWidgets.QFormLayout(self)

        # --- 运行时 ---
        self.modern_edit = QtWidgets.QLineEdit()
        self.modern_edit.setPlaceholderText("留空自动发现；如 C:\\Program Files\\GeoScene\\Pro\\bin\\Python\\envs\\arcgispro-py3\\python.exe")
        modern_browse = QtWidgets.QPushButton("浏览…")
        modern_browse.clicked.connect(lambda: self._pick_exe(self.modern_edit))
        modern_row = QtWidgets.QHBoxLayout()
        modern_row.addWidget(self.modern_edit, 1)
        modern_row.addWidget(modern_browse)
        layout.addRow("GeoScene/ArcGIS Pro Python：", modern_row)

        self.arcmap_edit = QtWidgets.QLineEdit()
        self.arcmap_edit.setPlaceholderText("如 C:\\Python27\\ArcGIS10.4\\python.exe")
        arcmap_browse = QtWidgets.QPushButton("浏览…")
        arcmap_browse.clicked.connect(lambda: self._pick_exe(self.arcmap_edit))
        arcmap_row = QtWidgets.QHBoxLayout()
        arcmap_row.addWidget(self.arcmap_edit, 1)
        arcmap_row.addWidget(arcmap_browse)
        layout.addRow("ArcMap Python 2.7：", arcmap_row)

        self.output_root_edit = QtWidgets.QLineEdit()
        self.output_root_edit.setPlaceholderText("版本化输出的默认父目录")
        out_browse = QtWidgets.QPushButton("浏览…")
        out_browse.clicked.connect(self._pick_dir)
        out_row = QtWidgets.QHBoxLayout()
        out_row.addWidget(self.output_root_edit, 1)
        out_row.addWidget(out_browse)
        layout.addRow("输出根目录：", out_row)

        layout.addItem(QtWidgets.QSpacerItem(0, 12, QtWidgets.QSizePolicy.Policy.Fixed,
                                             QtWidgets.QSizePolicy.Policy.Fixed))

        # --- LLM（OpenAI 格式兼容） ---
        ai_title = QtWidgets.QLabel("LLM 配置（OpenAI 兼容端点，Agent 用）")
        layout.addRow("", ai_title)

        self.preset_combo = QtWidgets.QComboBox()
        self.preset_combo.addItems([name for name, _, _ in _PRESETS])
        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        layout.addRow("端点预设：", self.preset_combo)

        self.baseurl_edit = QtWidgets.QLineEdit()
        self.baseurl_edit.setPlaceholderText("如 https://api.deepseek.com/v1")
        layout.addRow("Base URL：", self.baseurl_edit)

        self.apikey_edit = QtWidgets.QLineEdit()
        self.apikey_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
        self.apikey_edit.setPlaceholderText("API Key（仅本地存于 ~/.gisdo/settings.json，或用 GISDO_API_KEY 环境变量）")
        layout.addRow("API Key：", self.apikey_edit)

        self.model_edit = QtWidgets.QLineEdit()
        self.model_edit.setPlaceholderText("如 deepseek-chat / qwen-plus / gpt-4o-mini")
        layout.addRow("模型名：", self.model_edit)

        self.autonomy_combo = QtWidgets.QComboBox()
        self.autonomy_combo.addItems([label for label, _ in _AUTONOMY])
        layout.addRow("自主程度：", self.autonomy_combo)

        save_btn = QtWidgets.QPushButton("保存设置")
        save_btn.clicked.connect(self._save)
        layout.addRow("", save_btn)

    def _on_preset(self, idx: int) -> None:
        if idx <= 0:
            return
        _, base_url, model = _PRESETS[idx]
        if base_url:
            self.baseurl_edit.setText(base_url)
        if model and not self.model_edit.text().strip():
            self.model_edit.setText(model)

    def _pick_exe(self, edit: QtWidgets.QLineEdit) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 python.exe", "", "python.exe (python.exe)")
        if path:
            edit.setText(path)

    def _pick_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择输出根目录")
        if path:
            self.output_root_edit.setText(path)

    def _autonomy_value(self) -> str:
        idx = self.autonomy_combo.currentIndex()
        return _AUTONOMY[idx][1] if 0 <= idx < len(_AUTONOMY) else AUTONOMY_CONFIRM_WRITES

    def _set_autonomy(self, value: str) -> None:
        for i, (_, v) in enumerate(_AUTONOMY):
            if v == value:
                self.autonomy_combo.setCurrentIndex(i)
                return
        self.autonomy_combo.setCurrentIndex(0)

    def _load(self) -> None:
        s = self.state.settings
        self.modern_edit.setText(s.modern_python)
        self.arcmap_edit.setText(s.arcmap_python)
        self.output_root_edit.setText(s.output_root)
        self.baseurl_edit.setText(s.ai_base_url)
        self.apikey_edit.setText(s.ai_api_key)
        self.model_edit.setText(s.ai_model)
        self._set_autonomy(s.autonomy_mode or AUTONOMY_CONFIRM_WRITES)

    def _save(self) -> None:
        self.state.update_settings(
            modern_python=self.modern_edit.text().strip(),
            arcmap_python=self.arcmap_edit.text().strip(),
            output_root=self.output_root_edit.text().strip(),
            ai_base_url=self.baseurl_edit.text().strip(),
            ai_api_key=self.apikey_edit.text().strip(),
            ai_model=self.model_edit.text().strip(),
            autonomy_mode=self._autonomy_value(),
            ai_enabled=bool(self.baseurl_edit.text().strip() and self.model_edit.text().strip()),
        )
        QtWidgets.QMessageBox.information(self, "已保存", "设置已保存到 ~/.gisdo/settings.json")


__all__ = ["SettingsView"]
