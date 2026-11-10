"use client";

import React, { useState } from "react";
import {
  Check,
  Copy,
  ExternalLink,
  Info,
  Lightbulb,
  AlertTriangle,
  AlertCircle,
  ShieldAlert,
  Hash,
  ArrowUpRight,
} from "lucide-react";
import { cn } from "@/lib/cn";

export interface HeadingItem {
  id: string;
  text: string;
  level: number;
}

interface DocsMarkdownProps {
  content: string;
  articleTitle?: string;
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
    <div className="group relative my-5 overflow-hidden rounded-xl border border-line bg-surface-muted/60 dark:bg-black/50">
      <div className="flex items-center justify-between border-b border-line/60 bg-surface/80 px-4 py-1.5 text-xs text-muted">
        <span className="font-mono text-[11px] font-medium uppercase tracking-wider text-muted">
          {language || "text"}
        </span>
        <button
          type="button"
          onClick={handleCopy}
          className="flex items-center gap-1.5 rounded px-2 py-1 text-xs text-muted transition hover:bg-hover hover:text-ink cursor-pointer"
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

function CalloutBox({ type, title, children }: { type: string; title?: string; children: React.ReactNode }) {
  const configs: Record<string, { icon: typeof Info; label: string; bg: string; border: string; text: string }> = {
    NOTE: { icon: Info, label: "Note", bg: "bg-blue-500/10", border: "border-blue-500/30", text: "text-blue-500" },
    INFO: { icon: Info, label: "Info", bg: "bg-blue-500/10", border: "border-blue-500/30", text: "text-blue-500" },
    TIP: { icon: Lightbulb, label: "Tip", bg: "bg-emerald-500/10", border: "border-emerald-500/30", text: "text-emerald-500" },
    WARNING: { icon: AlertTriangle, label: "Warning", bg: "bg-amber-500/10", border: "border-amber-500/30", text: "text-amber-500" },
    IMPORTANT: { icon: AlertCircle, label: "Important", bg: "bg-violet-500/10", border: "border-violet-500/30", text: "text-violet-500" },
    CAUTION: { icon: ShieldAlert, label: "Caution", bg: "bg-rose-500/10", border: "border-rose-500/30", text: "text-rose-500" },
  };

  const key = (type || "NOTE").toUpperCase();
  const cfg = configs[key] || configs.NOTE;
  const Icon = cfg.icon;

  return (
    <div className={cn("my-6 rounded-xl border p-4.5 transition-colors", cfg.bg, cfg.border)}>
      <div className="flex items-center gap-2 font-medium text-xs tracking-wide uppercase mb-2">
        <Icon size={16} className={cfg.text} />
        <span className={cn("font-semibold", cfg.text)}>{title || cfg.label}</span>
      </div>
      <div className="text-[13.5px] leading-relaxed text-ink/90 space-y-2">{children}</div>
    </div>
  );
}

function CardView({
  title,
  href,
  description,
  onNavigateDoc,
}: {
  title: string;
  href: string;
  description: string;
  onNavigateDoc?: (slug: string) => void;
}) {
  const handleClick = (e: React.MouseEvent) => {
    if (href.startsWith("/docs?doc=") || href.startsWith("?doc=") || href.startsWith("/docs/")) {
      e.preventDefault();
      const slug = href.replace(/^\/docs\?doc=/, "").replace(/^\?doc=/, "").replace(/^\/docs\//, "").replace(/\.mdx?$/, "");
      onNavigateDoc?.(slug);
    }
  };

  return (
    <a
      href={href}
      onClick={handleClick}
      className="group flex flex-col justify-between rounded-xl border border-line bg-surface-muted/30 p-4 transition-all duration-150 hover:border-emerald-500/50 hover:bg-surface-muted/70 hover:shadow-xs cursor-pointer"
    >
      <div>
        <div className="flex items-center justify-between text-sm font-semibold text-ink group-hover:text-emerald-500 transition-colors">
          <span>{title}</span>
          <ArrowUpRight size={14} className="text-muted group-hover:text-emerald-500 transition-transform group-hover:translate-x-0.5 group-hover:-translate-y-0.5" />
        </div>
        {description && (
          <p className="mt-1.5 text-xs leading-relaxed text-muted">{description}</p>
        )}
      </div>
    </a>
  );
}

export function DocsMarkdown({ content, articleTitle, onNavigateDoc, onHeadingsExtracted }: DocsMarkdownProps) {

  // Extract headings on parse
  React.useEffect(() => {
    if (!onHeadingsExtracted) return;
    const headings: HeadingItem[] = [];
    const lines = content.split(/\r?\n/);
    for (const line of lines) {
      const trimmed = line.trim();
      const match = trimmed.match(/^(#{2,4})\s+(.+)$/);
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
      const isInternalDoc = /^\/docs\?doc=|\?doc=|\.mdx?(#.*)?$/i.test(href);

      if (isInternalDoc) {
        const slug = href
          .replace(/^\/docs\?doc=/, "")
          .replace(/^\?doc=/, "")
          .replace(/^(\.\/|\.\.\/|docs\/)+/, "");
        const [cleanSlug, anchor] = slug.split("#");
        const docSlug = cleanSlug.replace(/\.mdx?$/i, "").toLowerCase();

        parts.push(
          <button
            key={match.index}
            type="button"
            onClick={() => onNavigateDoc?.(docSlug)}
            className="inline font-medium text-emerald-500 underline underline-offset-4 decoration-emerald-500/40 hover:decoration-emerald-500 cursor-pointer"
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
            className="inline-flex items-center gap-0.5 font-medium text-emerald-500 underline underline-offset-4 decoration-emerald-500/40 hover:decoration-emerald-500"
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
            className="inline font-medium text-emerald-500 underline underline-offset-4 decoration-emerald-500/40 hover:decoration-emerald-500"
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

    return parts.length > 0 ? parts : renderInlineFormatting(text);
  };

  const renderInlineFormatting = (text: string): React.ReactNode => {
    // Handle inline code `code`, bold **bold**, italic *italic*, <kbd>
    const tokens = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|<kbd>[^<]+<\/kbd>|<span[^>]*>[^<]*<\/span>)/g);
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
            className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[11px] font-medium text-ink shadow-2xs"
          >
            {token.replace(/<\/?kbd>/g, "")}
          </kbd>
        );
      }
      if (token.startsWith("<span") && token.includes("omb-kicker")) {
        const textInside = token.replace(/<[^>]+>/g, "");
        return (
          <span
            key={i}
            className="block text-[11px] font-bold tracking-[0.12em] uppercase text-emerald-500 mb-2"
          >
            {textInside}
          </span>
        );
      }
      return token;
    });
  };

  // Block level parser
  const lines = content.split(/\r?\n/);
  const elements: React.ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    // 0. Empty lines
    if (!trimmed) {
      index++;
      continue;
    }

    // 1. Kicker span: <span className="omb-kicker">...</span>
    if (trimmed.startsWith("<span") && trimmed.includes("omb-kicker")) {
      const text = trimmed.replace(/<[^>]+>/g, "").trim();
      elements.push(
        <span
          key={`kicker-${index}`}
          className="block text-[11px] font-bold tracking-[0.12em] uppercase text-emerald-500 mb-3"
        >
          {text}
        </span>
      );
      index++;
      continue;
    }

    // 2. Stat row: <div className="omb-stat-row">
    if (trimmed.includes("omb-stat-row")) {
      const statItems: { strong: string; span: string }[] = [];
      index++;
      while (index < lines.length && !lines[index].includes("</div>\n") && !lines[index].trim().endsWith("</div>")) {
        const cur = lines[index];
        const match = cur.match(/<strong>([^<]+)<\/strong><span>([^<]+)<\/span>/);
        if (match) {
          statItems.push({ strong: match[1], span: match[2] });
        }
        index++;
      }
      if (index < lines.length && lines[index].includes("</div>")) index++;

      elements.push(
        <div
          key={`stat-row-${index}`}
          className="my-7 grid grid-cols-1 gap-3 sm:grid-cols-3"
        >
          {statItems.map((st, i) => (
            <div
              key={i}
              className="rounded-2xl border border-line bg-surface-muted/30 p-4 transition hover:bg-surface-muted/60"
            >
              <strong className="block text-sm font-semibold tracking-tight text-ink">
                {st.strong}
              </strong>
              <span className="mt-1 block text-xs leading-relaxed text-muted">
                {st.span}
              </span>
            </div>
          ))}
        </div>
      );
      continue;
    }

    // 3. Cards container: <Cards>
    if (trimmed.startsWith("<Cards>")) {
      const cardItems: { title: string; href: string; description: string }[] = [];
      index++;
      while (index < lines.length && !lines[index].trim().startsWith("</Cards>")) {
        const cur = lines[index].trim();
        if (cur.startsWith("<Card")) {
          const titleM = cur.match(/title=["']([^"']+)["']/);
          const hrefM = cur.match(/href=["']([^"']+)["']/);
          const descM = cur.match(/description=["']([^"']+)["']/);
          if (titleM && hrefM) {
            cardItems.push({
              title: titleM[1],
              href: hrefM[1],
              description: descM ? descM[1] : "",
            });
          }
        }
        index++;
      }
      if (index < lines.length && lines[index].trim().startsWith("</Cards>")) index++;

      elements.push(
        <div key={`cards-${index}`} className="my-6 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {cardItems.map((c, i) => (
            <CardView
              key={i}
              title={c.title}
              href={c.href}
              description={c.description}
              onNavigateDoc={onNavigateDoc}
            />
          ))}
        </div>
      );
      continue;
    }

