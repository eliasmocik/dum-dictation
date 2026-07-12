# dum dictation Telemetry - VS Code extension (Phase 1: measurement only)

In VS Code (Electron), macOS accessibility can't read the editor, so dum's "did the user fix
the dictation?" signal falls back to a keystroke proxy that can't tell a correction from
normal coding, inflating the apparent correction rate. This extension uses the document
model to measure the exact post-commit edit.

**It only observes. It never inserts or modifies text.** (That's Phase 2, if ever.)

## How it works

1. dum, on each editor-surface commit, appends `{commit_id, text, ts, sessions_dir, session}`
   to `~/.dum/vscode-bridge.jsonl` (gated by `DUM_VSCODE_BRIDGE=1`).
2. The extension tails that file, locates the inserted text in the active editor, and watches
   it for the 20s observation window (same window dum uses).
3. It writes a `user.refix` event with the exact `edit_distance` / `normalized` and
   `capture_method: "vscode-ext"` into `<sessions_dir>/vscode-ext-<session>.jsonl`.
4. `scripts/analyze_user_corrections.py` globs `dogfood/sessions/*.jsonl`, joins by
   `commit_id`, and prefers the exact `vscode-ext` capture over the keystroke proxy.

## Run it

Plain JS, no build step; `vscode` and Node builtins come from the host, nothing to `npm install`.

Dev: open this folder in VS Code, press F5 => Extension Development Host with the extension loaded.

Install locally (persistent):

```
npx --yes @vscode/vsce package --allow-missing-repository --skip-license   # -> dum-telemetry-0.1.0.vsix
code --install-extension dum-telemetry-0.1.0.vsix --force
# then: Cmd+Shift+P -> "Developer: Reload Window"
code --list-extensions | grep dum    # verify: dum.dum-telemetry
```

Do NOT hand-symlink into `~/.vscode/extensions/` - a symlink isn't in the registry cache
(`extensions.json`), so VS Code silently never loads it (see `tests/feel-log.md` 2026-06-20).
Re-run both commands after editing `extension.js` (the install is a copy, not a live link).
The `.vsix` is gitignored.

Then turn on the dum side:

```
DUM_VSCODE_BRIDGE=1 ./dum          # or export it in your shell before launching
```

Dictate into VS Code, edit (or don't), wait ~20s. Check it's flowing via the command palette
=> "dum: Dictation Telemetry Status" (announced / observed / written / missed).

## Verify the gap is closing

```
.venv/bin/python scripts/analyze_user_corrections.py 'dogfood/sessions/*.jsonl'
```

VS Code commits should appear under rate-eligible with real edit distances, and the per-app
capture table should show exact captures for Code instead of `blind`.

## Limits (Phase 1)

- No active text editor at announce time, or the inserted text can't be located near the
  cursor => commit skipped, counted as `missed`; the keystroke proxy still stands.
- Multiple windows each tail the bridge; only the one containing the text claims it. Rare
  double-claims are possible.
- Span tracking uses per-change offset math; pathological multi-cursor edits may mis-track.
- Editor documents only. Reads `activeTextEditor`, so it can't see the integrated terminal
  or TUIs running in it - those aren't TextDocuments; dictation there is `missed` and falls
  back to the keystroke proxy. No VS Code API exposes terminal contents.
