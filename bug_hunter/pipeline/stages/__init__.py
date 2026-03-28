"""Pipeline stages — all stages are registered on import."""

from bug_hunter.pipeline.stages.registry import get, list_stages

from bug_hunter.pipeline.stages.setup import SetupStage
from bug_hunter.pipeline.stages.scoper import ScoperStage
from bug_hunter.pipeline.stages.skills_hunter import SkillsHunterStage
from bug_hunter.pipeline.stages.bug_hunter import BugHunterStage
from bug_hunter.pipeline.stages.variant_hunter import VariantHunterStage
from bug_hunter.pipeline.stages.deduplicator import DeduplicatorStage
from bug_hunter.pipeline.stages.scope_validator import ScopeValidatorStage
from bug_hunter.pipeline.stages.strict_validator import StrictValidatorStage
from bug_hunter.pipeline.stages.perfectionist import PerfectionistStage
from bug_hunter.pipeline.stages.strict_triager import StrictTriagerStage
from bug_hunter.pipeline.stages.bug_chainer import BugChainerStage
from bug_hunter.pipeline.stages.summarizer import SummarizerStage
from bug_hunter.pipeline.stages.testing_setup import TestingSetupStage

__all__ = [
    "get", "list_stages",
    "SetupStage", "ScoperStage", "SkillsHunterStage",
    "BugHunterStage", "VariantHunterStage",
    "DeduplicatorStage", "ScopeValidatorStage",
    "StrictValidatorStage", "PerfectionistStage", "StrictTriagerStage",
    "BugChainerStage", "SummarizerStage", "TestingSetupStage",
]
