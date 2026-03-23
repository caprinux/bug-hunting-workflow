"""Pipeline stages — all stages are registered on import."""

from bug_hunter.pipeline.stages.registry import get, list_stages

from bug_hunter.pipeline.stages.setup import SetupStage
from bug_hunter.pipeline.stages.workload_divider import WorkloadDividerStage
from bug_hunter.pipeline.stages.scope_enumerator import ScopeEnumeratorStage
from bug_hunter.pipeline.stages.bug_hunter import BugHunterStage
from bug_hunter.pipeline.stages.deduplicator import DeduplicatorStage
from bug_hunter.pipeline.stages.scope_validator import ScopeValidatorStage
from bug_hunter.pipeline.stages.strict_validator import StrictValidatorStage
from bug_hunter.pipeline.stages.perfectionist import PerfectionistStage
from bug_hunter.pipeline.stages.strict_triager import StrictTriagerStage
from bug_hunter.pipeline.stages.bug_chainer import BugChainerStage

__all__ = [
    "get", "list_stages",
    "SetupStage", "WorkloadDividerStage", "ScopeEnumeratorStage",
    "BugHunterStage", "DeduplicatorStage", "ScopeValidatorStage",
    "StrictValidatorStage", "PerfectionistStage", "StrictTriagerStage",
    "BugChainerStage",
]
