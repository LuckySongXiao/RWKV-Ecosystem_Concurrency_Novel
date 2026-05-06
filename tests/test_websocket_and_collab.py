import sys
import os
import json
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tools.agent_collaboration import (
    AgentMessageBus,
    AgentEvent,
    EventType,
    MessagePriority,
    CollaborationPattern,
)


class TestAgentMessageBus(unittest.TestCase):
    def setUp(self):
        self.bus = AgentMessageBus()
        self.received_events = []

    def test_publish_and_subscribe(self):
        callback = lambda e: self.received_events.append(e)
        self.bus.subscribe("agent_a", EventType.REVIEW_REJECTION, callback)

        event = AgentEvent(
            event_type=EventType.REVIEW_REJECTION,
            source_agent="reviewer",
            data={"chapter_id": 1, "reason": "inconsistent"},
        )
        self.bus.publish(event)

        self.assertEqual(len(self.received_events), 1)
        self.assertEqual(self.received_events[0].source_agent, "reviewer")
        self.assertEqual(self.received_events[0].data["chapter_id"], 1)

    def test_filter_by_source_agent(self):
        callback_a = lambda e: self.received_events.append(("a", e))
        callback_b = lambda e: self.received_events.append(("b", e))

        self.bus.subscribe("agent_a", EventType.STATE_CHANGE, callback_a, filter_agent="world_mgr")
        self.bus.subscribe("agent_b", EventType.STATE_CHANGE, callback_b, filter_agent="writer")

        event1 = AgentEvent(event_type=EventType.STATE_CHANGE, source_agent="world_mgr", data={})
        event2 = AgentEvent(event_type=EventType.STATE_CHANGE, source_agent="writer", data={})

        self.bus.publish(event1)
        self.bus.publish(event2)

        sources = [r[1].source_agent for r in self.received_events]
        self.assertIn("world_mgr", sources)
        self.assertIn("writer", sources)

        agent_a_received = [r for r in self.received_events if r[0] == "a"]
        agent_b_received = [r for r in self.received_events if r[0] == "b"]
        self.assertEqual(len(agent_a_received), 1)
        self.assertEqual(agent_a_received[0][1].source_agent, "world_mgr")
        self.assertEqual(len(agent_b_received), 1)
        self.assertEqual(agent_b_received[0][1].source_agent, "writer")

    def test_send_point_to_point(self):
        self.bus.send("writer", "reviewer", EventType.TASK_REQUEST, data={"action": "review"})

        messages = self.bus.get_pending_messages("reviewer")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].source_agent, "writer")
        self.assertEqual(messages[0].data["action"], "review")

    def test_get_history(self):
        for i in range(5):
            self.bus.publish(AgentEvent(
                event_type=EventType.PROGRESS_UPDATE,
                source_agent="pipeline",
                data={"step": i},
            ))

        history = self.bus.get_history(limit=3)
        self.assertEqual(len(history), 3)

        history_filtered = self.bus.get_history(event_type=EventType.PROGRESS_UPDATE, limit=10)
        self.assertEqual(len(history_filtered), 5)

    def test_unsubscribe(self):
        callback = lambda e: self.received_events.append(e)
        self.bus.subscribe("agent_a", EventType.ERROR_REPORT, callback)

        self.bus.publish(AgentEvent(event_type=EventType.ERROR_REPORT, source_agent="sys", data={}))
        self.assertEqual(len(self.received_events), 1)

        self.bus.unsubscribe("agent_a", EventType.ERROR_REPORT)

        self.bus.publish(AgentEvent(event_type=EventType.ERROR_REPORT, source_agent="sys", data={}))
        self.assertEqual(len(self.received_events), 1)

    def test_clear_history(self):
        self.bus.publish(AgentEvent(event_type=EventType.PROGRESS_UPDATE, source_agent="sys", data={}))
        self.bus.clear_history()
        history = self.bus.get_history()
        self.assertEqual(len(history), 0)


