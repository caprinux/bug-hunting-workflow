"""Tier 0 — unit tests for the openai_codex serialization/merge helpers.

These cover the custom logic that translates the official SDK's typed
notifications into the legacy `codex_event` stream schema that the stream-replay
endpoint (routes.py) parses, plus the delta-union merge of structured messages.
"""

from __future__ import annotations

import json

import pytest

from bug_hunter.core.cli_wrapper import (
    _codex_reasoning_text,
    _merge_codex_messages,
    _serialize_codex_event,
    split_codex_agent_message,
)

# Real SDK types — build genuine notifications rather than mocks so the tests
# fail if the upstream schema shifts under us.
from openai_codex.models import Notification
from openai_codex.generated.v2_all import (
    AgentMessageThreadItem,
    CommandExecutionStatus,
    CommandExecutionThreadItem,
    ItemCompletedNotification,
    ReasoningThreadItem,
    ThreadItem,
    ThreadTokenUsage,
    ThreadTokenUsageUpdatedNotification,
    TokenUsageBreakdown,
    Turn,
    TurnCompletedNotification,
    TurnStatus,
)


def _item(item) -> ThreadItem:
    return ThreadItem(root=item)


def _completed(item) -> Notification:
    return Notification(
        method="item/completed",
        payload=ItemCompletedNotification(
            item=_item(item), threadId="t1", turnId="tn1", completedAtMs=1
        ),
    )


def test_serialize_agent_message_preserves_replay_schema():
    text = '{"narrative":"looking at auth","bugs":[{"x":1}]}'
    ev = _serialize_codex_event(
        _completed(AgentMessageThreadItem(id="i1", text=text, type="agentMessage"))
    )
    assert ev["type"] == "codex_event"
    assert ev["event_type"] == "item_completed"
    assert ev["item_type"] == "agent_message"
    assert ev["text"] == text


def test_serialize_command_execution():
    ev = _serialize_codex_event(
        _completed(
            CommandExecutionThreadItem(
                id="i2",
                command="grep -r secret .",
                aggregatedOutput="found 3\n",
                commandActions=[],
                cwd="/tmp",
                status=CommandExecutionStatus.completed,
                type="commandExecution",
            )
        )
    )
    assert ev["item_type"] == "command_execution"
    assert ev["command"] == "grep -r secret ."
    assert ev["output"].startswith("found 3")


def test_serialize_reasoning_joins_summary_and_content():
    item = ReasoningThreadItem(
        id="i3", summary=["thinking about idor"], content=["step1", "step2"], type="reasoning"
    )
    ev = _serialize_codex_event(_completed(item))
    assert ev["item_type"] == "reasoning"
    assert ev["text"] == "thinking about idor step1 step2"
    # helper directly
    assert _codex_reasoning_text(item) == "thinking about idor step1 step2"


def test_serialize_token_usage():
    tu = ThreadTokenUsage(
        total=TokenUsageBreakdown(
            inputTokens=100, outputTokens=50, cachedInputTokens=20,
            reasoningOutputTokens=5, totalTokens=175,
        ),
        last=TokenUsageBreakdown(
            inputTokens=1, outputTokens=1, cachedInputTokens=1,
            reasoningOutputTokens=1, totalTokens=4,
        ),
    )
    ev = _serialize_codex_event(
        Notification(
            method="thread/tokenUsage/updated",
            payload=ThreadTokenUsageUpdatedNotification(threadId="t1", turnId="tn1", tokenUsage=tu),
        )
    )
    assert ev["event_type"] == "token_usage"
    assert ev["usage"] == {"input_tokens": 100, "output_tokens": 50, "cached_input_tokens": 20}


def test_serialize_turn_completed_status():
    ev = _serialize_codex_event(
        Notification(
            method="turn/completed",
            payload=TurnCompletedNotification(
                threadId="t1", turn=Turn(id="tn1", items=[], status=TurnStatus.completed)
            ),
        )
    )
    assert ev["event_type"] == "turn_completed"
    assert ev["status"] == "completed"


def test_serialized_events_survive_the_routes_replay_parser():
    """The exact loop from routes.py:517-540 must reconstruct our events."""
    serialized = [
        _serialize_codex_event(
            _completed(AgentMessageThreadItem(id="i1", text='{"narrative":"hi there","bugs":[]}', type="agentMessage"))
        ),
        _serialize_codex_event(
            _completed(CommandExecutionThreadItem(
                id="i2", command="ls -la", aggregatedOutput="", commandActions=[],
                cwd="/tmp", status=CommandExecutionStatus.completed, type="commandExecution"))
        ),
        _serialize_codex_event(
            _completed(ReasoningThreadItem(id="i3", summary=["mulling"], content=[], type="reasoning"))
        ),
    ]
    events = []
    for raw in serialized:
        if raw.get("type") == "codex_event" and raw.get("event_type") == "item_completed":
            it = raw.get("item_type", "")
            if it == "agent_message" and raw.get("text"):
                narrative, data_preview = split_codex_agent_message(raw["text"])
                if narrative:
                    events.append({"event_type": "text", "text": narrative[:500]})
                if data_preview:
                    events.append({"event_type": "text", "text": data_preview[:500]})
            elif it == "command_execution":
                events.append({"event_type": "tool_use", "tool_name": "Bash", "tool_input": raw.get("command", "")[:200]})
            elif it == "reasoning" and raw.get("text"):
                events.append({"event_type": "thinking", "thinking": raw["text"][:500]})

    assert {"event_type": "text", "text": "hi there"} in events
    assert any(e["event_type"] == "tool_use" and e["tool_input"] == "ls -la" for e in events)
    assert any(e["event_type"] == "thinking" and e["thinking"] == "mulling" for e in events)


def test_serialize_never_raises_on_odd_payload():
    class Weird:
        method = "totally/unknown"
        payload = object()
    ev = _serialize_codex_event(Weird())
    assert ev["event_type"] == "other"


# --- split_codex_agent_message ---

def test_split_agent_message_narrative_and_data():
    narrative, data = split_codex_agent_message('{"narrative":"scanning","bugs":[{"x":1}]}')
    assert narrative == "scanning"
    assert json.loads(data) == {"bugs": [{"x": 1}]}


def test_split_agent_message_hides_empty_data():
    narrative, data = split_codex_agent_message('{"narrative":"nothing yet","bugs":[]}')
    assert narrative == "nothing yet"
    assert data is None


def test_split_agent_message_non_json_passthrough():
    narrative, data = split_codex_agent_message("just prose")
    assert narrative == "just prose"
    assert data is None


# --- _merge_codex_messages (delta union across streamed messages) ---

def test_merge_unions_array_deltas_and_drops_narrative():
    merged = _merge_codex_messages([
        '{"narrative":"a","bugs":[{"x":1}]}',
        '{"narrative":"b","bugs":[{"y":2}]}',
    ])
    assert merged == {"bugs": [{"x": 1}, {"y": 2}]}


def test_merge_scalar_latest_non_empty_wins():
    merged = _merge_codex_messages([
        '{"summary":"","bugs":[]}',
        '{"summary":"final","bugs":[{"z":3}]}',
    ])
    assert merged["summary"] == "final"
    assert merged["bugs"] == [{"z": 3}]


def test_merge_falls_back_to_last_when_unparseable():
    assert _merge_codex_messages(["not json", "also not"]) == "also not"


def test_merge_empty_returns_none():
    assert _merge_codex_messages([]) is None
