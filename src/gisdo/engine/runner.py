"""subprocess 派发层：用正确的解释器运行引擎脚本。

应用进程永不 ``import arcpy``。所有 arcpy 工作通过本模块派发到 GeoScene/ArcGIS Pro
或 ArcMap 的 Python 解释器执行，解析其 JSON stdout。``render_classified_lines`` 与
``verify_png`` 不依赖 arcpy，可用应用自身 Python 运行。

脚本退出码约定：``0`` 成功；``3`` 校验失败（如空白 PNG、断源、哈希不匹配）；
其余为运行错误。
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from gisdo.engine.jsonutil import JsonParseError, extract_trailing_json

SCRIPTS_PACKAGE = "gisdo.engine.scripts"

# 退出码 3 在所有脚本中统一表示"校验失败"（空白导出、断源、哈希不匹配等）。
VALIDATION_FAILED_RC = 3

# PyInstaller 打包后 sys.executable 指向 exe，无法再当解释器 subprocess 跑脚本。
# 这几个纯 Python 脚本（不依赖 arcpy）改为主进程内 runpy 执行。
PURE_PYTHON_SCRIPTS = {"discover_geoscene.py", "verify_png.py", "render_classified_lines.py"}


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


class RunCancelled(Exception):
    """运行被用户取消。"""


@dataclass
class ScriptResult:
    """单次脚本执行的结果。"""

    script: str
    interpreter: str
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    duration_s: float
    json: Any = field(default=None, repr=False)
    json_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def validation_failed(self) -> bool:
        return self.returncode == VALIDATION_FAILED_RC

    @property
    def failed(self) -> bool:
        return self.returncode != 0 and not self.validation_failed


def script_path(name: str) -> Path:
    """定位 ``engine/scripts/<name>``，兼容开发安装与 PyInstaller 打包。"""
    file = resources.files(SCRIPTS_PACKAGE).joinpath(name)
    try:
        # importlib.resources 在打包后可能返回 Traversable；落盘到真实路径。
        with resources.as_file(file) as resolved:
            return Path(resolved)
    except (AttributeError, FileNotFoundError):
        return Path(str(file))


def _build_command(interpreter: str, script_name: str, args: list[str], is_py2: bool) -> list[str]:
    script = script_path(script_name)
    if is_py2:
        # ArcMap Python 2.7 不识别 -X utf8；遗留脚本自行用 emit()/encode 处理编码。
        return [interpreter, str(script), *args]
    return [interpreter, "-X", "utf8", str(script), *args]


def _run_script_inplace(
    script_name: str,
    args: list[str] | None,
    *,
    on_stdout: Callable[[str], None] | None,
    on_stderr: Callable[[str], None] | None,
    cancel: threading.Event | None,
    parse_json: bool,
) -> ScriptResult:
    """打包后：纯 Python 脚本在主进程内执行（sys.executable 是 exe，无法 subprocess）。

    用 ``runpy`` 以 ``__main__`` 身份执行，捕获 stdout/stderr，返回与 subprocess 一致的
    :class:`ScriptResult`。脚本目录临时加入 ``sys.path``，兼容脚本间 ``from verify_png import``。
    """
    import contextlib
    import io
    import runpy

    script = script_path(script_name)
    scripts_dir = str(script.parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    old_argv = sys.argv
    sys.argv = [str(script)] + [str(a) for a in (args or [])]

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    start = time.monotonic()
    rc = 0
    try:
        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        rc = exc.code if isinstance(exc.code, int) else 0
    except KeyboardInterrupt:
        if cancel is not None:
            cancel.set()
        raise RunCancelled(f"已取消：{script_name}")
    except Exception:  # noqa: BLE001 - 模拟 subprocess：异常变 traceback + rc=1
        import traceback

        traceback.print_exc(file=stderr_buf)
        rc = 1
    finally:
        sys.argv = old_argv
        if scripts_dir in sys.path:
            sys.path.remove(scripts_dir)

    stdout = stdout_buf.getvalue()
    stderr = stderr_buf.getvalue()
    duration = time.monotonic() - start
    for line in stdout.splitlines():
        if on_stdout is not None:
            on_stdout(line.rstrip("\r\n"))
    for line in stderr.splitlines():
        if on_stderr is not None:
            on_stderr(line.rstrip("\r\n"))

    json_value: Any = None
    json_error: str | None = None
    if parse_json and rc in (0, VALIDATION_FAILED_RC):
        try:
            json_value = extract_trailing_json(stdout)
        except JsonParseError as exc:
            json_error = str(exc)

    return ScriptResult(
        script=script_name,
        interpreter=sys.executable,
        args=list(args or []),
        returncode=rc,
        stdout=stdout,
        stderr=stderr,
        duration_s=duration,
        json=json_value,
        json_error=json_error,
    )


def run_script(
    interpreter: str,
    script_name: str,
    args: list[str] | None = None,
    *,
    is_py2: bool = False,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
    timeout: float | None = None,
    cancel: threading.Event | None = None,
    parse_json: bool = True,
) -> ScriptResult:
    """运行一个引擎脚本并返回 :class:`ScriptResult`。

    回调 ``on_stdout``/``on_stderr`` 按行实时触发，用于 GUI 进度显示。
    ``cancel`` 被设置时终止子进程并抛 :class:`RunCancelled`。

    打包后（frozen）纯 Python 脚本改为主进程内执行，见 :func:`_run_script_inplace`。
    """
    args = [str(arg) for arg in (args or [])]
    # 仅当解释器是应用自身（APP_PYTHON）时才走 in-place；probe 传真实运行时不走这里。
    is_app_interpreter = (interpreter or "").lower() == (sys.executable or "").lower()
    if _is_frozen() and script_name in PURE_PYTHON_SCRIPTS and is_app_interpreter:
        return _run_script_inplace(
            script_name, args,
            on_stdout=on_stdout, on_stderr=on_stderr,
            cancel=cancel, parse_json=parse_json,
        )
    command = _build_command(interpreter, script_name, args, is_py2)
    start = time.monotonic()

    proc = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        cwd=cwd,
        bufsize=1,
    )

    stdout_lines: list[str] = []
    stderr_lines: list[str] = []

    def drain(stream, buffer: list[str], callback: Callable[[str], None] | None) -> None:
        try:
            for line in stream:
                buffer.append(line)
                if callback is not None:
                    callback(line.rstrip("\r\n"))
        finally:
            stream.close()

    out_thread = threading.Thread(
        target=drain, args=(proc.stdout, stdout_lines, on_stdout), daemon=True
    )
    err_thread = threading.Thread(
        target=drain, args=(proc.stderr, stderr_lines, on_stderr), daemon=True
    )
    out_thread.start()
    err_thread.start()

    cancelled = False
    try:
        while True:
            try:
                proc.wait(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    break
                if timeout is not None and time.monotonic() - start > timeout:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=5)
                    raise subprocess.TimeoutExpired(command, timeout)
    finally:
        out_thread.join(timeout=10)
        err_thread.join(timeout=10)

    stdout = "".join(stdout_lines)
    stderr = "".join(stderr_lines)
    duration = time.monotonic() - start

    if cancelled:
        raise RunCancelled(f"已取消：{script_name}")

    json_value: Any = None
    json_error: str | None = None
    if parse_json and proc.returncode in (0, VALIDATION_FAILED_RC):
        try:
            json_value = extract_trailing_json(stdout)
        except JsonParseError as exc:
            json_error = str(exc)

    return ScriptResult(
        script=script_name,
        interpreter=interpreter,
        args=args,
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_s=duration,
        json=json_value,
        json_error=json_error,
    )
