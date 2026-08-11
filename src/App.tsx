import { useEffect, useMemo, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as ScrollArea from "@radix-ui/react-scroll-area";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Blocks, Bot, Check, ChevronRight, CircleDot, Database, FolderKanban, Gauge, Map, MessageSquarePlus, Plus, Send, Settings as SettingsIcon, ShieldCheck, Sparkles, Square, X } from "lucide-react";
import { api } from "./api";
import { Button, Card, GhostButton, Kicker, MarkdownMessage, StatusIcon, WorkerChip } from "./components";
import { useBackendEvents } from "./hooks";
import { cn, shortPath } from "./lib";
import { useAppStore } from "./store";
import type { Project, Settings, TaskPlan } from "./types";

export default function App() {
  useBackendEvents();
  const boot = useQuery({ queryKey: ["bootstrap"], queryFn: api.bootstrap });
  const setBootstrap = useAppStore((s) => s.setBootstrap);
  useEffect(() => { if (boot.data) setBootstrap(boot.data); }, [boot.data, setBootstrap]);
  if (boot.isLoading) return <LaunchScreen />;
  if (boot.error) return <FatalScreen message={String(boot.error)} />;
  return <Workspace />;
}

function LaunchScreen() {
  return <main className="grid min-h-screen place-items-center bg-ink text-white"><div className="text-center"><div className="mx-auto mb-5 grid h-16 w-16 place-items-center rounded-2xl border border-mint/30 bg-mint/10 shadow-glow"><Map className="h-8 w-8 text-mint" /></div><h1 className="text-2xl font-semibold tracking-tight">GISdo</h1><p className="mt-2 animate-pulse text-sm text-fog">正在打开本地工作台…</p></div></main>;
}

function FatalScreen({ message }: { message: string }) {
  return <main className="grid min-h-screen place-items-center bg-ink p-8 text-white"><Card className="max-w-xl p-8"><Kicker>启动失败</Kicker><h1 className="text-2xl font-semibold">无法打开 GISdo</h1><p className="mt-4 break-words text-sm leading-7 text-fog">{message}</p></Card></main>;
}

function Workspace() {
  const [section, setSection] = useState<"agent" | "projects" | "runtime" | "settings">("agent");
  return <div className="flex h-screen min-h-[700px] overflow-hidden bg-ink text-[#edf5f2]">
    <NavRail section={section} setSection={setSection} />
    <ProjectSidebar />
    <div className="flex min-w-0 flex-1 flex-col">
      <TopBar />
      {section === "agent" && <AgentWorkspace />}
      {section === "projects" && <ProjectsView />}
      {section === "runtime" && <RuntimeView />}
      {section === "settings" && <SettingsView />}
    </div>
  </div>;
}

function NavRail({ section, setSection }: { section: string; setSection: (value: "agent" | "projects" | "runtime" | "settings") => void }) {
  const items = [
    ["agent", Bot, "Agent"], ["projects", FolderKanban, "项目"], ["runtime", Gauge, "运行时"], ["settings", SettingsIcon, "设置"],
  ] as const;
  return <aside className="flex w-[76px] shrink-0 flex-col items-center border-r border-line/80 bg-[#07100e] py-5">
    <div className="mb-8 grid h-10 w-10 place-items-center rounded-xl bg-mint text-ink shadow-glow"><Map className="h-5 w-5" /></div>
    <nav className="flex flex-1 flex-col gap-2">{items.map(([id, Icon, label]) => <button key={id} onClick={() => setSection(id)} className={cn("group flex w-14 flex-col items-center gap-1.5 rounded-xl py-2.5 text-[10px] font-medium text-[#668078] transition hover:bg-white/5 hover:text-white", section === id && "bg-mint/10 text-mint")}><Icon className="h-[19px] w-[19px]" /><span>{label}</span></button>)}</nav>
    <div className="h-2 w-2 rounded-full bg-mint shadow-[0_0_8px_#75f0c1]" title="应用在线" />
  </aside>;
}

