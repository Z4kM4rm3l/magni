"""
core/llm.py

The one place Magni talks to an LLM: Claude, via Anthropic's official SDK.

Models are environment-configurable, so moving models is a variable change,
not a code change:
  CLAUDE_MODEL       customer-facing answers            (default claude-haiku-5-5)
  CLAUDE_FAST_MODEL  routing / extraction / summaries   (default: CLAUDE_MODEL)
  ANTHROPIC_API_KEY  API key

Thinking: Claude Haiku 5.5 thinks adaptively, steered by `effort`. Thinking
tokens are billed as output, so:
  "minimal"  -> thinking disabled (quick classify / extract / summarize calls)
  "low"      -> adaptive thinking at low effort (customer answers)
  "medium" / "high" -> adaptive thinking at that effort

Prompt caching: the stable part of a system prompt (persona, rules, the
business's knowledge base) is marked cacheable, so repeat turns in a
conversation read it at a fraction of the normal input price. Prompts below the
model's minimum cacheable length simply aren't cached.

Tests replace generate()/chat() with stubs; call them as llm.generate(...).
"""
import os
import re
import threading

from core.utils import logger

DEFAULT_MODEL = "claude-haiku-5-5"
MAX_TOKENS = {"minimal": 1024, "low": 4096, "medium": 8192, "high": 16000}

_client = None
_client_lock = threading.Lock()


def model_name(fast: bool = False) -> str:
    main = os.getenv("CLAUDE_MODEL", "").strip() or DEFAULT_MODEL
    if fast:
        return os.getenv("CLAUDE_FAST_MODEL", "").strip() or main
    return main


def _get_client():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                import anthropic  # imported lazily so tests never need the SDK
                _client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _client


def _system_blocks(system):
    """str -> one cacheable block. list[str] -> first block cacheable (the
    stable part), the rest not (per-turn details like a conversation summary)."""
    if not system:
        return None
    parts = [system] if isinstance(system, str) else [p for p in system if p]
    blocks = []
    for i, text in enumerate(parts):
        block = {"type": "text", "text": text}
        if i == 0:
            block["cache_control"] = {"type": "ephemeral"}
        blocks.append(block)
    return blocks


def _thinking_kwargs(thinking: str) -> dict:
    if thinking == "minimal":
        return {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}
    return {"thinking": {"type": "adaptive"}, "output_config": {"effort": thinking or "low"}}


def _text(response) -> str:
    if getattr(response, "stop_reason", None) == "refusal":
        logger.warning("Claude declined to answer this request (stop_reason=refusal)")
        return ""
    return "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()


def _call(model, system, messages, thinking):
    kwargs = {
        "model": model,
        "max_tokens": MAX_TOKENS.get(thinking, 4096),
        "messages": messages,
        **_thinking_kwargs(thinking),
    }
    blocks = _system_blocks(system)
    if blocks:
        kwargs["system"] = blocks
    return _text(_get_client().messages.create(**kwargs))


def generate(prompt: str, system=None, *, fast: bool = True,
             thinking: str = "minimal", json_mode: bool = False) -> str:
    """Single-shot call. Raises on API errors; callers decide the fallback.
    json_mode adds an instruction to answer with bare JSON (callers still parse
    defensively with strip_fences)."""
    if json_mode:
        prompt = f"{prompt}\n\nRespond with only the JSON object, no other text."
    return _call(model_name(fast), system, [{"role": "user", "content": prompt}], thinking)


def _normalize_history(history: list, message: str) -> list:
    """Claude needs alternating turns that start and end with the user."""
    msgs = []
    for m in history:
        role = "user" if m.get("role") == "user" else "assistant"
        text = str(m.get("content") or "").strip()
        if not text:
            continue
        if not msgs and role == "assistant":
            continue  # a conversation can't open with the assistant
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n\n" + text
        else:
            msgs.append({"role": role, "content": text})
    if msgs and msgs[-1]["role"] == "user":
        msgs[-1]["content"] += "\n\n" + message
    else:
        msgs.append({"role": "user", "content": message})
    return msgs


def chat(system, history: list, message: str, *, thinking: str = "low") -> str:
    """Multi-turn answer. history is [{"role": "user"|"assistant", "content": str}].
    system may be a str or [stable_part, per_turn_part]."""
    return _call(model_name(fast=False), system, _normalize_history(history, message), thinking)


def strip_fences(text: str) -> str:
    """Pull the JSON object out of a reply, tolerating ``` fences or stray text."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    if not text.startswith(("{", "[")):
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            text = m.group(0)
    return text.strip()


def log_model_choice() -> None:
    logger.info(f"Claude models: answers={model_name()} fast={model_name(fast=True)}")
