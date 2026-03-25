"""Parse LLM results that may be JSON or prose text containing JSON."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def parse_agent_result(result: Any, expected_keys: list[str] = None,
                       stage_name: str = "", save_raw_to: str = "") -> dict:
    """Parse an agent result into a dict, handling both JSON and text responses.

    When Claude returns a prose report instead of JSON, this function
    attempts to extract embedded JSON. If extraction fails, it saves
    the raw text and returns an empty dict.

    Args:
        result: The raw result from CLIResult.result (dict, str, or other)
        expected_keys: Keys to look for when extracting JSON from text
        stage_name: Stage name for logging
        save_raw_to: If provided, save raw text to this file path

    Returns:
        Parsed dict, or empty dict if parsing fails
    """
    if isinstance(result, dict):
        return result

    if not isinstance(result, str) or not result.strip():
        return {}

    text = result.strip()

    # Try 1: parse entire text as JSON
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    # Try 2: extract JSON from markdown code blocks
    for match in re.finditer(r'```(?:json)?\s*([\s\S]*?)```', text, re.IGNORECASE):
        candidate = match.group(1).strip()
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                logger.info(f"[{stage_name}] Extracted JSON from code block")
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue

    # Try 3: find a JSON object with expected keys
    if expected_keys:
        for key in expected_keys:
            pattern = r'\{[^{}]*"' + re.escape(key) + r'"'
            for match in re.finditer(pattern, text):
                start = match.start()
                # Try to parse from this position
                try:
                    decoder = json.JSONDecoder()
                    parsed, _ = decoder.raw_decode(text[start:])
                    if isinstance(parsed, dict):
                        logger.info(f"[{stage_name}] Extracted JSON containing '{key}'")
                        return parsed
                except (json.JSONDecodeError, TypeError):
                    continue

    # Try 4: find any JSON object using raw_decode
    decoder = json.JSONDecoder()
    for i, char in enumerate(text):
        if char != '{':
            continue
        try:
            parsed, _ = decoder.raw_decode(text[i:])
            if isinstance(parsed, dict) and len(parsed) > 1:
                logger.info(f"[{stage_name}] Extracted JSON object at position {i}")
                return parsed
        except (json.JSONDecodeError, TypeError):
            continue

    # All extraction failed — save raw text if requested
    if save_raw_to:
        try:
            with open(save_raw_to, "w") as f:
                f.write(text)
            logger.warning(f"[{stage_name}] Could not extract JSON — saved raw text to {save_raw_to}")
        except Exception:
            pass

    logger.warning(f"[{stage_name}] Agent returned text instead of JSON ({len(text)} chars)")
    return {}
