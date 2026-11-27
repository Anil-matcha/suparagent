import test from "node:test";
import assert from "node:assert/strict";
import { normalizeTextForSpeech, speakText, DEFAULT_VOICE_PROFILE } from "./speech.ts";

test("DEFAULT_VOICE_PROFILE has safe defaults", () => {
  assert.equal(DEFAULT_VOICE_PROFILE.rate, 1.0);
  assert.equal(DEFAULT_VOICE_PROFILE.pitch, 1.0);
  assert.equal(DEFAULT_VOICE_PROFILE.autoSpeak, false);
  assert.equal(DEFAULT_VOICE_PROFILE.localService, true);
});

test("normalizeTextForSpeech strips markdown formatting, code blocks, links, and headers", () => {
  const input = `
# Summary Header

Here is some **bold text** and *italic words*.
Check this [Open Duet Documentation](https://example.com/docs) for info.

\`\`\`python
def secret_logic():
    return 42
\`\`\`

Also run \`npm test\` to check.
- First bullet item
- Second bullet item

> Important note in blockquote.
`;

  const output = normalizeTextForSpeech(input);
  assert.ok(!output.includes("# Summary Header"));
  assert.ok(output.includes("Summary Header"));
  assert.ok(output.includes("Here is some bold text and italic words."));
  assert.ok(output.includes("Check this Open Duet Documentation for info."));
  assert.ok(!output.includes("https://example.com/docs"));
  assert.ok(!output.includes("def secret_logic"));
  assert.ok(output.includes("[code omitted]"));
  assert.ok(output.includes("Also run npm test to check."));
  assert.ok(output.includes("First bullet item"));
  assert.ok(output.includes("Important note in blockquote."));
});

test("normalizeTextForSpeech handles empty or whitespace input gracefully", () => {
  assert.equal(normalizeTextForSpeech(""), "");
  assert.equal(normalizeTextForSpeech("   \n\t  "), "");
});

test("speakText invokes onError when speech synthesis is unsupported", () => {
  let errorCaught = false;
  const ctrl = speakText(
    "Hello agent",
    {
      onError: (err) => {
        errorCaught = true;
        assert.ok(err.message.includes("not supported"));
      },
    },
    null
  );

  assert.equal(errorCaught, true);
  assert.equal(ctrl.speaking, false);
});

test("speakText manages utterance lifecycle with fake synthesis engine", () => {
  let spoken = null;
  let cancelled = false;

  const fakeSynth = {
    speaking: false,
    cancel() {
      cancelled = true;
      this.speaking = false;
    },
    speak(utterance) {
      spoken = utterance;
      this.speaking = true;
      utterance.onstart?.();
    },
    getVoices() {
      return [
        { voiceURI: "test-voice-1", name: "Voice 1", lang: "en-US" },
        { voiceURI: "test-voice-2", name: "Voice 2", lang: "en-GB" },
      ];
    },
  };

  // Mock global SpeechSynthesisUtterance
  globalThis.SpeechSynthesisUtterance = class {
    constructor(text) {
      this.text = text;
      this.rate = 1.0;
      this.pitch = 1.0;
      this.voice = null;
      this.onstart = null;
      this.onend = null;
      this.onerror = null;
    }
  };

  let started = false;
  const ctrl = speakText(
    "Hello world! Check out `code`.",
    {
      voiceUri: "test-voice-2",
      rate: 1.2,
      pitch: 0.9,
      onStart: () => {
        started = true;
      },
    },
    fakeSynth
  );

  assert.equal(cancelled, true);
  assert.equal(started, true);
  assert.equal(spoken.text, "Hello world! Check out code.");
  assert.equal(spoken.voice.voiceURI, "test-voice-2");
  assert.equal(spoken.rate, 1.2);
  assert.equal(spoken.pitch, 0.9);
  assert.equal(ctrl.speaking, true);

  ctrl.stop();
  assert.equal(ctrl.speaking, false);
});
