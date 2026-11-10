"use client";

import React, { Suspense, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  Rocket,
  Layers,
  Cpu,
  Terminal,
  Server,
  Search,
  Menu,
  X,
  ChevronRight,
  ExternalLink,
  Copy,
  Check,
  BookOpen,
  ArrowLeft,
  ArrowRight,
  Clock,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { DOC_CATEGORIES, DOC_ARTICLES, DEFAULT_DOC_SLUG, type DocArticle } from "@/lib/docs-data";
import { DocsMarkdown, type HeadingItem } from "@/components/docs/DocsMarkdown";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

const CATEGORY_ICONS: Record<string, typeof Rocket> = {
  Rocket,
  Layers,
  Cpu,
  Terminal,
  Server,
};

function estimateReadTime(text: string): number {
  const words = text.trim().split(/\s+/).length;
  return Math.max(1, Math.round(words / 200));
}

function DocsLoadingSkeleton() {
  return (
    <div className="flex h-screen w-full items-center justify-center bg-surface text-muted">
      <div className="flex items-center gap-3">
        <Sparkles size={20} className="animate-spin text-accent" />
        <span className="text-sm font-medium">Loading documentation…</span>
      </div>
    </div>
  );
}

function DocsView() {
  const searchParams = useSearchParams();
  const paramSlug = searchParams.get("doc");

  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [headings, setHeadings] = useState<HeadingItem[]>([]);
  const [activeHeadingId, setActiveHeadingId] = useState<string>("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [copiedMd, setCopiedMd] = useState(false);

  // Derive activeSlug directly from searchParams or user selection (no cascading render effect)
  const activeSlug = (paramSlug && DOC_ARTICLES[paramSlug.toLowerCase()])
    ? paramSlug.toLowerCase()
    : (selectedSlug && DOC_ARTICLES[selectedSlug.toLowerCase()] ? selectedSlug.toLowerCase() : DEFAULT_DOC_SLUG);

  const article: DocArticle = DOC_ARTICLES[activeSlug] || DOC_ARTICLES[DEFAULT_DOC_SLUG];

  // Navigate doc helper
  const navigateToDoc = (slug: string) => {
    const clean = slug.toLowerCase();
    if (DOC_ARTICLES[clean]) {
      setSelectedSlug(clean);
      setSidebarOpen(false);
      setSearchOpen(false);
      window.history.pushState(null, "", `/docs?doc=${clean}`);
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  };

  // Keyboard shortcut ⌘K or / for search
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((prev) => !prev);
      } else if (e.key === "/" && !["INPUT", "TEXTAREA"].includes((e.target as HTMLElement).tagName)) {
        e.preventDefault();
        setSearchOpen(true);
      } else if (e.key === "Escape") {
        setSearchOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Track active heading on scroll
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setActiveHeadingId(entry.target.id);
            break;
          }
        }
      },
      { rootMargin: "0px 0px -60% 0px" }
    );

    const elements = headings.map((h) => document.getElementById(h.id)).filter(Boolean) as HTMLElement[];
    for (const el of elements) observer.observe(el);

    return () => observer.disconnect();
  }, [headings]);

  // Search filter across documents
  const searchResults = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return [];
    return Object.values(DOC_ARTICLES)
      .filter(
        (doc) =>
          doc.title.toLowerCase().includes(q) ||
          doc.category.toLowerCase().includes(q) ||
          doc.summary.toLowerCase().includes(q) ||
          doc.content.toLowerCase().includes(q)
      )
      .slice(0, 8);
  }, [searchQuery]);

  // Previous and Next article pagination
  const allArticlesList = useMemo(() => {
    const list: { slug: string; title: string; category: string }[] = [];
    for (const cat of DOC_CATEGORIES) {
      for (const item of cat.items) {
        list.push({ slug: item.slug, title: item.title, category: cat.name });
      }
    }
    return list;
  }, []);

  const currentIndex = allArticlesList.findIndex((a) => a.slug === activeSlug);
  const prevArticle = currentIndex > 0 ? allArticlesList[currentIndex - 1] : null;
  const nextArticle = currentIndex < allArticlesList.length - 1 ? allArticlesList[currentIndex + 1] : null;

  const handleCopyMarkdown = async () => {
    if (!article) return;
    try {
      await navigator.clipboard.writeText(article.content);
      setCopiedMd(true);
      setTimeout(() => setCopiedMd(false), 2000);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="min-h-screen bg-surface text-ink antialiased">
      {/* Top Navigation Header */}
      <header className="sticky top-0 z-40 flex h-14 w-full items-center justify-between border-b border-line bg-surface/90 px-4 backdrop-blur-md">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setSidebarOpen((prev) => !prev)}
            className="flex items-center justify-center rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink md:hidden"
            aria-label="Toggle navigation menu"
          >
            {sidebarOpen ? <X size={18} /> : <Menu size={18} />}
          </button>

          <Link href="/chat/" className="flex items-center gap-2 font-semibold text-ink">
            <span className="flex size-7 items-center justify-center rounded-lg bg-accent text-white shadow-xs font-bold text-sm">
              S
            </span>
            <span className="font-semibold text-sm tracking-tight hidden sm:inline">Suparagent</span>
            <span className="rounded-md bg-accent/10 px-1.5 py-0.5 text-[11px] font-semibold text-accent border border-accent/20">
              Docs
            </span>
          </Link>
        </div>

        {/* Global Quick Search Button */}
        <div className="flex-1 max-w-md mx-4 hidden sm:block">
          <button
            type="button"
            onClick={() => setSearchOpen(true)}
            className="flex w-full items-center justify-between rounded-lg border border-line bg-surface-muted/50 px-3 py-1.5 text-xs text-muted transition hover:border-line-hover hover:bg-surface-muted"
          >
            <span className="flex items-center gap-2">
              <Search size={14} className="text-muted" />
              <span>Search documentation…</span>
            </span>
            <kbd className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[10px] text-muted shadow-2xs">
              ⌘K
            </kbd>
          </button>
        </div>

        {/* Right header actions */}
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setSearchOpen(true)}
            className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink sm:hidden"
            aria-label="Search"
          >
            <Search size={18} />
          </button>

          <Link
            href="/chat/"
            className="hidden md:inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-muted transition hover:bg-hover hover:text-ink"
          >
            <ArrowLeft size={13} />
            <span>Chat</span>
          </Link>

          <Link
            href="/settings/"
            className="hidden md:inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-muted transition hover:bg-hover hover:text-ink"
          >
            Settings
          </Link>

          <a
            href="https://github.com/Anil-matcha/open-duet"
            target="_blank"
            rel="noreferrer"
            className="flex items-center gap-1 rounded-lg px-2 py-1.5 text-xs font-medium text-muted transition hover:bg-hover hover:text-ink"
          >
            <span>GitHub</span>
            <ExternalLink size={11} className="opacity-70" />
          </a>

          <div className="pl-1">
            <ThemeToggle iconOnly />
          </div>
        </div>
      </header>

      {/* Main Body Layout */}
      <div className="mx-auto flex max-w-7xl">
        {/* Left Sidebar Navigation */}
        <aside
          className={cn(
            "fixed inset-y-14 left-0 z-30 w-72 shrink-0 overflow-y-auto border-r border-line bg-surface px-4 py-6 transition-transform duration-200 md:static md:translate-x-0 md:h-[calc(100vh-3.5rem)]",
            sidebarOpen ? "translate-x-0 shadow-xl" : "-translate-x-full md:translate-x-0"
          )}
        >
          <div className="space-y-6">
            {DOC_CATEGORIES.map((category) => {
              const Icon = CATEGORY_ICONS[category.icon] || BookOpen;
              return (
                <div key={category.id} className="space-y-1">
                  <div className="flex items-center gap-2 px-2.5 py-1 text-xs font-semibold uppercase tracking-wider text-muted/80">
                    <Icon size={14} className="text-muted" />
                    <span>{category.name}</span>
                  </div>
                  <ul className="space-y-0.5">
                    {category.items.map((item) => {
                      const isActive = item.slug === activeSlug;
                      return (
                        <li key={item.slug}>
                          <button
                            type="button"
                            onClick={() => navigateToDoc(item.slug)}
                            className={cn(
                              "flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-[13px] font-medium transition-colors",
                              isActive
                                ? "bg-accent/10 text-accent font-semibold"
                                : "text-muted hover:bg-hover hover:text-ink"
                            )}
                          >
                            <span className="truncate">{item.title}</span>
                            {isActive && <ChevronRight size={13} className="shrink-0 text-accent" />}
                          </button>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              );
            })}
          </div>
        </aside>

        {/* Backdrop for mobile drawer */}
        {sidebarOpen && (
          <div
            onClick={() => setSidebarOpen(false)}
            className="fixed inset-0 z-20 bg-black/40 backdrop-blur-xs md:hidden"
          />
        )}

        {/* Center Main Content Article */}
        <main className="min-w-0 flex-1 px-4 py-8 sm:px-8 lg:px-12">
          <div className="mx-auto max-w-3xl">
            {/* Breadcrumbs */}
            <nav className="mb-4 flex items-center gap-2 text-xs font-medium text-muted" aria-label="Breadcrumb">
              <Link href="/docs" onClick={() => navigateToDoc(DEFAULT_DOC_SLUG)} className="hover:text-ink">
                Docs
              </Link>
              <ChevronRight size={12} />
              <span>{article.category}</span>
              <ChevronRight size={12} />
              <span className="text-ink font-semibold">{article.title}</span>
            </nav>

            {/* Document Header */}
            <div className="border-b border-line pb-6">
              <h1 className="text-3xl font-extrabold tracking-tight text-ink sm:text-4xl">
                {article.title}
              </h1>
              {article.summary && (
                <p className="mt-3 text-base leading-relaxed text-muted">
                  {article.summary}
                </p>
              )}

              {/* Action Toolbar */}
              <div className="mt-5 flex flex-wrap items-center justify-between gap-3 text-xs text-muted border-t border-line/60 pt-4">
                <div className="flex items-center gap-4">
                  <span className="flex items-center gap-1.5">
                    <Clock size={13} className="text-muted" />
                    <span>{estimateReadTime(article.content)} min read</span>
                  </span>
                  <span>•</span>
                  <span>Updated with repository source</span>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={handleCopyMarkdown}
                    className="flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1 text-xs font-medium text-muted transition hover:bg-hover hover:text-ink"
                    aria-label="Copy markdown text"
                  >
                    {copiedMd ? <Check size={12} className="text-emerald-500" /> : <Copy size={12} />}
                    <span>{copiedMd ? "Copied" : "Copy Markdown"}</span>
                  </button>

                  <a
                    href={`https://github.com/Anil-matcha/open-duet/blob/main/docs/${article.slug.toUpperCase()}.md`}
                    target="_blank"
                    rel="noreferrer"
                    className="flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1 text-xs font-medium text-muted transition hover:bg-hover hover:text-ink"
                  >
                    <span>View source</span>
                    <ExternalLink size={11} className="opacity-70" />
                  </a>
                </div>
              </div>
            </div>

            {/* Markdown Body */}
            <div className="mt-8">
              <DocsMarkdown
                content={article.content}
                onNavigateDoc={navigateToDoc}
                onHeadingsExtracted={setHeadings}
              />
            </div>

            {/* Pagination Navigation Footer */}
            <div className="mt-16 flex flex-col sm:flex-row items-stretch justify-between gap-4 border-t border-line pt-8">
              {prevArticle ? (
                <button
                  type="button"
                  onClick={() => navigateToDoc(prevArticle.slug)}
                  className="flex flex-1 flex-col items-start rounded-xl border border-line p-4 text-left transition hover:border-line-hover hover:bg-hover/30"
                >
                  <span className="flex items-center gap-1 text-xs font-medium text-muted">
                    <ArrowLeft size={12} />
                    <span>Previous</span>
                  </span>
                  <span className="mt-1 font-semibold text-sm text-ink">{prevArticle.title}</span>
                  <span className="text-[11px] text-muted">{prevArticle.category}</span>
                </button>
              ) : (
                <div className="flex-1" />
              )}

              {nextArticle && (
                <button
                  type="button"
                  onClick={() => navigateToDoc(nextArticle.slug)}
                  className="flex flex-1 flex-col items-end rounded-xl border border-line p-4 text-right transition hover:border-line-hover hover:bg-hover/30"
                >
                  <span className="flex items-center gap-1 text-xs font-medium text-muted">
                    <span>Next</span>
                    <ArrowRight size={12} />
                  </span>
                  <span className="mt-1 font-semibold text-sm text-ink">{nextArticle.title}</span>
                  <span className="text-[11px] text-muted">{nextArticle.category}</span>
                </button>
              )}
            </div>
          </div>
        </main>

        {/* Right Sidebar: "On This Page" Table of Contents */}
        <aside className="hidden xl:block w-64 shrink-0 px-6 py-8">
          <div className="sticky top-20">
            <h4 className="text-xs font-bold uppercase tracking-wider text-muted">On this page</h4>
            {headings.length > 0 ? (
              <ul className="mt-3 space-y-2 text-xs">
                {headings.map((h) => {
                  const isActive = activeHeadingId === h.id;
                  return (
                    <li
                      key={h.id}
                      style={{ paddingLeft: `${Math.max(0, (h.level - 2) * 12)}px` }}
                    >
                      <a
                        href={`#${h.id}`}
                        onClick={(e) => {
                          e.preventDefault();
                          const el = document.getElementById(h.id);
                          if (el) {
                            el.scrollIntoView({ behavior: "smooth" });
                            setActiveHeadingId(h.id);
                            window.history.pushState(null, "", `#${h.id}`);
                          }
                        }}
                        className={cn(
                          "block py-0.5 transition-colors leading-normal",
                          isActive
                            ? "font-semibold text-accent"
                            : "text-muted hover:text-ink"
                        )}
                      >
                        {h.text}
                      </a>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="mt-2 text-xs text-muted/60">No sections in this guide.</p>
            )}

            <div className="mt-8 border-t border-line/60 pt-4">
              <a
                href="https://github.com/Anil-matcha/open-duet/issues"
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-1.5 text-xs text-muted hover:text-ink"
              >
                <span>Have feedback on this doc?</span>
                <ExternalLink size={11} className="opacity-70" />
              </a>
            </div>
          </div>
        </aside>
      </div>

      {/* Global Search Dialog Modal */}
      {searchOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-20 bg-black/50 backdrop-blur-xs">
          <div
            className="w-full max-w-xl rounded-2xl border border-line bg-surface shadow-2xl overflow-hidden animate-in fade-in zoom-in-95 duration-150"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Search Input Bar */}
            <div className="flex items-center border-b border-line px-4 py-3">
              <Search size={18} className="text-muted mr-3 shrink-0" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search all guides, features, or setup instructions…"
                autoFocus
                className="w-full bg-transparent text-sm text-ink placeholder:text-muted focus:outline-hidden"
              />
              <button
                type="button"
                onClick={() => setSearchOpen(false)}
                className="rounded-md p-1 text-muted hover:bg-hover hover:text-ink"
                aria-label="Close search"
              >
                <X size={16} />
              </button>
            </div>

            {/* Search Results List */}
            <div className="max-h-96 overflow-y-auto p-2">
              {searchQuery.trim() === "" ? (
                <div className="px-4 py-6 text-center text-xs text-muted">
                  Type a keyword like <code className="text-accent">workflows</code>, <code className="text-accent">gemini</code>, or <code className="text-accent">self-hosting</code> to quickly jump to any document.
                </div>
              ) : searchResults.length === 0 ? (
                <div className="px-4 py-8 text-center text-xs text-muted">
                  No documents found matching &ldquo;{searchQuery}&rdquo;.
                </div>
              ) : (
                <ul className="space-y-1">
                  {searchResults.map((res) => (
                    <li key={res.slug}>
                      <button
                        type="button"
                        onClick={() => navigateToDoc(res.slug)}
                        className="flex w-full flex-col items-start rounded-xl p-3 text-left transition hover:bg-hover"
                      >
                        <div className="flex w-full items-center justify-between">
                          <span className="font-semibold text-sm text-ink">{res.title}</span>
                          <span className="text-[11px] font-medium text-accent uppercase tracking-wider">{res.category}</span>
                        </div>
                        <p className="mt-1 line-clamp-2 text-xs text-muted leading-relaxed">
                          {res.summary}
                        </p>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default function DocsPage() {
  return (
    <Suspense fallback={<DocsLoadingSkeleton />}>
      <DocsView />
    </Suspense>
  );
}
