"""Agent 间协作机制 - 消息总线与协作模式

核心设计:
- AgentMessageBus: Agent 间异步消息传递
- CollaborationPattern: 预定义协作模式（审核-重写循环、冲突裁决、伏笔协调）
- AgentEvent: 标准化事件格式
"""

import time
import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from src.core.logger import Logger


class EventType(Enum):
    TASK_REQUEST = "task_request"
    TASK_RESULT = "task_result"
    CONFLICT_REPORT = "conflict_report"
    CONFLICT_RESOLUTION = "conflict_resolution"
    REVIEW_REJECTION = "review_rejection"
    REVIEW_APPROVAL = "review_approval"
    FORESHADOWING_UPDATE = "foreshadowing_update"
    STATE_CHANGE = "state_change"
    PROGRESS_UPDATE = "progress_update"
    ERROR_REPORT = "error_report"


class MessagePriority(Enum):
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


@dataclass
class AgentEvent:
    event_type: EventType
    source_agent: str
    target_agent: str = ""
    data: Dict = field(default_factory=dict)
    priority: MessagePriority = MessagePriority.NORMAL
    timestamp: float = field(default_factory=time.time)
    event_id: str = ""

    def __post_init__(self):
        if not self.event_id:
            self.event_id = f"{self.source_agent}_{self.event_type.value}_{int(self.timestamp * 1000)}"

    def to_dict(self) -> Dict:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "source_agent": self.source_agent,
            "target_agent": self.target_agent,
            "data": self.data,
            "priority": self.priority.value,
            "timestamp": self.timestamp,
        }


@dataclass
class Subscription:
    subscriber_id: str
    event_type: EventType
    callback: Callable
    filter_agent: str = ""


class AgentMessageBus:
    """Agent 间消息总线

    支持:
    - 发布/订阅模式: Agent 订阅感兴趣的事件类型
    - 点对点消息: 直接向目标 Agent 发送消息
    - 事件过滤: 按来源 Agent 过滤事件
    - 消息历史: 保留最近的消息记录
    """

    MAX_HISTORY = 1000

    def __init__(self, logger: Optional[Logger] = None):
        self._logger = logger or Logger.get()
        self._subscriptions: List[Subscription] = []
        self._history: List[AgentEvent] = []
        self._lock = threading.Lock()
        self._agent_queues: Dict[str, List[AgentEvent]] = {}

    def subscribe(
        self,
        subscriber_id: str,
        event_type: EventType,
        callback: Callable,
        filter_agent: str = "",
    ):
        """订阅事件

        Args:
            subscriber_id: 订阅者 Agent ID
            event_type: 感兴趣的事件类型
            callback: 事件回调函数
            filter_agent: 只接收来自指定 Agent 的事件（空字符串表示不过滤）
        """
        sub = Subscription(
            subscriber_id=subscriber_id,
            event_type=event_type,
            callback=callback,
            filter_agent=filter_agent,
        )
        with self._lock:
            self._subscriptions.append(sub)
        self._logger.info(f"Agent [{subscriber_id}] subscribed to {event_type.value}")

    def unsubscribe(self, subscriber_id: str, event_type: EventType = None):
        """取消订阅"""
        with self._lock:
            if event_type:
                self._subscriptions = [
                    s for s in self._subscriptions
                    if not (s.subscriber_id == subscriber_id and s.event_type == event_type)
                ]
            else:
                self._subscriptions = [
                    s for s in self._subscriptions
                    if s.subscriber_id != subscriber_id
                ]

    def publish(self, event: AgentEvent):
        """发布事件"""
        with self._lock:
            self._history.append(event)
            if len(self._history) > self.MAX_HISTORY:
                self._history = self._history[-self.MAX_HISTORY:]

            if event.target_agent:
                if event.target_agent not in self._agent_queues:
                    self._agent_queues[event.target_agent] = []
                self._agent_queues[event.target_agent].append(event)

        for sub in self._subscriptions:
            if sub.event_type != event.event_type:
                continue
            if sub.filter_agent and sub.filter_agent != event.source_agent:
                continue
            try:
                sub.callback(event)
            except Exception as e:
                self._logger.error(
                    f"Event callback error: {sub.subscriber_id} - {e}"
                )

    def send(self, source: str, target: str, event_type: EventType,
             data: Dict = None, priority: MessagePriority = MessagePriority.NORMAL):
        """发送点对点消息"""
        event = AgentEvent(
            event_type=event_type,
            source_agent=source,
            target_agent=target,
            data=data or {},
            priority=priority,
        )
        self.publish(event)

    def get_pending_messages(self, agent_id: str) -> List[AgentEvent]:
        """获取 Agent 的待处理消息"""
        with self._lock:
            messages = self._agent_queues.pop(agent_id, [])
            return messages

    def get_history(self, event_type: EventType = None, source_agent: str = "",
                    limit: int = 50) -> List[Dict]:
        """获取消息历史"""
        with self._lock:
            history = self._history.copy()

        if event_type:
            history = [e for e in history if e.event_type == event_type]
        if source_agent:
            history = [e for e in history if e.source_agent == source_agent]

        history = history[-limit:]
        return [e.to_dict() for e in history]

    def clear_history(self):
        """清除消息历史"""
        with self._lock:
            self._history.clear()
            self._agent_queues.clear()