function ProjectSidebar() {
  const projects = useAppStore((s) => s.projects);
  const activeProjectId = useAppStore((s) => s.activeProjectId);
  const activeConversationId = useAppStore((s) => s.activeConversationId);
  const setActiveProject = useAppStore((s) => s.setActiveProject);
  const setActiveConversation = useAppStore((s) => s.setActiveConversation);
  const setMessages = useAppStore((s) => s.setMessages);
  const setConversations = useAppStore((s) => s.setConversations);
  const conversations = useAppStore((s) => s.conversations);
  const queryClient = useQueryClient();
  const conversationsQuery = useQuery({ queryKey: ["conversations", activeProjectId], queryFn: () => api.listConversations(activeProjectId!), enabled: Boolean(activeProjectId) });
  const messagesQuery = useQuery({ queryKey: ["messages", activeConversationId], queryFn: () => api.listMessages(activeConversationId!), enabled: Boolean(activeConversationId) });
  useEffect(() => { if (conversationsQuery.data) setConversations(conversationsQuery.data); }, [conversationsQuery.data, setConversations]);
  useEffect(() => { if (messagesQuery.data) setMessages(messagesQuery.data.map(({ id, role, content }) => ({ id, role, content }))); }, [messagesQuery.data, setMessages]);
  const createConversation = useMutation({ mutationFn: () => api.createConversation(activeProjectId!), onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["conversations", activeProjectId] }) });
  return <aside className="flex w-[250px] shrink-0 flex-col border-r border-line/80 bg-[#091411]">
    <div className="border-b border-line/70 px-4 py-5"><div className="flex items-center justify-between"><div><Kicker>Workspace</Kicker><div className="text-sm font-semibold">项目与会话</div></div><GhostButton className="h-8 w-8 p-0" aria-label="新建会话" onClick={() => createConversation.mutate()} disabled={!activeProjectId}><MessageSquarePlus className="h-4 w-4" /></GhostButton></div></div>
    <ScrollArea.Root className="min-h-0 flex-1"><ScrollArea.Viewport className="h-full w-full p-3">
      <div className="space-y-1">{projects.map((project) => <button key={project.id} onClick={() => setActiveProject(project.id)} className={cn("flex w-full items-center gap-2 rounded-xl px-3 py-2.5 text-left text-sm text-fog transition hover:bg-white/5 hover:text-white", activeProjectId === project.id && "bg-white/[.065] text-white")}><Database className="h-4 w-4 text-mint/70" /><span className="min-w-0 flex-1 truncate font-medium">{project.name}</span><ChevronRight className="h-3.5 w-3.5 opacity-40" /></button>)}</div>
      <div className="my-4 h-px bg-line/70" />
      <div className="mb-2 px-3 text-[10px] font-bold uppercase tracking-[.16em] text-[#536d65]">会话</div>
      <div className="space-y-1">{conversations.map((conversation) => <button key={conversation.id} onClick={() => setActiveConversation(conversation.id)} className={cn("w-full rounded-xl px-3 py-2.5 text-left text-xs leading-5 text-[#789087] transition hover:bg-white/5 hover:text-[#dce9e4]", activeConversationId === conversation.id && "bg-mint/[.07] text-[#dce9e4]")}><div className="line-clamp-2">{conversation.title}</div></button>)}</div>
    </ScrollArea.Viewport><ScrollArea.Scrollbar orientation="vertical" className="w-2 p-0.5"><ScrollArea.Thumb className="rounded-full bg-line" /></ScrollArea.Scrollbar></ScrollArea.Root>
    <div className="border-t border-line/70 p-4 text-[10px] leading-5 text-[#536d65]">项目和任务记录<br />仅保存在这台电脑</div>
  </aside>;
}

function TopBar() {
  const workerStates = useAppStore((s) => s.workerStates);
  return <header className="flex h-[72px] shrink-0 items-center justify-between border-b border-line/80 bg-[#08120f]/90 px-6 backdrop-blur-xl">
    <div><div className="flex items-center gap-2 text-sm font-semibold"><Sparkles className="h-4 w-4 text-lime" />智能地理处理工作台</div><div className="mt-1 text-[11px] text-[#607970]">可以直接提供路线图、资料或数据目录，我会先阅读再执行</div></div>
    <div className="flex items-center gap-2"><WorkerChip label="Pro" status={workerStates.pro ?? "stopped"} /><WorkerChip label="ArcMap" status={workerStates.arcmap ?? "stopped"} /></div>
  </header>;
}

