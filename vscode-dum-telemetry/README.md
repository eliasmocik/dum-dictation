# dum dictation Telemetry — VS Code extension (Phase 1: measurement only)

Closes the **VS Code coverage gap** in dum's dogfood telemetry. In VS Code (Electron) macOS
accessibility can't read the editor, so dum's "did the user fix the dictation?" signal falls back
to a keystroke proxy that can't tell a *correction* from *normal coding* — inflating the apparent
correction rate. This extension uses the one thing only an extension has, the **document model**, to
measure the exact post-commit edit.

**It only observes. It never inserts or modifies text.** (That's Phase 2, if ever.)

## How it works
1. dum, on each editor-surface commit, appends `{commit_id, text, ts, sessions_dir, session}` to
   `~/.dum/vscode-bridge.jsonl` (gated by `DUM_VSCODE_BRIDGE=1`).
2. This extension tails that file, locates the just-inserted text span in the active editor, and
   watches it for the 20s observation window (the same window dum uses).
3. At the end it writes a `user.refix` event with the **exact** `edit_distance` / `normalized` and
   `capture_method: "vscode-ext"` into `<sessions_dir>/vscode-ext-<session>.jsonl`.
4. `scripts/analyze_user_corrections.py` globs `dogfood/sessions/*.jsonl`, joins by `commit_id`, and
   **prefers the exact `vscode-ext` capture over the keystroke proxy** — so VS Code becomes
   rate-eligible instead of an AX-blind hole.

## Run it (no build step — plain JS)
The `vscode` module and Node builtins are provided by the host, so there's nothing to `npm install`.

**Dev (fastest):** open this folder in VS Code → press **F5** → an Extension Development Host window
opens with the extension loaded.

**Install locally (persistent):** package as a `.vsix` and install via the CLI — this registers it
in VS Code's extension registry so it survives restarts. **Do NOT hand-symlink into
`~/.vscode/extensions/`**: a manually-placed symlink is not in the registry cache (`extensions.json`),
so VS Code silently never loads it (this exact failure cost a debugging session — see
`tests/feel-log.md` 2026-06-20).
```
npx --yes @vscode/vsce package --allow-missing-repository --skip-license   # -> dum-telemetry-0.1.0.vsix
code --install-extension dum-telemetry-0.1.0.vsix --force
# then: Cmd+Shift+P -> "Developer: Reload Window"
code --list-extensions | grep dum    # verify: dum.dum-telemetry
```
Re-run both after editing `extension.js` (the install is a copy, not a live link). The `.vsix` is
gitignored.

**Then turn on the dum side:**
```
DUM_VSCODE_BRIDGE=1 ./dum          # or export it in your shell before launching
```
Dictate into VS Code, edit (or don't), wait ~20s. Check it's flowing with the command palette →
**"dum: Dictation Telemetry Status"** (shows announced / observed / written / missed counts).

## Verify the gap is closing
After a dictation session in VS Code:
```
.venv/bin/python scripts/analyze_user_corrections.py 'dogfood/sessions/*.jsonl'
```
VS Code commits should now appear under **rate-eligible** with real edit distances, and the per-app
capture table should show exact captures for Code instead of `blind`.

## Limits (Phase 1)
- If there's no active text editor when the announce arrives, or the inserted text can't be located
  near the cursor, the commit is **skipped** (the keystroke proxy still stands) and counted as
  `missed` — honest partial coverage, never a fabricated number.
- Multiple VS Code windows each tail the bridge; only the window that actually contains the text
  claims it. Rare double-claims are possible.
- Span tracking uses per-change offset math; pathological multi-cursor edits may mis-track. Good
  enough for the "was the dictation corrected, and by how much?" question.
- **Editor documents ONLY.** The extension reads `activeTextEditor` (the document model), so it
  cannot see the **integrated terminal**, any **TUI** running in it, or the **Claude Code prompt** —
  those aren't TextDocuments. Dictation there is `missed` here and falls back to the keystroke proxy.
  Exact capture for the **Claude Code prompt** comes from a *different* mechanism —
  `scripts/join_claude_transcripts.py`, which joins commits to Claude Code's own transcript
  (`capture_method=claude-transcript`, local-only). That is a transcript join, **NOT** terminal-buffer
  reading — no VS Code API exposes terminal contents.
