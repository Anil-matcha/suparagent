"use client";

import { useCallback, useEffect, useState } from "react";
import { Laptop, Plus, Server, Trash2 } from "lucide-react";
import { Banner } from "@/components/ui/Banner";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useToast } from "@/components/ui/Toast";

export type LocalProfileEntry = {
  id: string;
  name: string;
  color: string | null;
  dataDir: string;
  isDefault: boolean;
  createdAt: number;
};

export type RemoteEnvironmentEntry = {
  id: string;
  kind: "remote";
  name: string;
  origin: string;
  environmentId: string;
  paired: boolean;
  createdAt: number;
};

export type EnvironmentsState = {
  activeLocalProfileId: string;
  activeRemoteId: string | null;
  localProfiles: LocalProfileEntry[];
  remotes: RemoteEnvironmentEntry[];
};

export type EnvironmentsBridge = (payload: {
  action: "state" | "createLocal" | "renameLocal" | "removeLocal" | "switchLocal" | "connectRemote" | "switchRemote" | "forgetRemote" | "restartApp";
  id?: string;
  name?: string;
  color?: string | null;
  input?: string;
  code?: string;
}) => Promise<unknown>;

export function ConnectedWorkspacesSettings() {
  const toast = useToast();
  const bridge = typeof window === "undefined" ? undefined : window.suparagent?.environments;
  const [state, setState] = useState<EnvironmentsState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [newProfileName, setNewProfileName] = useState("");
  const [remoteUrl, setRemoteUrl] = useState("");
  const [remoteCode, setRemoteCode] = useState("");
  const [remoteName, setRemoteName] = useState("");
  const [showRemoteForm, setShowRemoteForm] = useState(false);

  const load = useCallback(async () => {
    if (!bridge) return;
    try {
      const result = (await bridge({ action: "state" })) as EnvironmentsState;
      setState(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not read workspace environments.");
    }
  }, [bridge]);

  useEffect(() => {
    void load();
  }, [load]);

  if (!bridge) return null;

  const createProfile = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!newProfileName.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await bridge({ action: "createLocal", name: newProfileName.trim() });
      setNewProfileName("");
      await load();
      toast("Local workspace profile created.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create profile.");
    } finally {
      setBusy(false);
    }
  };

  const switchProfile = async (id: string) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await bridge({ action: "switchLocal", id });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not switch workspace profile.");
    } finally {
      setBusy(false);
    }
  };

  const removeProfile = async (id: string) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await bridge({ action: "removeLocal", id });
      await load();
      toast("Local workspace profile removed.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove workspace profile.");
    } finally {
      setBusy(false);
    }
  };

  const connectRemote = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!remoteUrl.trim() || busy) return;
    setBusy(true);
    setError(null);
    try {
      await bridge({
        action: "connectRemote",
        input: remoteUrl.trim(),
        code: remoteCode.trim() || undefined,
        name: remoteName.trim() || undefined,
      });
      setRemoteUrl("");
      setRemoteCode("");
      setRemoteName("");
      setShowRemoteForm(false);
      await load();
      toast("Remote server connected and verified.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not connect to server.");
    } finally {
      setBusy(false);
    }
  };

  const switchRemote = async (id: string) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await bridge({ action: "switchRemote", id });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not open remote server.");
    } finally {
      setBusy(false);
    }
  };

  const forgetRemote = async (id: string) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await bridge({ action: "forgetRemote", id });
      await load();
      toast("Removed saved server.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove server.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="mt-6 space-y-4 rounded-lg border border-line p-5">
      <div>
        <h3 className="font-semibold">Connected workspaces &amp; profiles</h3>
        <p className="mt-1 text-xs text-muted">
          Keep separate local data profiles on this computer or connect to self-hosted Suparagent servers over LAN, Tailscale, or HTTPS.
        </p>
      </div>

      {error && <Banner tone="danger">{error}</Banner>}

      <div className="space-y-2">
        <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
          <Laptop size={13} />
          <span>Local profiles on this computer</span>
        </h4>
        <div className="divide-y divide-line rounded-md border border-line">
          {(state?.localProfiles ?? []).map((prof) => {
            const isActive = state?.activeLocalProfileId === prof.id && !state.activeRemoteId;
            return (
              <div key={prof.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-ink">{prof.name}</span>
                    {isActive && (
                      <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[11px] font-medium text-accent">
                        Active
                      </span>
                    )}
                  </div>
                  <p className="truncate text-xs text-muted">{prof.dataDir}</p>
                </div>
                <div className="flex items-center gap-2">
                  {!isActive && (
                    <Button variant="secondary" size="sm" disabled={busy} onClick={() => void switchProfile(prof.id)}>
                      Switch &amp; restart
                    </Button>
                  )}
                  {!prof.isDefault && !isActive && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={busy}
                      onClick={() => void removeProfile(prof.id)}
                      aria-label={`Remove ${prof.name}`}
                    >
                      <Trash2 size={14} />
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <form onSubmit={(e) => void createProfile(e)} className="flex items-center gap-2 pt-1">
          <Input
            value={newProfileName}
            onChange={(e) => setNewProfileName(e.target.value)}
            placeholder="New local workspace name..."
            className="max-w-xs"
            disabled={busy}
          />
          <Button type="submit" variant="secondary" size="sm" disabled={busy || !newProfileName.trim()}>
            <Plus size={14} className="mr-1" />
            Create profile
          </Button>
        </form>
      </div>

      <div className="space-y-2 border-t border-line pt-4">
        <div className="flex items-center justify-between">
          <h4 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted">
            <Server size={13} />
            <span>Remote servers</span>
          </h4>
          <Button variant="secondary" size="sm" onClick={() => setShowRemoteForm((v) => !v)}>
            {showRemoteForm ? "Cancel" : "Connect server"}
          </Button>
        </div>

        {(state?.remotes ?? []).length === 0 ? (
          <p className="text-xs text-muted">No remote Suparagent servers connected yet.</p>
        ) : (
          <div className="divide-y divide-line rounded-md border border-line">
            {(state?.remotes ?? []).map((remote) => (
              <div key={remote.id} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium text-ink">{remote.name}</span>
                    {state?.activeRemoteId === remote.id && (
                      <span className="rounded-full bg-accent/15 px-2 py-0.5 text-[11px] font-medium text-accent">
                        Open
                      </span>
                    )}
                  </div>
                  <p className="truncate text-xs text-muted">
                    {remote.origin} · id {remote.environmentId.slice(0, 8)}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Button variant="secondary" size="sm" disabled={busy} onClick={() => void switchRemote(remote.id)}>
                    Open
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={busy}
                    onClick={() => void forgetRemote(remote.id)}
                    aria-label={`Forget ${remote.name}`}
                  >
                    <Trash2 size={14} />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}

        {showRemoteForm && (
          <form onSubmit={(e) => void connectRemote(e)} className="space-y-2 rounded-md border border-line bg-inset/40 p-3">
            <Input
              value={remoteUrl}
              onChange={(e) => setRemoteUrl(e.target.value)}
              placeholder="Server URL or pairing link (e.g. https://my-server.ts.net/pair#code=...)"
              disabled={busy}
            />
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <Input
                value={remoteCode}
                onChange={(e) => setRemoteCode(e.target.value)}
                placeholder="Pairing code (optional if inside link)"
                disabled={busy}
              />
              <Input
                value={remoteName}
                onChange={(e) => setRemoteName(e.target.value)}
                placeholder="Display name (optional)"
                disabled={busy}
              />
            </div>
            <div className="flex justify-end">
              <Button type="submit" size="sm" loading={busy} disabled={!remoteUrl.trim()}>
                Verify &amp; connect
              </Button>
            </div>
          </form>
        )}
      </div>
    </section>
  );
}
