"""异常处理策略测试

验证:
- API超时重试机制
- 批量部分失败处理
- JSON解析失败标记
- 死循环检测
"""

import pytest
import time
from unittest.mock import Mock, patch
from src.core.error_handler import ErrorHandler, RetryConfig, RejectionTracker, BatchFailureInfo


class TestErrorHandler:
    """异常处理器测试"""

    @pytest.fixture
    def handler(self):
        """创建异常处理器"""
        return ErrorHandler()

    def test_retry_success(self, handler):
        """测试重试成功场景"""
        call_count = 0

        def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("Temporary failure")
            return "success"

        result = handler.with_retry(flaky_function)
        assert result == "success"
        assert call_count == 3

    def test_retry_exhausted(self, handler):
        """测试重试耗尽"""
        def always_failing():
            raise ConnectionError("Persistent failure")

        with pytest.raises(ConnectionError):
            handler.with_retry(always_failing)

    def test_non_retryable_error(self, handler):
        """测试不可重试的错误"""
        def raises_value_error():
            raise ValueError("Invalid input")

        with pytest.raises(ValueError):
            handler.with_retry(raises_value_error)

    def test_batch_failure_handling(self, handler):
        """测试批量失败处理"""
        result = handler.handle_batch_failure(
            batch_id="batch_001",
            total_count=10,
            successful_indices=[0, 1, 2, 3, 4, 6, 7, 8, 9],
            failed_indices=[5],
            error_messages=["Timeout"],
        )

        assert len(result.failed_indices) == 1
        assert result.failed_indices[0] == 5
        assert result.retry_count == 1

    def test_batch_failure_max_retries(self, handler):
        """测试批量失败达到最大重试次数"""
        batch_id = "batch_002"

        # 第一次失败
        handler.handle_batch_failure(
            batch_id=batch_id,
            total_count=10,
            successful_indices=list(range(10)),
            failed_indices=[5],
        )

        # 第二次失败
        handler.handle_batch_failure(
            batch_id=batch_id,
            total_count=10,
            successful_indices=list(range(10)),
            failed_indices=[5],
        )

        # 第三次失败（达到最大重试次数）
        result = handler.handle_batch_failure(
            batch_id=batch_id,
            total_count=10,
            successful_indices=list(range(10)),
            failed_indices=[5],
        )

        assert result.retry_count >= result.max_retries

    def test_rejection_tracking(self, handler):
        """测试驳回追踪"""
        chapter_id = 1

        # 第一次驳回
        tracker = handler.track_rejection(chapter_id, "叙事不一致")
        assert tracker.rejection_count == 1
        assert not tracker.is_dead_loop()

        # 第二次驳回
        tracker = handler.track_rejection(chapter_id, "角色行为不符合设定")
        assert tracker.rejection_count == 2
        assert not tracker.is_dead_loop()

        # 第三次驳回（死循环）
        tracker = handler.track_rejection(chapter_id, "事实错误")
        assert tracker.rejection_count == 3
        assert tracker.is_dead_loop()

    def test_dead_loop_detection(self, handler):
        """测试死循环检测"""
        chapter_id = 1

        # 添加3次驳回
        for i in range(3):
            handler.track_rejection(chapter_id, f"Reason {i+1}")

        assert handler.is_dead_loop(chapter_id)
        assert not handler.is_dead_loop(999)  # 不存在的章节

    def test_json_parse_error_handling(self, handler):
        """测试JSON解析失败处理"""
        result = handler.handle_json_parse_error(
            chapter_id=1,
            raw_text="Invalid JSON content",
            error=ValueError("Invalid JSON"),
        )

        assert result["chapter_id"] == 1
        assert result["status"] == "pending_review"
        assert "Invalid JSON" in result["error"]

    def test_unresolved_conflict_marking(self, handler):
        """测试不可调和冲突标记"""
        handler.mark_unresolved_conflict(
            chapter_id=1,
            conflict_type="location_conflict",
            description="角色同时出现在两地",
            entities_involved=["凌云"],
        )

        conflicts = handler.get_unresolved_conflicts()
        assert len(conflicts) == 1
        assert conflicts[0]["status"] == "unresolved"
        assert conflicts[0]["chapter_id"] == 1

    def test_error_summary(self, handler):
        """测试错误汇总"""
        # 添加一些错误记录
        handler.handle_batch_failure(
            batch_id="batch_001",
            total_count=10,
            successful_indices=list(range(10)),
            failed_indices=[5],
        )

        handler.track_rejection(1, "Test rejection")

        handler.mark_unresolved_conflict(
            chapter_id=1,
            conflict_type="test",
            description="Test conflict",
        )

        summary = handler.get_error_summary()

        assert "batch_failures" in summary
        assert "rejection_trackers" in summary
        assert "unresolved_conflicts" in summary

    def test_reset(self, handler):
        """测试重置"""
        handler.handle_batch_failure(
            batch_id="batch_001",
            total_count=10,
            successful_indices=list(range(10)),
            failed_indices=[5],
        )

        handler.track_rejection(1, "Test")

        handler.reset()

        summary = handler.get_error_summary()
        assert len(summary["batch_failures"]) == 0
        assert len(summary["rejection_trackers"]) == 0
        assert len(summary["unresolved_conflicts"]) == 0


class TestRejectionTracker:
    """驳回追踪器测试"""

    def test_add_rejection(self):
        """测试添加驳回"""
        tracker = RejectionTracker(chapter_id=1)
        
        tracker.add_rejection("Reason 1")
        assert tracker.rejection_count == 1
        assert len(tracker.rejection_reasons) == 1

    def test_is_dead_loop(self):
        """测试死循环检测"""
        tracker = RejectionTracker(chapter_id=1, max_rejections=3)
        
        assert not tracker.is_dead_loop()
        
        tracker.add_rejection("Reason 1")
        tracker.add_rejection("Reason 2")
        assert not tracker.is_dead_loop()
        
        tracker.add_rejection("Reason 3")
        assert tracker.is_dead_loop()

    def test_to_dict(self):
        """测试转换为字典"""
        tracker = RejectionTracker(chapter_id=1)
        tracker.add_rejection("Test reason")
        
        d = tracker.to_dict()
        
        assert d["chapter_id"] == 1
        assert d["rejection_count"] == 1
        assert d["is_dead_loop"] == False


class TestBatchFailureInfo:
    """批量失败信息测试"""

    def test_initial_state(self):
        """测试初始状态"""
        info = BatchFailureInfo()
        
        assert len(info.failed_indices) == 0
        assert len(info.failed_prompts) == 0
        assert len(info.error_messages) == 0
        assert info.retry_count == 0
        assert info.max_retries == 2
