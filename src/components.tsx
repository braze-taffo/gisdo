import { memo } from "react";
import type { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, CircleAlert, LoaderCircle } from "lucide-react";
import { cn } from "./lib";
import type { TaskStatus, UiMessage, WorkerState } from "./types";

export function Button({ className, children, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={cn("inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-transparent px-4 text-sm font-semibold transition duration-200 focus:outline-none focus:ring-2 focus:ring-mint/30 disabled:cursor-not-allowed disabled:opacity-45", "bg-mint text-ink hover:bg-[#9af7d2] active:translate-y-px", className)} {...props}>{children}</button>;
}

export function GhostButton({ className, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <Button className={cn("border-line bg-white/[.035] text-[#dbe9e4] hover:border-[#315249] hover:bg-white/[.065]", className)} {...props} />;
}

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("rounded-2xl border border-line/90 bg-panel/85 shadow-panel backdrop-blur-xl", className)} {...props} />;
}

export function Kicker({ children }: { children: ReactNode }) {
  return <div className="mb-2 text-[10px] font-bold uppercase tracking-[.2em] text-mint/70">{children}</div>;
}

export function WorkerChip({ label, status }: { label: string; status: WorkerState }) {
  const ready = status === "ready";
  const busy = status === "busy" || status === "starting" || status === "recycling";
  return <div className="flex items-center gap-2 rounded-full border border-line bg-black/15 px-3 py-1.5 text-xs text-fog">
    <span className={cn("h-1.5 w-1.5 rounded-full", ready && "bg-mint shadow-[0_0_8px_#75f0c1]", busy && "animate-pulse bg-lime", !ready && !busy && "bg-[#566c65]")} />
    <span className="font-medium text-[#d2e0db]">{label}</span><span>{status}</span>
  </div>;
}

export function StatusIcon({ status }: { status: TaskStatus }) {
  if (status === "completed") return <Check className="h-4 w-4 text-mint" />;
  if (["failed", "uncertain", "cancelled"].includes(status)) return <CircleAlert className="h-4 w-4 text-[#ff9b82]" />;
  return <LoaderCircle className="h-4 w-4 animate-spin text-lime" />;
}

/** memo：流式期间每 token 触发的是目标消息的重建，历史消息不重跑 Markdown 解析。 */
export const MarkdownMessage = memo(function MarkdownMessage({ message }: { message: UiMessage }) {
  if (message.role === "user") return <div className="ml-auto max-w-[78%] animate-fade-up rounded-2xl rounded-br-md bg-[#18372f] px-4 py-3 text-[15px] leading-7 text-[#edf8f4]">{message.content}</div>;
  return <div className="markdown-body max-w-[880px] animate-fade-up text-[15px] leading-7 text-[#dbe7e3]">
    {/* The content has exactly one presentation path: rendered Markdown. */}
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.content}</ReactMarkdown>
    {message.streaming && <span aria-label="streaming" className="ml-1 inline-block h-4 w-1 animate-pulseSoft rounded bg-mint align-middle" />}
  </div>;
});

