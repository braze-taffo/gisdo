"""Agent 对话视图：用自然语言下 GIS 任务，Agent 自主调用工具。

布局：页头（含当前项目 chip）→ 工具栏 → 对话区（气泡样式）/ 引导卡（前置条件缺失时）
→ 工具日志（可折叠）→ 输入区。

Agent 跑在 QThreadPool 上，回调经 Qt 信号回主线程刷新 UI。写操作的确认用
跨线程握手：worker 线程的 ``on_confirm`` 发信号后等 ``threading.Event``，
主线程弹模态对话框，用户选择后置位 Event，worker 继续。

引导卡：当前项目 / LLM 配置 / 现代运行时 三项不齐时，对话区切换为引导卡，
按钮经 ``navigate_requested`` 信号请主窗口跳转对应页。
"""

from __future__ import annotations

import html
import json
import threading

from PySide6 import QtCore, QtGui, QtWidgets

from gisdo.agent import (
    AUTONOMY_AUTONOMOUS,
    AUTONOMY_CONFIRM_EVERY_STEP,
    AUTONOMY_CONFIRM_WRITES,
    Agent,
    AgentCallbacks,
    LlmClient,
    LlmConfig,
    ToolContext,
    build_tool_inventory,
)
from gisdo.agent.loop import sanitize_history
from gisdo.agent.prompt import format_project_context
from gisdo.engine.runner import RunCancelled
from gisdo.gui import theme
from gisdo.gui.icons import get_icon
from gisdo.gui.markdown import render_markdown
from gisdo.gui.widgets import LogConsole, PageHeader
from gisdo.project import history_path

_AUTONOMY_ITEMS = [
    ("仅写操作确认", AUTONOMY_CONFIRM_WRITES),
    ("全程自主", AUTONOMY_AUTONOMOUS),
    ("每步都确认", AUTONOMY_CONFIRM_EVERY_STEP),
]

_SPACING = '<p style="font-size:5px">&nbsp;</p>'


def _user_bubble(content_html: str) -> str:
    return (
        f'<table width="100%" border="0" cellspacing="0" cellpadding="8"><tr>'
        f'<td width="18%"></td>'
        f'<td bgcolor="{theme.ACCENT_SOFT}">'
        f'<span style="color:{theme.ACCENT};font-weight:600">你</span><br/>'
        f'{content_html}</td>'
        f'</tr></table>' + _SPACING
    )


def _agent_bubble(content_html: str) -> str:
    return (
        f'<table width="100%" border="0" cellspacing="0" cellpadding="8"><tr>'
        f'<td bgcolor="{theme.BG}">'
        f'<span style="color:{theme.TEXT};font-weight:600">🤖 Agent</span><br/>'
        f'{content_html}</td>'
        f'<td width="18%"></td>'
        f'</tr></table>' + _SPACING
    )


def _tool_line(text: str) -> str:
    return f'<p style="color:{theme.TEXT_DIM};font-size:12px">🔧 {text}</p>'


def _info_line(text: str) -> str:
    return f'<p style="color:{theme.TEXT_DIM}">ℹ️ {text}</p>'


def _error_block(text: str) -> str:
    return (
        f'<table width="100%" border="0" cellspacing="0" cellpadding="8"><tr>'
        f'<td bgcolor="{theme.DANGER_BG}"><span style="color:{theme.DANGER}">⚠️ {text}</span></td>'
        f'</tr></table>' + _SPACING
    )


class _ChatSignals(QtCore.QObject):
    assistant_text = QtCore.Signal(str)
    token = QtCore.Signal(str)             # 流式增量文本
    tool_start = QtCore.Signal(str, str)   # name, args_json
    tool_end = QtCore.Signal(str, str)     # name, result
    info = QtCore.Signal(str)
    stream = QtCore.Signal(str)            # 子进程 stdout/stderr 原始行
    error = QtCore.Signal(str)
    confirm_requested = QtCore.Signal(str, str, str)  # name, args_json, block
    ask_requested = QtCore.Signal(str, str)  # question, options_json
    finished = QtCore.Signal()


