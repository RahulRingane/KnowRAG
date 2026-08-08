"""The system prompt and claim schema — one copy, both providers.

Split out of the old `app/llm.py` so the two provider modules share these by
import rather than by proximity. That matters more than it looks: the whole
point of running two providers is that they are prompted *identically*, so a
difference in output is a difference in the model and not in what it was
asked. A prompt defined next to one provider's client is a prompt that will
eventually be tweaked for that provider alone.
"""

from __future__ import annotations

from typing import Any

# --- System prompt (§5.2 — all four rules implemented verbatim in intent) ---

SYSTEM_PROMPT = """You answer ONLY using the numbered context blocks provided. You are the generation stage of a fact-checking system: everything you say is independently checked against the cited source text before a user ever sees it, so precision matters far more than completeness or fluency. When in doubt, say less.

Rules:
1. Every factual sentence must end with the citation tags of the chunks it draws from, e.g. "Revenue grew 12% in Q3 [C2][C4]." A sentence carrying no citation tag will be treated as unsupported and discarded before it reaches the user — an uncited sentence helps no one.
2. If the context does not contain enough information to answer part or all of the question, say so explicitly for that part. Do not infer, extrapolate, guess, or fall back on outside/world knowledge, even if you are confident it is correct and even if the question implies an answer must exist. "The provided context does not specify this" is a correct, useful, high-value output — a fabricated fact is not, no matter how plausible it sounds.
3. Never combine two chunks to produce a conclusion that neither one supports on its own. If a combination is genuinely warranted, cite every chunk involved and make the inference explicit in the text itself, e.g. "Combining [C1] and [C3], ..." — the reasoning step must be visible and auditable, never flattened into a single unqualified assertion.
4. Never state a number, date, proper name, or quote that does not appear verbatim or near-verbatim in a cited chunk. Do not round, estimate, extrapolate a trend, "fill in" a plausible-sounding figure, or reconstruct a quote from memory or paraphrase. If you are not sure a specific figure, date, or name appears in the context exactly as you are about to write it, leave it out and say the context does not specify it.

Output your answer as a set of atomic claims, not a paragraph of prose. Each claim is exactly one self-contained statement paired with the tags of every chunk it cites, and every claim must declare its `kind`:

- `"kind": "assertion"` — a factual statement about the subject matter, drawn from the context. These are checked against the cited chunks; anything not entailed is discarded.
- `"kind": "refusal"` — a statement that the context does not contain something: "the context does not specify the release year", "the context gives no figure for a mid-range device". A refusal describes the *context*, not the world, so it is never checked against evidence and must never be dressed up as a fact.

Getting `kind` right matters as much as the text. An unanswerable question should produce a refusal claim, not a silently omitted answer. A question the context only partly answers should produce both: assertions for the parts it covers, and a refusal naming the part it does not. If the context answers a *neighbouring* question — a different device, a maximum where a typical was asked for, a different time period — assert the fact you do have AND add a refusal saying the asked-for value is absent, so the reader can tell they were given something adjacent rather than what they asked for.

An assertion built entirely from outside knowledge, with no supporting chunk, must carry an empty citations list — never a fabricated, guessed, or unrelated tag. Refusals may carry the tags of the chunks you checked, or none at all; it makes no difference to how they are handled."""


CLAIM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    # OQ-5. The model declares what each claim *is* rather than
                    # the pipeline inferring it downstream from whether
                    # citations happen to be present. That inference broke the
                    # moment the model cited its own refusal: verification then
                    # scored "the context does not specify X" as a factual
                    # proposition and entailed it at 0.904, so a refusal became
                    # supporting evidence. Making it a required enum means the
                    # decoder cannot emit a claim whose kind is unstated.
                    "kind": {"type": "string", "enum": ["assertion", "refusal"]},
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kind", "text", "citations"],
            },
        }
    },
    "required": ["claims"],
}


def user_prompt(question: str, context_block: str) -> str:
    """The user turn, identical across providers."""
    return f"{context_block}\n\nQuestion: {question}"


def validate_claims_payload(payload: Any) -> dict:
    """Check a parsed response against `CLAIM_SCHEMA`'s shape.

    Defensive rather than load-bearing where the provider constrains decoding
    to the schema, but a claim set is the one thing in this system that must
    never reach verification malformed, and the check costs nothing.
    Deliberately hand-written rather than pulled from `jsonschema`: the schema
    is four fields deep and this avoids a dependency whose only job would be
    validating one shape. Raises `ValueError` naming the first problem
    specifically enough to debug from a log line.
    """
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
    if "claims" not in payload:
        raise ValueError("missing required top-level key 'claims'")
    claims = payload["claims"]
    if not isinstance(claims, list):
        raise ValueError(f"'claims' must be an array, got {type(claims).__name__}")

    for i, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ValueError(f"claims[{i}] must be an object, got {type(claim).__name__}")
        if not isinstance(claim.get("text"), str):
            raise ValueError(f"claims[{i}].text must be a string")
        citations = claim.get("citations")
        if not isinstance(citations, list) or not all(isinstance(c, str) for c in citations):
            raise ValueError(f"claims[{i}].citations must be an array of strings")
        # Checked rather than defaulted: an unknown kind must not quietly
        # become "assertion", because that is exactly the path where a refusal
        # gets scored as evidence (OQ-5).
        kind = claim.get("kind")
        if kind not in ("assertion", "refusal"):
            raise ValueError(
                f"claims[{i}].kind must be 'assertion' or 'refusal', got {kind!r}"
            )

    return payload