function AgentWorkspace() {
  return <main className="flex min-h-0 flex-1"><AgentChat /><TaskInspector /></main>;
}

function AgentChat() {
  const [input, setInput] = useState("");
  const [error, setError] = useState("");
  const messages = useAppStore((s) => s.messages);
  const activeConversationId = useAppStore((s) => s.activeConversationId);
  const activeProjectId = useAppStore((s) => s.activeProjectId);
  const activeTaskId = useAppStore((s) => s.activeTaskId);
  const tasks = useAppStore((s) => s.tasks);
  const addMessage = useAppStore((s) => s.addMessage);
  const setTaskStatus = useAppStore((s) => s.setTaskStatus);
  const bottom = useRef<HTMLDivElement>(null);
  const running = activeTaskId ? ["planning", "running", "awaiting_approval", "needs_input", "cancelling"].includes(tasks[activeTaskId]?.status) : false;
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [messages]);
  async function submit() {
    const goal = input.trim(); if (!goal || running) return;
    setInput(""); setError(""); addMessage({ role: "user", content: goal });
    try {
      const taskId = await api.startTask(activeConversationId, goal, { goal, project_id: activeProjectId, conversation_id: activeConversationId });
      setTaskStatus(taskId, "planning");
    } catch (reason) { setError(String(reason)); }
  }
  return <section className="relative flex min-w-0 flex-1 flex-col bg-[radial-gradient(circle_at_50%_-15%,rgba(49,119,94,.12),transparent_42%)]">
    <ScrollArea.Root className="min-h-0 flex-1"><ScrollArea.Viewport className="h-full w-full"><div className="mx-auto flex min-h-full max-w-[940px] flex-col px-8 pb-8 pt-12">
      {messages.length === 0 ? <Welcome /> : <div className="space-y-8">{messages.map((message) => <MarkdownMessage key={message.id} message={message} />)}</div>}
      {error && <div className="mt-6 rounded-xl border border-[#7a362d] bg-[#351914] px-4 py-3 text-sm text-[#ffb4a3]">{error}</div>}
      <div ref={bottom} />
    </div></ScrollArea.Viewport><ScrollArea.Scrollbar orientation="vertical" className="w-2 p-0.5"><ScrollArea.Thumb className="rounded-full bg-line" /></ScrollArea.Scrollbar></ScrollArea.Root>
    <div className="bg-gradient-to-t from-ink via-ink/95 to-transparent px-8 pb-7 pt-8"><div className="mx-auto max-w-[940px]"><div className="rounded-2xl border border-[#29463d] bg-[#0c1c17] p-2 shadow-[0_18px_55px_rgba(0,0,0,.3)] transition focus-within:border-mint/45 focus-within:shadow-glow"><textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void submit(); } }} placeholder={activeProjectId ? "告诉我想完成什么，也可以直接粘贴文件或文件夹路径…" : "请先创建或选择项目…"} disabled={!activeProjectId || running} rows={2} className="max-h-40 min-h-[58px] w-full resize-none bg-transparent px-3 py-2 text-[15px] leading-6 text-white outline-none placeholder:text-[#587068] disabled:opacity-50" /><div className="flex items-center justify-between px-2 pb-1"><div className="flex items-center gap-2 text-[10px] text-[#5d766d]"><Sparkles className="h-3.5 w-3.5" />我会先查看数据，再决定处理方法</div>{running ? <GhostButton className="h-9 px-3" onClick={() => activeTaskId && api.cancelTask(activeTaskId)}><Square className="h-3.5 w-3.5 fill-current" />停止</GhostButton> : <Button className="h-9 w-10 p-0" onClick={() => void submit()} disabled={!input.trim() || !activeProjectId} aria-label="发送"><Send className="h-4 w-4" /></Button>}</div></div></div></div>
  </section>;
}