class CollaborationPattern:
    """预定义协作模式

    提供常见的 Agent 间协作流程模板。
    """

    def __init__(self, message_bus: AgentMessageBus, logger: Optional[Logger] = None):
        self._bus = message_bus
        self._logger = logger or Logger.get()

    def review_rewrite_cycle(
        self,
        reviewer_id: str,
        writer_id: str,
        drafts: List[Dict],
        max_attempts: int = 3,
        review_fn: Optional[Callable] = None,
        rewrite_fn: Optional[Callable] = None,
    ) -> List[Dict]:
        """审核-重写循环

        流程: Writer提交 → Reviewer审核 → 驳回则重写 → 循环直到通过或超限

        Args:
            reviewer_id: 审核 Agent ID
            writer_id: 写作 Agent ID
            drafts: 待审核的草稿列表
            max_attempts: 最大重写次数
            review_fn: 审核函数 (drafts) -> ReviewResult
            rewrite_fn: 重写函数 (rejections, drafts) -> rewritten_drafts

        Returns:
            审核通过或达到最大重写次数后的草稿列表
        """
        current_drafts = drafts
        rewrite_counts: Dict[int, int] = {}

        for attempt in range(max_attempts):
            self._bus.send(
                source=writer_id,
                target=reviewer_id,
                event_type=EventType.TASK_REQUEST,
                data={"action": "review", "attempt": attempt + 1, "draft_count": len(current_drafts)},
            )

            if review_fn is None:
                self._bus.send(
                    source=reviewer_id,
                    target=writer_id,
                    event_type=EventType.TASK_RESULT,
                    data={"action": "review_complete", "attempt": attempt + 1},
                )
                continue

            review_result = review_fn(current_drafts)

            if review_result and hasattr(review_result, 'passed') and review_result.passed:
                self._bus.publish(AgentEvent(
                    event_type=EventType.REVIEW_APPROVAL,
                    source_agent=reviewer_id,
                    data={"attempt": attempt + 1, "total_chapters": len(current_drafts)},
                ))
                self._logger.info(f"审核通过 (attempt {attempt + 1})")
                return current_drafts

            rejections = []
            if review_result and hasattr(review_result, 'rejections'):
                rejections = review_result.rejections

            self._bus.publish(AgentEvent(
                event_type=EventType.REVIEW_REJECTION,
                source_agent=reviewer_id,
                data={"attempt": attempt + 1, "rejection_count": len(rejections)},
            ))

            for r in rejections:
                ch_id = r.get("chapter_id", 0)
                rewrite_counts[ch_id] = rewrite_counts.get(ch_id, 0) + 1

            if rewrite_fn is not None and rejections:
                rewritten = rewrite_fn(rejections, current_drafts)
                rewritten_ids = {d.get("chapter_id") for d in rewritten}
                current_drafts = [d for d in current_drafts if d.get("chapter_id") not in rewritten_ids]
                current_drafts.extend(rewritten)
                current_drafts.sort(key=lambda d: d.get("chapter_id", 0))

                self._bus.send(
                    source=writer_id,
                    target=reviewer_id,
                    event_type=EventType.TASK_RESULT,
                    data={"action": "rewrite_complete", "attempt": attempt + 1, "rewritten_count": len(rewritten)},
                )
            else:
                break

        dead_loop_chapters = [ch_id for ch_id, count in rewrite_counts.items() if count >= max_attempts]
        if dead_loop_chapters:
            self._bus.publish(AgentEvent(
                event_type=EventType.ERROR_REPORT,
                source_agent=reviewer_id,
                data={"error_type": "dead_loop", "chapters": dead_loop_chapters},
                priority=MessagePriority.HIGH,
            ))

        return current_drafts

    def conflict_resolution(
        self,
        detector_id: str,
        resolver_id: str,
        conflicts: List[Dict],
        resolve_fn: Optional[Callable] = None,
        autonomy_level: str = "suggest",
    ) -> List[Dict]:
        """冲突裁决流程

        流程: 检测冲突 → 通知裁决者 → 裁决结果 → 应用

        Args:
            detector_id: 冲突检测 Agent ID
            resolver_id: 冲突裁决 Agent ID
            conflicts: 冲突列表
            resolve_fn: 冲突解决函数 (conflict) -> resolution_dict
            autonomy_level: 自主级别 (auto/suggest/confirm)

        Returns:
            冲突解决结果列表
        """
        resolutions = []

        for conflict in conflicts:
            self._bus.send(
                source=detector_id,
                target=resolver_id,
                event_type=EventType.CONFLICT_REPORT,
                data=conflict,
                priority=MessagePriority.HIGH,
            )

            if resolve_fn is not None:
                resolution = resolve_fn(conflict)
                if resolution is None:
                    resolution = {
                        "conflict": conflict,
                        "status": "pending",
                        "resolution": None,
                    }

                self._bus.publish(AgentEvent(
                    event_type=EventType.CONFLICT_RESOLUTION,
                    source_agent=resolver_id,
                    data=resolution,
                    priority=MessagePriority.HIGH,
                ))

                if autonomy_level == "auto":
                    resolution["status"] = "auto_resolved"
                elif autonomy_level == "confirm":
                    resolution["status"] = "needs_human_confirmation"

                resolutions.append(resolution)
            else:
                resolutions.append({
                    "conflict": conflict,
                    "status": "reported",
                })

        return resolutions

    def foreshadowing_coordination(
        self,
        writer_id: str,
        world_manager_id: str,
        chapter_id: int,
        planted: List[Dict],
        resolved: List[Dict],
    ):
        """伏笔协调

        流程: Writer报告伏笔操作 → WorldManager更新状态 → 通知相关Agent
        """
        for fs in planted:
            self._bus.send(
                source=writer_id,
                target=world_manager_id,
                event_type=EventType.FORESHADOWING_UPDATE,
                data={"action": "plant", "chapter_id": chapter_id, "foreshadowing": fs},
            )

        for fs in resolved:
            self._bus.send(
                source=writer_id,
                target=world_manager_id,
                event_type=EventType.FORESHADOWING_UPDATE,
                data={"action": "resolve", "chapter_id": chapter_id, "foreshadowing": fs},
            )

    def state_change_broadcast(
        self,
        source_agent: str,
        chapter_id: int,
        changes: Dict,
    ):
        """状态变更广播

        当世界状态发生变更时，通知所有相关Agent。
        """
        self._bus.publish(AgentEvent(
            event_type=EventType.STATE_CHANGE,
            source_agent=source_agent,
            data={"chapter_id": chapter_id, "changes": changes},
            priority=MessagePriority.HIGH,
        ))
