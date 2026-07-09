# G2 payoff script - READ THESE ALOUD

Goal: prove the curated global pack (`packs/global-tech.aliases`) actually LANDS its terms on real
mic audio (recall pack-ON > pack-OFF) without harming prose. Read each line **exactly as written** -
say the **spoken form** ("web pack", "pie test", "oh llama"), NOT the canonical ("webpack" etc.); the
whole point is to feed Parakeet the messy form and watch the pack fix it.

Speak naturally, **pause ~1s between lines** so the recognizer segments them.

## Lines that USE the jargon (the payoff - these should get corrected)

1. restart engine x then tail the logs
2. run cube control to scale the deployment
3. deploy it on local host first
4. bundle the assets with web pack
5. run the tests using pie test
6. scale the cluster with kuber netes
7. write the handler in java script
8. store the documents in mongo DB
9. use dynamo DB for the session table
10. open a web socket for live updates
11. trigger the deploy with a web hook
12. push the code to git hub
13. connect through wire guard
14. add the auth check as middle ware
15. run the model locally with oh llama
16. transcribe the audio with para keet
17. the engine uses sherpa onnx under the hood
18. we also tried whisper cpp for comparison
19. the streaming model is a zip former

## Controls - these MUST stay untouched (over-correction tripwires)

20. let's grab a coffee and talk about the plan
21. the quick brown fox jumps over the lazy dog
22. I saw a llama at the zoo last weekend
23. I need to type a script for the video tomorrow

## What we check (you don't read this)

- **Payoff:** lines 1-19 → canonical forms appear with the pack ON (webpack, pytest, MongoDB,
  WireGuard, Ollama, Parakeet, sherpa-onnx, whisper.cpp, Zipformer, …).
- **Controls hold:** line 22 "a llama" must NOT become Ollama (we kept only "oh llama"); line 23
  "type a script" must NOT become TypeScript (that alias was dropped); lines 20-21 plain prose intact.
- **Spoken-form check:** if a line's canonical does NOT appear pack-ON, read the RAW column - it shows
  the true form Parakeet emits, so we fix that alias's left side (or drop it if already correct).