function Welcome() {
  const examples = ["把广州建筑投影到 WGS84，再按从化区边界裁剪", "检查 APRX 中的断裂数据源并生成清单", "从 MXD 导出道路并生成分级 PNG"];
  return <div className="my-auto animate-fade-up py-10"><div className="mb-7 flex items-center gap-4"><div className="grid h-14 w-14 place-items-center rounded-2xl border border-mint/25 bg-mint/10"><Blocks className="h-7 w-7 text-mint" /></div><div><Kicker>GISdo Agent</Kicker><h1 className="text-3xl font-semibold tracking-[-.03em]">今天想完成什么？</h1></div></div><p className="max-w-2xl text-sm leading-7 text-fog">把任务、项目路线图和相关资料交给我即可。我会先阅读要求，再查看数据、识别坐标系并组织完整流程；只有资料和数据都无法回答的业务选择才会询问你。</p><div className="mt-8 grid gap-3 md:grid-cols-3">{examples.map((example) => <Card key={example} className="border-line/70 bg-white/[.025] p-4 text-xs leading-6 text-[#9db4ac] shadow-none"><CircleDot className="mb-3 h-4 w-4 text-lime/80" />{example}</Card>)}</div></div>;
}

function TaskInspector() {
  const activeTaskId = useAppStore((s) => s.activeTaskId);
  const task = useAppStore((s) => activeTaskId ? s.tasks[activeTaskId] : undefined);
  const pending = useAppStore((s) => s.pendingPlan);
  const currentPlan = useAppStore((s) => s.currentPlan);
  const question = useAppStore((s) => s.pendingQuestion);
  return <aside className="hidden w-[330px] shrink-0 border-l border-line/80 bg-[#091411] xl:flex xl:flex-col"><div className="border-b border-line/70 p-5"><Kicker>Task</Kicker><h2 className="text-sm font-semibold">任务进度</h2></div><div className="min-h-0 flex-1 overflow-auto p-5">{!task && !pending && !question ? <EmptyInspector /> : <>{task && <div className="mb-5 flex items-center gap-3 rounded-xl border border-line bg-white/[.025] p-3"><StatusIcon status={task.status} /><div><div className="text-xs font-semibold">{statusLabel(task.status)}</div><div className="mt-1 text-[10px] text-[#617a71]">{task.id.slice(0, 8)}</div></div></div>}{question && <QuestionCard {...question} />}{pending ? <PlanCard plan={pending.plan} hash={pending.hash} taskId={pending.taskId} /> : currentPlan && <PlanTimeline plan={currentPlan} />}</>}</div><div className="border-t border-line/70 p-4 text-[10px] leading-5 text-[#5f776f]"><div className="flex items-center gap-2"><Activity className="h-3.5 w-3.5 text-mint" />自动查看输入数据</div><div className="mt-1 flex items-center gap-2"><ShieldCheck className="h-3.5 w-3.5 text-mint" />完成后检查处理结果</div></div></aside>;
}

function EmptyInspector() { return <div className="grid h-full place-items-center text-center"><div><Activity className="mx-auto h-8 w-8 text-[#29443b]" /><p className="mt-4 text-xs leading-6 text-[#5f776f]">任务开始后，这里会显示<br />计划、依赖和实时进度</p></div></div>; }

function PlanCard({ plan, hash, taskId }: { plan: TaskPlan; hash: string; taskId: string }) {
  const setPending = useAppStore((s) => s.setPendingPlan);
  const stepStates = useAppStore((s) => s.stepStates);
  const [busy, setBusy] = useState(false);
  async function approve() { setBusy(true); try { await api.approvePlan(taskId, hash); setPending(undefined); } finally { setBusy(false); } }
  return <Card className="border-mint/20 bg-mint/[.035] p-4 shadow-none"><div className="flex items-start justify-between"><div><div className="text-xs font-semibold text-mint">请确认处理方案</div><div className="mt-1 text-[10px] text-[#6d887f]">共 {plan.steps.length} 个处理步骤</div></div><ShieldCheck className="h-4 w-4 text-mint" /></div><div className="mt-4 space-y-2">{plan.steps.map((step, index) => <div key={step.id} className="flex gap-3 rounded-lg bg-black/15 p-2.5"><span className={cn("grid h-5 w-5 shrink-0 place-items-center rounded-md bg-white/5 text-[9px] text-fog", stepStates[step.id]?.status === "completed" && "bg-mint/15 text-mint")}>{stepStates[step.id]?.status === "completed" ? <Check className="h-3 w-3" /> : index + 1}</span><div className="min-w-0"><div className="truncate text-[11px] font-medium text-[#d7e5e0]">{toolLabel(step.tool)}</div><div className="mt-0.5 text-[9px] tracking-wider text-[#5f796f]">{step.stage ? `${step.stage} · ` : ""}{stepStateLabel(stepStates[step.id]?.status ?? "pending")}</div></div></div>)}</div>{plan.expected_outputs.length > 0 && <div className="mt-4 rounded-lg border border-line/70 p-2.5 text-[10px] leading-5 text-[#789087]">{plan.expected_outputs.map((path) => <div key={path} title={path}>{shortPath(path, 38)}</div>)}</div>}<Button className="mt-4 w-full" disabled={busy} onClick={() => void approve()}>{busy ? "正在确认…" : <><Check className="h-4 w-4" />确认并执行</>}</Button></Card>;
}

