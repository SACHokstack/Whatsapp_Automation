from __future__ import annotations

import os

from groq import Groq

from services.knowledge_base import search_knowledge

_client: Groq | None = None


def _get_client() -> Groq:
    global _client
    if _client is None:
        _client = Groq(api_key=os.getenv("GROQ_API_KEY"))
    return _client


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


def ai_reply(message: str) -> str:
    docs = search_knowledge(message, limit=3)
    if not docs:
        return _DEFAULT_REPLY

    context = "\n\n".join(
        f"[{d['topic'].upper()}]\n{d['content']}" for d in docs
    )

    try:
        response = _get_client().chat.completions.create(
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
    except Exception as e:
        print("AI_REPLY ERROR:", e)
        return _DEFAULT_REPLY
