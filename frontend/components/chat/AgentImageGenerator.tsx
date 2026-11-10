"use client";

import { useCallback, useEffect, useState } from "react";
import { api, type Agent } from "@/lib/api";
import { IMAGE_SIZES, PROMPT_MAX, pickProvider, promptProblem, type GeneratedImage, type ImageProvider } from "@/lib/image-gen";
import { Button } from "@/components/ui/Button";
import { useToast } from "@/components/ui/Toast";
import { AgentImage } from "./AgentImage";

const FIELD = "w-full rounded-field border border-line bg-raised px-2 py-1.5 text-sm text-ink";

// Make a picture for one agent from a description, then keep it or discard it.
export function AgentImageGenerator({ agent, onChanged }: { agent: Agent; onChanged: () => void }) {
  const toast = useToast();
  const [providers, setProviders] = useState<ImageProvider[] | null>(null);
  const [provider, setProvider] = useState("");
  const [size, setSize] = useState<string>(IMAGE_SIZES[0].value);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<{ image: GeneratedImage; url: string } | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    api.listImageProviders().then(
      (rows) => {
        if (!live) return;
        setProviders(rows);
        setProvider(pickProvider(rows)?.id ?? "");
      },
      (e) => { if (live) { setProviders([]); setError(e instanceof Error ? e.message : "Could not load image providers."); } },
    );
    return () => { live = false; };
  }, []);

  useEffect(() => () => { if (result) URL.revokeObjectURL(result.url); }, [result]);

  const chosen = providers?.find((p) => p.id === provider) ?? null;
  const problem = promptProblem(prompt);

  const generate = useCallback(async () => {
    if (problem || !chosen?.available) return;
    setBusy(true);
    setError(null);
    try {
      const image = await api.generateImage({ prompt, provider, size });
      const url = await api.generatedImageUrl(image.id);
      setResult({ image, url });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not make the image.");
    } finally {
      setBusy(false);
    }
  }, [problem, chosen, prompt, provider, size]);

  const use = async () => {
    if (!result) return;
    setBusy(true);
    setError(null);
    try {
      await api.setAgentAvatar(agent.id, result.image.id);
      setResult(null);
      setTick((n) => n + 1);
      onChanged();
      toast("Picture saved", "success");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save the picture.");
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.clearAgentAvatar(agent.id);
      setTick((n) => n + 1);
      onChanged();
      toast("Picture removed", "success");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not remove the picture.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3">
        <AgentImage agentId={agent.id} name={agent.name} size="lg" tick={tick} />
        <p className="min-w-0 flex-1 text-sm text-muted">Describe a picture for {agent.name}. The result is shown here first and is only kept if you choose it.</p>
        <Button size="sm" variant="ghost" onClick={remove} disabled={busy}>Remove picture</Button>
      </div>
      {providers && !providers.some((p) => p.available) && (
        <p role="status" className="rounded-field border border-warning/40 bg-warning/10 px-2.5 py-1.5 text-xs text-warning">
          No image service is set up. Add an image API key in the server settings (IMAGE_API_KEY), then reload.
        </p>
      )}
      <label className="block text-xs font-medium text-muted">
        Description
        <textarea className={`${FIELD} mt-1 min-h-20`} value={prompt} maxLength={PROMPT_MAX} onChange={(e) => setPrompt(e.target.value)} placeholder="A friendly fox in flat illustration style" />
      </label>
      <div className="flex flex-wrap gap-2">
        <label className="min-w-0 flex-1 text-xs font-medium text-muted">
          Provider
          <select className={`${FIELD} mt-1`} value={provider} onChange={(e) => setProvider(e.target.value)}>
            {(providers ?? []).map((p) => (
              <option key={p.id} value={p.id}>{p.label}{p.available ? "" : " (not set up)"}</option>
            ))}
          </select>
        </label>
        <label className="min-w-0 flex-1 text-xs font-medium text-muted">
          Shape
          <select className={`${FIELD} mt-1`} value={size} onChange={(e) => setSize(e.target.value)}>
            {IMAGE_SIZES.map((s) => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </label>
      </div>
      {error && <p role="alert" className="text-xs text-danger">{error}</p>}
      <Button onClick={generate} disabled={busy || Boolean(problem) || !chosen?.available}>{busy ? "Working…" : "Generate image"}</Button>
      {result && (
        <div className="space-y-2 rounded-card border border-line bg-raised p-2.5">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={result.url} alt="Generated preview" className="mx-auto max-h-64 rounded-field object-contain" />
          <div className="flex gap-2">
            <Button size="sm" onClick={use} disabled={busy}>Use as picture</Button>
            <Button size="sm" variant="ghost" onClick={() => setResult(null)} disabled={busy}>Discard</Button>
          </div>
        </div>
      )}
    </div>
  );
}