function QuestionCard({ taskId, question, options }: { taskId: string; question: string; options: string[] }) {
  const setQuestion = useAppStore((s) => s.setPendingQuestion); const [busy, setBusy] = useState(false); const [custom, setCustom] = useState("");
  async function answer(value: string) { setBusy(true); try { await api.answerTaskQuestion(taskId, value); setQuestion(undefined); } finally { setBusy(false); } }
  return <Card className="mb-4 border-lime/20 bg-lime/[.035] p-4 shadow-none"><div className="text-xs font-semibold text-lime">需要你的选择</div><p className="mt-2 text-xs leading-6 text-[#c3d1cc]">{question}</p><div className="mt-3 space-y-2">{options.map((option) => <GhostButton key={option} disabled={busy} className="h-auto w-full justify-start py-2 text-left text-xs" onClick={() => void answer(option)}>{option}</GhostButton>)}</div><div className="mt-3 flex gap-2"><input className="field h-9 text-xs" value={custom} onChange={(event) => setCustom(event.target.value)} placeholder="或输入其他答案" /><Button className="h-9 px-3" disabled={!custom.trim() || busy} onClick={() => void answer(custom)}><Send className="h-3.5 w-3.5" /></Button></div></Card>;
}

function PlanTimeline({ plan }: { plan: TaskPlan }) {
  const stepStates = useAppStore((s) => s.stepStates);
  return <div className="space-y-2">{plan.steps.map((step, index) => { const state = stepStates[step.id]?.status ?? "pending"; return <div key={step.id} className="flex gap-3 rounded-xl border border-line/70 bg-white/[.02] p-3"><span className={cn("mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-full border border-line text-[9px]", state === "running" && "animate-pulse border-lime text-lime", state === "completed" && "border-mint bg-mint/10 text-mint")}>{state === "completed" ? <Check className="h-3 w-3" /> : index + 1}</span><div className="min-w-0"><div className="truncate text-[11px] font-medium text-[#cfddd8]">{toolLabel(step.tool)}</div><div className="mt-1 text-[9px] tracking-wider text-[#5c756c]">{step.stage ? `${step.stage} · ` : ""}{stepStateLabel(state)}</div></div></div>; })}</div>;
}

function toolLabel(tool: string) {
  const name = tool.split(".").pop()?.toLowerCase() ?? tool.toLowerCase();
  if (name.includes("pairwiseclip") || name === "clip") return "按边界裁剪";
  if (name === "project" || name.includes("projectraster")) return "转换坐标系";
  if (name.includes("buffer")) return "生成缓冲区";
  if (name.includes("merge")) return "合并数据";
  if (name.includes("dissolve")) return "融合要素";
  if (name.includes("intersect")) return "计算相交范围";
  if (name.includes("copy") || name.includes("export")) return "导出结果";
  if (name.includes("inspect")) return "检查数据";
  if (name.includes("package")) return "打包项目";
  return tool;
}

function stepStateLabel(status: string) {
  return ({ pending: "等待处理", awaiting_approval: "等待确认", running: "正在处理", completed: "已完成", failed: "处理失败", cancelled: "已取消", uncertain: "需要检查" } as Record<string, string>)[status] ?? status;
}

