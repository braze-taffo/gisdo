"""engine 脚本包。

脚本不作为常规模块被 import，而是由 runner 经 subprocess（开发）或 runpy
（打包后）以文件路径执行。本包保留 regular package 形态，保证 PyInstaller
打包后 ``importlib.resources.files`` 能稳定定位脚本文件。
"""
