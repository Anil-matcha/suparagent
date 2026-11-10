"use client";

import React, { useState, useId } from "react";
import { Check, Copy, ExternalLink, Info, Lightbulb, AlertTriangle, AlertCircle, ShieldAlert, Hash } from "lucide-react";
import { cn } from "@/lib/cn";

export interface HeadingItem {
  id: string;
  text: string;
  level: number;
}

interface DocsMarkdownProps {
  content: string;
  onNavigateDoc?: (slug: string) => void;
  onHeadingsExtracted?: (headings: HeadingItem[]) => void;
}

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

function CodeBlock({ code, language }: { code: string; language: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="group relative my-4 overflow-hidden rounded-xl border border-line bg-surface-muted/60 dark:bg-black/40">
      <div className="flex items-center justify-between border-b border-line/60 bg-surface/80 px-4 py-1.5 text-xs text-muted">
        <span className="font-mono text-[11px] font-medium uppercase tracking-wider text-muted">
          {language || "text"}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1.5 rounded px-2 py-1 text-xs text-muted transition hover:bg-hover hover:text-ink"
          aria-label="Copy code"
        >
          {copied ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre className="overflow-x-auto p-4 font-mono text-[13px] leading-relaxed text-ink">
        <code>{code}</code>
      </pre>
    </div>
  );
}

function Callout({ type, title, children }: { type: string; title?: string; children: React.ReactNode }) {
  const configs: Record<string, { icon: typeof Info; label: string; bg: string; border: string; text: string }> = {
    NOTE: { icon: Info, label: "Note", bg: "bg-blue-500/10", border: "border-blue-500/30", text: "text-blue-600 dark:text-blue-400" },
    TIP: { icon: Lightbulb, label: "Tip", bg: "bg-emerald-500/10", border: "border-emerald-500/30", text: "text-emerald-600 dark:text-emerald-400" },
    WARNING: { icon: AlertTriangle, label: "Warning", bg: "bg-amber-500/10", border: "border-amber-500/30", text: "text-amber-600 dark:text-amber-400" },
    IMPORTANT: { icon: AlertCircle, label: "Important", bg: "bg-violet-500/10", border: "border-violet-500/30", text: "text-violet-600 dark:text-violet-400" },
    CAUTION: { icon: ShieldAlert, label: "Caution", bg: "bg-rose-500/10", border: "border-rose-500/30", text: "text-rose-600 dark:text-rose-400" },
  };

  const key = type.toUpperCase();
  const cfg = configs[key] || configs.NOTE;
  const Icon = cfg.icon;

  return (
    <div className={cn("my-4 rounded-xl border p-4 transition-colors", cfg.bg, cfg.border)}>
      <div className="flex items-center gap-2 font-medium text-xs tracking-wide uppercase mb-1.5" style={{ color: "inherit" }}>
        <Icon size={16} className={cfg.text} />
        <span className={cfg.text}>{title || cfg.label}</span>
      </div>
      <div className="text-sm leading-relaxed text-ink/90 space-y-2">{children}</div>
    </div>
  );
}

export function DocsMarkdown({ content, onNavigateDoc, onHeadingsExtracted }: DocsMarkdownProps) {
  const compId = useId();

  // Extract headings on parse
  React.useEffect(() => {
    if (!onHeadingsExtracted) return;
    const headings: HeadingItem[] = [];
    const lines = content.split("\n");
    for (const line of lines) {
      const match = line.match(/^(#{2,4})\s+(.+)$/);
      if (match) {
        const level = match[1].length;
        const text = match[2].trim().replace(/\*\*|__|\*|_/g, "");
        headings.push({ id: slugify(text), text, level });
      }
    }
    onHeadingsExtracted(headings);
  }, [content, onHeadingsExtracted]);

  const renderInline = (text: string): React.ReactNode => {
    // Process markdown links [text](url)
    const linkRegex = /\[([^\]]+)\]\(([^)]+)\)/g;
    const parts: React.ReactNode[] = [];
    let lastIndex = 0;
    let match;

    while ((match = linkRegex.exec(text)) !== null) {
      if (match.index > lastIndex) {
        parts.push(renderInlineFormatting(text.slice(lastIndex, match.index)));
      }
      const label = match[1];
      const href = match[2];

      const isExternal = /^https?:\/\//i.test(href);
      const isInternalMd = /\.md(#.*)?$/i.test(href);

      if (isInternalMd) {
        const cleanHref = href.replace(/^(\.\/|\.\.\/|docs\/)+/, "");
        const [filename, anchor] = cleanHref.split("#");
        const docSlug = filename.replace(/\.md$/i, "").toLowerCase();

        parts.push(
          <button
            key={match.index}
            type="button"
            onClick={() => onNavigateDoc?.(docSlug)}
            className="inline font-medium text-accent underline underline-offset-4 decoration-accent/40 hover:decoration-accent"
          >
            {label}
            {anchor && <span className="text-xs text-muted ml-0.5">#{anchor}</span>}
          </button>
        );
      } else if (isExternal) {
        parts.push(
          <a
            key={match.index}
            href={href}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-0.5 font-medium text-accent underline underline-offset-4 decoration-accent/40 hover:decoration-accent"
          >
            <span>{label}</span>
            <ExternalLink size={11} className="inline ml-0.5 opacity-70" />
          </a>
        );
      } else {
        parts.push(
          <a
            key={match.index}
            href={href}
            className="inline font-medium text-accent underline underline-offset-4 decoration-accent/40 hover:decoration-accent"
          >
            {label}
          </a>
        );
      }
      lastIndex = linkRegex.lastIndex;
    }

    if (lastIndex < text.length) {
      parts.push(renderInlineFormatting(text.slice(lastIndex)));
    }

    return parts.length > 0 ? parts : text;
  };

  const renderInlineFormatting = (text: string): React.ReactNode => {
    // Handle inline code `code`, bold **bold**, italic *italic*, <kbd>
    const tokens = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|<kbd>[^<]+<\/kbd>)/g);
    return tokens.map((token, i) => {
      if (token.startsWith("`") && token.endsWith("`")) {
        return (
          <code
            key={i}
            className="rounded bg-surface-muted px-1.5 py-0.5 font-mono text-[12px] font-medium text-ink border border-line/60"
          >
            {token.slice(1, -1)}
          </code>
        );
      }
      if (token.startsWith("**") && token.endsWith("**")) {
        return <strong key={i} className="font-semibold text-ink">{token.slice(2, -2)}</strong>;
      }
      if (token.startsWith("*") && token.endsWith("*")) {
        return <em key={i} className="italic text-ink/90">{token.slice(1, -1)}</em>;
      }
      if (token.startsWith("<kbd>") && token.endsWith("</kbd>")) {
        return (
          <kbd
            key={i}
            className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] font-medium text-ink shadow-xs"
          >
            {token.replace(/<\/?kbd>/g, "")}
          </kbd>
        );
      }
      return token;
    });
  };

  // Block level parser
  const lines = content.split("\n");
  const elements: React.ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    // 1. Code blocks
    if (line.trim().startsWith("```")) {
      const language = line.trim().slice(3).trim();
      const codeLines: string[] = [];
      index++;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        codeLines.push(lines[index]);
        index++;
      }
      elements.push(
        <CodeBlock
          key={`code-${index}-${compId}`}
          code={codeLines.join("\n")}
          language={language}
        />
      );
      index++;
      continue;
    }

    // 2. GitHub Alerts / Callouts (> [!NOTE])
    const calloutMatch = line.match(/^>\s+\[!(NOTE|TIP|WARNING|IMPORTANT|CAUTION)\](?:\s+(.*))?$/i);
    if (calloutMatch) {
      const type = calloutMatch[1];
      const title = calloutMatch[2];
      const calloutLines: string[] = [];
      index++;
      while (index < lines.length && lines[index].startsWith(">")) {
        calloutLines.push(lines[index].replace(/^>\s?/, ""));
        index++;
      }
      elements.push(
        <Callout key={`callout-${index}-${compId}`} type={type} title={title}>
          {calloutLines.map((cl, ci) => (
            <p key={ci}>{renderInline(cl)}</p>
          ))}
        </Callout>
      );
      continue;
    }

    // 3. Headings
    const hMatch = line.match(/^(#{1,4})\s+(.+)$/);
    if (hMatch) {
      const level = hMatch[1].length;
      const hText = hMatch[2].trim();
      const hId = slugify(hText.replace(/\*\*|__|\*|_/g, ""));

      if (level === 1) {
        elements.push(
          <div key={`h1-${index}`} className="mb-6 border-b border-line pb-4">
            <h1 id={hId} className="group flex items-center gap-2 text-2xl sm:text-3xl font-bold tracking-tight text-ink">
              <span>{renderInline(hText)}</span>
              <a href={`#${hId}`} className="opacity-0 transition-opacity group-hover:opacity-60 text-muted" aria-label="Link to section">
                <Hash size={18} />
              </a>
            </h1>
          </div>
        );
      } else if (level === 2) {
        elements.push(
          <h2 key={`h2-${index}`} id={hId} className="group mt-8 mb-3 flex items-center gap-2 text-xl font-semibold tracking-tight text-ink border-b border-line/40 pb-2">
            <span>{renderInline(hText)}</span>
            <a href={`#${hId}`} className="opacity-0 transition-opacity group-hover:opacity-60 text-muted" aria-label="Link to section">
              <Hash size={16} />
            </a>
          </h2>
        );
      } else if (level === 3) {
        elements.push(
          <h3 key={`h3-${index}`} id={hId} className="group mt-6 mb-2 flex items-center gap-2 text-base font-semibold text-ink">
            <span>{renderInline(hText)}</span>
            <a href={`#${hId}`} className="opacity-0 transition-opacity group-hover:opacity-60 text-muted" aria-label="Link to section">
              <Hash size={14} />
            </a>
          </h3>
        );
      } else {
        elements.push(
          <h4 key={`h4-${index}`} id={hId} className="mt-4 mb-2 text-sm font-semibold text-ink">
            {renderInline(hText)}
          </h4>
        );
      }
      index++;
      continue;
    }

    // 4. Horizontal rules
    if (/^(\*\*\*|---|___)$/.test(line.trim())) {
      elements.push(<hr key={`hr-${index}`} className="my-8 border-line/60" />);
      index++;
      continue;
    }

    // 5. Tables
    if (line.includes("|") && lines[index + 1]?.includes("|--")) {
      const tableLines: string[] = [];
      while (index < lines.length && lines[index].includes("|")) {
        tableLines.push(lines[index]);
        index++;
      }
      const headerCols = tableLines[0].split("|").filter((_, i, arr) => i > 0 && i < arr.length - 1).map(c => c.trim());
      const bodyRows = tableLines.slice(2).map(row =>
        row.split("|").filter((_, i, arr) => i > 0 && i < arr.length - 1).map(c => c.trim())
      );

      elements.push(
        <div key={`table-${index}-${compId}`} className="my-5 overflow-x-auto rounded-xl border border-line bg-surface shadow-2xs">
          <table className="w-full border-collapse text-left text-sm">
            <thead>
              <tr className="border-b border-line bg-surface-muted/50 font-semibold text-ink">
                {headerCols.map((h, hi) => (
                  <th key={hi} className="px-4 py-2.5 font-medium">{renderInline(h)}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-line/60">
              {bodyRows.map((r, ri) => (
                <tr key={ri} className="transition-colors hover:bg-hover/40">
                  {r.map((c, ci) => (
                    <td key={ci} className="px-4 py-2.5 text-ink/90 leading-normal">{renderInline(c)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    // 6. Lists
    if (/^(\s*[-*+]|\s*\d+\.)\s+/.test(line)) {
      const listItems: { text: string; ordered: boolean }[] = [];
      while (index < lines.length && /^(\s*[-*+]|\s*\d+\.)\s+/.test(lines[index])) {
        const itemLine = lines[index];
        const isOrdered = /^\s*\d+\./.test(itemLine);
        const itemText = itemLine.replace(/^(\s*[-*+]|\s*\d+\.)\s+/, "");
        listItems.push({ text: itemText, ordered: isOrdered });
        index++;
      }

      const isOrdered = listItems[0]?.ordered;
      if (isOrdered) {
        elements.push(
          <ol key={`ol-${index}`} className="my-3 list-decimal space-y-1.5 pl-6 text-sm leading-relaxed text-ink/90">
            {listItems.map((li, lii) => (
              <li key={lii}>{renderInline(li.text)}</li>
            ))}
          </ol>
        );
      } else {
        elements.push(
          <ul key={`ul-${index}`} className="my-3 list-disc space-y-1.5 pl-6 text-sm leading-relaxed text-ink/90">
            {listItems.map((li, lii) => (
              <li key={lii}>{renderInline(li.text)}</li>
            ))}
          </ul>
        );
      }
      continue;
    }

    // 7. Regular paragraphs
    if (line.trim().length > 0) {
      elements.push(
        <p key={`p-${index}`} className="my-3 text-[14px] sm:text-[15px] leading-relaxed text-ink/90">
          {renderInline(line)}
        </p>
      );
    }

    index++;
  }

  return <div className="docs-prose max-w-none">{elements}</div>;
}
