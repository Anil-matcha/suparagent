// Pure citation and reply-annotation helpers shared across transcript, composer, and Node tests.

export const CITATION_MAX_QUOTE_LENGTH = 12_000;
export const CITATION_MAX_COMMENT_LENGTH = 4_000;
const CITATION_CONTEXT_LENGTH = 64;

const CITATION_MARKER = "<!--od-citation-v1:";
const CITATION_MARKER_RE = /<!--od-citation-v1:([A-Za-z0-9_-]{1,100000})-->/g;
const REPLY_MARKER = "<!--od-reply-v1:";
const REPLY_MARKER_RE = /<!--od-reply-v1:([A-Za-z0-9_-]{1,40000})-->/g;

export type CitationSource = {
  threadId: string;
  itemKey: string;
  start: number;
  end: number;
  prefix: string;
  suffix: string;
};

export type CitationAttachment = {
  kind: "citation";
  version: 1;
  id: string;
  quote: string;
  comment?: string;
  source: CitationSource;
  size: number;
};

export type CitationTextSelector = Pick<CitationSource, "start" | "end" | "prefix" | "suffix"> & {
  text: string;
};

export type ReplyTarget = {
  kind: "reply";
  version: 1;
  itemKey: string;
  role: "user" | "assistant";
  excerpt: string;
};

function boundedString(value: unknown, max: number, allowEmpty = false): value is string {
  return typeof value === "string" && value.length <= max && (allowEmpty || value.length > 0);
}

export function isCitationAttachment(value: unknown): value is CitationAttachment {
  if (!value || typeof value !== "object") return false;
  const citation = value as Record<string, unknown>;
  const source = citation.source as Record<string, unknown> | undefined;
  return (
    citation.kind === "citation" &&
    citation.version === 1 &&
    boundedString(citation.id, 200) &&
    boundedString(citation.quote, CITATION_MAX_QUOTE_LENGTH) &&
    (citation.comment === undefined || boundedString(citation.comment, CITATION_MAX_COMMENT_LENGTH, true)) &&
    typeof citation.size === "number" &&
    Number.isFinite(citation.size) &&
    citation.size >= 0 &&
    Boolean(source) &&
    boundedString(source!.threadId, 200) &&
    boundedString(source!.itemKey, 300) &&
    Number.isSafeInteger(source!.start) &&
    Number(source!.start) >= 0 &&
    Number.isSafeInteger(source!.end) &&
    Number(source!.end) >= Number(source!.start) &&
    boundedString(source!.prefix, CITATION_CONTEXT_LENGTH, true) &&
    boundedString(source!.suffix, CITATION_CONTEXT_LENGTH, true)
  );
}

export function isReplyTarget(value: unknown): value is ReplyTarget {
  if (!value || typeof value !== "object") return false;
  const reply = value as Record<string, unknown>;
  return (
    reply.kind === "reply" &&
    reply.version === 1 &&
    boundedString(reply.itemKey, 300) &&
    (reply.role === "user" || reply.role === "assistant") &&
    boundedString(reply.excerpt, 600)
  );
}

function newId(): string {
  return globalThis.crypto?.randomUUID?.() ?? `c${Math.random().toString(36).slice(2)}`;
}

function byteLength(text: string): number {
  return new TextEncoder().encode(text).length;
}

export function citationAttachment(
  source: Pick<CitationSource, "threadId" | "itemKey">,
  selector: CitationTextSelector,
  comment = "",
): CitationAttachment {
  const trimmedComment = comment.trim();
  if (!selector.text.trim() || selector.text.length > CITATION_MAX_QUOTE_LENGTH) {
    throw new Error("Citation quote is empty or too long");
  }
  if (trimmedComment.length > CITATION_MAX_COMMENT_LENGTH) {
    throw new Error("Citation comment is too long");
  }
  return {
    kind: "citation",
    version: 1,
    id: newId(),
    quote: selector.text,
    ...(trimmedComment ? { comment: trimmedComment } : {}),
    source: {
      ...source,
      start: selector.start,
      end: selector.end,
      prefix: selector.prefix,
      suffix: selector.suffix,
    },
    size: byteLength(selector.text) + byteLength(trimmedComment),
  };
}

export function withCitationComment(citation: CitationAttachment, comment: string): CitationAttachment {
  const trimmed = comment.trim();
  if (trimmed.length > CITATION_MAX_COMMENT_LENGTH) {
    throw new Error("Citation comment is too long");
  }
  const { comment: _old, ...rest } = citation;
  return {
    ...rest,
    ...(trimmed ? { comment: trimmed } : {}),
    size: byteLength(citation.quote) + byteLength(trimmed),
  };
}

export function makeReplyTarget(itemKey: string, role: "user" | "assistant", rawText: string): ReplyTarget {
  const cleaned = rawText.replace(/\s+/g, " ").trim();
  const excerpt = cleaned.length > 240 ? `${cleaned.slice(0, 239)}…` : cleaned || "Message";
  return { kind: "reply", version: 1, itemKey, role, excerpt };
}

