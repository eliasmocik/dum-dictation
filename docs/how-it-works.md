# Getting dictation to type "kubectl", not "cube cuttle"

In today's era of Claude Code and other tools I dictate a lot. And I have noticed my colleagues do too, which is why I found it weird that there aren't any dictation tools for developers which would pass the following criteria: free, local and live dictation which actually catches all your technical terms right. You may be asking yourself that that's a strong claim, what about Talon? nerd-dictation? whisper-typer? Those exist and are local. I'm not saying nothing exists, it's that nothing does all four at once (free + local + live + tech-vocab-aware).

Every generic live dictation tool is great at English and useless the moment a technical word shows up. I'd say "kubectl" and get "cube cuttle". "nginx" came out "engine x". "PostgreSQL" turned into "post gray sequel". "TanStack Query" was hopeless. So dictation was fine for texting my girlfriend and worthless for the actual work I do at a keyboard.

The paid cloud tools (Wispr Flow, Superwhisper) are better at raw transcription but they still fumble the jargon, and they ship your audio off your machine to do it. They also do not show you live text as you dictate, dumping a wall of text when you stop which makes you proofread a paragraph after. I wanted something more responsive, something that shows each word as you speak, so you catch a wrong term the instant it lands. I didn't want to settle for less, so I built the thing I wanted: local dictation that gets the vocab right and never leaves the laptop. It's called dum, it's MIT, and here is how it works:

## Why normal dictation gets this wrong (or: LLM theory 101)

Speech models are trained on how people talk and almost nobody says "kubectl" out loud on the internet. So a technical term is a rare or out-of-vocabulary token that the model happily rounds off to the nearest common English words it actually *does* know. "kubectl" is phonetically close to "cube cuttle", and the model has seen "cube" and "cuttle" a million times while "kubectl" almost never. It's not broken, it's doing exactly what it was trained to do. It just wasn't trained for the task I was aiming at.

You may be asking: couldn't you just fine-tune a model on tech speech and save yourself all the effort? Technically yes, but that has a big problem: fine-tuning bakes the vocab into a big artifact you have to reship, and so adding new jargon over time would be a total nightmare.

## Recognizer first, then a correction pipeline

The recognizer is [Parakeet TDT 0.6b v3](https://github.com/k2-fsa/sherpa-onnx) (int8), running offline through sherpa-onnx as a greedy transducer. Nothing exotic. It gives me word-by-word previews off a growing audio window, and a voice-activity pause marks the end of a sentence and triggers the final transcript. To keep the per-word latency roughly flat instead of creeping up as the sentence grows, the decode window is bounded with a lock-and-trim trick: once a prefix is stable it gets locked and trimmed out of the window that keeps getting re-decoded.

The interesting part isn't the recognizer, tho. It's what happens to the text after it. Instead of one big model trying to be right about everything, there's an ordered pipeline of small stages, each one `text => (text, events)`:

1. **Punctuation cleanup.** Drop the spurious sentence-final marks the recognizer sprinkles in at micro-pauses ("See? this" becomes "See this").
2. **Vocab correction.** A word-bounded alias layer maps known misheard forms to the canonical term: `engine x => nginx`, `cube control => kubectl`. These live in plain text packs (`packs/*.aliases`) that ship with the tool and are always on. They're additive and word-boundaried, so they fix "engine x" without touching a real "engine" somewhere else.
3. **A homophone LLM, but only just.** Some mistakes aren't vocab, they're homophones the aliases can't catch because both spellings are real words: "grep" vs "grab", "git" vs "get". For those there's a 4-bit Llama-3.2-1B running locally (via mlx on Apple Silicon, llama.cpp everywhere else). It only edits when it's confident, and it's the last resort, not the first.
4. **Capitalization, last.** Because the alias and LLM stages can lowercase a leading word, sentence capitalization runs at the very end.

The reason it's a pipeline of dumb, deterministic stages and not one clever model is that I can reason about it. An alias pack is a diff you can read. It's fast, it never hallucinates a "correction" into text that was already fine, and when it's wrong you can see exactly which line did it and fix that one line. The LLM is walled off behind a confidence check specifically so it can't get creative with your text. There's a rule for what goes in the shipped packs btw: only *general* mishears (the recognizer's fault, any user hits them) go in, never *personal* ones (your accent, where the model heard you correctly). "postgres" heard as "Ugres" is general, add it. You saying "JITHUB" for GitHub is personal, and a global fix for it would break the tool for everyone who doesn't talk like you.

## Typing it out without eating your text

Getting the words right is half of it. The other half is putting them on screen live, as you speak, without ever corrupting what's already there.

In editors and terminals dum uses an overlay: it types synthetic keystrokes word by word as previews come in, and at the sentence-end pause, if the corrected sentence differs from what it previewed, it reconciles by backspacing the difference and retyping. The one guarantee I actually cared about is that it never drops or mangles text you already had. That path (the keystroke diff and reconcile) is the most battle-tested code in the repo. Some apps scramble under synthetic keystrokes (rich text editors mostly), so for a small block list it falls back to pasting the finished sentence at commit, saving and restoring your clipboard around it.

Typing is behind a per-OS seam: Quartz CGEvents on macOS, SendInput on Windows, xdotool or ydotool on Linux. They post raw Unicode rather than going through a generic key-injection layer, which matters more than it sounds: I use a Slovak keyboard layout half the time, and naive key injection mangles dead-key characters. Raw Unicode doesn't.

## Local, and staying that way

There are no network calls in the hot path. The models are on disk, the correction is on the CPU/GPU you already own, and there's no account. There's an opt-in local log (off by default, writes only to a gitignored folder) that measures how often I manually fix a dictated word, which is how I find the next vocab gap to close. That signal never leaves the machine either.

## Where it's at

macOS on Apple Silicon is the version I daily-drive and trust. Windows is in beta (a contributor built and tested it), and Linux support (X11 and Wayland) just landed from another contributor. It's honestly rough in places: on pure Wayland the focus-away safety is off, the homophone LLM is fast on Apple Silicon and slower elsewhere, and a strong accent will still trip it up.

It's open source (MIT), so if it mishears a term you can add the fix in one line, or just tell me. Repo: https://github.com/eliasmocik/dum-dictation

Cheers, Elias