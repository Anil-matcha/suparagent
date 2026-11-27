"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ArchiveRestore, Download, ShieldCheck } from "lucide-react";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { Checkbox } from "@/components/ui/Checkbox";
import { Input } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1";

type BackupStatus = {
  database_exists: boolean;
  total_files: number;
  total_bytes: number;
  parts: Record<string, { files: number; bytes: number }>;
  exclusions: string[];
  staged_restore_supported: boolean;
};

type BackupPreviewSummary = {
  created_at: string | null;
  encrypted: boolean;
  database_revision: string | null;
  older_than_code: boolean;
  compatible: boolean;
  workspaces_included: boolean;
  counts: { agents: number; threads: number; messages: number; rooms: number; files: number; bytes: number };
  exclusions: string[];
  warnings: string[];
};

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

export function WorkspaceBackupSettings() {
  const toast = useToast();
  const [status, setStatus] = useState<BackupStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [exportPass, setExportPass] = useState("");
  const [confirmPass, setConfirmPass] = useState("");
  const [skipWorkspaces, setSkipWorkspaces] = useState(false);
  const [exportBusy, setExportBusy] = useState(false);

  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restorePass, setRestorePass] = useState("");
  const [preview, setPreview] = useState<{ id: string; summary: BackupPreviewSummary } | null>(null);
  const [confirmText, setConfirmText] = useState("");
  const [restoreBusy, setRestoreBusy] = useState(false);
  const [stagedReady, setStagedReady] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const loadStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/workspace-backup/status`, { credentials: "include" });
      if (res.ok) {
        setStatus((await res.json()) as BackupStatus);
      }
    } catch {
      // keep silent when offline
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  const startExport = async (event: React.FormEvent) => {
    event.preventDefault();
    setError(null);
    if (exportPass.length < 12) {
      setError("Backup password must be at least 12 characters.");
      return;
    }
    if (exportPass !== confirmPass) {
      setError("Backup passwords do not match.");
      return;
    }
    setExportBusy(true);
    try {
      const start = await fetch(`${API_BASE}/workspace-backup/export`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ passphrase: exportPass, skip_workspaces: skipWorkspaces }),
      });
      const started = await start.json();
      if (!start.ok) throw new Error(started?.detail?.message ?? "Could not start backup.");
      const jobId = started.job_id as string;

      for (;;) {
        await new Promise((r) => setTimeout(r, 400));
        const poll = await fetch(`${API_BASE}/workspace-backup/export/${encodeURIComponent(jobId)}`, { credentials: "include" });
        const job = await poll.json();
        if (!poll.ok) throw new Error(job?.detail?.message ?? "Backup failed.");
        if (job.status === "error") throw new Error(job.error ?? "Backup failed.");
        if (job.status === "done") {
          const dl = await fetch(`${API_BASE}/workspace-backup/download/${encodeURIComponent(jobId)}`, { credentials: "include" });
          if (!dl.ok) throw new Error("Could not download generated archive.");
          const url = URL.createObjectURL(await dl.blob());
          const a = document.createElement("a");
          a.href = url;
          a.download = job.archive_name || `suparagent-backup-${new Date().toISOString().slice(0, 10)}.tar.gz.enc`;
          a.click();
          setTimeout(() => URL.revokeObjectURL(url), 0);
          setExportPass("");
          setConfirmPass("");
          toast("Encrypted workspace backup downloaded.");
          break;
        }
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create backup.");
    } finally {
      setExportBusy(false);
    }
  };

  const inspectBackup = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!restoreFile) return;
    setError(null);
    setRestoreBusy(true);
    setPreview(null);
    try {
      const form = new FormData();
      form.append("file", restoreFile);
      form.append("passphrase", restorePass);
      const res = await fetch(`${API_BASE}/workspace-backup/preview`, {
        method: "POST",
        credentials: "include",
        body: form,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail?.message ?? "Could not inspect backup archive.");
      setPreview({ id: data.preview_id, summary: data.summary });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not read backup archive.");
    } finally {
      setRestoreBusy(false);
    }
  };

  const confirmRestore = async () => {
    if (!preview) return;
    setError(null);
    setRestoreBusy(true);
    try {
      const res = await fetch(`${API_BASE}/workspace-backup/restore`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          preview_id: preview.id,
          passphrase: restorePass,
          confirm: confirmText.trim(),
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail?.message ?? "Could not stage restore.");
      setPreview(null);
      setRestoreFile(null);
      setRestorePass("");
      setConfirmText("");
      setStagedReady(true);
      toast("Backup validated and staged for restore on restart.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not stage restore.");
    } finally {
      setRestoreBusy(false);
    }
  };

  const restartToApply = async () => {
    const bridge = typeof window === "undefined" ? undefined : window.suparagent?.environments;
    if (bridge) {
      await bridge({ action: "restartApp" });
    }
  };

  return (
    <section className="mt-6 space-y-4 rounded-lg border border-line p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="flex items-center gap-1.5 font-semibold">
            <ShieldCheck size={16} />
            <span>Encrypted workspace backup &amp; restore</span>
          </h3>
          <p className="mt-1 text-xs text-muted">
            Export an AES-256-GCM password-protected archive of your database, attachments, agent notes, and workspace files, or restore from a previous archive.
          </p>
        </div>
        {status && (
          <span className="text-xs text-muted tabular-nums">
            {status.total_files.toLocaleString()} files · {formatBytes(status.total_bytes)}
          </span>
        )}
      </div>

      {error && <Banner tone="danger">{error}</Banner>}
      {stagedReady && (
        <Banner tone="info">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span>Restore verified and staged. Restart Suparagent to apply it safely (a copy of current data is kept).</span>
            {typeof window !== "undefined" && window.suparagent?.environments && (
              <Button size="sm" onClick={() => void restartToApply()}>
                Restart now
              </Button>
            )}
          </div>
        </Banner>
      )}

      <form onSubmit={(e) => void startExport(e)} className="space-y-3 rounded-md border border-line p-3.5">
        <div className="text-sm font-medium text-ink">Export encrypted backup</div>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
          <Input
            type="password"
            value={exportPass}
            onChange={(e) => setExportPass(e.target.value)}
            placeholder="Backup passphrase (12+ chars)"
            disabled={exportBusy}
          />
          <Input
            type="password"
            value={confirmPass}
            onChange={(e) => setConfirmPass(e.target.value)}
            placeholder="Confirm passphrase"
            disabled={exportBusy}
          />
        </div>
        <Checkbox
          checked={skipWorkspaces}
          onChange={(e) => setSkipWorkspaces(e.target.checked)}
          disabled={exportBusy}
          label="Skip workspace files (back up database, attachments, and agent notes only)"
        />
        <div className="flex items-center justify-between gap-2">
          <p className="text-[11px] text-muted">Encryption key (OS keystore) and CLI logins remain local and are never inside archives.</p>
          <Button type="submit" size="sm" loading={exportBusy} disabled={!status?.database_exists}>
            <Download size={14} className="mr-1" />
            Export backup
          </Button>
        </div>
      </form>

      <div className="space-y-3 rounded-md border border-line p-3.5">
        <div className="text-sm font-medium text-ink">Inspect &amp; restore backup</div>
        <form onSubmit={(e) => void inspectBackup(e)} className="space-y-2">
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".enc,.gz,.tar"
              onChange={(e) => {
                setRestoreFile(e.target.files?.[0] ?? null);
                setPreview(null);
              }}
              className="text-xs text-muted file:mr-2 file:rounded-md file:border file:border-line file:bg-raised file:px-2.5 file:py-1 file:text-xs file:text-ink"
            />
            <Input
              type="password"
              value={restorePass}
              onChange={(e) => setRestorePass(e.target.value)}
              placeholder="Archive passphrase"
              className="max-w-xs"
              disabled={restoreBusy}
            />
            <Button type="submit" variant="secondary" size="sm" loading={restoreBusy} disabled={!restoreFile}>
              Preview archive
            </Button>
          </div>
        </form>

        {preview && (
          <div className="space-y-2 rounded-md border border-line bg-inset/40 p-3 text-xs">
            <div className="font-medium text-ink">Archive manifest preview</div>
            <div className="grid grid-cols-2 gap-x-4 gap-y-1 sm:grid-cols-3 text-muted">
              <span>Agents: <strong className="text-ink">{preview.summary.counts.agents}</strong></span>
              <span>Threads: <strong className="text-ink">{preview.summary.counts.threads}</strong></span>
              <span>Messages: <strong className="text-ink">{preview.summary.counts.messages}</strong></span>
              <span>Files: <strong className="text-ink">{preview.summary.counts.files}</strong></span>
              <span>Size: <strong className="text-ink">{formatBytes(preview.summary.counts.bytes)}</strong></span>
              <span>Encrypted: <strong className="text-ink">{preview.summary.encrypted ? "Yes" : "No"}</strong></span>
            </div>
            {preview.summary.warnings.length > 0 && (
              <ul className="list-disc pl-4 text-danger space-y-0.5">
                {preview.summary.warnings.map((w, idx) => <li key={idx}>{w}</li>)}
              </ul>
            )}
            {preview.summary.compatible && (
              <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-line/60">
                <Input
                  value={confirmText}
                  onChange={(e) => setConfirmText(e.target.value)}
                  placeholder='Type RESTORE to stage'
                  className="max-w-[200px]"
                  disabled={restoreBusy}
                />
                <Button
                  type="button"
                  size="sm"
                  loading={restoreBusy}
                  disabled={confirmText.trim().toUpperCase() !== "RESTORE"}
                  onClick={() => void confirmRestore()}
                >
                  <ArchiveRestore size={14} className="mr-1" />
                  Stage restore
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
