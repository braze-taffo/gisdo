"""geoprocessing.py 脚本内部逻辑单测（stub arcpy，不跑真实子进程）。

重点验证 call_tool：按函数真实签名过滤派生输出，避免多传位置参数报 TypeError
（如 management.CreateFolder 的 out_folder 不在 Python 函数签名里）。
"""

import sys
import types

# 导入脚本前先塞一个假 arcpy 模块（脚本顶层 import arcpy，但测试不真跑它）。
_arcpy = types.ModuleType("arcpy")
sys.modules.setdefault("arcpy", _arcpy)

from gisdo.engine.scripts import geoprocessing as gp


class _Param:
    def __init__(self, name):
        self.name = name


def _call(params, values, func):
    gp.call_tool(func, [_Param(n) for n in params], values)


def test_call_tool_drops_derived_output_param():
    # CreateFolder 的签名只有 out_folder_path / out_name，out_folder 是派生输出。
    calls = {}

    def CreateFolder(out_folder_path, out_name):
        calls["path"] = out_folder_path
        calls["name"] = out_name

    _call(["out_folder_path", "out_name", "out_folder"],
          ["E:/tmp", "clip_v1", "E:/tmp/clip_v1"], CreateFolder)
    assert calls == {"path": "E:/tmp", "name": "clip_v1"}


def test_call_tool_keeps_real_output_param():
    # Clip 的 out_feature_class 是真正的位置参数，必须保留。
    calls = {}

    def Clip(in_features, clip_features, out_feature_class, cluster_tolerance=None):
        calls.update(in_features=in_features, clip_features=clip_features,
                     out=out_feature_class, tol=cluster_tolerance)

    _call(["in_features", "clip_features", "out_feature_class", "cluster_tolerance"],
          ["a.shp", "b.shp", "out.shp", None], Clip)
    assert calls == {"in_features": "a.shp", "clip_features": "b.shp",
                     "out": "out.shp", "tol": None}


def test_call_tool_falls_back_when_signature_unavailable():
    calls = []

    class Opaque:
        def __call__(self, *args):
            calls.append(list(args))

    _call(["a", "b"], [1, 2], Opaque())
    assert calls == [[1, 2]]


def test_positional_params_lists_names():
    def Fn(a, b, *, c):
        pass

    assert gp._positional_params(Fn) == ["a", "b"]


def test_positional_params_none_for_uninspectable():
    assert gp._positional_params(42) is None  # 非 callable，签名解析抛 TypeError
