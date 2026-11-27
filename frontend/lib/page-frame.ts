// Builds the srcdoc of a bot page frame. The Content-Security-Policy meta must be the first element the parser sees, so
// whatever the page puts before its content (doctype, byte order mark, comments, bogus comments) is removed first.
import { buildHelperScript, type PageTheme } from "./page-helper.ts";

const CDNS = "https://cdnjs.cloudflare.com https://cdn.jsdelivr.net https://unpkg.com";

export const PAGE_CSP = [
  "default-src 'none'",
  `script-src 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' ${CDNS}`,
  `style-src 'unsafe-inline' ${CDNS} https://fonts.googleapis.com`,
  `font-src data: ${CDNS} https://fonts.gstatic.com`,
  `img-src data: blob: ${CDNS}`,
  "media-src data: blob:",
  `connect-src ${CDNS}`,
  "worker-src blob:",
  "frame-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "manifest-src 'none'",
].join("; ");

export function escapeAttribute(value: string): string {
  return value.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// Removes, repeatedly, everything that may precede the content: whitespace, BOMs, doctypes, comments and the other
// "bogus comment" forms (<!x>, <?x>, </ followed by a non-letter) the HTML parser treats as comments.
export function stripPreamble(html: string): string {
  let rest = html;
  for (;;) {
    const before = rest;
    rest = rest.replace(/^[\s﻿\u0000]+/, "");
    if (rest.startsWith("<!--")) {
      const end = rest.indexOf("-->", 4);
      // "<!-->" and "<!--->" are complete (empty) comments in HTML.
      const short = /^<!-{2,3}>/.exec(rest);
      rest = short ? rest.slice(short[0].length) : end === -1 ? "" : rest.slice(end + 3);
    } else if (/^<![^>]*>?/.test(rest) || /^<\?[^>]*>?/.test(rest) || /^<\/[^a-zA-Z>][^>]*>?/.test(rest) || rest.startsWith("</>")) {
      rest = rest.replace(/^<[!?/][^>]*>?/, "");
    }
    if (rest === before) return rest;
  }
}

const FONT_SANS = "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif";
const FONT_MONO = "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";

export function themeCss(theme: PageTheme): string {
  return `:root{color-scheme:light dark}:root{color-scheme:${theme}}`
    + ":root{"
    + "--color-text-primary:light-dark(#1c1b19,#ececee);"
    + "--color-text-secondary:light-dark(#5b574f,#a3a3ab);"
    + "--color-background-primary:light-dark(#ffffff,#1e1e21);"
    + "--color-background-secondary:light-dark(#f7f6f3,#151517);"
    + "--color-border-primary:light-dark(#dcd8cf,#2f2f35);"
    + `--font-sans:${FONT_SANS};--font-mono:${FONT_MONO}}`
    + "body{margin:0;padding:12px;color:var(--color-text-primary);background:transparent;font-family:var(--font-sans)}";
}

export function buildSrcdoc({ html, theme, uses }: { html: string; theme: PageTheme; uses: readonly string[] }): string {
  return `<!DOCTYPE html><meta http-equiv="Content-Security-Policy" content="${escapeAttribute(PAGE_CSP)}">`
    + `<style>${themeCss(theme)}</style>${buildHelperScript(theme, uses)}${stripPreamble(html)}`;
}
