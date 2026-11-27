// Actions held back for a moment so they can be undone (a removal with an Undo toast). Pure: timers are injected.
export type Timers = { set: (fn: () => void, ms: number) => unknown; clear: (handle: unknown) => void };
const real: Timers = { set: (fn, ms) => setTimeout(fn, ms), clear: (h) => clearTimeout(h as ReturnType<typeof setTimeout>) };

export function createDelayedActions(delayMs: number, timers: Timers = real) {
  const pending = new Map<string, { run: () => void; handle: unknown }>();
  const fire = (key: string) => {
    const entry = pending.get(key);
    if (!entry) return;
    pending.delete(key);
    entry.run();
  };
  return {
    // Runs `run` after the delay unless undone. Starting the same key again runs the earlier one right away.
    start(key: string, run: () => void) {
      if (pending.has(key)) fire(key);
      const handle = timers.set(() => fire(key), delayMs);
      const entry = { run, handle };
      pending.set(key, entry);
    },
    // True when it was still pending (and is now cancelled).
    undo(key: string): boolean {
      const entry = pending.get(key);
      if (!entry) return false;
      timers.clear(entry.handle);
      pending.delete(key);
      return true;
    },
    has: (key: string) => pending.has(key),
    // Runs everything still waiting now (the page is closing).
    flush() {
      for (const [key, entry] of [...pending]) { timers.clear(entry.handle); fire(key); }
    },
  };
}
