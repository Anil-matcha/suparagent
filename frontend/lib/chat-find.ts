export type TextMatchSpan = {
  start: number;
  end: number;
};

const EXCLUDED_SELECTOR =
  "button, input, textarea, select, [role=button], [contenteditable], [hidden], [aria-hidden=true], script, style, template, noscript, svg";
const BLOCK_SELECTOR =
  "address, article, aside, blockquote, dd, div, dl, dt, figcaption, figure, footer, h1, h2, h3, h4, h5, h6, header, hr, li, main, nav, ol, p, pre, section, table, td, th, tr, ul";

type TextChunk = { node: Text; start: number; end: number };

type HighlightRegistryLike = {
  set(name: string, highlight: unknown): void;
  delete(name: string): void;
};

type HighlightCtor = new (...ranges: Range[]) => unknown;

export function findTextMatches(text: string, query: string): TextMatchSpan[] {
  const needle = query.trim();
  if (!needle || !text) return [];
  const lowerText = text.toLowerCase();
  const lowerNeedle = needle.toLowerCase();
  const matches: TextMatchSpan[] = [];
  let from = 0;
  while (from <= lowerText.length - lowerNeedle.length) {
    const idx = lowerText.indexOf(lowerNeedle, from);
    if (idx === -1) break;
    matches.push({ start: idx, end: idx + lowerNeedle.length });
    from = idx + Math.max(1, lowerNeedle.length);
  }
  return matches;
}

export function stepMatchIndex(current: number, delta: 1 | -1, total: number): number {
  if (total <= 0) return -1;
  if (current < 0 || current >= total) {
    return delta > 0 ? 0 : total - 1;
  }
  return (((current + delta) % total) + total) % total;
}

export function isFindShortcut(
  event: { key: string; metaKey: boolean; ctrlKey: boolean; altKey: boolean; shiftKey: boolean },
  isMac: boolean,
): boolean {
  if (event.key.toLowerCase() !== "f" || event.altKey || event.shiftKey) return false;
  return isMac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey;
}

function readMessageChunks(root: HTMLElement): { text: string; chunks: TextChunk[] } {
  const parts: string[] = [];
  const chunks: TextChunk[] = [];
  let length = 0;
  let separator = false;

  const visit = (node: Node) => {
    if (node.nodeType === Node.TEXT_NODE) {
      const text = node as Text;
      if (!text.length) return;
      if (separator && length > 0) {
        parts.push("\n");
        length += 1;
      }
      separator = false;
      chunks.push({ node: text, start: length, end: length + text.length });
      parts.push(text.data);
      length += text.length;
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node as Element;
    if (el.matches(EXCLUDED_SELECTOR)) return;
    const block = el.matches(BLOCK_SELECTOR);
    if (block || el.tagName === "BR") separator = true;
    for (const child of el.childNodes) visit(child);
    if (block) separator = true;
  };

  visit(root);
  return { text: parts.join(""), chunks };
}

export type FindDomMatch = {
  range: Range;
  messageEl: HTMLElement;
};

export function collectFindDomMatches(container: HTMLElement, query: string): FindDomMatch[] {
  const needle = query.trim();
  if (!needle) return [];
  const results: FindDomMatch[] = [];
  const messages = container.querySelectorAll<HTMLElement>("[data-message-key]");

  for (const messageEl of messages) {
    const { text, chunks } = readMessageChunks(messageEl);
    if (!chunks.length) continue;
    const spans = findTextMatches(text, needle);
    for (const span of spans) {
      const first = chunks.find((c) => c.end > span.start);
      const last = chunks.findLast((c) => c.start < span.end);
      if (!first || !last) continue;
      const range = document.createRange();
      range.setStart(first.node, Math.max(0, span.start - first.start));
      range.setEnd(last.node, Math.max(0, Math.min(last.node.length, span.end - last.start)));
      results.push({ range, messageEl });
    }
  }
  return results;
}

const FIND_STYLE_ID = "suparagent-chat-find-style";

function ensureHighlightStyles(): void {
  if (typeof document === "undefined" || document.getElementById(FIND_STYLE_ID)) return;
  const style = document.createElement("style");
  style.id = FIND_STYLE_ID;
  style.textContent = [
    "::highlight(chat-find) { background-color: color-mix(in srgb, var(--warning) 28%, transparent); color: inherit; }",
    "::highlight(chat-find-active) { background-color: color-mix(in srgb, var(--accent) 34%, transparent); color: inherit; }",
  ].join("\n");
  document.head.appendChild(style);
}

function getHighlightApi(): { registry: HighlightRegistryLike; HighlightClass: HighlightCtor } | null {
  if (typeof window === "undefined" || typeof CSS === "undefined") return null;
  const cssWithHighlights = CSS as unknown as { highlights?: HighlightRegistryLike };
  const HighlightClass = (globalThis as unknown as { Highlight?: HighlightCtor }).Highlight;
  if (!cssWithHighlights.highlights || !HighlightClass) return null;
  ensureHighlightStyles();
  return { registry: cssWithHighlights.highlights, HighlightClass };
}

export function applyFindHighlights(matches: FindDomMatch[], activeIndex: number): void {
  const api = getHighlightApi();
  if (!api) return;
  api.registry.delete("chat-find");
  api.registry.delete("chat-find-active");
  if (!matches.length) return;

  const rest: Range[] = [];
  let active: Range | null = null;
  matches.forEach((m, idx) => {
    if (idx === activeIndex) active = m.range;
    else rest.push(m.range);
  });

  if (rest.length) api.registry.set("chat-find", new api.HighlightClass(...rest));
  if (active) api.registry.set("chat-find-active", new api.HighlightClass(active));
}

export function clearFindHighlights(): void {
  const api = getHighlightApi();
  if (!api) return;
  api.registry.delete("chat-find");
  api.registry.delete("chat-find-active");
}

export function scrollToFindMatch(viewport: HTMLElement, match: FindDomMatch): void {
  const rect = match.range.getBoundingClientRect();
  if (rect.width > 0 || rect.height > 0) {
    const vpRect = viewport.getBoundingClientRect();
    const targetTop = viewport.scrollTop + (rect.top - vpRect.top) - vpRect.height * 0.4;
    viewport.scrollTo({ top: Math.max(0, targetTop), behavior: "smooth" });
    return;
  }
  match.messageEl.scrollIntoView({ behavior: "smooth", block: "center" });
}
