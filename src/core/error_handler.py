"""异常处理策略模块

实现以下策略:
- API调用失败: 超时重试(最多3次)，最终失败记录并暂停
- 批量生成部分失败: 记录失败章节，单独重试
- 状态变更JSON解析失败: 标记"待审"
- 反复驳回死循环: 同一章节连续驳回超3次，暂停并提交人工修改
- 不可调和冲突: 标记"未解决"，跳过该章状态更新
"""

import time
import traceback
from typing import Any, Callable, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from .logger import Logger


@dataclass
class RetryConfig:
    """重试配置"""
    max_retries: int = 3
    base_delay: float = 2.0
    max_delay: float = 30.0
    exponential_base: float = 2.0
    retryable_exceptions: tuple = (
        ConnectionError,
        TimeoutError,
        OSError,
    )


@dataclass
class BatchFailureInfo:
    """批量失败信息"""
    failed_indices: List[int] = field(default_factory=list)
    failed_prompts: List[str] = field(default_factory=list)
    error_messages: List[str] = field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 2


@dataclass
class RejectionTracker:
    """驳回追踪器"""
    chapter_id: int
    rejection_count: int = 0
    max_rejections: int = 3
    rejection_reasons: List[str] = field(default_factory=list)
    last_rejection_time: Optional[str] = None

    def add_rejection(self, reason: str):
        """添加驳回记录"""
        self.rejection_count += 1
        self.rejection_reasons.append(reason)
        self.last_rejection_time = datetime.now().isoformat()

    def is_dead_loop(self) -> bool:
        """检查是否进入死循环"""
        return self.rejection_count >= self.max_rejections

    def to_dict(self) -> Dict:
        return {
            "chapter_id": self.chapter_id,
            "rejection_count": self.rejection_count,
            "max_rejections": self.max_rejections,
            "rejection_reasons": self.rejection_reasons,
            "last_rejection_time": self.last_rejection_time,
            "is_dead_loop": self.is_dead_loop(),
        }


