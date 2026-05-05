"""Configuration system with YAML loading, validation, and defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


DEFAULT_CONFIG_PATH = str(Path(__file__).resolve().parents[2] / "config.yaml")


@dataclass
class PipelineConfig:
    output_dir: str = "./audit_output"
    verbose: bool = True
    retry_limit: int = 3
    subagent_timeout: int = 100000
    resume: bool = True
    auto_install_tools: bool = True
    bug_schema_version: str = "1.0"
    request_delay: float = 0.0
    max_concurrent_infra_agents: int = 5
    codex_reasoning_effort: str = "xhigh"  # minimal | low | medium | high | xhigh
    codex_reasoning_summary: str = "auto"  # none | auto | concise | detailed


@dataclass
class EngagementConfig:
    type: str = "source_code"  # "source_code" or "black_box"
    source_path: str = ""
    source_repo: str = ""
    target_domains: list[str] = field(default_factory=list)
    scope_definition: str = ""
    infra_config: str = ""


@dataclass
class ScoperConfig:
    enabled: bool = False


@dataclass
class BugHunterConfig:
    agents: list[str] = field(default_factory=lambda: ["claude", "codex"])
    iterations: int = 1
    mode: str = "parallel"  # "parallel" or "sequential"
    exclude_paths: list[str] = field(default_factory=list)
    codex_model: str = "gpt-5.5"


@dataclass
class DeduplicatorConfig:
    enabled: bool = False
    similarity_threshold: float = 0.8


@dataclass
class StrictValidatorConfig:
    destructive_poc_policy: str = "cannot_validate"  # or "allow"
    max_concurrent: int = 5
    poc_language: str = "python"


@dataclass
class PerfectionistConfig:
    enabled: bool = False
    max_concurrent: int = 3


@dataclass
class StrictTriagerConfig:
    contrived_threshold: int = 3
    severity_floor: str = "low"


@dataclass
class BugChainerConfig:
    enabled: bool = False
    max_concurrent: int = 2
    rehunt_auto_approve: bool = False


@dataclass
class SkillsHunterConfig:
    enabled: bool = True


@dataclass
class VariantHunterConfig:
    enabled: bool = True


@dataclass
class ModelsConfig:
    scoper: str = "gpt-5.5"
    skills_hunter: str = "gpt-5.5"
    bug_hunter_subagent: str = "opus"
    variant_hunter: str = "gpt-5.5"
    deduplicator: str = "gpt-5.5"
    strict_validator: str = "gpt-5.5"
    perfectionist: str = "gpt-5.5"
    strict_triager: str = "gpt-5.5"
    bug_chainer: str = "gpt-5.5"


@dataclass
class AuthConfig:
    password: str = ""


@dataclass
class AppConfig:
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    engagement: EngagementConfig = field(default_factory=EngagementConfig)
    bug_hunter: BugHunterConfig = field(default_factory=BugHunterConfig)
    deduplicator: DeduplicatorConfig = field(default_factory=DeduplicatorConfig)
    strict_validator: StrictValidatorConfig = field(default_factory=StrictValidatorConfig)
    perfectionist: PerfectionistConfig = field(default_factory=PerfectionistConfig)
    strict_triager: StrictTriagerConfig = field(default_factory=StrictTriagerConfig)
    bug_chainer: BugChainerConfig = field(default_factory=BugChainerConfig)
    scoper: ScoperConfig = field(default_factory=ScoperConfig)
    skills_hunter: SkillsHunterConfig = field(default_factory=SkillsHunterConfig)
    variant_hunter: VariantHunterConfig = field(default_factory=VariantHunterConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    # Legacy aliases for backward compatibility with existing config files
    broad_bug_hunter: BugHunterConfig = field(default_factory=BugHunterConfig)


def _merge_dict_into_dataclass(dc: object, data: dict) -> None:
    """Recursively merge a dictionary into a dataclass instance."""
    for key, value in data.items():
        if not hasattr(dc, key):
            continue
        current = getattr(dc, key)
        if isinstance(current, (PipelineConfig, EngagementConfig,
                                BugHunterConfig, DeduplicatorConfig,
                                StrictValidatorConfig, PerfectionistConfig,
                                StrictTriagerConfig, BugChainerConfig,
                                SkillsHunterConfig, VariantHunterConfig,
                                ModelsConfig, AuthConfig)):
            if isinstance(value, dict):
                _merge_dict_into_dataclass(current, value)
        else:
            # Reject None and type mismatches to prevent bad settings from bricking the app
            if value is None:
                continue
            # Reject bool for int/float fields (bool is subclass of int in Python)
            if isinstance(current, (int, float)) and not isinstance(current, bool) and isinstance(value, bool):
                continue
            if current is not None and not isinstance(value, type(current)):
                # Allow int/float interchange
                if isinstance(current, (int, float)) and isinstance(value, (int, float)):
                    value = type(current)(value)
                else:
                    continue
            # For lists, validate all elements match expected type (default: str)
            if isinstance(value, list) and isinstance(current, list):
                elem_type = type(current[0]) if current else str
                if not all(isinstance(v, elem_type) for v in value):
                    continue
            setattr(dc, key, value)


def load_config(config_path: Optional[str] = None) -> AppConfig:
    """Load configuration from YAML file, falling back to defaults."""
    config = AppConfig()

    if config_path is None and Path(DEFAULT_CONFIG_PATH).exists():
        config_path = DEFAULT_CONFIG_PATH

    if config_path and Path(config_path).exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
        _merge_dict_into_dataclass(config, data)

    return config


def config_to_dict(config: AppConfig) -> dict:
    """Convert a config dataclass tree to a plain dictionary."""
    import dataclasses
    def _convert(obj):
        if dataclasses.is_dataclass(obj):
            return {k: _convert(v) for k, v in dataclasses.asdict(obj).items()}
        return obj
    return _convert(config)


def save_config(config: AppConfig, path: str) -> None:
    """Save configuration to a YAML file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(config_to_dict(config), f, default_flow_style=False, sort_keys=False)
