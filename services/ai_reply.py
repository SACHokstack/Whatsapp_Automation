from __future__ import annotations

import os
from typing import TYPE_CHECKING

from groq import Groq, RateLimitError

from services.knowledge_base import build_rag_context

if TYPE_CHECKING:
    from services.course_loader import CourseConfig


def _load_api_keys() -> list[str]:
    keys = []
    # Primary key
    primary = os.getenv("GROQ_API_KEY", "")
    if primary:
        keys.append(primary)
    # Fallback keys: GROQ_API_KEY_2, GROQ_API_KEY_3, ...
    i = 2
    while True:
        key = os.getenv(f"GROQ_API_KEY_{i}", "")
        if not key:
            break
        keys.append(key)
        i += 1
    return keys


_SYSTEM_PROMPT = """You are a helpful WhatsApp assistant for Timmins Training & Consulting, a Malaysia-based corporate training company.

Answer the customer's question using ONLY the knowledge base content provided below.
Rules:
- Keep responses concise and under 150 words
- Use plain text only — no markdown headers, no ** bold **, no # symbols
- Use a dash (-) for bullet points if needed
- Be warm and professional
- If the answer is not in the knowledge base, say exactly: "Thank you for your interest. A consultant will contact you shortly."
- Never invent fees, dates, or policies not in the knowledge base"""

_DEFAULT_REPLY = "Thank you for your interest. A consultant will contact you shortly."


def ai_reply(message: str, course=None) -> str:
    context = build_rag_context(message, course)
    if not context:
        return _DEFAULT_REPLY

    keys = _load_api_keys()
    if not keys:
        print("AI_REPLY ERROR: no GROQ_API_KEY configured")
        return _DEFAULT_REPLY

    for i, key in enumerate(keys):
        try:
            client = Groq(api_key=key)
            response = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                max_tokens=300,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": f"Knowledge base:\n{context}\n\nCustomer message: {message}",
                    },
                ],
            )
            return response.choices[0].message.content.strip()
        except RateLimitError:
            label = "primary" if i == 0 else f"key_{i + 1}"
            print(f"AI_REPLY RATE LIMITED on {label}, trying next key...")
            continue
        except Exception as e:
            print("AI_REPLY ERROR:", e)
            return _DEFAULT_REPLY

    print("AI_REPLY ERROR: all API keys rate limited")
    return _DEFAULT_REPLY
