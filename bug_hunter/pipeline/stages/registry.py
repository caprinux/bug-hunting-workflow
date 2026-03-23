"""Stage registry — maps stage names to implementations."""

from __future__ import annotations

from typing import Optional, Type

from bug_hunter.pipeline.stages.base import PipelineStage

_REGISTRY: dict[str, Type[PipelineStage]] = {}


def register(stage_cls: Type[PipelineStage]) -> Type[PipelineStage]:
    """Decorator to register a pipeline stage."""
    instance = stage_cls()
    _REGISTRY[instance.name] = stage_cls
    return stage_cls


def get(name: str) -> Optional[Type[PipelineStage]]:
    """Get a stage class by name."""
    return _REGISTRY.get(name)


def list_stages() -> list[str]:
    """List all registered stage names."""
    return list(_REGISTRY.keys())
