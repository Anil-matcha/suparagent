"use client";

import { BarChart3, CalendarDays, Folder, Link2, Menu as MenuIcon, MoreHorizontal, Search, ShieldCheck, Undo2 } from "lucide-react";
import type { ReactNode } from "react";
import type { Agent } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useT } from "@/lib/use-i18n";
import { Avatar } from "@/components/ui/Avatar";
import { IconButton } from "@/components/ui/IconButton";
import { Select, type SelectOption } from "@/components/ui/Select";

const CHIP =
  "inline-flex h-8 max-w-20 shrink-0 cursor-pointer items-center sm:max-w-72 rounded-full border border-line bg-raised px-3 text-[13px] text-ink transition-colors duration-(--dur-fast) hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50 touch:h-10";

// The strip above a conversation: what it is (agent and title), how it runs (engine and model,
// approval mode, computer), and a few quiet icon buttons. Everything else lives in the sidebar
// or opens in a panel. On a phone the secondary buttons fold into one menu.
export function ChatHeader({
  title,
  agent,
  onOpenSidebar,
  agentChoice,
  engine,
  computer,
  approvalMode,
  onApprovals,
  onFiles,
  onUsage,
  onToday,
  onFind,
  onCopyLink,
  onUndo,
  usageHint,
  trailing,
  status,
}: {
  title: string;
  // The conversation's agent, for the avatar; null for a plain chat.
  agent: Pick<Agent, "id" | "name"> | null;
  // Present when the sidebar is a drawer: the button that opens it.
  onOpenSidebar?: () => void;
  // Only on a new chat: pick the agent it starts with.
  agentChoice?: { agents: Agent[]; value: number | null; onChange: (id: number | null) => void };
  engine?: { label: string; onClick: () => void; disabled: boolean };
  computer?: { text: string; hint: string; onClick: () => void };
  approvalMode?: { label: string; hint: string };
  onApprovals?: () => void;
  onFiles?: () => void;
  onUsage?: () => void;
  onToday?: () => void;
  onFind?: () => void;
  // Copies a link that opens this chat or room again.
  onCopyLink?: () => void;
  // Opens the "undo this task's file changes" confirmation; absent while a task runs.
  onUndo?: () => void;
  usageHint?: string;
  // The notification bell and anything else that sits at the far right.
  trailing?: ReactNode;
  // Short text such as "Reconnecting…" or the engine's version.
  status?: { text: string; warn?: boolean } | null;
}) {
  const t = useT();
  type Action = { key: string; label: string; icon: ReactNode; run: () => void; hint?: string };
  const secondary: Action[] = [];
  if (onFind) secondary.push({ key: "find", label: "Find in chat (Ctrl+F)", icon: <Search size={16} />, run: onFind, hint: "Find in chat (Ctrl+F)" });
  if (onApprovals) secondary.push({ key: "approvals", label: t("header.approvals"), icon: <ShieldCheck size={16} />, run: onApprovals });
  if (onFiles) secondary.push({ key: "files", label: t("header.files"), icon: <Folder size={16} />, run: onFiles });
  if (onUsage) secondary.push({ key: "usage", label: t("header.usage"), icon: <BarChart3 size={16} />, run: onUsage, hint: usageHint });
  if (onToday) secondary.push({ key: "today", label: t("header.today"), icon: <CalendarDays size={16} />, run: onToday, hint: t("header.todayHint") });

  if (onCopyLink) secondary.push({ key: "copyLink", label: t("header.copyLink"), icon: <Link2 size={16} />, run: onCopyLink });
  if (onUndo) secondary.push({ key: "undo", label: t("header.undo"), icon: <Undo2 size={16} />, run: onUndo });

  return (
    <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line bg-app px-3 pt-[env(safe-area-inset-top)] sm:px-4">
      {onOpenSidebar && (
        <IconButton label={t("shell.openSidebar")} onClick={onOpenSidebar} className="-ml-1">
          <MenuIcon size={20} />
        </IconButton>
      )}

      <div className="flex min-w-0 flex-1 items-center gap-2.5">
        {agentChoice ? (
          <>
            <Avatar agentId={agentChoice.value} name={agentChoice.agents.find((a) => a.id === agentChoice.value)?.name} size="md" />
            <Select
              value={agentChoice.value ?? ""}
              onChange={(e) => agentChoice.onChange(e.target.value === "" ? null : Number(e.target.value))}
              aria-label={t("header.agentForChat")}
              className="h-8 min-w-0 max-w-48 cursor-pointer truncate rounded-field border border-line bg-raised px-2 text-sm font-medium touch:h-10"
            >
              <option value="">{t("header.noAgent")}</option>
              {agentChoice.agents.map((a) => (
                <option key={a.id} value={a.id}>{a.name}</option>
              ))}
            </Select>
          </>
        ) : (
          <>
            <Avatar agentId={agent?.id ?? null} name={agent?.name} size="md" />
            <h1 className="min-w-0 truncate text-[15px] font-semibold">
              {title}
              {agent ? <span className="ml-2 hidden text-sm font-normal text-muted sm:inline">{agent.name}</span> : null}
            </h1>
          </>
        )}
      </div>

      {status ? <span className={cn("shrink-0 text-xs", status.warn ? "text-warning" : "hidden text-muted xl:inline")}>{status.text}</span> : null}

      {computer && (
        <button type="button" onClick={computer.onClick} title={computer.hint} className={cn(CHIP, "hidden border-accent/40 text-accent lg:inline-flex")}>
          {computer.text}
        </button>
      )}
      {approvalMode && (
        <span title={approvalMode.hint} className="hidden h-8 shrink-0 items-center rounded-full bg-inset px-3 text-[13px] text-muted xl:inline-flex">
          {approvalMode.label}
        </span>
      )}
      {engine && (
        <button type="button" aria-label={t("header.engine")} disabled={engine.disabled} onClick={engine.onClick} title={engine.label} className={CHIP}>
          <span className="truncate">{engine.label}</span>
        </button>
      )}

      {(secondary.length > 0 || computer) && (
        <Select
          aria-label={t("header.more")}
          value="__none__"
          onChange={(event) => {
            if (event.target.value === "computer") {
              computer?.onClick();
              return;
            }
            secondary.find((item) => item.key === event.target.value)?.run();
          }}
          containerClassName="w-auto shrink-0"
          triggerClassName="size-9 touch:size-10 justify-center border-0 bg-transparent px-0 text-muted hover:border-0 hover:bg-hover hover:text-ink focus:ring-0"
          menuClassName="min-w-52"
          menuAlign="end"
          triggerLabel={
            <span className="flex items-center justify-center">
              <MoreHorizontal size={18} aria-hidden="true" />
            </span>
          }
          chevronClassName="hidden"
          options={[
            ...(computer
              ? ([{ value: "computer", label: computer.text, className: "lg:hidden" }] satisfies SelectOption[])
              : []),
            ...secondary.map(
              (item): SelectOption => ({
                value: item.key,
                label: (
                  <span className="inline-flex items-center gap-2">
                    <span className="shrink-0 text-muted" aria-hidden="true">{item.icon}</span>
                    <span>{item.label}</span>
                  </span>
                ),
              }),
            ),
          ]}
        />
      )}
      {trailing}
    </header>
  );
}