    // 5. Callout: <Callout type="..." title="...">
    if (trimmed.startsWith("<Callout")) {
      const typeM = trimmed.match(/type=["']([^"']+)["']/);
      const titleM = trimmed.match(/title=["']([^"']+)["']/);
      const calloutType = typeM ? typeM[1] : "info";
      const calloutTitle = titleM ? titleM[1] : undefined;
      const calloutLines: string[] = [];
      index++;
      while (index < lines.length && !lines[index].trim().startsWith("</Callout>")) {
        calloutLines.push(lines[index]);
        index++;
      }
      if (index < lines.length && lines[index].trim().startsWith("</Callout>")) index++;

      elements.push(
        <CalloutBox key={`callout-${index}`} type={calloutType} title={calloutTitle}>
          {calloutLines.map((cl, i) => (
            <p key={i}>{renderInline(cl)}</p>
          ))}
        </CalloutBox>
      );
      continue;
    }

    // 6. Code blocks
    if (trimmed.startsWith("```")) {
      const language = trimmed.slice(3).trim();
      const codeLines: string[] = [];
      index++;
      while (index < lines.length && !lines[index].trim().startsWith("```")) {
        codeLines.push(lines[index]);
        index++;
      }
      if (index < lines.length) index++; // skip closing ```
      elements.push(
        <CodeBlock
          key={`code-${index}`}
          code={codeLines.join("\n")}
          language={language}
        />
      );
      continue;
    }