class ChatWorker(QtCore.QRunnable):
    """在线程池里跑一轮 ``agent.run``。Agent 已带好发信号的回调。"""

    class _Sigs(QtCore.QObject):
        finished = QtCore.Signal()
        error = QtCore.Signal(str)
        info = QtCore.Signal(str)

    def __init__(self, agent: Agent, message: str, history_path: str | None = None) -> None:
        super().__init__()
        self.agent = agent
        self.message = message
        self.history_path = history_path
        self.sigs = ChatWorker._Sigs()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            # 首条消息前把本机已装地理处理工具清单注入系统提示词（只跑一次）
            if not self.agent.has_tool_inventory:
                self.sigs.info.emit("正在注入本机已装地理处理工具清单…")
                inventory = build_tool_inventory(self.agent.ctx.modern_runtime)
                if inventory:
                    self.agent.inject_tool_inventory(inventory)
                    self.sigs.info.emit("工具清单已注入：模型将使用真实官方参数名。")
                else:
                    self.sigs.info.emit("未注入工具清单（未选定现代运行时），模型会先 discover_runtimes。")
            self.agent.run(self.message)
        except RunCancelled:  # 覆盖 LlmCancelled，取消不当错误报
            self.sigs.info.emit("已取消。")
        except Exception as exc:  # noqa: BLE001
            self.sigs.error.emit(f"运行异常：{type(exc).__name__}: {exc}")
        finally:
            if self.history_path:
                try:
                    self.agent.save_history(self.history_path)
                except OSError:
                    pass  # 落盘失败不阻断对话
        self.sigs.finished.emit()


