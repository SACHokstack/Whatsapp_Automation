from __future__ import annotations

import os
from typing import TYPE_CHECKING

from groq import Groq, RateLimitError

from services.knowledge_base import build_rag_context

if TYPE_CHECKING:
    from services.course_loader import CourseConfig


def _load_api_keys() -> list[str]:
    keys = []
    primary = os.getenv("GROQ_API_KEY", "")
    if primary:
        keys.append(primary)
    i = 2
    while True:
        key = os.getenv(f"GROQ_API_KEY_{i}", "")
        if not key:
            break
        keys.append(key)
        i += 1
    return keys


_SYSTEM_PROMPT = """You are a friendly, knowledgeable WhatsApp assistant for Timmins Training & Consulting, a Malaysia-based technical training company.

Use the knowledge base below to give clear, helpful answers. You are talking to a working professional who wants quick, direct information.

Rules:
- Keep responses under 120 words — conversational, not essay-length
- Plain text only — no asterisks, no markdown headers, no # symbols
- Use a dash (-) for bullet points if needed
- Be warm and direct — like a helpful colleague on chat, not a customer-service script
- When the answer is in the knowledge base, give it confidently with the specific details (fees, dates, venue, etc.)
- If the specific detail is NOT in the knowledge base, briefly acknowledge the question, share what you do know, and say you'll have a consultant follow up with the rest
- Never invent fees, dates, or policies not explicitly stated in the knowledge base
- Do not repeat "Thank you for your interest" as a canned response — it sounds robotic"""

_DEFAULT_REPLY = "Let me check that and have our consultant get back to you with the details."

# Primary model — high quality, fast on Groq
_MODEL_PRIMARY = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
# Fallback if rate limited
_MODEL_FALLBACK = "llama-3.1-8b-instant"

# Maximum overview characters to include — the full overview is often 10KB+ which
# dilutes the signal for small factual questions. The first 3000 chars cover all
# key facts (fees, dates, prereqs, trainer). Full content is still available for
# curriculum-specific questions via keyword search.
_MAX_OVERVIEW_CHARS = 3000


def _trim_context(context: str) -> str:
    """Prevent the context from overwhelming the model on factual questions."""
    if len(context) <= _MAX_OVERVIEW_CHARS * 2:
        return context
    # Keep the course facts block (always first) in full; trim the overview section
    overview_marker = "[COURSE OVERVIEW]"
    idx = context.find(overview_marker)
    if idx == -1:
        return context[:_MAX_OVERVIEW_CHARS * 2]
    facts_block = context[:idx]
    overview_block = context[idx: idx + len(overview_marker) + _MAX_OVERVIEW_CHARS]
    rest = context[idx + len(overview_marker) + _MAX_OVERVIEW_CHARS:]
    return facts_block + overview_block + ("\n\n" + rest.strip() if rest.strip() else "")


def ai_reply(message: str, course=None) -> str:
    context = build_rag_context(message, course)
    if not context:
        return _DEFAULT_REPLY

    keys = _load_api_keys()
    if not keys:
        print("AI_REPLY ERROR: no GROQ_API_KEY configured")
        return _DEFAULT_REPLY

    trimmed_context = _trim_context(context)
    models_to_try = [(_MODEL_PRIMARY, key) for key in keys] + [(_MODEL_FALLBACK, keys[0])]

    for model, key in models_to_try:
        try:
            client = Groq(api_key=key)
            response = client.chat.completions.create(
                model=model,
                max_tokens=450,
                temperature=0.4,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Knowledge base:\n{trimmed_context}\n\nCustomer message: {message}",
                    },
                ],
            )
            reply = response.choices[0].message.content.strip()
            print(f"AI_REPLY model={model} tokens={response.usage.completion_tokens if response.usage else '?'}")
            return reply
        except RateLimitError:
            label = f"{model}"
            print(f"AI_REPLY RATE LIMITED on {label}, trying next...")
            continue
        except Exception as e:
            print("AI_REPLY ERROR:", e)
            return _DEFAULT_REPLY

    print("AI_REPLY ERROR: all models/keys rate limited")
    return _DEFAULT_REPLY