    // 7. GitHub Alerts (> [!NOTE], > [!TIP], etc.)
    if (trimmed.startsWith("> [!")) {
      const alertMatch = trimmed.match(/^>\s*\[!(NOTE|TIP|WARNING|IMPORTANT|CAUTION)\]/i);
      if (alertMatch) {
        const alertType = alertMatch[1].toUpperCase();
        const alertLines: string[] = [];
        index++;
        while (index < lines.length && lines[index].trim().startsWith(">")) {
          alertLines.push(lines[index].replace(/^>\s?/, ""));
          index++;
        }
        elements.push(
          <CalloutBox key={`alert-${index}`} type={alertType}>
            {alertLines.map((al, i) => (
              <p key={i}>{renderInline(al)}</p>
            ))}
          </CalloutBox>
        );
        continue;
      }
    }

    // 8. Standard blockquotes
    if (trimmed.startsWith(">")) {
      const quoteLines: string[] = [];
      while (index < lines.length && lines[index].trim().startsWith(">")) {
        quoteLines.push(lines[index].replace(/^>\s?/, ""));
        index++;
      }
      elements.push(
        <blockquote
          key={`quote-${index}`}
          className="my-4 border-l-2 border-emerald-500/60 pl-4 italic text-muted"
        >
          {quoteLines.map((ql, i) => (
            <p key={i} className="my-1">{renderInline(ql)}</p>
          ))}
        </blockquote>
      );
      continue;
    }

    // 9. Headings (# H1, ## H2, ### H3, #### H4)
    const headingMatch = trimmed.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const rawText = headingMatch[2].trim();
      const cleanText = rawText.replace(/\*\*|__|\*|_/g, "");
      const id = slugify(cleanText);

      // Skip initial level-1 heading if it duplicates the article title or is at the start
      if (level === 1 && (elements.length === 0 || (articleTitle && cleanText.toLowerCase() === articleTitle.toLowerCase()))) {
        index++;
        continue;
      }

