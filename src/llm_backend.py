#!/usr/bin/env python3
"""
LLMBackend — the inference seam for the Layer-3 homophone-fix LLM.

All the *smart* correction logic (the prompt, the SUSPECTS gate, IT-term validation,
the grep/git context gates, plausibility filtering) lives ONCE in
`llm_stage.LLMCorrector` and is OS-agnostic. The only platform-specific atoms are model
LOADING and token GENERATION. Those — and only those — are fenced behind `LLMBackend`,
so the same dictation behaves identically on every OS: to support a new platform we add
a backend, we do NOT fork the corrector.

Backends:
  - MlxBackend   — Apple Silicon (mlx-lm). The only impl today.
  - (future) LlamaCppBackend / OnnxGenAIBackend — portable to Windows/Linux, same contract.

Contract (one method):
    generate(messages: list[dict], max_tokens: int) -> str
        messages : OpenAI-style chat turns ([{"role", "content"}, ...]).
        returns  : the model's raw decoded text, stripped.
    The backend owns chat-template application + decoding (tokenizer-specific). It knows
    NOTHING about IT terms, gating, or corrections — that all stays in LLMCorrector.

Selection: make_backend(model_id) picks the impl for the current platform. Override with
DUM_LLM_BACKEND (e.g. "mlx") to pin/benchmark one backend against another on one machine.
"""
import atexit
import os
import sys


class LLMBackend:
    """Abstract inference primitive. Subclasses implement generate()."""

    def generate(self, messages, max_tokens):
        raise NotImplementedError


class MlxBackend(LLMBackend):
    """Apple-Silicon inference via mlx-lm.

    Loads the model on the CONSTRUCTING thread: MLX GPU streams are thread-local, so the
    caller (llm_stage.LLMWorker) must build this on the single persistent MLX thread that
    also runs generate(). See LLMWorker's docstring for why."""

    def __init__(self, model_id):
        from mlx_lm import load
        self.model_id = model_id
        self.model, self.tok = load(model_id)

    def generate(self, messages, max_tokens):
        from mlx_lm import generate
        prompt = self.tok.apply_chat_template(messages, add_generation_prompt=True)
        return generate(self.model, self.tok, prompt=prompt,
                        max_tokens=max_tokens, verbose=False).strip()


# Portable backend default model: a GGUF build of the same Llama-3.2-1B-Instruct, so the
# correction behaviour matches the MLX backend. Q4_K_M ~= the 4-bit MLX quant. Override the
# repo/file with DUM_LLM_GGUF_REPO / DUM_LLM_GGUF_FILE, or point at a local file with
# DUM_LLM_GGUF_PATH.
DEFAULT_GGUF_REPO = os.environ.get("DUM_LLM_GGUF_REPO", "bartowski/Llama-3.2-1B-Instruct-GGUF")
DEFAULT_GGUF_FILE = os.environ.get("DUM_LLM_GGUF_FILE", "Llama-3.2-1B-Instruct-Q4_K_M.gguf")


class LlamaCppBackend(LLMBackend):
    """Portable inference via llama.cpp (GGUF) — the unifying backend.

    Runs the SAME model on every OS: Metal on macOS, CUDA/Vulkan or CPU on Windows/Linux
    (n_gpu_layers=-1 offloads all layers where a GPU exists, falls back to CPU otherwise).
    This is what makes the L3 LLM polish identical across the three platforms. llama.cpp
    applies the GGUF's own chat template, so the prompt format matches the tokenizer the
    model was trained with — same as MLX's apply_chat_template."""

    # chat_format: forced to "chatml", NOT the model's native "llama-3" — a measured choice.
    # On this narrow few-shot task the 1B follows the "wrong->right" mandate markedly better
    # under chatml: vs MLX as ground truth, chatml scored 92% agreement on a 24-sentence battery
    # (and BOTH disagreements were llama.cpp being *more* correct), where llama-3 / auto scored
    # only ~67% — they dropped few-shot-covered fixes like get->git and pseudo->sudo. Re-measure
    # if the model or prompt changes. Override with DUM_LLM_CHAT_FORMAT.
    CHAT_FORMAT = os.environ.get("DUM_LLM_CHAT_FORMAT", "chatml")

    def __init__(self, model_id=None, n_ctx=2048, n_gpu_layers=-1):
        from llama_cpp import Llama
        path = os.environ.get("DUM_LLM_GGUF_PATH")
        if not path:
            from huggingface_hub import hf_hub_download
            path = hf_hub_download(repo_id=DEFAULT_GGUF_REPO, filename=DEFAULT_GGUF_FILE)
        self.model_path = path
        self.llm = Llama(model_path=path, n_ctx=n_ctx, n_gpu_layers=n_gpu_layers,
                         chat_format=self.CHAT_FORMAT, verbose=False)
        # llama.cpp's Metal device is torn down by a C++ static destructor at process exit. If a
        # Llama still holds Metal resource-sets at that point, ggml asserts and the process
        # SIGABRTs — so a clean Quit would exit non-zero with a scary native trace. Releasing the
        # model at normal interpreter shutdown (atexit runs BEFORE __cxa_finalize) lets the device
        # free cleanly. Harmless on CPU/CUDA builds. See llama.cpp ggml-metal device-free assert.
        self._closed = False
        atexit.register(self.close)

    def close(self):
        """Free the underlying llama.cpp context. Idempotent; safe to call at exit or by hand."""
        if self._closed:
            return
        self._closed = True
        try:
            self.llm.close()
        except Exception:
            pass

    def generate(self, messages, max_tokens):
        # temperature=0 => greedy/deterministic, matching the narrow "wrong->right" mandate.
        res = self.llm.create_chat_completion(messages=messages, max_tokens=max_tokens,
                                              temperature=0.0)
        return (res["choices"][0]["message"]["content"] or "").strip()


def _default_backend_name():
    """Platform default: MLX on Apple Silicon (native), llama.cpp everywhere else.

    On Windows/Linux MLX doesn't exist, so the portable llama.cpp backend is both the only
    option AND the unifying one — picking it automatically means Win/Linux get the L3 LLM
    polish with no flags. On Mac we keep mlx the default for now (llama.cpp is actually faster
    — see bench — so this default is expected to flip to llamacpp after per-OS validation)."""
    return "mlx" if sys.platform == "darwin" else "llamacpp"


def make_backend(model_id, name=None):
    """Pick the inference backend.

    - "mlx"      — Apple-Silicon native (Mac-only).
    - "llamacpp" — portable llama.cpp/GGUF; runs on macOS/Windows/Linux (the unifying backend).
    Default is platform-aware (see _default_backend_name). Override per-run with DUM_LLM_BACKEND."""
    name = (name or os.environ.get("DUM_LLM_BACKEND") or _default_backend_name()).lower()
    if name == "mlx":
        return MlxBackend(model_id)
    if name in ("llamacpp", "llama", "gguf"):
        return LlamaCppBackend()
    raise ValueError(f"unknown LLM backend: {name!r} (known: mlx, llamacpp)")
