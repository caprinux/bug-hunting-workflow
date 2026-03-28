"""Validate agent outputs against JSON schemas."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas"

_schema_cache: dict[str, dict] = {}


def _normalize_finding(finding: dict) -> dict:
    """Light normalization — only copies the finding, no value transformations.

    Field name aliases and value normalization have been removed.
    Agent prompts now enforce strict field names and values directly.
    """
    return dict(finding)


def _load_schema(schema_name: str) -> Optional[dict]:
    """Load a JSON schema by name."""
    if schema_name in _schema_cache:
        return _schema_cache[schema_name]
    schema_path = SCHEMAS_DIR / f"{schema_name}.json"
    if not schema_path.exists():
        logger.warning(f"Schema not found: {schema_path}")
        return None
    with open(schema_path) as f:
        schema = json.load(f)
    _schema_cache[schema_name] = schema
    return schema


def validate_bug_finding(finding: dict) -> tuple[bool, list[str]]:
    """Validate a single bug finding against the schema.

    Returns (is_valid, list_of_errors).
    """
    errors = []
    schema = _load_schema("bug_finding")
    if not schema:
        return True, []  # No schema = skip validation

    required = schema.get("required", [])
    for field in required:
        if field not in finding or not finding[field]:
            errors.append(f"Missing required field: {field}")

    props = schema.get("properties", {})

    if "confidence" in finding:
        allowed = props.get("confidence", {}).get("enum", [])
        if allowed and finding["confidence"] not in allowed:
            errors.append(f"Invalid confidence value: {finding['confidence']}, expected one of {allowed}")

    if "severity" in finding:
        allowed = props.get("severity", {}).get("enum", [])
        if allowed and finding["severity"] not in allowed:
            errors.append(f"Invalid severity value: {finding['severity']}, expected one of {allowed}")

    if "found_by" in finding and not isinstance(finding["found_by"], list):
        errors.append("'found_by' must be a list")

    return len(errors) == 0, errors


def validate_findings_list(findings: list[dict],
                           quarantine_dir: str = None) -> tuple[list[dict], list[dict]]:
    """Validate a list of findings, separating valid from invalid.

    Normalizes field names before validation so findings from different
    LLMs (Claude vs Codex) are accepted even if they use different names.
    """
    valid = []
    quarantined = []

    for finding in findings:
        # Normalize first, then validate
        normalized = _normalize_finding(finding)
        is_valid, errors = validate_bug_finding(normalized)
        if is_valid:
            valid.append(normalized)
        else:
            normalized["_validation_errors"] = errors
            quarantined.append(normalized)
            logger.warning(f"Quarantined finding {normalized.get('id', '?')}: {errors}")

    if quarantine_dir and quarantined:
        os.makedirs(quarantine_dir, exist_ok=True)
        with open(os.path.join(quarantine_dir, "quarantined_findings.json"), "w") as f:
            json.dump(quarantined, f, indent=2)

    return valid, quarantined
