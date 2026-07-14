import json
import re
from typing import Any

from anthropic import Anthropic

from .config import settings
from . import kb


def _make_client():
    if settings.llm_provider == "bedrock":
        # Use the classic InvokeModel Bedrock endpoint (same one Claude Code CLI
        # uses). The newer bedrock-mantle:CreateInference endpoint requires
        # additional IAM permissions most Bedrock roles don't grant by default.
        from anthropic import AnthropicBedrock
        return AnthropicBedrock(aws_region=settings.aws_region)
    return Anthropic(api_key=settings.anthropic_api_key)


def _model_id() -> str:
    return settings.bedrock_model_id if settings.llm_provider == "bedrock" else settings.anthropic_model_id


TRIAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "is_known_issue": {
            "type": "boolean",
            "description": "True if the log line matches one of the provided KB candidates.",
        },
        "matched_kb_id": {
            "type": ["string", "null"],
            "description": "The id of the matched KB entry, or null if no match.",
        },
        "confidence": {
            "type": "number",
            "description": "Confidence in the match, between 0 and 1.",
        },
        "affected_product": {
            "type": "string",
            "description": "Which product/service the log line belongs to. Best guess if not obvious.",
        },
        "reasoning": {
            "type": "string",
            "description": "Short (1-2 sentence) explanation of the decision.",
        },
    },
    "required": [
        "is_known_issue",
        "matched_kb_id",
        "confidence",
        "affected_product",
        "reasoning",
    ],
    "additionalProperties": False,
}


SYSTEM_PROMPT = """You are an incident triage assistant for a production monitoring system.

You will receive a single log line and up to 3 candidate known-issue patterns retrieved by embedding similarity from a historical knowledge base. Your job:

1. Decide if the log line matches ANY of the candidates. A match means the log line describes the same underlying failure mode as the candidate — not just superficial keyword overlap.
2. If it matches, return the candidate's kb_id and a confidence score in [0, 1].
3. If it does not match any candidate, return is_known_issue=false, matched_kb_id=null, and confidence should reflect how novel the issue looks.
4. Always identify the affected product/service from the log line. If unclear, make your best guess from the log content.
5. Keep reasoning to 1-2 sentences.

Be conservative — false positives cause the wrong automated action to run. Prefer novelty over a weak match."""


_client = None


def _get_client():
    global _client
    if _client is None:
        _client = _make_client()
    return _client


# Broad regex for lines that look like errors/warnings worth triaging.
_ERROR_RE = re.compile(
    r"\b(error|exception|fatal|fail(ed|ure)?|panic|timeout|refused|denied|unavailable|"
    r"crash|oom|killed|expired|exhaust|full|elevated|spike|hung|stuck|"
    r"5\d\d|4\d\d)\b",
    re.IGNORECASE,
)


def is_triageable(log_line: str) -> bool:
    return bool(_ERROR_RE.search(log_line))


def triage(log_line: str) -> dict[str, Any]:
    """Return a triage decision for a single log line."""
    candidates = kb.search(log_line, top_k=3)

    # Fast path: very high similarity → skip the LLM call.
    if candidates and candidates[0]["similarity"] >= settings.confidence_threshold:
        top = candidates[0]
        return {
            "is_known_issue": True,
            "matched_kb_id": top["kb_id"],
            "confidence": top["similarity"],
            "affected_product": top["metadata"]["product"],
            "reasoning": f"Embedding similarity {top['similarity']:.2f} exceeded threshold {settings.confidence_threshold}; skipped LLM confirmation.",
            "source": "vector",
            "candidates": candidates,
        }

    # LLM confirmation path.
    if not candidates:
        # No KB at all — still call the model to at least extract the product.
        candidates_summary = "(no candidates)"
    else:
        candidates_summary = "\n\n".join(
            f"[{c['kb_id']}] similarity={c['similarity']:.2f} product={c['metadata']['product']}\n{c['document']}"
            for c in candidates
        )

    user_msg = (
        f"Log line:\n{log_line}\n\n"
        f"Candidate known-issue patterns (from vector search):\n{candidates_summary}\n\n"
        "Return the triage decision as JSON matching the schema."
    )

    client = _get_client()
    response = client.messages.create(
        model=_model_id(),
        max_tokens=800,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": TRIAGE_SCHEMA,
            }
        },
        messages=[{"role": "user", "content": user_msg}],
    )

    text = next((b.text for b in response.content if b.type == "text"), "")
    data = json.loads(text)
    data["source"] = "llm"
    data["candidates"] = candidates
    return data