class TestCollaborationPattern(unittest.TestCase):
    def setUp(self):
        self.bus = AgentMessageBus()
        self.pattern = CollaborationPattern(self.bus)

    def test_review_rewrite_cycle_without_fns(self):
        drafts = [{"chapter_id": 1, "content": "test"}]
        result = self.pattern.review_rewrite_cycle("writer", "reviewer", drafts, max_attempts=2)
        self.assertEqual(len(result), 1)

    def test_review_rewrite_cycle_with_review_fn(self):
        drafts = [
            {"chapter_id": 1, "content": "short"},
            {"chapter_id": 2, "content": "good content here"},
        ]

        class MockReviewResult:
            def __init__(self, passed, rejections=None):
                self.passed = passed
                self.rejections = rejections or []

        call_count = [0]

        def mock_review(drafts):
            call_count[0] += 1
            if call_count[0] >= 2:
                return MockReviewResult(passed=True)
            return MockReviewResult(passed=False, rejections=[{"chapter_id": 1, "reason": "too short"}])

        def mock_rewrite(rejections, drafts):
            return [{"chapter_id": 1, "content": "expanded content"}]

        result = self.pattern.review_rewrite_cycle(
            "writer", "reviewer", drafts,
            max_attempts=3,
            review_fn=mock_review,
            rewrite_fn=mock_rewrite,
        )

        self.assertTrue(call_count[0] >= 2)
        self.assertEqual(len(result), 2)

    def test_conflict_resolution_without_fn(self):
        conflicts = [{"conflict_type": "unique_item", "description": "test"}]
        result = self.pattern.conflict_resolution("detector", "resolver", conflicts)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "reported")

    def test_conflict_resolution_with_fn(self):
        conflicts = [{"conflict_type": "unique_item", "description": "test"}]

        def mock_resolve(conflict):
            return {"conflict": conflict, "resolution": "keep_first", "status": "resolved"}

        result = self.pattern.conflict_resolution(
            "detector", "resolver", conflicts,
            resolve_fn=mock_resolve,
            autonomy_level="auto",
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "auto_resolved")
        self.assertEqual(result[0]["resolution"], "keep_first")

    def test_conflict_resolution_confirm_level(self):
        conflicts = [{"conflict_type": "territory", "description": "test"}]

        def mock_resolve(conflict):
            return {"conflict": conflict, "resolution": "merge", "status": "resolved"}

        result = self.pattern.conflict_resolution(
            "detector", "resolver", conflicts,
            resolve_fn=mock_resolve,
            autonomy_level="confirm",
        )
        self.assertEqual(result[0]["status"], "needs_human_confirmation")

    def test_foreshadowing_coordination(self):
        planted = [{"foreshadowing_id": "fs_1", "description": "mysterious key"}]
        resolved = [{"foreshadowing_id": "fs_0", "description": "old prophecy"}]

        self.pattern.foreshadowing_coordination("writer", "world_mgr", 5, planted, resolved)

        messages = self.bus.get_pending_messages("world_mgr")
        self.assertEqual(len(messages), 2)

    def test_state_change_broadcast(self):
        self.pattern.state_change_broadcast("world_mgr", 3, {"character_changes": [{"character_id": "char_1"}]})

        history = self.bus.get_history(event_type=EventType.STATE_CHANGE)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["data"]["chapter_id"], 3)


class TestWorldStateEnginePersistence(unittest.TestCase):
    def test_load_from_files_includes_conflicts_and_changelog(self):
        from src.core.world_state_engine import WorldStateEngine, CharacterState, FactionState, Conflict
        from src.core.file_manager import FileManager

        with patch.object(FileManager, '__init__', lambda self, *a, **kw: None):
            fm = FileManager.__new__(FileManager)
            fm.output_dir = os.path.join(os.path.dirname(__file__), "test_output_persist")
            os.makedirs(os.path.join(fm.output_dir, "tracking"), exist_ok=True)

            engine = WorldStateEngine.__new__(WorldStateEngine)
            engine._fm = fm
            engine._logger = MagicMock()
            engine.characters = {}
            engine.factions = {}
            engine.economy = None
            engine.entity_store = MagicMock()
            engine.entity_store.to_dict = lambda: {"entities": {}, "relations": [], "foreshadowings": [], "timeline": []}
            engine._conflicts = []
            engine.changelog = []

            conflict = Conflict(
                conflict_type="unique_item",
                description="test conflict",
                chapter_id=1,
                resolution="pending",
            )
            engine._conflicts.append(conflict)
            engine.changelog.append({"chapter_id": 1, "timestamp": "2026-01-01", "character_changes": []})

            engine.persist()

            conflicts_path = fm.tracking_path("conflicts.jsonl")
            changelog_path = fm.tracking_path("changelog.jsonl")

            self.assertTrue(os.path.exists(conflicts_path))
            self.assertTrue(os.path.exists(changelog_path))

            with open(conflicts_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            data = json.loads(lines[0])
            self.assertEqual(data["conflict_type"], "unique_item")

            with open(changelog_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            self.assertEqual(len(lines), 1)
            data = json.loads(lines[0])
            self.assertEqual(data["chapter_id"], 1)

            import shutil
            shutil.rmtree(fm.output_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