class ChatView(QtWidgets.QWidget):
    navigate_requested = QtCore.Signal(str)  # 请主窗口跳转到某页（如 "项目"）

    def __init__(self, state, log) -> None:
        super().__init__()
        self.state = state
        self.log = log
        self._agent: Agent | None = None
        self._signals = _ChatSignals()
        self._cancel = threading.Event()
        self._confirm_event = threading.Event()
        self._confirm_result = False
        self._ask_event = threading.Event()
        self._ask_result: str | None = None
        self._busy = False
        self._current_worker = None
        self._stream_open = False
        self._stream_start = 0
        self._stream_text = ""
        self._pending_reload = False
        self._build()
        self._wire()
        self._reload_project_ui()  # 初始加载当前项目

    # --- UI ---
    def _build(self) -> None:
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        self.header = PageHeader("Agent 对话", "用自然语言描述 GIS 任务，Agent 自动调用工具完成")
        self.project_chip = QtWidgets.QLabel()
        self.project_chip.setObjectName("chip")
        self.project_chip.setVisible(False)
        self.header.add_widget(self.project_chip)
        layout.addWidget(self.header)

        bar = QtWidgets.QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QtWidgets.QLabel("自主程度："))
        self.autonomy_combo = QtWidgets.QComboBox()
        for label, _value in _AUTONOMY_ITEMS:
            self.autonomy_combo.addItem(label)
        self.autonomy_combo.setCurrentIndex(0)
        self.autonomy_combo.currentIndexChanged.connect(self._on_autonomy_changed)
        bar.addWidget(self.autonomy_combo)
        bar.addStretch(1)
        self.tool_log_toggle = QtWidgets.QCheckBox("工具日志")
        self.tool_log_toggle.setChecked(False)
        self.tool_log_toggle.toggled.connect(self._on_tool_log_toggled)
        bar.addWidget(self.tool_log_toggle)
        self.reset_btn = QtWidgets.QPushButton(get_icon("refresh", theme.ACCENT), "重置对话")
        self.reset_btn.setProperty("kind", "ghost")
        self.reset_btn.clicked.connect(self._on_reset)
        bar.addWidget(self.reset_btn)
        self.stop_btn = QtWidgets.QPushButton(get_icon("stop", theme.DANGER), "停止")
        self.stop_btn.setProperty("kind", "danger")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        bar.addWidget(self.stop_btn)
        layout.addLayout(bar)

        # 对话区 / 引导卡
        self.chat_stack = QtWidgets.QStackedWidget()
        self._build_onboarding()
        self.chat_stack.addWidget(self.onboarding)  # index 0

        self.view = QtWidgets.QTextEdit()
        self.view.setObjectName("chatView")
        self.view.setReadOnly(True)
        self.chat_stack.addWidget(self.view)          # index 1
        layout.addWidget(self.chat_stack, 1)

        # 工具活动日志：默认收起，勾选后查看完整参数与原始输出
        self.tool_log = LogConsole()
        self.tool_log.setMaximumHeight(200)
        self.tool_log.setVisible(False)
        layout.addWidget(self.tool_log)

        input_row = QtWidgets.QHBoxLayout()
        input_row.setSpacing(8)
        self.input = QtWidgets.QPlainTextEdit()
        self.input.setFixedHeight(64)
        self.input.setPlaceholderText("用自然语言描述任务，回车发送，Shift+回车换行…")
        self.input.installEventFilter(self)
        input_row.addWidget(self.input, 1)
        self.send_btn = QtWidgets.QPushButton(get_icon("send", "#FFFFFF"), "发送")
        self.send_btn.setProperty("kind", "primary")
        self.send_btn.setDefault(True)
        self.send_btn.setFixedHeight(40)
        self.send_btn.clicked.connect(self._on_send)
        input_row.addWidget(self.send_btn)
        layout.addLayout(input_row)

    def _build_onboarding(self) -> None:
        panel = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(panel)
        outer.addStretch(1)
        card = QtWidgets.QFrame()
        card.setObjectName("card")
        card.setMaximumWidth(560)
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(28, 22, 28, 22)
        card_layout.setSpacing(10)

        title = QtWidgets.QLabel("开始使用 Agent")
        title.setStyleSheet(f"font-size:16px; font-weight:700; color:{theme.TEXT};")
        card_layout.addWidget(title)
        hint = QtWidgets.QLabel("Agent 需要以下三项就绪后才能工作：")
        hint.setStyleSheet(f"color:{theme.TEXT_DIM};")
        card_layout.addWidget(hint)

        self._check_rows: dict[str, QtWidgets.QLabel] = {}
        self._check_btns: dict[str, QtWidgets.QPushButton] = {}
        items = [
            ("project", "当前项目（任务的上下文与写操作落点）", "去项目页", "项目"),
            ("runtime", "现代运行时（GeoScene/ArcGIS Pro Python）", "去运行时页", "运行时"),
            ("llm", "LLM 配置（OpenAI 兼容端点）", "去设置页", "设置"),
        ]
        for key, text, btn_text, page in items:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(8)
            lbl = QtWidgets.QLabel()
            lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
            row.addWidget(lbl, 1)
            btn = QtWidgets.QPushButton(btn_text)
            btn.setProperty("kind", "ghost")
            btn.clicked.connect(lambda _=False, p=page: self.navigate_requested.emit(p))
            row.addWidget(btn)
            card_layout.addLayout(row)
            self._check_rows[key] = lbl
            self._check_btns[key] = btn

        note = QtWidgets.QLabel("三项就绪后，这里会变成对话窗口。")
        note.setStyleSheet(f"color:{theme.TEXT_DIM}; font-size:12px;")
        card_layout.addWidget(note)

        center = QtWidgets.QHBoxLayout()
        center.addStretch(1)
        center.addWidget(card)
        center.addStretch(1)
        outer.addLayout(center)
        outer.addStretch(2)
        self.onboarding = panel

    def _wire(self) -> None:
        s = self._signals
        s.assistant_text.connect(self._on_assistant_text)
        s.token.connect(self._on_token)
        s.tool_start.connect(self._log_tool_start)
        s.tool_end.connect(self._log_tool_end)
        s.info.connect(self._log_info)
        s.stream.connect(self._on_stream)
        s.error.connect(self._on_error)
        s.confirm_requested.connect(self._on_confirm_requested)
        s.ask_requested.connect(self._on_ask_requested)
        self.state.current_project_changed.connect(self._on_project_changed)
        self.state.settings_changed.connect(lambda *_: self._on_prereq_changed())
        self.state.modern_runtime_changed.connect(lambda *_: self._on_prereq_changed())

    # --- 前置条件与引导 ---
    def _prereqs(self) -> dict[str, bool]:
        s = self.state.settings
        return {
            "project": self.state.current_project is not None,
            "runtime": self.state.modern is not None,
            "llm": bool(s.ai_base_url and s.ai_model),
        }

    def _refresh_onboarding(self) -> None:
        prereqs = self._prereqs()
        labels = {
            "project": "当前项目（任务的上下文与写操作落点）",
            "runtime": "现代运行时（GeoScene/ArcGIS Pro Python）",
            "llm": "LLM 配置（OpenAI 兼容端点）",
        }
        for key, ok in prereqs.items():
            color = theme.SUCCESS if ok else theme.WARNING
            mark = "✓" if ok else "✗"
            self._check_rows[key].setText(
                f'<span style="color:{color};font-weight:700">{mark}</span> {labels[key]}'
            )
            self._check_btns[key].setVisible(not ok)

    def _update_stack_page(self) -> None:
        ok = all(self._prereqs().values())
        if not self._busy:
            self.chat_stack.setCurrentIndex(1 if ok else 0)
        self.input.setEnabled(ok and not self._busy)
        self.send_btn.setEnabled(ok and not self._busy)

    def _on_prereq_changed(self) -> None:
        if self._busy:
            return
        if self._agent is None:
            self._reload_project_ui()
        else:
            self._refresh_onboarding()
            self._update_stack_page()

    # --- 对话显示 ---
    def _default_char_format(self) -> QtGui.QTextCharFormat:
        """正文默认字符格式：避免继承上一条气泡尾部 5px 间隔段的格式。"""
        fmt = QtGui.QTextCharFormat()
        fmt.setFont(self.view.document().defaultFont())
        fmt.setForeground(QtGui.QColor(theme.TEXT))
        return fmt

    def _prepare_insert_block(self, cursor: QtGui.QTextCursor) -> None:
        """把光标安顿到一个默认格式的块：末尾块非空则新开块，

        否则直接重置该空块的字符格式（可能残留小字号）。
        这样连续的 <p> 片段不会被 insertHtml 并到一行，流式文本也不继承旧格式。
        """
        last = self.view.document().lastBlock()
        if last.isValid() and last.length() > 1:
            cursor.insertBlock(QtGui.QTextBlockFormat(), self._default_char_format())
        else:
            cursor.setCharFormat(self._default_char_format())

    def _insert(self, html_body: str) -> None:
        """在文档末尾插入 HTML 块并滚动到底。"""
        cursor = self.view.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        self._prepare_insert_block(cursor)
        cursor.insertHtml(html_body)
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()

    def _on_token(self, token: str) -> None:
        # 首个 token：记录该流式块起点（收尾时整块替换为气泡）
        if not self._stream_open:
            cursor = self.view.textCursor()
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
            self._prepare_insert_block(cursor)
            self.view.setTextCursor(cursor)
            self._stream_start = cursor.position()
            self._stream_text = ""
            self._stream_open = True
        self._stream_text += token
        cursor = self.view.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertText(token)  # 纯文本增量，防 XSS；块末统一套气泡模板
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()

    def _replace_stream_block(self, text: str) -> None:
        """把流式期间写入的原始文本块精确替换为 markdown 气泡。"""
        if not self._stream_open:
            return
        cursor = self.view.textCursor()
        cursor.setPosition(self._stream_start)
        cursor.movePosition(
            QtGui.QTextCursor.MoveOperation.Right,
            QtGui.QTextCursor.MoveMode.KeepAnchor,
            len(text),
        )
        cursor.removeSelectedText()
        cursor.insertHtml(_agent_bubble(render_markdown(text)))
        self._stream_open = False
        self.view.setTextCursor(cursor)
        self.view.ensureCursorVisible()

    def _on_assistant_text(self, text: str) -> None:
        if self._stream_open:
            self._replace_stream_block(text)
            return
        self._insert(_agent_bubble(render_markdown(text)))

    def _finalize_stream(self, text: str) -> None:
        """取消/异常收尾：把未闭合的原始文本块升级为气泡。"""
        self._replace_stream_block(text)

    # --- 项目关联 ---
    def _history_path(self) -> str | None:
        proj = self.state.current_project
        return str(history_path(proj.id)) if proj is not None else None

    def _on_project_changed(self, _project) -> None:
        if self._busy:
            self._pending_reload = True  # 流式进行中，延后到 _on_finished
            return
        self._reload_project_ui()

    def _reload_project_ui(self) -> None:
        # 旧 Agent 若存在，先落盘其历史到旧项目路径
        old_path = None
        if self._agent is not None:
            proj = getattr(self._agent.ctx, "project", None)
            if proj is not None:
                old_path = str(history_path(proj.id))
        if self._agent is not None and old_path:
            try:
                self._agent.save_history(old_path)
            except OSError:
                pass
        self._agent = None
        self._stream_open = False
        self._cancel.clear()
        self.view.clear()
        proj = self.state.current_project
        self.project_chip.setVisible(proj is not None)
        if proj is not None:
            self.project_chip.setText(f"项目：{proj.name}")
            loaded = self._load_history_messages(proj.id)
            self._insert(_info_line(
                f'当前项目：{html.escape(proj.name)}'
                f'（地图输出：{html.escape(proj.map_output_dir or "未设置")}）'
            ))
            if loaded:
                self._render_transcript(loaded)
        self._refresh_onboarding()
        self._update_stack_page()

    def _load_history_messages(self, project_id: str):
        from pathlib import Path

        p = Path(history_path(project_id))
        if not p.is_file():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return sanitize_history(data.get("messages", []) or [])

    def _render_transcript(self, messages: list[dict]) -> None:
        """把历史消息重渲染进对话区（只读，无流式）。"""
        html_parts = []
        for m in messages:
            role = m.get("role")
            if role == "user":
                content = html.escape(str(m.get("content", ""))).replace("\n", "<br/>")
                html_parts.append(_user_bubble(content))
            elif role == "assistant":
                if m.get("tool_calls"):
                    names = ", ".join(
                        (tc.get("function", {}) or {}).get("name", "?")
                        for tc in (m.get("tool_calls") or [])
                    )
                    html_parts.append(_tool_line(f"调用工具：{html.escape(names)}"))
                if m.get("content"):
                    html_parts.append(_agent_bubble(render_markdown(str(m["content"]))))
            elif role == "tool":
                content = str(m.get("content", ""))
                preview = content if len(content) <= 300 else content[:300] + "…"
                html_parts.append(
                    f'<p style="color:{theme.TEXT_DIM};font-size:12px;margin-left:16px">🔧 '
                    f'{html.escape(str(m.get("name", "?")))} → {html.escape(preview)}</p>'
                )
        if html_parts:
            self.view.setHtml("\n".join(html_parts))

    def _on_tool_log_toggled(self, checked: bool) -> None:
        self.tool_log.setVisible(checked)

    def _clip(self, text: str, limit: int = 2000) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"…（已截断，共 {len(text)} 字符）"

    def _log_tool_start(self, name: str, args_json: str) -> None:
        self.tool_log.append_log(f"🔧 {name}({self._clip(args_json)})")

    def _log_tool_end(self, name: str, result: str) -> None:
        self.tool_log.append_log(f"   ↳ {name}：{self._clip(result)}")

    def _log_info(self, msg: str) -> None:
        self.tool_log.append_log(f"ℹ️ {msg}")

    def _on_stream(self, line: str) -> None:
        # 子进程原始 stdout/stderr 走底部日志坞，不进工具日志面板（避免与大结果双显）。
        if self.log is not None:
            self.log.append_log(line)

    def _on_info(self, msg: str) -> None:
        self._insert(_info_line(html.escape(msg)))

    def _on_error(self, msg: str) -> None:
        self._insert(_error_block(html.escape(msg)))

    # --- 发送 ---
    def _on_send(self) -> None:
        if self._busy:
            return
        text = self.input.toPlainText().strip()
        if not text:
            return
        agent = self._ensure_agent()
        if agent is None:
            return
        self._insert(_user_bubble(html.escape(text).replace("\n", "<br/>")))
        self.input.clear()
        self._set_busy(True)
        worker = ChatWorker(agent, text, self._history_path())
        self._current_worker = worker  # 持引用，防 GC 导致 signals 被删
        worker.sigs.finished.connect(self._on_finished)
        worker.sigs.error.connect(self._on_error)
        worker.sigs.info.connect(self._signals.info.emit)
        QtCore.QThreadPool.globalInstance().start(worker)

    def _ensure_agent(self) -> Agent | None:
        if self._agent is not None:
            return self._agent
        prereqs = self._prereqs()
        if not all(prereqs.values()):
            self._refresh_onboarding()
            self._update_stack_page()
            return None
        s = self.state.settings
        config = LlmConfig(base_url=s.ai_base_url, api_key=s.ai_api_key, model=s.ai_model)
        client = LlmClient(config)
        proj = self.state.current_project
        ctx = ToolContext(
            modern_runtime=self.state.modern,
            arcmap_runtime=self.state.arcmap,
            cancel=self._cancel,
            on_log=self._signals.stream.emit,
            project=proj,
        )
        autonomy = _AUTONOMY_ITEMS[self.autonomy_combo.currentIndex()][1]

        def _on_confirm(name, args, alignment):
            block = alignment.as_block() if alignment is not None else "（只读操作，无对齐块）"
            self._confirm_event.clear()
            self._signals.confirm_requested.emit(name, json.dumps(args, ensure_ascii=False), block)
            self._confirm_event.wait(timeout=3600)
            return self._confirm_result

        def _on_ask(question, options):
            self._ask_event.clear()
            self._signals.ask_requested.emit(question, json.dumps(options, ensure_ascii=False))
            self._ask_event.wait(timeout=3600)
            return self._ask_result

        callbacks = AgentCallbacks(
            on_assistant_text=self._signals.assistant_text.emit,
            on_token=self._signals.token.emit,
            on_tool_start=lambda n, a: self._signals.tool_start.emit(n, json.dumps(a, ensure_ascii=False)),
            on_tool_end=self._signals.tool_end.emit,
            on_confirm=_on_confirm,
            on_ask_user=_on_ask,
            on_error=self._signals.error.emit,
            on_info=self._signals.info.emit,
        )
        self._agent = Agent(client.chat, ctx, callbacks=callbacks, autonomy=autonomy,
                            cancel=self._cancel)
        if proj is not None:
            self._agent.inject_project_context(format_project_context(proj))
            loaded = self._load_history_messages(proj.id)
            if loaded:
                self._agent.load_history(loaded)
        self._insert(_info_line(
            f'Agent 就绪：模型={html.escape(s.ai_model)}，自主={autonomy}，'
            f'现代运行时={"有" if self.state.modern else "无"}，'
            f'项目={html.escape(proj.name if proj else "无")}。'
        ))
        return self._agent

    def _on_autonomy_changed(self, _idx: int) -> None:
        if self._agent is not None:
            self._agent.set_autonomy(_AUTONOMY_ITEMS[self.autonomy_combo.currentIndex()][1])

    def _on_reset(self) -> None:
        if self._busy:
            return
        self._agent = None
        self.view.clear()
        self._insert(_info_line("已重置。下一条消息会重建 Agent（拾取最新运行时/设置）。"))
        hp = self._history_path()
        if hp:
            try:
                # 落盘空历史，保证「重置→关闭→再开」一致
                self._empty_history_file(hp)
            except OSError:
                pass
        self._update_stack_page()

    def _empty_history_file(self, path: str) -> None:
        from pathlib import Path

        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": 1, "messages": []}), encoding="utf-8")

    def _on_stop(self) -> None:
        self._cancel.set()
        self._on_info("已请求停止（流式生成将中断，当前子进程将终止）。")

    def _on_finished(self) -> None:
        self._current_worker = None
        self._cancel.clear()  # run 结束/失败后统一清，避免下轮误判为已取消
        self._finalize_stream(self._stream_text)  # 取消时收尾未闭合的流式块
        self._set_busy(False)
        if self._pending_reload:
            self._pending_reload = False
            self._reload_project_ui()  # 流式期间的切换项目请求延后执行

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.stop_btn.setEnabled(busy)
        self.reset_btn.setEnabled(not busy)
        if not busy:
            self.chat_stack.setCurrentIndex(1)  # 有对话后固定在对话页
        self._update_stack_page()

    def _on_confirm_requested(self, name: str, args_json: str, block: str) -> None:
        msg = QtWidgets.QMessageBox(self)
        msg.setWindowTitle("确认写操作")
        msg.setIcon(QtWidgets.QMessageBox.Icon.Question)
        msg.setText(f"Agent 请求执行写操作：{name}\n参数：{args_json}")
        msg.setInformativeText(f"对齐确认块：\n\n{block}\n\n是否批准？")
        msg.setStandardButtons(QtWidgets.QMessageBox.StandardButton.Yes |
                               QtWidgets.QMessageBox.StandardButton.No)
        msg.setDefaultButton(QtWidgets.QMessageBox.StandardButton.No)
        self._confirm_result = (msg.exec() == QtWidgets.QMessageBox.StandardButton.Yes)
        self._confirm_event.set()

    def _on_ask_requested(self, question: str, options_json: str) -> None:
        try:
            options = json.loads(options_json) or []
        except json.JSONDecodeError:
            options = []
        if not isinstance(options, list) or not options:
            self._ask_result = self._ask_free_text(question)
            self._ask_event.set()
            return
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Agent 提问")
        dlg.setMinimumWidth(420)
        lay = QtWidgets.QVBoxLayout(dlg)
        lbl = QtWidgets.QLabel(question)
        lbl.setWordWrap(True)
        lay.addWidget(lbl)
        for opt in options[:6]:
            btn = QtWidgets.QPushButton(str(opt))
            btn.setAutoDefault(False)
            btn.clicked.connect(lambda _=False, o=opt: self._ask_choose(dlg, o))
            lay.addWidget(btn)
        edit = QtWidgets.QLineEdit()
        edit.setPlaceholderText("或自行输入答案后回车…")
        edit.returnPressed.connect(lambda: self._ask_choose(dlg, edit.text().strip()))
        lay.addWidget(edit)
        skip = QtWidgets.QPushButton("不回答")
        skip.clicked.connect(dlg.reject)
        lay.addWidget(skip)
        self._ask_result = None
        dlg.exec()
        self._ask_event.set()

    def _ask_choose(self, dlg, value) -> None:
        self._ask_result = value or None  # 空输入视为未回答
        dlg.accept()

    def _ask_free_text(self, question: str) -> str | None:
        text, ok = QtWidgets.QInputDialog.getText(self, "Agent 提问", question)
        return text.strip() if ok and text.strip() else None

    # --- 回车发送 ---
    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QtCore.QEvent.Type.KeyPress:
            key = event.key()
            if key in (QtCore.Qt.Key.Key_Return, QtCore.Qt.Key.Key_Enter):
                if event.modifiers() & QtCore.Qt.KeyboardModifier.ShiftModifier:
                    return False  # Shift+回车换行
                self._on_send()
                return True
        return super().eventFilter(obj, event)


__all__ = ["ChatView"]
