"""Shared AWS Bedrock Runtime helpers for structured GPT-OSS responses."""

from __future__ import annotations

import json
import os
from typing import Any

DEFAULT_BEDROCK_MODEL = "openai.gpt-oss-120b-1:0"
DEFAULT_BEDROCK_REGION = "ap-south-1"


def bedrock_region() -> str:
    """Resolve the Bedrock region without bypassing the AWS credential chain."""
    return (
        os.getenv("BEDROCK_REGION", "").strip()
        or os.getenv("AWS_REGION", "").strip()
        or os.getenv("AWS_DEFAULT_REGION", "").strip()
        or DEFAULT_BEDROCK_REGION
    )


def bedrock_model(configured: str = "") -> str:
    return configured.strip() or DEFAULT_BEDROCK_MODEL


def _runtime_client(*, region: str, timeout_seconds: float):
    # Import lazily so deterministic routes and retrieval continue to work even
    # before the optional AWS dependency/credentials have been configured.
    import boto3
    from botocore.config import Config

    return boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            connect_timeout=min(5.0, timeout_seconds),
            read_timeout=timeout_seconds,
            retries={"mode": "standard", "max_attempts": 2},
        ),
    )


def converse_json(
    *,
    system_prompt: str,
    user_prompt: str,
    schema: dict[str, Any],
    schema_name: str,
    model: str = DEFAULT_BEDROCK_MODEL,
    region: str | None = None,
    max_tokens: int = 4000,
    timeout_seconds: float = 30.0,
    client=None,
) -> str:
    """Call Bedrock Converse with a strict JSON schema and return its text block.

    Authentication is deliberately left to boto3's normal credential provider
    chain. This supports an IAM role, AWS profile/access keys, or a Bedrock API
    key supplied through ``AWS_BEARER_TOKEN_BEDROCK`` without secrets in code.
    """
    selected_region = (region or bedrock_region()).strip()
    selected_model = bedrock_model(model)
    runtime = client or _runtime_client(
        region=selected_region,
        timeout_seconds=timeout_seconds,
    )
    response = runtime.converse(
        modelId=selected_model,
        system=[{"text": system_prompt}],
        messages=[
            {
                "role": "user",
                "content": [{"text": user_prompt}],
            }
        ],
        inferenceConfig={
            "maxTokens": max_tokens,
            "temperature": 0,
        },
        additionalModelRequestFields={"reasoning_effort": "low"},
        outputConfig={
            "textFormat": {
                "type": "json_schema",
                "structure": {
                    "jsonSchema": {
                        "name": schema_name,
                        "description": "Structured response required by the application.",
                        "schema": json.dumps(schema, separators=(",", ":")),
                    }
                },
            }
        },
    )

    if response.get("stopReason") == "max_tokens":
        raise ValueError(f"Bedrock output was truncated at maxTokens={max_tokens}")

    content = response.get("output", {}).get("message", {}).get("content", [])
    text_blocks = [block["text"] for block in content if isinstance(block.get("text"), str)]
    if not text_blocks:
        raise ValueError("Bedrock response did not contain a text block")
    return "".join(text_blocks)
