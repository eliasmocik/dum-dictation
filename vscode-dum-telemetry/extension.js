// dum dictation Telemetry — VS Code extension (Phase 1, measurement only).
//
// WHY: in VS Code (Electron) macOS accessibility can't read the editor, so dum's post-commit
// "did the user fix it?" signal falls back to a keystroke proxy that can't tell a correction from
// normal coding. This extension closes that gap using the one thing only an extension has: the
// document model. When dum inserts a dictation it announces {commit_id, text} over a file bridge;
// we locate the inserted span, watch it for a window, and write the EXACT edit distance back as a
// user.refix event (capture_method="vscode-ext") that analyze_user_corrections.py joins by commit_id.
//
// It NEVER inserts or modifies text — observation only.

const vscode = require("vscode");
const fs = require("fs");
const os = require("os");
const path = require("path");

const BRIDGE = path.join(os.homedir(), ".dum", "vscode-bridge.jsonl");
const WINDOW_MS = 20000;        // must match dogfood_log OBSERVE_WINDOW_S
const LOCATE_BACK = 4000;       // how far back from the cursor to search for the inserted text

let stats = { announced: 0, observed: 0, written: 0, missed: 0 };
const active = new Map();        // commit_id -> observation

function levenshtein(a, b) {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;
  let prev = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i++) {
    let cur = [i];
    for (let j = 1; j <= b.length; j++) {
      cur.push(Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] !== b[j - 1] ? 1 : 0)));
    }
    prev = cur;
  }
  return prev[b.length];
}

function writeRefix(obs, region) {
  const dist = levenshtein(obs.text, region);
  const evt = {
    type: "user.refix",
    commit_id: obs.commitId,
    commit_app: "Code",
    edit_capture: "ok",
    capture_method: "vscode-ext",
    accepted_unchanged: dist === 0,
    edit_distance: dist,
    normalized: Math.round((dist / Math.max(1, obs.text.length)) * 1000) / 1000,
    edited_span: obs.edited,
    ts: Date.now() / 1000,
  };
  try {
    fs.mkdirSync(obs.sessionsDir, { recursive: true });
    fs.appendFileSync(path.join(obs.sessionsDir, `vscode-ext-${obs.session}.jsonl`), JSON.stringify(evt) + "\n");
    stats.written++;
  } catch (e) { /* never throw from telemetry */ }
}

// Locate the just-inserted text in the active editor and start an observation.
function observe(announce) {
  const ed = vscode.window.activeTextEditor;
  if (!ed) { stats.missed++; return; }
  const doc = ed.document;
  const text = announce.text || "";
  if (!text) return;
  const cursor = doc.offsetAt(ed.selection.active);
  const full = doc.getText();

  // The text was just typed at the cursor, so it should sit immediately before it. Verify; if a
  // dead-key/layout quirk shifted it, fall back to the nearest occurrence ending at/near the cursor.
  let start = cursor - text.length;
  if (start < 0 || full.slice(start, cursor) !== text) {
    const from = Math.max(0, cursor - LOCATE_BACK);
    const idx = full.lastIndexOf(text, cursor);
    if (idx < from || idx === -1) { stats.missed++; return; }   // can't locate -> let the keystroke proxy stand
    start = idx;
  }
  const obs = {
    commitId: announce.commit_id, sessionsDir: announce.sessions_dir, session: announce.session,
    docUri: doc.uri.toString(), text, spanStart: start, spanEnd: start + text.length, edited: false,
  };
  active.set(obs.commitId, obs);
  stats.observed++;
  setTimeout(() => {
    active.delete(obs.commitId);
    let region = obs.text;
    try {
      const d = vscode.workspace.textDocuments.find((x) => x.uri.toString() === obs.docUri);
      if (d) {
        const full2 = d.getText();
        region = full2.slice(obs.spanStart, Math.max(obs.spanStart, Math.min(obs.spanEnd, full2.length)));
      }
    } catch (e) { /* fall back to assuming unchanged */ }
    writeRefix(obs, region);
  }, WINDOW_MS);
}

// Keep each observation's span aligned as the user edits elsewhere, and flag edits that hit the span.
function onDocChange(e) {
  for (const obs of active.values()) {
    if (e.document.uri.toString() !== obs.docUri) continue;
    for (const ch of e.contentChanges) {
      const cs = ch.rangeOffset, ce = ch.rangeOffset + ch.rangeLength;
      const delta = ch.text.length - ch.rangeLength;
      if (ce <= obs.spanStart) { obs.spanStart += delta; obs.spanEnd += delta; }     // edit before span
      else if (cs >= obs.spanEnd) { /* edit after span — ignore */ }
      else { obs.edited = true; obs.spanEnd = Math.max(obs.spanStart, obs.spanEnd + delta); }  // edit IN span
    }
  }
}

// --- bridge tail: read only NEW lines appended after activation (ignore history) ---
let offset = 0;
function drainBridge() {
  let fd;
  try {
    const st = fs.statSync(BRIDGE);
    if (st.size < offset) offset = 0;            // file rotated/truncated
    if (st.size === offset) return;
    fd = fs.openSync(BRIDGE, "r");
    const buf = Buffer.alloc(st.size - offset);
    fs.readSync(fd, buf, 0, buf.length, offset);
    offset = st.size;
    for (const line of buf.toString("utf8").split("\n")) {
      if (!line.trim()) continue;
      try { const a = JSON.parse(line); stats.announced++; observe(a); } catch (e) { /* skip bad line */ }
    }
  } catch (e) { /* bridge file may not exist yet */ }
  finally { if (fd !== undefined) try { fs.closeSync(fd); } catch (e) {} }
}

function activate(context) {
  try { offset = fs.statSync(BRIDGE).size; } catch (e) { offset = 0; }   // start at the tail
  try { fs.mkdirSync(path.dirname(BRIDGE), { recursive: true }); } catch (e) {}

  // watch the bridge file for appends; fall back to a slow poll if fs.watch misses events
  try { fs.watch(path.dirname(BRIDGE), () => drainBridge()); } catch (e) {}
  const poll = setInterval(drainBridge, 1000);

  context.subscriptions.push(
    vscode.workspace.onDidChangeTextDocument(onDocChange),
    vscode.commands.registerCommand("dum.telemetry.status", () => {
      vscode.window.showInformationMessage(
        `dum telemetry — announced ${stats.announced}, observed ${stats.observed}, ` +
        `written ${stats.written}, missed ${stats.missed}. Bridge: ${BRIDGE}`);
    }),
    { dispose: () => clearInterval(poll) }
  );
}

function deactivate() {}

module.exports = { activate, deactivate };