function base64UrlEncode(value: unknown): string {
  const bytes = new TextEncoder().encode(JSON.stringify(value));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function base64UrlDecode(value: string): unknown {
  const padded = value.replaceAll("-", "+").replaceAll("_", "/").padEnd(Math.ceil(value.length / 4) * 4, "=");
  const binary = atob(padded);
  return JSON.parse(new TextDecoder().decode(Uint8Array.from(binary, (character) => character.charCodeAt(0))));
}

export function citationFallback(citation: CitationAttachment): string {
  const quote = citation.quote.split("\n").map((line) => `> ${line}`).join("\n");
  return `> Quoted message:\n${quote}${citation.comment ? `\n\nComment:\n${citation.comment}` : ""}`;
}

export function serializeCitation(citation: CitationAttachment): string {
  return `${CITATION_MARKER}${base64UrlEncode(citation)}-->\n${citationFallback(citation)}`;
}

export function replyFallback(reply: ReplyTarget): string {
  const quote = reply.excerpt.split("\n").map((line) => `> ${line}`).join("\n");
  return `> Replying to:\n${quote}`;
}

export function serializeReply(reply: ReplyTarget): string {
  return `${REPLY_MARKER}${base64UrlEncode(reply)}-->\n${replyFallback(reply)}`;
}

export function buildAnnotatedMessage(
  message: string,
  { replyTo = null, citations = [] }: { replyTo?: ReplyTarget | null; citations?: CitationAttachment[] } = {},
): string {
  const blocks: string[] = [];
  if (replyTo) blocks.push(serializeReply(replyTo));
  for (const citation of citations) blocks.push(serializeCitation(citation));
  const body = message.trim();
  if (body) blocks.push(body);
  return blocks.join("\n\n");
}

export function splitMessageAnnotations(text: string): {
  display: string;
  reply: ReplyTarget | null;
  citations: CitationAttachment[];
} {
  let remaining = text;
  let reply: ReplyTarget | null = null;

  for (const match of remaining.matchAll(REPLY_MARKER_RE)) {
    if (match.index === undefined) continue;
    try {
      const decoded = base64UrlDecode(match[1]!);
      if (!isReplyTarget(decoded)) continue;
      const block = `${match[0]}\n${replyFallback(decoded)}`;
      if (!remaining.startsWith(block, match.index)) continue;
      reply = decoded;
      remaining = `${remaining.slice(0, match.index)}${remaining.slice(match.index + block.length)}`;
      break;
    } catch {
      continue;
    }
  }

  const citations: CitationAttachment[] = [];
  let display = "";
  let cursor = 0;
  for (const match of remaining.matchAll(CITATION_MARKER_RE)) {
    if (match.index === undefined || match.index < cursor) continue;
    let citation: CitationAttachment;
    try {
      const decoded = base64UrlDecode(match[1]!);
      if (!isCitationAttachment(decoded)) continue;
      citation = decoded;
    } catch {
      continue;
    }
    const block = `${match[0]}\n${citationFallback(citation)}`;
    if (!remaining.startsWith(block, match.index)) continue;
    display += remaining.slice(cursor, match.index);
    cursor = match.index + block.length;
    citations.push(citation);
  }
  return {
    display: `${display}${remaining.slice(cursor)}`.trim(),
    reply,
    citations,
  };
}

function normalizeWhitespace(text: string): string {
  return text.replace(/\s+/g, " ");
}

function splitsSurrogatePair(text: string, offset: number): boolean {
  const before = text.charCodeAt(offset - 1);
  const after = text.charCodeAt(offset);
  return before >= 0xd800 && before <= 0xdbff && after >= 0xdc00 && after <= 0xdfff;
}

export function createCitationTextSelector(text: string, rawStart: number, rawEnd: number): CitationTextSelector | null {
  const quote = text.slice(rawStart, rawEnd);
  if (!quote.trim()) return null;
  const normalized = normalizeWhitespace(text);
  let start = normalizeWhitespace(text.slice(0, rawStart)).length;
  if (rawStart > 0 && /\s/.test(text[rawStart - 1]!) && /\s/.test(text[rawStart]!)) start -= 1;
  const end = normalizeWhitespace(text.slice(0, rawEnd)).length;
  let prefixStart = Math.max(0, start - CITATION_CONTEXT_LENGTH);
  let suffixEnd = Math.min(normalized.length, end + CITATION_CONTEXT_LENGTH);
  if (splitsSurrogatePair(normalized, prefixStart)) prefixStart += 1;
  if (splitsSurrogatePair(normalized, suffixEnd)) suffixEnd -= 1;
  return {
    text: quote,
    start,
    end,
    prefix: normalized.slice(prefixStart, start),
    suffix: normalized.slice(end, suffixEnd),
  };
}

export function findCitationText(text: string, selector: CitationTextSelector): { start: number; end: number } | null {
  const normalized = normalizeWhitespace(text);
  const quote = normalizeWhitespace(selector.text);
  if (!quote.trim()) return null;
  const prefix = normalizeWhitespace(selector.prefix);
  const suffix = normalizeWhitespace(selector.suffix);
  const matchesContext = (start: number, end: number) =>
    normalized.slice(Math.max(0, start - prefix.length), start) === prefix &&
    normalized.slice(end, end + suffix.length) === suffix;
  let match =
    Number.isSafeInteger(selector.start) &&
    Number.isSafeInteger(selector.end) &&
    selector.start >= 0 &&
    selector.end - selector.start === quote.length &&
    normalized.slice(selector.start, selector.end) === quote &&
    matchesContext(selector.start, selector.end)
      ? { start: selector.start, end: selector.end }
      : null;
  let onlyQuote: { start: number; end: number } | null = null;
  let quoteCount = 0;
  for (let start = normalized.indexOf(quote); start !== -1; start = normalized.indexOf(quote, start + 1)) {
    const end = start + quote.length;
    quoteCount += 1;
    onlyQuote = { start, end };
    if (!matchesContext(start, end)) continue;
    if (match && match.start !== start) return null;
    match = { start, end };
  }
  return match ?? (quoteCount === 1 ? onlyQuote : null);
}
