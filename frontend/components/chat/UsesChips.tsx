import { usesToolkits } from "@/lib/saved-apps";

// What a page declared it may touch (display only): the connected apps in its `uses`. Nothing when it declared none.
export function UsesChips({ uses }: { uses: readonly string[] }) {
  const toolkits = usesToolkits(uses);
  if (toolkits.length === 0) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1 text-xs text-muted" aria-label="Connected apps this page may use">
      <span>Uses:</span>
      {toolkits.map((t) => <span key={t} className="rounded-field border border-line px-1.5 py-0.5">{t}</span>)}
    </span>
  );
}
