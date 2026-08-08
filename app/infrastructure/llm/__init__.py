"""Generation providers.

`provider.py` is the only module the rest of the application imports from
here. `gemini.py` and `openai_client.py` are reached through it, so a
deployment that uses one provider never imports the other's SDK.
"""

from app.infrastructure.llm.provider import LLMClaimGenerator, generate_claims, stream_claims

__all__ = ["LLMClaimGenerator", "generate_claims", "stream_claims"]
