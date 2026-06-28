"""Token counting utilities — accurate estimation to prevent OOM.

Provides a fast, cached tokenizer-based counter with a char-ratio
fallback when the tokenizer isn't available.

The tokenizer is loaded lazily and cached globally.  The ``count_tokens``
function is the primary entry point — it handles messages, strings, and
tool definitions.

Key insight: the naive ``chars / 4`` heuristic underestimates by ~25%
for code and structured output (JSON, tool calls).  This module uses the
actual model tokenizer when possible, which prevents sending prompts that
exceed ``max_model_len`` and cause vLLM to OOM/hang.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

log = logging.getLogger(__name__)

_tokenizer = None
_tokenizer_lock = threading.Lock()
_tokenizer_loaded = False

# Conservative fallback: 3.2 chars/token (between English=4 and code=3)
_FALLBACK_CHARS_PER_TOKEN = 3.2

# Per-message overhead (role, formatting tokens added by chat template)
_MESSAGE_OVERHEAD_TOKENS = 4

# Per-tool-call overhead
_TOOL_CALL_OVERHEAD_TOKENS = 8


def _get_tokenizer():
    """Lazily load the tokenizer (cached globally)."""
    global _tokenizer, _tokenizer_loaded

    if _tokenizer_loaded:
        return _tokenizer

    with _tokenizer_lock:
        if _tokenizer_loaded:
            return _tokenizer

        try:
            from transformers import AutoTokenizer
            from acai.orchestrator.config import option

            model_name = option("llm.model", str, "")
            if not model_name:
                model_name = option("llm.model_name", str, "")

            if model_name:
                _tokenizer = AutoTokenizer.from_pretrained(
                    model_name, trust_remote_code=True
                )
                log.info("Token counter: loaded tokenizer for %s", model_name)
            else:
                log.info("Token counter: no model configured, using char fallback")
        except Exception as exc:
            log.debug("Token counter: tokenizer unavailable (%s), using char fallback", exc)
            _tokenizer = None

        _tokenizer_loaded = True
        return _tokenizer


def count_text_tokens(text: str) -> int:
    """Count tokens in a raw text string."""
    if not text:
        return 0

    tok = _get_tokenizer()
    if tok is not None:
        try:
            return len(tok.encode(text, add_special_tokens=False))
        except Exception:
            pass

    return int(len(text) / _FALLBACK_CHARS_PER_TOKEN)


def count_message_tokens(message: dict) -> int:
    """Count tokens in a single chat message (content + tool_calls)."""
    total = _MESSAGE_OVERHEAD_TOKENS

    content = message.get("content")
    if isinstance(content, str):
        total += count_text_tokens(content)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict):
                text = part.get("text", "")
                if text:
                    total += count_text_tokens(text)

    for tc in message.get("tool_calls", []):
        total += _TOOL_CALL_OVERHEAD_TOKENS
        fn = tc.get("function", {})
        total += count_text_tokens(fn.get("name", ""))
        total += count_text_tokens(fn.get("arguments", ""))

    return total


def count_messages_tokens(messages: list[dict]) -> int:
    """Count total tokens across all messages."""
    return sum(count_message_tokens(m) for m in messages)


def count_tools_tokens(tools: list[dict] | None) -> int:
    """Estimate tokens used by tool definitions in the payload."""
    if not tools:
        return 0

    import json
    text = json.dumps(tools, ensure_ascii=False)
    return count_text_tokens(text)


def estimate_payload_tokens(payload: dict) -> int:
    """Estimate total token usage for a complete LLM payload.

    Includes messages + tool definitions.
    """
    messages = payload.get("messages", [])
    tools = payload.get("tools")

    msg_tokens = count_messages_tokens(messages)
    tool_tokens = count_tools_tokens(tools)

    return msg_tokens + tool_tokens


def fits_context(
    payload: dict,
    context_window: int,
    max_output_tokens: int,
    *,
    safety_margin: float = 0.05,
) -> tuple[bool, int, int]:
    """Check if a payload fits within the context budget.

    Returns (fits, estimated_input_tokens, available_tokens).
    The safety_margin (default 5%) provides extra buffer for
    chat template formatting, BOS/EOS tokens, etc.
    """
    estimated = estimate_payload_tokens(payload)
    available = int(context_window * (1.0 - safety_margin)) - max_output_tokens
    return estimated <= available, estimated, available


def reset_tokenizer():
    """Reset the cached tokenizer (for testing)."""
    global _tokenizer, _tokenizer_loaded
    with _tokenizer_lock:
        _tokenizer = None
        _tokenizer_loaded = False
