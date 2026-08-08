"""PyInstaller 打包入口：启动 GISdo GUI。

独立的薄入口脚本（不直接指向 src/gisdo/gui/app.py），PyInstaller 以此为依赖分析根。
"""

from gisdo.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
