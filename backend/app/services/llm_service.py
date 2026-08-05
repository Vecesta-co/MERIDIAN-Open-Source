"""
MERIDIAN LLM Service — Phase 2 Agent Runtime.

Provides a thin wrapper around LiteLLM for making LLM calls.
Supports all major providers (OpenAI, Anthropic, Gemini, etc.)
via LiteLLM's unified interface.

Environment variables for providers:
  - OPENAI_API_KEY
  - ANTHROPIC_API_KEY
  - GEMINI_API_KEY
  - etc. (see LiteLLM docs)

The default model is configurable via LITELLM_MODEL.
Use the `mock_llm` dependency injection in tests to avoid external API calls.
"""

import asyncio
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# System prompt for all MERIDIAN mission steps
SYSTEM_PROMPT = "You are executing MERIDIAN mission step. Follow the instructions precisely and return only the requested output."


async def call_llm(
    prompt: str,
    model: Optional[str] = None,
    system_prompt: str = SYSTEM_PROMPT,
    temperature: float = 0.2,
    max_tokens: int = 2048,
    timeout_seconds: int = 300,
) -> Dict[str, Any]:
    """
    Make an LLM call via LiteLLM.

    Args:
        prompt: The user prompt / rendered template.
        model: Model to use (defaults to settings.LITELLM_MODEL).
        system_prompt: System prompt to prepend.
        temperature: Sampling temperature.
        max_tokens: Max tokens to generate.
        timeout_seconds: Timeout for the LLM call.

    Returns:
        Dict with keys:
            - text: str — the generated response text
            - model: str — the model used
            - prompt_tokens: int
            - completion_tokens: int
            - total_tokens: int
            - finish_reason: str

    Raises:
        TimeoutError: If the LLM call exceeds timeout_seconds.
        RuntimeError: If the LLM call fails (transient errors wrapped).
    """
    try:
        import litellm
    except ImportError as exc:
        raise RuntimeError(
            "LiteLLM is not installed. Run `pip install litellm` or add it to requirements.txt."
        ) from exc

    # Use per-call model or default
    model_name = model or settings.LITELLM_MODEL

    # Build kwargs
    kwargs: Dict[str, Any] = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if settings.LITELLM_API_KEY:
        kwargs["api_key"] = settings.LITELLM_API_KEY
    if settings.LITELLM_API_BASE:
        kwargs["api_base"] = settings.LITELLM_API_BASE

    logger.info(
        "LLM call: model=%s prompt_len=%d timeout=%ds",
        model_name,
        len(prompt),
        timeout_seconds,
    )

    try:
        response = await asyncio.wait_for(
            litellm.acompletion(**kwargs),
            timeout=timeout_seconds,
        )

        # Extract response
        choice = response.choices[0]
        text = choice.message.content or ""
        usage = response.usage or {}

        result = {
            "text": text,
            "model": response.model or model_name,
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
            "total_tokens": getattr(usage, "total_tokens", 0),
            "finish_reason": getattr(choice, "finish_reason", "stop"),
        }
        logger.info(
            "LLM call succeeded: model=%s tokens=%d",
            result["model"],
            result["total_tokens"],
        )
        return result

    except asyncio.TimeoutError:
        raise TimeoutError(
            f"LLM call timed out after {timeout_seconds}s (model={model_name})"
        )
    except Exception as exc:
        # Wrap transient errors (rate limits, network, etc.)
        logger.warning("LLM call failed: %s", str(exc))
        raise RuntimeError(f"LLM call failed: {str(exc)}") from exc
