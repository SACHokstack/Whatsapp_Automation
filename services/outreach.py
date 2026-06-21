from __future__ import annotations

import os
from dataclasses import asdict

from services.storage import save_message
from services.whatsapp import send_message, send_template_message


def build_outreach_text(full_name: str) -> str:
    template = os.getenv(
        "OUTREACH_MESSAGE",
        "Hi {full_name}, we help professionals improve their software testing skills. Would you be open to a quick chat?",
    )
    return template.format(full_name=full_name or "there")


def run_outreach(limit: int | None = None, dry_run: bool = False) -> dict[str, object]:
    from services.google_sheets import GoogleSheetsClient, get_pending_leads

    client = GoogleSheetsClient.from_env()
    leads = get_pending_leads()
    if limit is not None and limit >= 0:
        leads = leads[:limit]

    results: list[dict[str, object]] = []
    sent_count = 0
    failed_count = 0
    template_name = os.getenv("WHATSAPP_TEMPLATE_NAME", "").strip()
    template_language = os.getenv("WHATSAPP_TEMPLATE_LANGUAGE", "en_US").strip()

    for lead in leads:
        message_text = build_outreach_text(lead.name)
        if dry_run:
            results.append({"lead": asdict(lead), "mode": "dry_run", "message": message_text})
            continue

        if template_name:
            response = send_template_message(
                lead.phone,
                template_name=template_name,
                language=template_language,
                variables=[lead.name],
            )
        else:
            response = send_message(lead.phone, message_text)

        if response.ok:
            client.mark_as_sent(lead)
            save_message(
                phone=lead.phone,
                direction="outbound",
                whatsapp_message_id=None,
                message_type="template" if template_name else "text",
                message_status="accepted",
                body=message_text,
                raw_json={"response": response.json() if response.content else {}},
            )
            sent_count += 1
            results.append(
                {
                    "lead": asdict(lead),
                    "status": "sent",
                    "http_status": response.status_code,
                }
            )
        else:
            client.mark_as_failed(lead, reason=response.text[:250])
            failed_count += 1
            results.append(
                {
                    "lead": asdict(lead),
                    "status": "failed",
                    "http_status": response.status_code,
                    "error": response.text[:500],
                }
            )

    return {
        "dry_run": dry_run,
        "template_name": template_name or None,
        "processed": len(leads),
        "sent": sent_count,
        "failed": failed_count,
        "results": results,
    }