function ProjectsView() {
  const projects = useAppStore((s) => s.projects);
  const [open, setOpen] = useState(false);
  return <Page title="项目" kicker="Authoritative sources" action={<Button onClick={() => setOpen(true)}><Plus className="h-4 w-4" />新建项目</Button>}><div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">{projects.map((project) => <Card key={project.id} className="p-5"><div className="flex items-start justify-between"><div className="grid h-10 w-10 place-items-center rounded-xl bg-mint/10"><Database className="h-5 w-5 text-mint" /></div><span className="rounded-full border border-line px-2 py-1 text-[9px] uppercase tracking-widest text-[#668077]">Local</span></div><h3 className="mt-5 font-semibold">{project.name}</h3><p className="mt-3 truncate text-xs text-fog" title={project.project_dir}>{project.project_dir || "未设置项目目录"}</p><p className="mt-1 truncate text-[10px] text-[#5b736a]" title={project.map_output_dir}>输出：{project.map_output_dir || "未设置"}</p></Card>)}</div><NewProjectDialog open={open} onOpenChange={setOpen} /></Page>;
}

function NewProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [form, setForm] = useState({ name: "", projectDir: "", mapOutputDir: "" }); const queryClient = useQueryClient();
  const mutation = useMutation({ mutationFn: () => api.createProject(form.name, form.projectDir, form.mapOutputDir), onSuccess: async () => { onOpenChange(false); await queryClient.invalidateQueries({ queryKey: ["bootstrap"] }); window.location.reload(); } });
  return <Dialog.Root open={open} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="fixed inset-0 bg-black/70 backdrop-blur-sm" /><Dialog.Content className="fixed left-1/2 top-1/2 w-[520px] -translate-x-1/2 -translate-y-1/2 rounded-2xl border border-line bg-panel p-6 shadow-panel"><Dialog.Title className="text-lg font-semibold">新建 GIS 项目</Dialog.Title><Dialog.Description className="mt-2 text-xs leading-6 text-fog">设置常用数据位置和结果保存位置，之后可以直接描述任务。</Dialog.Description><div className="mt-6 space-y-4"><Field label="项目名称" value={form.name} onChange={(name) => setForm({ ...form, name })} /><Field label="项目文件夹" value={form.projectDir} onChange={(projectDir) => setForm({ ...form, projectDir })} placeholder="E:\\GIS\\项目" /><Field label="地图输出文件夹" value={form.mapOutputDir} onChange={(mapOutputDir) => setForm({ ...form, mapOutputDir })} placeholder="E:\\GIS\\项目\\outputs" /></div><div className="mt-6 flex justify-end gap-2"><Dialog.Close asChild><GhostButton>取消</GhostButton></Dialog.Close><Button onClick={() => mutation.mutate()} disabled={!form.name.trim()}>创建</Button></div><Dialog.Close className="absolute right-4 top-4 text-fog hover:text-white"><X className="h-4 w-4" /></Dialog.Close></Dialog.Content></Dialog.Portal></Dialog.Root>;
}

function RuntimeView() {
  const query = useQuery({ queryKey: ["runtimes"], queryFn: api.discoverRuntimes });
  return <Page title="运行时" kicker="Persistent workers" action={<GhostButton onClick={() => query.refetch()}><Activity className="h-4 w-4" />重新探测</GhostButton>}><div className="grid gap-4 lg:grid-cols-2">{query.data?.map((runtime) => <Card key={`${runtime.kind}-${runtime.python_path}`} className="p-5"><div className="flex items-center justify-between"><div className="text-sm font-semibold">{runtime.kind === "pro" ? "GeoScene / ArcGIS Pro" : "ArcMap Desktop"}</div><span className={cn("rounded-full px-2.5 py-1 text-[10px]", runtime.healthy ? "bg-mint/10 text-mint" : "bg-[#44211b] text-[#ff9c86]")}>{runtime.healthy ? "可用" : "不可用"}</span></div><div className="mt-5 rounded-xl bg-black/20 p-3 font-mono text-[11px] leading-5 text-fog">{runtime.python_path}</div><div className="mt-3 text-xs text-[#657e75]">{runtime.version}</div></Card>)}</div>{query.data?.length === 0 && <Card className="p-8 text-center text-sm text-fog">尚未发现运行时，请在设置中填写 Python 路径。</Card>}</Page>;
}

