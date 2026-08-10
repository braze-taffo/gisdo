"""用户设置（纯数据，无 GUI 依赖）。

``Settings`` 持久化到 ``~/.gisdo/settings.json``，CLI 与 GUI 共用。
GUI 的 ``AppState`` 在 ``gui/state.py`` 里 import 此类并 re-export。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

SETTINGS_DIR = Path.home() / ".gisdo"
SETTINGS_FILE = SETTINGS_DIR / "settings.json"


@dataclass
class Settings:
    """运行时与 LLM 配置。新增字段用默认值，旧 settings.json 仍兼容。"""

    # 运行时
    modern_python: str = ""
    arcmap_python: str = ""
    output_root: str = ""
    # LLM（OpenAI 格式兼容）
    ai_enabled: bool = False
    ai_base_url: str = ""
    ai_api_key: str = ""
    ai_model: str = ""
    ai_thinking_level: str = "auto"  # auto | disabled | low | medium | high | max
    autonomy_mode: str = "confirm_writes"  # confirm_writes | autonomous | confirm_every_step
    # 其他
    language: str = "zh"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> Settings:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        with SETTINGS_FILE.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> Settings:
        if SETTINGS_FILE.is_file():
            try:
                return cls.from_dict(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
        return cls()


def seed_defaults_if_missing() -> bool:
    """打包后首次运行：settings.json 不存在时预置模型配置（base_url/key/model）。

    仅当 settings 文件缺失时写入；已存在（开发机）或 `_model_config.py` 缺失
    （开源源码）时跳过。返回是否写入。模型 Key 在 `src/gisdo/_model_config.py`，
    该文件不入 git，PyInstaller 打进 exe。
    """
    if SETTINGS_FILE.is_file():
        return False
    try:
        import importlib

        _model_config = importlib.import_module("gisdo._model_config")
    except ImportError:
        return False
    preset = getattr(_model_config, "MODEL_PRESET", {})
    if not preset:
        return False
    settings = Settings()
    for key, value in preset.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
    settings.save()
    return True


__all__ = ["SETTINGS_DIR", "SETTINGS_FILE", "Settings", "seed_defaults_if_missing"]
