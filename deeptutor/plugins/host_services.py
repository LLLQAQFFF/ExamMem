"""Versioned, domain-neutral Host services available to first-party plugins."""

from __future__ import annotations

from deeptutor.agents._shared.json_output import extract_json_object
from deeptutor.services.embedding.validation import validate_embedding_batch


async def complete(
    *,
    prompt: str,
    system_prompt: str,
    response_format: dict[str, object],
    temperature: float,
) -> str:
    """Call the configured non-streaming Host LLM through a stable plugin seam."""

    from deeptutor.services.llm import complete as host_complete

    return await host_complete(
        prompt=prompt,
        system_prompt=system_prompt,
        response_format=response_format,
        temperature=temperature,
    )


__all__ = ["complete", "extract_json_object", "validate_embedding_batch"]
