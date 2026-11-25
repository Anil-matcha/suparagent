"use client";

import { useCallback, useEffect, useState, type ComponentType } from "react";
import { api, ApiError, type ApplicationSetting } from "@/lib/api";
import type { IntegrationBlockId } from "@/lib/integrations";
import { savedAudioText, type SpeechVoices } from "@/lib/speech";
import { SettingsCard } from "../SettingsCard";

const BUTTON = "rounded-md border border-line px-3 py-1.5 text-xs font-medium hover:bg-hover disabled:opacity-50";

type Loaded = { voices: SpeechVoices; editable: boolean; setting: ApplicationSetting | null };

// The voice list, and the saved voice setting when this install can change it (the desktop app).
async function fetchState(): Promise<Loaded> {
  const [voices, desktop] = await Promise.all([
    api.speechVoices(),
    api.getDesktopSettings().then(
      (found) => ({ editable: true, setting: found.settings.find((item) => item.key === "SPEECH_VOICE") ?? null }),
      (cause: unknown) => {
        if (cause instanceof ApiError && cause.status === 404) return { editable: false, setting: null };
        throw cause;
      },
    ),
  ]);
  return { voices, editable: desktop.editable, setting: desktop.setting };
}

// The voice a reply is read in, and the audio that is saved so a reply can be played again for free.
// The voice is a saved application setting in the desktop app; a server install reads it from its
// SPEECH_VOICE setting and cannot change it here.
function SpokenRepliesCard() {
  const [voices, setVoices] = useState<SpeechVoices | null>(null);
  const [editable, setEditable] = useState<boolean | null>(null);
  const [setting, setSetting] = useState<ApplicationSetting | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const apply = useCallback((state: Loaded) => {
    setVoices(state.voices);
    setEditable(state.editable);
    setSetting(state.setting);
  }, []);
  const load = useCallback(async () => apply(await fetchState()), [apply]);

  useEffect(() => {
    let live = true;
    fetchState().then((state) => {
      if (live) apply(state);
    }).catch((cause: unknown) => {
      if (live) setError(cause instanceof Error ? cause.message : "Could not load the voice settings.");
    });
    return () => { live = false; };
  }, [apply]);

  const chooseVoice = async (id: string) => {
    setBusy(true); setError(null); setMessage(null);
    try {
      await api.saveDesktopSetting("SPEECH_VOICE", id, setting?.version ?? 0);
      await load();
      setMessage("Voice saved. It applies to the next reply you read aloud.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not save the voice.");
      await load().catch(() => undefined);
    } finally { setBusy(false); }
  };

  const clearSaved = async () => {
    setBusy(true); setError(null); setMessage(null);
    try {
      await api.clearSavedSpeech();
      await load();
      setMessage("Saved audio was deleted. Reading a reply again will make a new request.");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not delete the saved audio.");
    } finally { setBusy(false); }
  };

  const current = voices?.voices.find((voice) => voice.id === voices.default) ?? null;
  return (
    <div id="setting-integration-spoken-replies" data-setting-container>
      <SettingsCard
        title="Spoken replies"
        description="The speaker button under a reply reads it aloud in this voice. Reading a reply uses MuAPI credit; reading the same reply again is free while its audio is saved."
      >
        {voices === null ? <p className="text-sm text-muted">Loading voices…</p> : editable ? (
          <label className="block text-sm">Voice
            <select
              value={voices.default}
              disabled={busy}
              onChange={(event) => void chooseVoice(event.target.value)}
              className="mt-1 w-full rounded-md border border-line bg-transparent px-3 py-2"
            >
              {voices.voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.label} · {voice.price_hint}</option>)}
            </select>
          </label>
        ) : (
          <p className="text-sm">Voice: {current?.label ?? voices.default}. This server install takes the voice from its SPEECH_VOICE setting.</p>
        )}
        {voices !== null && <p className="text-xs text-muted">Prices are the list prices MuAPI gives for a short reply. The real cost is shown after each reading.</p>}
        {voices !== null && (
          <div className="flex flex-wrap items-center gap-3 border-t border-line pt-3">
            <span className="text-sm">{savedAudioText(voices.saved)}</span>
            <button type="button" disabled={busy || voices.saved.clips === 0} onClick={() => void clearSaved()} className={BUTTON}>Clear saved audio</button>
            <span className="text-xs text-muted">Saved audio stays on this computer. The newest 50 replies are kept.</span>
          </div>
        )}
        {error && <p role="alert" className="text-xs text-danger">{error}</p>}
        {message && <p role="status" className="text-xs text-muted">{message}</p>}
      </SettingsCard>
    </div>
  );
}

export const INTEGRATION_BLOCKS: Record<IntegrationBlockId, ComponentType> = {
  "spoken-replies": SpokenRepliesCard,
};
