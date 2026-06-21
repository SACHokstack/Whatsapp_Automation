from __future__ import annotations

from typing import Any

from services.storage import save_message, save_reply, upsert_lead


YES_KEYWORDS = {"yes", "interested", "book call", "book a call", "callback"}


def _extract_text(message: dict[str, Any]) -> str:
    if "text" in message and isinstance(message["text"], dict):
        return str(message["text"].get("body", "")).strip()
    if "button" in message and isinstance(message["button"], dict):
        return str(message["button"].get("text", "")).strip()
    interactive = message.get("interactive")
    if isinstance(interactive, dict):
        button_reply = interactive.get("button_reply")
        if isinstance(button_reply, dict):
            return str(button_reply.get("title", "")).strip()
        list_reply = interactive.get("list_reply")
        if isinstance(list_reply, dict):
            return str(list_reply.get("title", "")).strip()
    return ""


def _classify_intent(reply_text: str) -> str | None:
    normalized = reply_text.strip().lower()
    if not normalized:
        return None
    if "book" in normalized and "call" in normalized:
        return "book_call"
    if normalized in YES_KEYWORDS or any(keyword in normalized for keyword in ("yes", "interested")):
        return "interested"
    return "other"


def process_webhook_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    processed: list[dict[str, Any]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contacts = value.get("contacts", []) or []
            messages = value.get("messages", []) or []
            statuses = value.get("statuses", []) or []

            for contact in contacts:
                wa_id = str(contact.get("wa_id", "")).strip()
                profile = contact.get("profile", {}) if isinstance(contact.get("profile", {}), dict) else {}
                name = str(profile.get("name", "")).strip()
                upsert_lead(phone=wa_id, name=name, source_status="webhook_contact")
                processed.append({"type": "contact", "phone": wa_id, "name": name})

            for message in messages:
                phone = str(message.get("from", "")).strip()
                message_id = str(message.get("id", "")).strip()
                message_type = str(message.get("type", "")).strip()
                body = _extract_text(message)
                intent = _classify_intent(body)

                upsert_lead(phone=phone, source_status="webhook_message")
                save_message(
                    phone=phone,
                    direction="inbound",
                    whatsapp_message_id=message_id,
                    message_type=message_type,
                    body=body,
                    raw_json=message,
                )
                if body:
                    save_reply(phone=phone, reply_text=body, intent=intent, raw_json=message)

                processed.append(
                    {
                        "type": "message",
                        "phone": phone,
                        "message_id": message_id,
                        "message_type": message_type,
                        "body": body,
                        "intent": intent,
                    }
                )

            for status in statuses:
                phone = str(status.get("recipient_id", "")).strip()
                message_id = str(status.get("id", "")).strip()
                message_status = str(status.get("status", "")).strip()
                upsert_lead(phone=phone, source_status="webhook_status")
                save_message(
                    phone=phone,
                    direction="status",
                    whatsapp_message_id=message_id,
                    message_type="status",
                    message_status=message_status,
                    raw_json=status,
                )
                processed.append(
                    {
                        "type": "status",
                        "phone": phone,
                        "message_id": message_id,
                        "status": message_status,
                    }
                )

    return processed

