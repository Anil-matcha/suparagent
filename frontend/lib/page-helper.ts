// The in-page helper, `window.suparagent`, injected into every bot page. It is a plain ES2017 string (it runs inside
// a sandboxed frame with an opaque origin, so it can import nothing). Every call is a JSON-RPC 2.0 style message to the
// host page, which decides what to allow (lib/page-bridge.ts). Outside an iframe it does nothing.

export type PageTheme = "light" | "dark";

// `<` is escaped so a value can never close the script element it is placed in.
function literal(value: unknown): string {
  return JSON.stringify(value).replace(/</g, "\\u003c").replace(/[\u2028\u2029]/g, (c) => (c === "\u2028" ? "\\u2028" : "\\u2029"));
}

const HELPER_BODY = `
  if (window.parent === window) return;
  var seq = 0, pending = {}, lastHeight = -1, allowed = {};
  uses.forEach(function (u) { allowed[u] = true; });
  function post(message) { window.parent.postMessage(message, "*"); }
  function call(method, params) {
    return new Promise(function (resolve, reject) {
      var id = "sp:" + (++seq);
      pending[id] = { resolve: resolve, reject: reject };
      post({ jsonrpc: "2.0", id: id, method: method, params: params });
    });
  }
  window.addEventListener("message", function (event) {
    if (event.source !== window.parent) return;
    var m = event.data;
    if (!m || m.jsonrpc !== "2.0" || typeof m.id !== "string" || !pending[m.id]) return;
    var p = pending[m.id];
    delete pending[m.id];
    if (m.error) p.reject(new Error(String((m.error && m.error.message) || "Request refused")));
    else p.resolve(m.result);
  });
  function needState() {
    if (!allowed.state) return Promise.reject(new Error("This page did not declare the 'state' capability, so it cannot store data."));
  }
  var anyConnector = uses.some(function (u) { return u.indexOf(":") > 0; });
  function needConnectors() {
    if (!anyConnector) return Promise.reject(new Error("This page did not declare any connected-app tools in 'uses' (for example 'gmail:GMAIL_FETCH_EMAILS'), so it cannot use connected apps."));
  }
  function needTool(toolkit, tool) {
    var key = String(toolkit) + ":" + String(tool);
    if (!allowed[key]) return Promise.reject(new Error("This page did not declare '" + key + "' in 'uses', so it cannot call that tool."));
  }
  var api = {
    send: function (text) { return call("ui/message", { text: String(text) }); },
    open: function (url) { return call("ui/open-link", { url: String(url) }); },
    setContext: function (text) { return call("ui/update-model-context", { text: String(text) }); },
    theme: theme,
    connectors: {
      list: function () { return needConnectors() || call("suparagent/connectors/list", {}); },
      call: function (toolkit, tool, args) {
        return needConnectors() || needTool(toolkit, tool) ||
          call("suparagent/connectors/call", { toolkit: String(toolkit), tool: String(tool), arguments: args === undefined ? {} : args });
      }
    },
    state: {
      get: function (key) { return needState() || call("state/get", { key: String(key) }); },
      set: function (key, value) { return needState() || call("state/set", { key: String(key), value: value }); },
      keys: function () { return needState() || call("state/keys", {}); }
    }
  };
  Object.freeze(api.state);
  Object.freeze(api.connectors);
  Object.freeze(api);
  Object.defineProperty(window, "suparagent", { value: api, writable: false, configurable: false, enumerable: true });
  function measure() {
    var html = document.documentElement, body = document.body;
    if (!html || !body) return;
    var h0 = html.style.height, b0 = body.style.height;
    html.style.height = "max-content";
    body.style.height = "max-content";
    var h = Math.ceil(Math.max(html.scrollHeight, body.scrollHeight));
    html.style.height = h0;
    body.style.height = b0;
    if (h !== lastHeight) { lastHeight = h; post({ jsonrpc: "2.0", method: "size-changed", params: { height: h } }); }
  }
  function start() {
    measure();
    if (window.ResizeObserver) { var ro = new ResizeObserver(measure); ro.observe(document.documentElement); ro.observe(document.body); }
    window.addEventListener("load", measure);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
`;

export function buildHelperScript(theme: PageTheme, uses: readonly string[]): string {
  return `<script>(function(theme,uses){"use strict";${HELPER_BODY}})(${literal(theme)},${literal(uses)});</script>`;
}