      if (level === 1) {
        elements.push(
          <h1
            key={`h1-${index}-${id}`}
            id={id}
            className="group relative mt-10 mb-4 scroll-mt-24 text-2.5xl font-extrabold tracking-tight text-ink sm:text-3xl"
          >
            <span>{renderInline(rawText)}</span>
            <a
              href={`#${id}`}
              className="ml-2 inline-block opacity-0 transition-opacity group-hover:opacity-100 text-muted hover:text-emerald-500"
              aria-label={`Link to ${cleanText}`}
            >
              <Hash size={18} className="inline" />
            </a>
          </h1>
        );
      } else if (level === 2) {
        elements.push(
          <h2
            key={`h2-${index}-${id}`}
            id={id}
            className="group relative mt-8 mb-3.5 scroll-mt-24 border-b border-line/60 pb-2 text-xl font-bold tracking-tight text-ink sm:text-2xl"
          >
            <span>{renderInline(rawText)}</span>
            <a
              href={`#${id}`}
              className="ml-2 inline-block opacity-0 transition-opacity group-hover:opacity-100 text-muted hover:text-emerald-500"
              aria-label={`Link to ${cleanText}`}
            >
              <Hash size={16} className="inline" />
            </a>
          </h2>
        );
      } else if (level === 3) {
        elements.push(
          <h3
            key={`h3-${index}-${id}`}
            id={id}
            className="group relative mt-6 mb-2.5 scroll-mt-24 text-base font-semibold tracking-tight text-ink sm:text-lg"
          >
            <span>{renderInline(rawText)}</span>
            <a
              href={`#${id}`}
              className="ml-2 inline-block opacity-0 transition-opacity group-hover:opacity-100 text-muted hover:text-emerald-500"
              aria-label={`Link to ${cleanText}`}
            >
              <Hash size={14} className="inline" />
            </a>
          </h3>
        );
      } else {
        elements.push(
          <h4
            key={`h4-${index}-${id}`}
            id={id}
            className="group relative mt-5 mb-2 scroll-mt-24 text-sm font-semibold text-ink"
          >
            <span>{renderInline(rawText)}</span>
            <a
              href={`#${id}`}
              className="ml-2 inline-block opacity-0 transition-opacity group-hover:opacity-100 text-muted hover:text-emerald-500"
              aria-label={`Link to ${cleanText}`}
            >
              <Hash size={13} className="inline" />
            </a>
          </h4>
        );
      }
      index++;
      continue;
    }

    // 10. Tables (| a | b |)
    if (trimmed.startsWith("|") && trimmed.endsWith("|")) {
      const tableRows: string[][] = [];

      while (index < lines.length && lines[index].trim().startsWith("|")) {
        const rowStr = lines[index].trim();
        if (/^\|(\s*[-:]+\s*\|)+$/.test(rowStr)) {
          index++;
          continue;
        }
        const cells = rowStr
          .slice(1, -1)
          .split("|")
          .map((c) => c.trim());
        tableRows.push(cells);
        index++;
      }

      if (tableRows.length > 0) {
        const headerRow = tableRows[0];
        const bodyRows = tableRows.slice(1);

        elements.push(
          <div key={`table-${index}`} className="my-6 overflow-x-auto rounded-xl border border-line">
            <table className="w-full text-left text-xs sm:text-sm">
              <thead className="border-b border-line bg-surface-muted/70 text-ink font-semibold">
                <tr>
                  {headerRow.map((cell, ci) => (
                    <th key={ci} className="px-4 py-2.5 font-semibold text-ink">
                      {renderInline(cell)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-line/60 bg-surface">
                {bodyRows.map((row, ri) => (
                  <tr key={ri} className="transition-colors hover:bg-hover/50">
                    {row.map((cell, ci) => (
                      <td key={ci} className="px-4 py-2.5 text-ink/90 align-top leading-relaxed">
                        {renderInline(cell)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
        continue;
      }
    }

    // 11. Bullet & Ordered Lists (* item, - item, 1. item)
    if (/^(\*|-|\d+\.)\s+/.test(trimmed)) {
      const isOrdered = /^\d+\.\s+/.test(trimmed);
      const listItems: string[] = [];

      while (index < lines.length && /^(\*|-|\d+\.)\s+/.test(lines[index].trim())) {
        listItems.push(lines[index].trim().replace(/^(\*|-|\d+\.)\s+/, ""));
        index++;
      }

      if (isOrdered) {
        elements.push(
          <ol key={`ol-${index}`} className="my-4 ml-6 list-decimal space-y-1.5 text-sm text-ink leading-relaxed">
            {listItems.map((item, i) => (
              <li key={i}>{renderInline(item)}</li>
            ))}
          </ol>
        );
      } else {
        elements.push(
          <ul key={`ul-${index}`} className="my-4 ml-6 list-disc space-y-1.5 text-sm text-ink leading-relaxed">
            {listItems.map((item, i) => (
              <li key={i}>{renderInline(item)}</li>
            ))}
          </ul>
        );
      }
      continue;
    }

    // 12. Horizontal rule (---)
    if (/^(\*\*\*|---|___)$/.test(trimmed)) {
      elements.push(<hr key={`hr-${index}`} className="my-8 border-line/60" />);
      index++;
      continue;
    }

    // 13. Regular paragraph
    elements.push(
      <p key={`p-${index}`} className="my-3.5 text-sm sm:text-[15px] leading-relaxed text-ink/90">
        {renderInline(line)}
      </p>
    );
    index++;
  }

  return <div className="docs-prose space-y-1">{elements}</div>;
}
