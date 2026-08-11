GISdo {{VERSION}}（Windows x64）
===============================

运行条件
--------
1. Windows 10 或 Windows 11，64 位。
2. 至少安装以下一种 GIS 运行时：
   - ArcGIS Pro / GeoScene Pro（推荐）；或
   - ArcMap 10.x。
3. 需要联网调用模型 API。API Key 由应用写入 Windows 凭据管理器，
   不会保存在本目录。

安装版
------
普通用户推荐运行 GISdo-{{VERSION}}-Windows-x64-Setup.exe。
安装器按当前用户安装，不要求管理员权限。若系统缺少 Microsoft
WebView2 Runtime，安装器会从微软官方下载并静默安装。

目录版
------
解压 Portable.zip 后，保持目录结构不变，运行 GISdo {{VERSION}} Windows x64\gisdo.exe。
workers、skills、fixtures 目录是程序的一部分，请勿单独移动或删除。

数据位置
--------
设置、任务、对话和性能记录保存在：
%LOCALAPPDATA%\GISdo\

卸载应用不会删除用户自己的 GIS 数据或任务产物。

当前限制
--------
- ArcPy 由已安装的 ArcGIS/GeoScene Python 环境提供，本安装包不重新分发 ArcPy。
- 扫描版 PDF 需要 OCR；当前版本会提示，不会把空白识别当作成功。
- 当前测试包尚未进行商业代码签名，Windows SmartScreen 可能显示未知发布者提示。