function SettingsView() {
  const current = useAppStore((s) => s.settings); const setSettings = useAppStore((s) => s.setSettings);
  const [draft, setDraft] = useState<Settings | undefined>(current); const [apiKey, setApiKey] = useState(""); const [saved, setSaved] = useState(false);
  useEffect(() => setDraft(current), [current]);
  const mutation = useMutation({ mutationFn: () => api.saveSettings(draft!, apiKey), onSuccess: (settings) => { setSettings(settings); setApiKey(""); setSaved(true); setTimeout(() => setSaved(false), 1800); } });
  if (!draft) return null;
  return <Page title="设置" kicker="Local configuration" action={<Button onClick={() => mutation.mutate()}>{saved ? <><Check className="h-4 w-4" />已保存</> : "保存设置"}</Button>}><div className="grid max-w-5xl gap-5 lg:grid-cols-2"><Card className="p-6"><Kicker>GIS Environment</Kicker><h3 className="mb-5 font-semibold">GIS 软件位置</h3><div className="space-y-4"><Field label="Pro / GeoScene Python 3" value={draft.modern_python} onChange={(modern_python) => setDraft({ ...draft, modern_python })} /><Field label="ArcMap Python 2.7" value={draft.arcmap_python} onChange={(arcmap_python) => setDraft({ ...draft, arcmap_python })} /><Field label="默认输出目录" value={draft.output_root} onChange={(output_root) => setDraft({ ...draft, output_root })} /></div></Card><Card className="p-6"><Kicker>AI Connection</Kicker><h3 className="mb-5 font-semibold">智能助手</h3><div className="space-y-4"><Toggle label="启用智能规划" checked={draft.ai_enabled} onChange={(ai_enabled) => setDraft({ ...draft, ai_enabled })} /><Field label="Base URL" value={draft.ai_base_url} onChange={(ai_base_url) => setDraft({ ...draft, ai_base_url })} placeholder="https://.../v1" /><Field label="模型" value={draft.ai_model} onChange={(ai_model) => setDraft({ ...draft, ai_model })} /><Field label="API Key（留空即保留原凭据）" value={apiKey} onChange={setApiKey} type="password" /><label className="block"><span className="mb-2 block text-xs text-fog">执行前确认</span><select value={draft.autonomy_mode} onChange={(event) => setDraft({ ...draft, autonomy_mode: event.target.value as Settings["autonomy_mode"] })} className="field"><option value="confirm_writes">执行前确认一次</option><option value="autonomous">直接执行</option><option value="confirm_every_step">每一步都确认</option></select></label></div><div className="mt-5 rounded-xl border border-line bg-black/15 p-3 text-[10px] leading-5 text-[#678078]">API Key 由 Windows 安全保存，不会写入项目文件。</div></Card></div></Page>;
}

function Page({ title, kicker, action, children }: { title: string; kicker: string; action?: React.ReactNode; children: React.ReactNode }) { return <main className="min-h-0 flex-1 overflow-auto bg-[radial-gradient(circle_at_65%_0%,rgba(35,102,78,.12),transparent_35%)] p-8"><div className="mx-auto max-w-7xl"><div className="mb-8 flex items-end justify-between"><div><Kicker>{kicker}</Kicker><h1 className="text-3xl font-semibold tracking-[-.03em]">{title}</h1></div>{action}</div>{children}</div></main>; }
function Field({ label, value, onChange, placeholder, type = "text" }: { label: string; value: string; onChange: (value: string) => void; placeholder?: string; type?: string }) { return <label className="block"><span className="mb-2 block text-xs text-fog">{label}</span><input className="field" type={type} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} /></label>; }
function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) { return <label className="flex items-center justify-between rounded-xl border border-line bg-black/10 px-3 py-3 text-xs text-[#c9d9d3]"><span>{label}</span><button type="button" role="switch" aria-checked={checked} onClick={() => onChange(!checked)} className={cn("relative h-6 w-11 rounded-full bg-[#243c34] transition", checked && "bg-mint")}><span className={cn("absolute left-1 top-1 h-4 w-4 rounded-full bg-white transition", checked && "translate-x-5 bg-ink")} /></button></label>; }
function statusLabel(status: string) { return ({ queued: "等待中", planning: "正在查看数据并制定方案", needs_input: "等待补充信息", awaiting_approval: "等待确认", running: "正在处理", cancelling: "正在停止", completed: "任务完成", failed: "任务失败", cancelled: "已取消", uncertain: "结果需要检查" } as Record<string, string>)[status] ?? status; }
