"""seed_defaults_if_missing 测试：打包后首次运行预置模型配置。"""

from gisdo import config as config_mod
from gisdo.config import Settings, seed_defaults_if_missing


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(config_mod, "SETTINGS_DIR", tmp_path)
    monkeypatch.setattr(config_mod, "SETTINGS_FILE", tmp_path / "settings.json")


def test_seed_writes_when_missing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    assert seed_defaults_if_missing() is True
    s = Settings.load()
    assert s.ai_enabled is True
    assert s.ai_base_url  # 预置了 base_url
    assert s.ai_model  # 预置了 model
    assert s.ai_api_key  # 预置了 key


def test_seed_does_not_overwrite_existing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    s = Settings()
    s.ai_model = "custom-model"
    s.save()
    assert seed_defaults_if_missing() is False
    assert Settings.load().ai_model == "custom-model"


def test_seed_skips_when_model_config_missing(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    # 模拟开源环境：_model_config.py 不存在 -> importlib.import_module 抛 ImportError
    import sys

    def fake_import_module(name):
        if name == "gisdo._model_config":
            raise ImportError("No module named '_model_config'")
        raise AssertionError(f"unexpected import_module: {name}")

    monkeypatch.setattr("importlib.import_module", fake_import_module)
    assert seed_defaults_if_missing() is False
    assert not (tmp_path / "settings.json").exists()
