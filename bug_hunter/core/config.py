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
    subagent_timeout: int = 3600  # 1 hour per subagent
    resume: bool = True
    auto_install_tools: bool = True
    bug_schema_version: str = "1.0"
    request_delay: float = 0.0
    max_concurrent_infra_agents: int = 5


@dataclass
class EngagementConfig:
    type: str = "source_code"  # "source_code" or "black_box"
    source_path: str = ""
    source_repo: str = ""
    target_domains: list[str] = field(default_factory=list)
    scope_definition: str = ""
    infra_config: str = ""


@dataclass
class WorkloadDividerConfig:
    enabled: bool = False
    subsystem_strategy: str = "auto"
    manual_subsystems: list[str] = field(default_factory=list)


@dataclass
class BroadBugHunterConfig:
    agents: list[str] = field(default_factory=lambda: ["claude", "codex"])
    context_budget: int = 150000
    phase2_enabled: bool = True
    max_concurrent_subagents: Optional[int] = None
    shared_code_paths: list[str] = field(default_factory=list)
    file_extensions: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    codex_model: str = "gpt-5.4"  # model passed to codex CLI via -m flag


@dataclass
class ScopeEnumeratorConfig:
    recon_mode: str = "both"  # "active", "passive", "both"


@dataclass
class BlackBoxBugHunterConfig:
    checkpoint_context_threshold: float = 0.7


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
    max_concurrent: int = 3


@dataclass
class StrictTriagerConfig:
    contrived_threshold: int = 3
    severity_floor: str = "low"


@dataclass
class BugChainerConfig:
    max_concurrent: int = 2
    rehunt_auto_approve: bool = False


@dataclass
class ModelsConfig:
    scoper: str = "opus"
    bug_hunter_subagent: str = "opus"
    deduplicator: str = "opus"
    strict_validator: str = "opus"
    perfectionist: str = "opus"
    strict_triager: str = "opus"
    bug_chainer: str = "opus"


@dataclass
class AuthConfig:
    password: str = ""


@dataclass
class AppConfig:
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    engagement: EngagementConfig = field(default_factory=EngagementConfig)
    workload_divider: WorkloadDividerConfig = field(default_factory=WorkloadDividerConfig)
    broad_bug_hunter: BroadBugHunterConfig = field(default_factory=BroadBugHunterConfig)
    scope_enumerator: ScopeEnumeratorConfig = field(default_factory=ScopeEnumeratorConfig)
    black_box_bug_hunter: BlackBoxBugHunterConfig = field(default_factory=BlackBoxBugHunterConfig)
    deduplicator: DeduplicatorConfig = field(default_factory=DeduplicatorConfig)
    strict_validator: StrictValidatorConfig = field(default_factory=StrictValidatorConfig)
    perfectionist: PerfectionistConfig = field(default_factory=PerfectionistConfig)
    strict_triager: StrictTriagerConfig = field(default_factory=StrictTriagerConfig)
    bug_chainer: BugChainerConfig = field(default_factory=BugChainerConfig)
    models: ModelsConfig = field(default_factory=ModelsConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)


def _merge_dict_into_dataclass(dc: object, data: dict) -> None:
    """Recursively merge a dictionary into a dataclass instance."""
    for key, value in data.items():
        if not hasattr(dc, key):
            continue
        current = getattr(dc, key)
        if isinstance(current, (PipelineConfig, EngagementConfig, WorkloadDividerConfig,
                                BroadBugHunterConfig, ScopeEnumeratorConfig,
                                BlackBoxBugHunterConfig, DeduplicatorConfig,
                                StrictValidatorConfig, PerfectionistConfig,
                                StrictTriagerConfig, BugChainerConfig,
                                ModelsConfig, AuthConfig)):
            if isinstance(value, dict):
                _merge_dict_into_dataclass(current, value)
        else:
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
