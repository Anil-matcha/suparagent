"use client";

import React, { Suspense, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import {
  BookOpen,
  Rocket,
  Sparkles,
  Cpu,
  Monitor,
  PlugZap,
  Smartphone,
  Server,
  ShieldCheck,
  Wrench,
  Clock,
  GitPullRequest,
  Search,
  Menu,
  X,
  ChevronRight,
  ChevronDown,
  ExternalLink,
  Copy,
  Check,
  ArrowLeft,
  ArrowRight,
  FileText,
} from "lucide-react";
import { cn } from "@/lib/cn";
import {
  DOC_CATEGORIES,
  DOC_ARTICLES,
  DEFAULT_DOC_SLUG,
  type DocArticle,
} from "@/lib/docs-data";
import { DocsMarkdown, type HeadingItem } from "@/components/docs/DocsMarkdown";
import { ThemeToggle } from "@/components/ui/ThemeToggle";

const CATEGORY_ICONS: Record<string, React.ComponentType<{ size?: number; className?: string }>> = {
  Rocket,
  Sparkles,
  Cpu,
  Monitor,
  PlugZap,
  Smartphone,
  Server,
  ShieldCheck,
  Wrench,
  Clock,
  GitPullRequest,
  BookOpen,
  FileText,
};

function estimateReadTime(text: string): number {
  const words = text.trim().split(/\s+/).length;
  return Math.max(1, Math.round(words / 200));
}

function DocsLoadingSkeleton() {
  return (
    <div className="flex h-screen w-full items-center justify-center bg-surface text-muted">
      <div className="flex items-center gap-3">
        <Sparkles size={20} className="animate-spin text-emerald-500" />
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
  const [copyFeedback, setCopyFeedback] = useState<{ slug: string; status: "copied" | "failed" } | null>(null);
  const [copyingMd, setCopyingMd] = useState(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [openDropdown, setOpenDropdown] = useState(false);

  // Derive activeSlug directly from searchParams or user selection
  const activeSlug =
    paramSlug && DOC_ARTICLES[paramSlug.toLowerCase()]
      ? paramSlug.toLowerCase()
      : selectedSlug && DOC_ARTICLES[selectedSlug.toLowerCase()]
      ? selectedSlug.toLowerCase()
      : DEFAULT_DOC_SLUG;

  const article: DocArticle = DOC_ARTICLES[activeSlug] || DOC_ARTICLES[DEFAULT_DOC_SLUG];
  const copiedMd = copyFeedback?.slug === activeSlug && copyFeedback.status === "copied";
  const copyFailed = copyFeedback?.slug === activeSlug && copyFeedback.status === "failed";

  useEffect(() => () => { if (copyTimer.current) clearTimeout(copyTimer.current); }, []);

  // Accordion state for categories: expand the active one by default
  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    for (const cat of DOC_CATEGORIES) {
      const containsActive = cat.items.some((i) => i.slug === activeSlug);
      initial[cat.id] = containsActive || cat.id === "getting-started" || cat.id === "features";
    }
    return initial;
  });

  const toggleCategory = (id: string) => {
    setExpandedCategories((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  // Navigate doc helper
  const navigateToDoc = (slug: string) => {
    const clean = slug.toLowerCase();
    if (DOC_ARTICLES[clean]) {
      setSelectedSlug(clean);
      setSidebarOpen(false);
      setSearchOpen(false);
      setOpenDropdown(false);
      window.history.pushState(null, "", `/docs?doc=${clean}`);
      window.scrollTo({ top: 0, behavior: "smooth" });

      // Ensure its parent category is expanded
      for (const cat of DOC_CATEGORIES) {
        if (cat.items.some((i) => i.slug === clean)) {
          setExpandedCategories((prev) => ({ ...prev, [cat.id]: true }));
          break;
        }
      }
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
        setOpenDropdown(false);
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
      .slice(0, 10);
  }, [searchQuery]);

  // Previous and Next article pagination
  const allArticlesList = useMemo(() => {
    const list: { slug: string; title: string; category: string }[] = [];
    if (DOC_ARTICLES["index"]) {
      list.push({ slug: "index", title: DOC_ARTICLES["index"].title, category: "Overview" });
    }
    for (const cat of DOC_CATEGORIES) {
      for (const item of cat.items) {
        if (item.slug !== "index") {
          list.push({ slug: item.slug, title: item.title, category: cat.name });
        }
      }
    }
    return list;
  }, []);

  const currentIndex = allArticlesList.findIndex((a) => a.slug === activeSlug);
  const prevArticle = currentIndex > 0 ? allArticlesList[currentIndex - 1] : null;
  const nextArticle = currentIndex < allArticlesList.length - 1 ? allArticlesList[currentIndex + 1] : null;

  const handleCopyMarkdown = async () => {
    if (copyTimer.current) clearTimeout(copyTimer.current);
    setCopyFeedback(null);
    setCopyingMd(true);
    try {
      await navigator.clipboard.writeText(article.content);
      setCopyFeedback({ slug: activeSlug, status: "copied" });
      copyTimer.current = setTimeout(() => setCopyFeedback(null), 2000);
    } catch {
      setCopyFeedback({ slug: activeSlug, status: "failed" });
    } finally {
      setCopyingMd(false);
    }
  };

  return (
    <div className="min-h-screen bg-surface text-ink antialiased scroll-smooth">
      {/* Top Header */}
      <header className="sticky top-0 z-40 flex h-14 w-full items-center justify-between border-b border-line bg-surface/95 px-4 backdrop-blur-md">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setSidebarOpen((prev) => !prev)}
            className="flex items-center justify-center rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink md:hidden cursor-pointer"
            aria-label="Toggle navigation menu"
          >
            {sidebarOpen ? <X size={18} /> : <Menu size={18} />}
          </button>

          <Link href="/chat/" className="flex items-center gap-2.5 font-semibold text-ink">
            <span className="flex size-7 items-center justify-center rounded-lg bg-emerald-600 text-white shadow-xs font-bold text-sm">
              S
            </span>
            <span className="font-semibold text-sm tracking-tight hidden sm:inline">Suparagent</span>
            <span className="rounded-md bg-emerald-500/10 px-1.5 py-0.5 text-[11px] font-semibold text-emerald-500 border border-emerald-500/20">
              DOCS
            </span>
          </Link>
        </div>

        {/* Global Quick Search Pill */}
        <div className="flex-1 max-w-md mx-4 hidden sm:block">
          <button
            type="button"
            onClick={() => setSearchOpen(true)}
            className="flex w-full items-center justify-between rounded-lg border border-line bg-surface-muted/50 px-3 py-1.5 text-xs text-muted transition hover:border-line-hover hover:bg-surface-muted cursor-pointer"
          >
            <span className="flex items-center gap-2">
              <Search size={14} className="text-muted" />
              <span>Search documentation…</span>
            </span>
            <kbd className="rounded border border-line bg-surface px-1.5 py-0.5 font-mono text-[10px] text-muted shadow-2xs">
              Ctrl K
            </kbd>
          </button>
        </div>

        {/* Right header actions */}
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setSearchOpen(true)}
            className="rounded-lg p-1.5 text-muted hover:bg-hover hover:text-ink sm:hidden cursor-pointer"
            aria-label="Search"
          >
            <Search size={18} />
          </button>

          <Link
            href="/chat/"
            className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-muted transition hover:bg-hover hover:text-ink"
          >
            <ArrowLeft size={13} />
            <span>Chat</span>
          </Link>

          <Link
            href="/settings/"
            className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium text-muted transition hover:bg-hover hover:text-ink"
          >
            Settings
          </Link>
        </div>
      </header>

      {/* Main Body Layout */}
      <div className="mx-auto flex max-w-[90rem]">
        {/* Left Sidebar Navigation (Sticky, never scrolling off) */}
        <aside
          className={cn(
            "fixed inset-y-14 left-0 z-30 flex w-72 flex-col border-r border-line bg-surface transition-transform duration-200 md:sticky md:top-14 md:h-[calc(100vh-3.5rem)]",
            sidebarOpen ? "translate-x-0 shadow-2xl" : "-translate-x-full md:translate-x-0"
          )}
        >
          {/* Navigation Tree (Scrollable) */}
          <div className="flex-1 overflow-y-auto p-3 space-y-3.5">
            {/* Overview / Docs Home */}
            <div>
              <button
                type="button"
                onClick={() => navigateToDoc("index")}
                className={cn(
                  "flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-xs font-semibold transition-colors cursor-pointer",
                  activeSlug === "index"
                    ? "bg-emerald-500/10 text-emerald-500 border-l-2 border-emerald-500"
                    : "text-ink/80 hover:bg-hover hover:text-ink"
                )}
              >
                <BookOpen size={14} className={activeSlug === "index" ? "text-emerald-500" : "text-muted"} />
                <span>Suparagent documentation</span>
              </button>
            </div>

            {/* Categorized Folders */}
            {DOC_CATEGORIES.map((category) => {
              const Icon = CATEGORY_ICONS[category.icon] || FileText;
              const isExpanded = !!expandedCategories[category.id];
              const hasActiveChild = category.items.some((i) => i.slug === activeSlug);

              return (
                <div key={category.id} className="space-y-1">
                  <button
                    type="button"
                    onClick={() => toggleCategory(category.id)}
                    className={cn(
                      "flex w-full items-center justify-between rounded-md px-2 py-1 text-left text-xs font-semibold transition-colors cursor-pointer",
                      hasActiveChild ? "text-ink font-bold" : "text-muted hover:text-ink"
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Icon size={14} className={hasActiveChild ? "text-emerald-500" : "text-muted"} />
                      <span>{category.name}</span>
                    </div>
                    <ChevronDown
                      size={13}
                      className={cn("text-muted transition-transform duration-150", !isExpanded && "-rotate-90")}
                    />
                  </button>

                  {isExpanded && (
                    <ul className="ml-2.5 space-y-0.5 border-l border-line/60 pl-2">
                      {category.items.map((item) => {
                        const isActive = item.slug === activeSlug;
                        return (
                          <li key={item.slug}>
                            <button
                              type="button"
                              onClick={() => navigateToDoc(item.slug)}
                              className={cn(
                                "flex w-full items-center justify-between rounded-md px-2 py-1 text-left text-[12.5px] transition-colors cursor-pointer",
                                isActive
                                  ? "bg-emerald-500/10 text-emerald-500 font-semibold border-l-2 -ml-[9px] pl-2 border-emerald-500"
                                  : "text-muted hover:bg-hover hover:text-ink"
                              )}
                            >
                              <span className="truncate">{item.title}</span>
                              {isActive && <ChevronRight size={12} className="shrink-0 text-emerald-500" />}
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              );
            })}
          </div>

          {/* Sticky Sidebar Footer */}
          <div className="flex items-center justify-between border-t border-line/60 p-3 text-xs text-muted">
            <a
              href="https://github.com/Anil-matcha/suparagent"
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 transition hover:text-ink"
            >
              <ExternalLink size={12} />
              <span>GitHub</span>
            </a>
            <ThemeToggle iconOnly />
          </div>
        </aside>

        {/* Mobile Backdrop */}
        {sidebarOpen && (
          <div
            onClick={() => setSidebarOpen(false)}
            className="fixed inset-0 z-20 bg-black/40 backdrop-blur-xs md:hidden"
          />
        )}

        {/* Center Main Article Content */}
        <main className="min-w-0 flex-1 px-5 py-8 sm:px-8 lg:px-12 max-w-4xl">
          {/* Breadcrumbs */}
          <nav className="mb-5 flex items-center gap-2 text-xs font-medium text-muted" aria-label="Breadcrumb">
            <button
              type="button"
              onClick={() => navigateToDoc("index")}
              className="hover:text-ink cursor-pointer"
            >
              Docs
            </button>
            <ChevronRight size={12} />
            <span>{article.category}</span>
            <ChevronRight size={12} />
            <span className="text-ink font-semibold">{article.title}</span>
          </nav>

          {/* Article Header & Actions */}
          <div className="border-b border-line pb-6 mb-6">
            <h1 className="text-3xl font-extrabold tracking-tight text-ink sm:text-4xl">
              {article.title}
            </h1>
            {article.summary && (
              <p className="mt-3 text-base leading-relaxed text-muted">
                {article.summary}
              </p>
            )}

            {/* Action Bar matching Image 2 */}
            <div className="mt-5 flex flex-wrap items-center justify-between gap-3 text-xs text-muted border-t border-line/60 pt-4">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleCopyMarkdown}
                  disabled={copyingMd}
                  className="flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1 text-xs font-medium text-muted transition hover:bg-hover hover:text-ink cursor-pointer"
                  aria-label="Copy markdown text"
                >
                  {copiedMd ? <Check size={12} className="text-emerald-500" /> : <Copy size={12} />}
                  <span>{copiedMd ? "Copied" : "Copy Markdown"}</span>
                </button>
                <span role="status" className={copyFailed ? "text-danger" : "sr-only"}>{copyFailed ? "Could not copy Markdown. Try again." : copiedMd ? "Markdown copied." : ""}</span>

                <div className="relative">
                  <button
                    type="button"
                    onClick={() => setOpenDropdown((prev) => !prev)}
                    className="flex items-center gap-1.5 rounded-md border border-line bg-surface px-2.5 py-1 text-xs font-medium text-muted transition hover:bg-hover hover:text-ink cursor-pointer"
                  >
                    <span>Open</span>
                    <ChevronDown size={11} className={cn("transition-transform", openDropdown && "rotate-180")} />
                  </button>

                  {openDropdown && (
                    <div className="absolute left-0 mt-1.5 w-44 rounded-lg border border-line bg-surface p-1 shadow-lg z-20 text-xs">
                      <a
                        href={`https://github.com/Anil-matcha/suparagent/tree/main/docs`}
                        target="_blank"
                        rel="noreferrer"
                        className="flex items-center justify-between rounded px-2.5 py-1.5 text-muted hover:bg-hover hover:text-ink"
                      >
                        <span>GitHub Docs</span>
                        <ExternalLink size={11} />
                      </a>
                      <button
                        type="button"
                        onClick={handleCopyMarkdown}
                        className="flex w-full items-center justify-between rounded px-2.5 py-1.5 text-muted hover:bg-hover hover:text-ink text-left cursor-pointer"
                      >
                        <span>Raw Markdown</span>
                        <Copy size={11} />
                      </button>
                    </div>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-4 text-xs text-muted">
                <span className="flex items-center gap-1.5">
                  <Clock size={12} />
                  <span>{estimateReadTime(article.content)} min read</span>
                </span>
                <span>•</span>
                <span>Live reference</span>
              </div>
            </div>
          </div>

          {/* Article Markdown Body */}
          <div className="mt-6">
            <DocsMarkdown
              content={article.content}
              articleTitle={article.title}
              onNavigateDoc={navigateToDoc}
              onHeadingsExtracted={setHeadings}
            />
          </div>

          {/* Article Pagination (Previous / Next) */}
          <div className="mt-14 border-t border-line pt-8">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {prevArticle ? (
                <button
                  type="button"
                  onClick={() => navigateToDoc(prevArticle.slug)}
                  className="group flex flex-col rounded-xl border border-line bg-surface-muted/20 p-4 text-left transition hover:border-emerald-500/50 hover:bg-surface-muted/50 cursor-pointer"
                >
                  <span className="flex items-center gap-1 text-xs text-muted">
                    <ArrowLeft size={12} className="transition-transform group-hover:-translate-x-0.5" />
                    <span>Previous</span>
                  </span>
                  <span className="mt-1 text-sm font-semibold text-ink group-hover:text-emerald-500 transition-colors">
                    {prevArticle.title}
                  </span>
                  <span className="text-[11px] text-muted">{prevArticle.category}</span>
                </button>
              ) : (
                <div />
              )}

              {nextArticle ? (
                <button
                  type="button"
                  onClick={() => navigateToDoc(nextArticle.slug)}
                  className="group flex flex-col items-end rounded-xl border border-line bg-surface-muted/20 p-4 text-right transition hover:border-emerald-500/50 hover:bg-surface-muted/50 cursor-pointer"
                >
                  <span className="flex items-center gap-1 text-xs text-muted">
                    <span>Next</span>
                    <ArrowRight size={12} className="transition-transform group-hover:translate-x-0.5" />
                  </span>
                  <span className="mt-1 text-sm font-semibold text-ink group-hover:text-emerald-500 transition-colors">
                    {nextArticle.title}
                  </span>
                  <span className="text-[11px] text-muted">{nextArticle.category}</span>
                </button>
              ) : (
                <div />
              )}
            </div>
          </div>
        </main>

        {/* Right Sticky Table of Contents ("On this page") */}
        <aside className="hidden lg:block w-64 shrink-0 pl-6 pr-4 py-8">
          <div className="sticky top-20 max-h-[calc(100vh-6rem)] overflow-y-auto space-y-3">
            <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted">
              <span>≡</span>
              <span>On this page</span>
            </div>

            {headings.length === 0 ? (
              <p className="text-xs text-muted/60 italic">Overview</p>
            ) : (
              <ul className="space-y-1 text-xs">
                {headings.map((h) => {
                  const isActive = activeHeadingId === h.id;
                  return (
                    <li
                      key={h.id}
                      style={{ paddingLeft: `${Math.max(0, h.level - 2) * 0.75}rem` }}
                    >
                      <a
                        href={`#${h.id}`}
                        onClick={(e) => {
                          e.preventDefault();
                          const el = document.getElementById(h.id);
                          if (el) {
                            el.scrollIntoView({ behavior: "smooth", block: "start" });
                            setActiveHeadingId(h.id);
                            window.history.pushState(null, "", `#${h.id}`);
                          }
                        }}
                        className={cn(
                          "block py-1 transition-colors border-l-2 pl-2.5 cursor-pointer",
                          isActive
                            ? "border-emerald-500 text-emerald-500 font-semibold"
                            : "border-transparent text-muted hover:text-ink hover:border-line"
                        )}
                      >
                        {h.text}
                      </a>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </aside>
      </div>

      {/* Instant Search Modal (Ctrl+K or /) */}
      {searchOpen && (
        <div className="fixed inset-0 z-50 flex items-start justify-center bg-black/60 p-4 pt-20 backdrop-blur-xs">
          <div
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-xl overflow-hidden rounded-2xl border border-line bg-surface shadow-2xl"
          >
            <div className="flex items-center border-b border-line px-4 py-3">
              <Search size={16} className="text-muted" />
              <input
                type="text"
                autoFocus
                placeholder="Search documentation, guides, engines…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="ml-3 flex-1 bg-transparent text-sm text-ink outline-none placeholder:text-muted"
              />
              <kbd className="rounded border border-line bg-surface-muted px-1.5 py-0.5 font-mono text-[10px] text-muted">
                ESC
              </kbd>
            </div>

            <div className="max-h-96 overflow-y-auto p-2">
              {searchQuery.trim() === "" ? (
                <div className="p-4 text-center text-xs text-muted">
                  Type words to search across all guides, engines, and features.
                </div>
              ) : searchResults.length === 0 ? (
                <div className="p-6 text-center text-xs text-muted">
                  No matching documentation found for &ldquo;{searchQuery}&rdquo;.
                </div>
              ) : (
                <ul className="space-y-1">
                  {searchResults.map((res) => (
                    <li key={res.slug}>
                      <button
                        type="button"
                        onClick={() => navigateToDoc(res.slug)}
                        className="flex w-full flex-col rounded-lg px-3 py-2 text-left transition hover:bg-hover cursor-pointer"
                      >
                        <div className="flex items-center justify-between text-xs">
                          <span className="font-semibold text-ink">{res.title}</span>
                          <span className="rounded bg-surface-muted px-1.5 py-0.5 text-[10px] text-muted">
                            {res.category}
                          </span>
                        </div>
                        {res.summary && (
                          <span className="mt-1 line-clamp-1 text-xs text-muted">
                            {res.summary}
                          </span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
          <div
            onClick={() => setSearchOpen(false)}
            className="fixed inset-0 -z-10"
          />
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
