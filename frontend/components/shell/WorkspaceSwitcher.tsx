"use client";

import { Building2, Check, ChevronsUpDown, Search, Settings2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { BASE_PATH, SIGN_IN_URL, withBase } from "@/lib/paths";
import { lastPage, noteRecent, recentWorkspaces, rememberPage, setWorkspaceScope } from "@/lib/workspace-scope";
import {
  activeWorkspace,
  arrange,
  isSwitcherShortcut,
  listWorkspaces,
  roleLabel,
  selectWorkspace,
  type Workspace,
} from "@/lib/workspaces";

// The hosted company switcher: name, role and a list of the companies you belong to, with search, recent
// workspaces first and Alt+W to open it. Only a hosted deployment has one (it has its own sign-in site); the
// desktop app and self-hosting never show it. Switching signs you in nowhere new: the account service moves your
// selection, and the page reloads into that company's own computer, conversations and drafts.
export function WorkspaceSwitcher({ onNavigate }: { onNavigate?: () => void }) {
  const hosted = SIGN_IN_URL !== "";
  const [workspaces, setWorkspaces] = useState<Workspace[] | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const search = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!hosted) return;
    let live = true;
    listWorkspaces()
      .then((list) => {
        if (!live) return;
        setWorkspaces(list);
        const current = activeWorkspace(list);
        if (current) {
          setWorkspaceScope(current.id);
          noteRecent(current.id);
        }
      })
      .catch(() => {}); // not a hosted deployment after all, or signed out: stay hidden
    return () => {
      live = false;
    };
  }, [hosted]);

  // Remember where this workspace was left, so switching back reopens the same conversation.
  useEffect(() => {
    const current = workspaces && activeWorkspace(workspaces);
    if (current) rememberPage(current.id, window.location.pathname + window.location.search, BASE_PATH || "/");
  });

  // Opening (button or Alt+W) starts with an empty search and no old error.
  const toggle = useCallback(() => {
    setOpen((value) => {
      if (!value) {
        setQuery("");
        setCursor(0);
        setError(null);
      }
      return !value;
    });
  }, []);

  useEffect(() => {
    if (!hosted) return;
    const onKey = (event: KeyboardEvent) => {
      if (isSwitcherShortcut(event)) {
        event.preventDefault();
        toggle();
      } else if (event.key === "Escape") {
        setOpen(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hosted, toggle]);

  useEffect(() => {
    if (!open) return;
    search.current?.focus();
    const away = (event: MouseEvent) => {
      if (box.current && !box.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);

  const shown = workspaces ? arrange(workspaces, recentWorkspaces(), query) : [];

  const choose = useCallback(
    async (target: Workspace) => {
      if (target.active) {
        setOpen(false);
        return;
      }
      setBusy(true);
      setError(null);
      try {
        await selectWorkspace(target.id);
        setWorkspaceScope(target.id);
        noteRecent(target.id);
        onNavigate?.();
        // The other company has its own computer, conversations and agents: reopen where you left it, else its start.
        window.location.assign(lastPage(target.id, BASE_PATH || "/") ?? withBase("/"));
      } catch {
        setError("Could not switch. Try again.");
        setBusy(false);
      }
    },
    [onNavigate],
  );

  if (!hosted || !workspaces || workspaces.length === 0) return null;
  const current = activeWorkspace(workspaces);

  return (
    <div ref={box} className="relative px-3 pt-3">
      <button
        type="button"
        onClick={toggle}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-keyshortcuts="Alt+W"
        title="Switch workspace (Alt+W)"
        className="flex w-full cursor-pointer items-center gap-2 rounded-field border border-line px-2.5 py-1.5 text-left text-sm hover:bg-hover"
      >
        <Building2 size={15} aria-hidden="true" className="shrink-0 text-muted" />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-medium">{current?.name ?? "Workspace"}</span>
          {current && <span className="block text-[11px] text-muted">{roleLabel(current.role)}</span>}
        </span>
        <ChevronsUpDown size={14} aria-hidden="true" className="shrink-0 text-muted" />
      </button>
      {open && (
        <div className="absolute inset-x-3 top-full z-30 mt-1 rounded-field border border-line bg-surface p-1.5 shadow-lg">
          {workspaces.length > 4 && (
            <label className="mb-1 flex items-center gap-1.5 rounded-field border border-line px-2 py-1 text-sm">
              <Search size={13} aria-hidden="true" className="text-muted" />
              <input
                ref={search}
                value={query}
                onChange={(event) => {
                  setQuery(event.target.value);
                  setCursor(0);
                }}
                onKeyDown={(event) => {
                  if (event.key === "ArrowDown") setCursor((value) => Math.min(value + 1, shown.length - 1));
                  else if (event.key === "ArrowUp") setCursor((value) => Math.max(value - 1, 0));
                  else if (event.key === "Enter" && shown[cursor]) void choose(shown[cursor]);
                }}
                placeholder="Find a workspace"
                aria-label="Find a workspace"
                className="min-w-0 flex-1 bg-transparent outline-none"
              />
            </label>
          )}
          <ul role="listbox" aria-label="Workspaces" className="max-h-64 space-y-0.5 overflow-y-auto">
            {shown.map((w, index) => (
              <li key={w.id} role="option" aria-selected={w.active}>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void choose(w)}
                  className={cn(
                    "flex w-full cursor-pointer items-center gap-2 rounded-field px-2 py-1.5 text-left text-sm hover:bg-hover",
                    index === cursor && "bg-hover",
                  )}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">{w.name}</span>
                    <span className="block text-[11px] text-muted">{roleLabel(w.role)}</span>
                  </span>
                  {w.active && <Check size={14} aria-hidden="true" />}
                </button>
              </li>
            ))}
            {shown.length === 0 && <li className="px-2 py-2 text-xs text-muted">No workspace matches.</li>}
          </ul>
          {error && (
            <p role="alert" className="px-2 py-1 text-xs text-danger">
              {error}
            </p>
          )}
          <a
            href="/account/workspaces"
            className="mt-1 flex items-center gap-2 rounded-field border-t border-line px-2 pb-1 pt-2 text-xs text-muted hover:text-ink"
          >
            <Settings2 size={13} aria-hidden="true" />
            Manage workspaces and people
          </a>
        </div>
      )}
    </div>
  );
}
