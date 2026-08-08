# -*- mode: python ; coding: utf-8 -*-
"""GISdo PyInstaller 打包配置（onedir）。

打包后 sys.executable 指向 exe：纯 Python 脚本（discover/verify_png/render）经
runner._run_script_inplace 主进程内 runpy 执行，engine/scripts/*.py 需作为数据打入
（collect_data_files('gisdo.engine')）。arcpy 脚本仍经真实 GeoScene/ArcMap 运行时
subprocess 跑，无需打包 arcpy。
"""

from PyInstaller.utils.hooks import collect_data_files

block_cipher = None

# engine/scripts/*.py 是运行时经 runpy/importlib.resources 动态执行的，必须作为
# 数据文件打进包（include_py_files=True），否则 frozen 下 resources.files 找不到。
datas = collect_data_files("gisdo.engine.scripts", include_py_files=True)

a = Analysis(
    ["entry_gui.py"],
    pathex=["..", "."],  # 项目根 + spec 所在目录（src 布局，包在 src/ 下）
    binaries=[],
    datas=datas,
    hiddenimports=[
        "openai",  # llm.py 懒加载，需显式收集
        "gisdo._model_config",  # importlib.import_module 动态加载，静态分析不可见
        "matplotlib",
        "matplotlib.backends.backend_agg",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["arcpy"],  # 应用进程永不 import arcpy
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="gisdo",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # GUI 无黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
