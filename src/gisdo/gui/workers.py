"""后台工作线程：把 engine 操作放到 QThreadPool 执行，不阻塞 UI。

用法::

    worker = Worker(ops.inspect_aprx, modern_rt, project)
    worker.signals.log.connect(log_console.append_log)
    worker.signals.finished.connect(on_finished)
    worker.signals.error.connect(on_error)
    QThreadPool.globalInstance().start(worker)

取消：调用 ``worker.request_cancel()``，engine 会终止子进程并抛 :class:`RunCancelled`。
"""

from __future__ import annotations

import inspect
import threading
from collections.abc import Callable

from PySide6 import QtCore

from gisdo.engine.runner import RunCancelled


class WorkerSignals(QtCore.QObject):
    log = QtCore.Signal(str)        # 子进程 stdout/stderr 按行
    progress = QtCore.Signal(str)   # 状态文本
    finished = QtCore.Signal(object)  # ScriptResult / dict / 任意返回值
    error = QtCore.Signal(str)


def _stream_to(signals: WorkerSignals, prefix: str = ""):
    def cb(line: str) -> None:
        signals.log.emit(f"{prefix}{line}" if prefix else line)
    return cb


def _accepts(fn: Callable, name: str) -> bool:
    """函数是否接受某关键字参数（含 **kwargs）。"""
    try:
        params = inspect.signature(fn).parameters
    except (ValueError, TypeError):
        return True
    if name in params:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


class Worker(QtCore.QRunnable):
    """在线程池中运行一个 engine 操作的可运行对象。"""

    def __init__(self, fn: Callable, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self._cancel = threading.Event()
        self.setAutoDelete(True)

    def request_cancel(self) -> None:
        self._cancel.set()
        self.signals.progress.emit("正在取消…")

    def run(self) -> None:
        # 按函数签名注入取消事件与日志回调（ops 用 on_output，runtime 用 on_stdout）。
        if _accepts(self.fn, "cancel"):
            self.kwargs.setdefault("cancel", self._cancel)
        if "on_output" not in self.kwargs and "on_stdout" not in self.kwargs:
            name = "on_output" if _accepts(self.fn, "on_output") else (
                "on_stdout" if _accepts(self.fn, "on_stdout") else None
            )
            if name:
                self.kwargs[name] = _stream_to(self.signals)
        if "on_stderr" not in self.kwargs and _accepts(self.fn, "on_stderr"):
            self.kwargs["on_stderr"] = _stream_to(self.signals, prefix="[stderr] ")
        try:
            result = self.fn(*self.args, **self.kwargs)
        except RunCancelled:
            self.signals.error.emit("已取消。")
            return
        except Exception as exc:  # noqa: BLE001 - 顶层兜底，避免线程静默崩溃
            self.signals.error.emit(f"{type(exc).__name__}: {exc}")
            return
        self.signals.finished.emit(result)


def start_worker(fn: Callable, *args, on_finished=None, on_error=None, on_log=None,
                 **kwargs) -> Worker:
    """便捷构造并启动一个 worker，返回 worker 引用（可用于取消）。"""
    worker = Worker(fn, *args, **kwargs)
    _alive_workers.add(worker)  # 防止 Python 侧被 GC，导致 signals QObject 先于 emit 被删
    if on_finished is not None:
        worker.signals.finished.connect(on_finished)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    if on_log is not None:
        worker.signals.log.connect(on_log)
    # 回调执行完再释放引用（连接顺序 = 调用顺序）
    worker.signals.finished.connect(lambda *_: _alive_workers.discard(worker))
    worker.signals.error.connect(lambda *_: _alive_workers.discard(worker))
    QtCore.QThreadPool.globalInstance().start(worker)
    return worker


_alive_workers: set = set()