class ErrorHandler:
    """统一异常处理器"""

    def __init__(self, logger: Optional[Logger] = None):
        self._logger = logger or Logger.get()
        self._retry_config = RetryConfig()
        self._batch_failures: Dict[str, BatchFailureInfo] = {}
        self._rejection_trackers: Dict[int, RejectionTracker] = {}
        self._unresolved_conflicts: List[Dict] = []

    def with_retry(self, func: Callable, *args, **kwargs) -> Any:
        """带重试的函数执行

        使用指数退避策略重试失败操作。
        """
        last_exception = None

        for attempt in range(self._retry_config.max_retries + 1):
            try:
                result = func(*args, **kwargs)
                if attempt > 0:
                    self._logger.info(f"重试成功 (第{attempt + 1}次尝试)")
                return result
            except self._retry_config.retryable_exceptions as e:
                last_exception = e
                if attempt < self._retry_config.max_retries:
                    delay = min(
                        self._retry_config.base_delay *
                        (self._retry_config.exponential_base ** attempt),
                        self._retry_config.max_delay
                    )
                    self._logger.warning(
                        f"API调用失败 (第{attempt + 1}次): {e}, "
                        f"等待 {delay:.1f}s 后重试..."
                    )
                    time.sleep(delay)
                else:
                    self._logger.error(
                        f"API调用失败，已达最大重试次数 "
                        f"({self._retry_config.max_retries}): {e}"
                    )
            except Exception as e:
                self._logger.error(f"不可重试的错误: {e}\n{traceback.format_exc()}")
                raise

        raise last_exception

    def handle_batch_failure(
        self,
        batch_id: str,
        total_count: int,
        successful_indices: List[int],
        failed_indices: List[int],
        error_messages: Optional[List[str]] = None,
    ) -> BatchFailureInfo:
        """处理批量生成部分失败

        记录失败章节，返回需要重试的章节信息。
        """
        if batch_id not in self._batch_failures:
            self._batch_failures[batch_id] = BatchFailureInfo()

        failure_info = self._batch_failures[batch_id]
        failure_info.failed_indices = failed_indices
        if error_messages:
            failure_info.error_messages = error_messages

        self._logger.warning(
            f"批量生成部分失败: {len(failed_indices)}/{total_count} 章失败, "
            f"失败章节: {failed_indices}"
        )

        if failure_info.retry_count >= failure_info.max_retries:
            self._logger.error(
                f"批量失败章节已达最大重试次数，"
                f"标记为待审: {failed_indices}"
            )
            return failure_info

        failure_info.retry_count += 1
        return failure_info

    def track_rejection(self, chapter_id: int, reason: str) -> RejectionTracker:
        """追踪章节驳回情况

        检测是否进入死循环（同一章节连续驳回超3次）。
        """
        if chapter_id not in self._rejection_trackers:
            self._rejection_trackers[chapter_id] = RejectionTracker(
                chapter_id=chapter_id
            )

        tracker = self._rejection_trackers[chapter_id]
        tracker.add_rejection(reason)

        if tracker.is_dead_loop():
            self._logger.error(
                f"章节 {chapter_id} 进入死循环: "
                f"连续驳回 {tracker.rejection_count} 次, "
                f"暂停并提交人工修改"
            )
            self._logger.error(f"驳回历史: {tracker.rejection_reasons}")

        return tracker

    def is_dead_loop(self, chapter_id: int) -> bool:
        """检查章节是否进入死循环"""
        if chapter_id not in self._rejection_trackers:
            return False
        return self._rejection_trackers[chapter_id].is_dead_loop()

    def get_rejection_tracker(self, chapter_id: int) -> Optional[RejectionTracker]:
        """获取章节驳回追踪器"""
        return self._rejection_trackers.get(chapter_id)

    def mark_unresolved_conflict(
        self,
        chapter_id: int,
        conflict_type: str,
        description: str,
        entities_involved: Optional[List[str]] = None,
    ):
        """标记不可调和冲突

        跳过该章状态更新，记录冲突信息。
        """
        conflict_entry = {
            "chapter_id": chapter_id,
            "conflict_type": conflict_type,
            "description": description,
            "entities_involved": entities_involved or [],
            "timestamp": datetime.now().isoformat(),
            "status": "unresolved",
        }
        self._unresolved_conflicts.append(conflict_entry)

        self._logger.warning(
            f"不可调和冲突 [章节 {chapter_id}]: {conflict_type} - {description}"
        )

    def get_unresolved_conflicts(self) -> List[Dict]:
        """获取所有未解决冲突"""
        return self._unresolved_conflicts

    def handle_json_parse_error(
        self,
        chapter_id: int,
        raw_text: str,
        error: Exception,
    ) -> Dict:
        """处理状态变更JSON解析失败

        标记为"待审"，返回待审记录。
        """
        self._logger.warning(
            f"章节 {chapter_id} 状态变更JSON解析失败: {error}"
        )

        return {
            "chapter_id": chapter_id,
            "status": "pending_review",
            "raw_text": raw_text[:500],
            "error": str(error),
            "timestamp": datetime.now().isoformat(),
        }

    def get_error_summary(self) -> Dict:
        """获取错误汇总信息"""
        return {
            "batch_failures": {
                k: {
                    "failed_indices": v.failed_indices,
                    "retry_count": v.retry_count,
                    "max_retries": v.max_retries,
                }
                for k, v in self._batch_failures.items()
            },
            "rejection_trackers": {
                k: v.to_dict()
                for k, v in self._rejection_trackers.items()
            },
            "unresolved_conflicts": self._unresolved_conflicts,
        }

    def reset(self):
        """重置所有追踪器"""
        self._batch_failures.clear()
        self._rejection_trackers.clear()
        self._unresolved_conflicts.clear()
