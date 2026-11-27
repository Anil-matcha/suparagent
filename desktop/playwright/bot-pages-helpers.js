// Helpers for bot-pages.spec.js: writing rows into the app's own database (there is no engine here to show a page),
// and a second Electron start that hands a suparagent:// link to the one already running.
const { spawn, execFileSync } = require("node:child_process");

const { paths } = require("../e2e/app");
const { findElectronBinary } = require("../src/preflight");

const PY_APPEND = `
import json, sqlite3, sys
db, thread_id, events = sys.argv[1], int(sys.argv[2]), json.loads(sys.argv[3])
c = sqlite3.connect(db, timeout=30, isolation_level=None)
c.execute("BEGIN IMMEDIATE")
(last,) = c.execute("select last_seq from thread where id=?", (thread_id,)).fetchone()
for data in events:
    last += 1
    c.execute("insert into event(thread_id, turn_id, seq, type, data) values(?,?,?,?,?)",
              (thread_id, None, last, "page.shown", json.dumps(data)))
c.execute("update thread set last_seq=?, updated_at=strftime('%Y-%m-%d %H:%M:%f','now') where id=?", (last, thread_id))
c.execute("COMMIT")
print(last)
`;

const PY_QUERY = `
import json, sqlite3, sys
c = sqlite3.connect(sys.argv[1], timeout=30)
c.row_factory = sqlite3.Row
print(json.dumps([dict(r) for r in c.execute(sys.argv[2], json.loads(sys.argv[3]))]))
`;

// The way the backend does it (thread_store.add_event): the next gap-free seq, thread.last_seq moved with it.
function appendPageEvents(db, threadId, pages) {
  return Number(execFileSync("python3", ["-I", "-c", PY_APPEND, db, String(threadId), JSON.stringify(pages)], { encoding: "utf8" }));
}

function query(db, sql, params = []) {
  return JSON.parse(execFileSync("python3", ["-I", "-c", PY_QUERY, db, sql, JSON.stringify(params)], { encoding: "utf8" }));
}

// What a Windows or Linux launcher does: start Electron again with the link as an argument. The running app owns the
// single-instance lock on this data folder, so the new process hands the link over and exits.
function sendLink({ dataDir, link, env = {} }) {
  const binary = findElectronBinary(paths.desktop);
  const environment = { ...process.env, SUPARAGENT_DATA_DIR: dataDir, ...env };
  delete environment.ELECTRON_RUN_AS_NODE;
  const child = spawn(binary, [paths.desktop, "--mode=dev", link], { env: environment, stdio: "ignore", detached: process.platform !== "win32" });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      try { process.kill(child.pid, "SIGKILL"); } catch { /* already gone */ }
      reject(new Error("The second start did not hand the link over and exit."));
    }, 30_000);
    child.once("exit", (code) => { clearTimeout(timer); resolve(code); });
    child.once("error", (error) => { clearTimeout(timer); reject(error); });
  });
}

function plutilLint(file) {
  return execFileSync("plutil", ["-lint", file], { encoding: "utf8" });
}

module.exports = { appendPageEvents, query, sendLink, plutilLint };
