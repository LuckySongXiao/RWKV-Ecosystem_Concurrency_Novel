"""中央调度器 - 管线编排核心

主管线流程:
1. 宏观规划层（全串行）: 全书大纲 → 各卷大纲 → 初始化世界状态
2. 超级并发创作层（逐卷）: 章节大纲并行 → 章节正文并行
3. 状态串行结算层（全串行）: 收集变更 → 排序 → 冲突检测 → 合并
4. 质量审核（全串行）: 事实校验 → 叙事审查 → 通过/驳回
5. 闭环迭代: 驳回重写 → 重新结算+审核
6. 最终成书

增强:
- 集成 ErrorHandler 死循环检测
- 断点恢复时重建世界状态
- 阶段间数据校验
- 审核结果持久化
"""

import json
import os
import time
from enum import Enum
from typing import Dict, List, Optional

from src.core.config import PipelineConfig, load_config
from src.core.rwkv_client import RWKVClient
from src.core.file_manager import FileManager
from src.core.world_state_engine import WorldStateEngine, Conflict
from src.core.error_handler import ErrorHandler
from src.core.logger import Logger
from src.tools.tool_registry import ToolRegistry
from src.tools.builtin_tools import register_builtin_tools
from src.tools.agent_collaboration import AgentMessageBus, CollaborationPattern, EventType, AgentEvent, MessagePriority
from src.agents.editor_agent import EditorAgent
from src.agents.writer_agent import WriterAgent
from src.agents.roleplay_agent import RoleplayAgent
from src.agents.world_manager_agent import WorldManagerAgent
from src.agents.reviewer_agent import ReviewerAgent


class PipelineState(Enum):
    INIT = "init"
    MACRO_PLANNING = "macro_planning"
    SUPER_CONCURRENT = "super_concurrent"
    STATE_SETTLEMENT = "state_settlement"
    REVIEW = "review"
    FINAL = "final"
    PAUSED = "paused"
    COMPLETED = "completed"
    ERROR = "error"


class Orchestrator:
    """中央调度器"""

    def __init__(self, config_path: str):
        self._config = load_config(config_path)
        self._fm = FileManager(self._config.paths)
        self._logger = Logger.get(os.path.join(self._config.paths.project_root, "output", "logs"))

        self._client = RWKVClient(self._config.api, self._logger)

        self._world = WorldStateEngine(self._fm, self._logger)

        self._error_handler = ErrorHandler(self._logger)

        self._tools = ToolRegistry(self._logger)
        register_builtin_tools(self._tools, self._world, self._fm, self._client, self._config)

        self._message_bus = AgentMessageBus(self._logger)
        self._collaboration = CollaborationPattern(self._message_bus, self._logger)

        self._editor = EditorAgent(self._client, self._config, self._fm, self._tools, self._logger)
        self._writer = WriterAgent(self._client, self._config, self._fm, self._world, self._tools, self._logger)
        self._roleplay = RoleplayAgent(self._client, self._config, self._world, self._tools, self._logger)
        self._world_mgr = WorldManagerAgent(self._client, self._config, self._world, self._tools, self._logger)
        self._reviewer = ReviewerAgent(self._client, self._config, self._world, self._tools, self._logger)

        self._state = PipelineState.INIT
        self._checkpoint_path = os.path.join(self._config.paths.project_root, ".checkpoint.json")

        self._current_volume_idx = 0
        self._volumes = []

    @property
    def state(self) -> PipelineState:
        return self._state

    @property
    def world_engine(self) -> WorldStateEngine:
        return self._world

    @property
    def roleplay(self) -> RoleplayAgent:
        return self._roleplay

    def run(self):
        """主管线入口"""
        self._logger.info("=" * 60)
        self._logger.info("Pipeline started")
        self._logger.info("=" * 60)

        if self._try_resume():
            self._logger.info(f"Resumed from checkpoint: {self._state.value}")
            self._restore_world_state()
        else:
            self._state = PipelineState.MACRO_PLANNING

        try:
            # ---- 阶段1: 宏观规划层 ----
            if self._state == PipelineState.MACRO_PLANNING:
                outline, volumes = self._macro_planning()
                self._validate_outline(outline)
                self._save_checkpoint("outline_done", {
                    "volume_count": len(volumes),
                })
                self._volumes = volumes
                self._state = PipelineState.SUPER_CONCURRENT
            else:
                outline_path = self._fm.outline_path()
                volumes_path = self._fm.volumes_path()

                if not self._fm.exists(outline_path) or not self._fm.exists(volumes_path):
                    self._logger.warning("Checkpoint references missing output files, restarting from macro_planning")
                    self._state = PipelineState.MACRO_PLANNING
                    outline, volumes = self._macro_planning()
                    self._validate_outline(outline)
                    self._save_checkpoint("outline_done", {"volume_count": len(volumes)})
                    self._volumes = volumes
                    self._state = PipelineState.SUPER_CONCURRENT
                else:
                    outline = self._fm.read_json(outline_path)
                    self._volumes = self._fm.read_jsonl(volumes_path)

            # ---- 阶段2-4: 逐卷创作+结算+审核 ----
            for vol_idx, volume in enumerate(self._volumes):
                if vol_idx < self._current_volume_idx:
                    continue

                self._current_volume_idx = vol_idx
                self._logger.info(f"\n{'='*60}\nVolume {vol_idx + 1}/{len(self._volumes)}: {volume.get('volume_title', '')}\n{'='*60}")

                start_chapter_id = sum(
                    v.get("chapter_count", 0) for v in self._volumes[:vol_idx]
                ) + 1

                # 阶段2: 超级并发创作
                self._state = PipelineState.SUPER_CONCURRENT
                chapters = self._writer.generate_chapter_outlines(volume, start_chapter_id)
                self._validate_chapters(chapters, volume)

                style_guide = self._fm.read_style_guide()
                drafts = self._writer.generate_chapter_content(chapters, style_guide)

                # 阶段3: 状态串行结算
                self._state = PipelineState.STATE_SETTLEMENT
                conflicts, settlement_log = self._world_mgr.settle(drafts)
                if conflicts:
                    self._handle_conflicts(conflicts)

                # 阶段4: 质量审核 + 闭环迭代
                self._state = PipelineState.REVIEW
                drafts = self._review_loop(drafts, chapters, style_guide)

                self._save_checkpoint(f"volume_{vol_idx + 1}_done", {
                    "volume_id": volume.get("volume_id"),
                    "chapters": len(chapters),
                    "current_volume_idx": vol_idx + 1,
                })

            # ---- 阶段5: 最终成书 ----
            self._finalize()
            self._state = PipelineState.COMPLETED
            self._logger.info("Pipeline completed! Book is ready in output/final/")

        except KeyboardInterrupt:
            self._state = PipelineState.PAUSED
            self._save_checkpoint("interrupted", {
                "state": self._state.value,
                "current_volume_idx": self._current_volume_idx,
            })
            self._logger.info("Pipeline paused by user. Use --resume to continue.")
        except Exception as e:
            self._state = PipelineState.ERROR
            self._save_checkpoint("error", {
                "error": str(e),
                "state": self._state.value,
                "current_volume_idx": self._current_volume_idx,
            })
            self._logger.error(f"Pipeline error: {e}")
            raise

    def _macro_planning(self):
        """宏观规划层：全书大纲 → 各卷大纲 → 初始化世界状态"""
        self._logger.info("--- Macro Planning Layer ---")

        spec = self._fm.read_specification()
        style_guide = self._fm.read_style_guide()

        outline = self._editor.generate_outline(spec, style_guide)

        volumes = self._editor.generate_volumes(outline)

        self._editor.init_world_state_from_outline(outline, self._world)

        return outline, volumes

    def _validate_outline(self, outline: Dict):
        """校验大纲数据完整性"""
        if not outline:
            raise ValueError("大纲生成失败：返回为空")

        required_keys = ["title", "volumes"]
        for key in required_keys:
            if key not in outline:
                self._logger.warning(f"大纲缺少必要字段: {key}")

        volumes = outline.get("volumes", [])
        if not volumes:
            self._logger.warning("大纲中没有卷信息")

        self._logger.info(f"大纲校验通过: {outline.get('title', '未命名')}, {len(volumes)}卷")

    def _validate_chapters(self, chapters: List[Dict], volume: Dict):
        """校验章节大纲数据"""
        if not chapters:
            self._logger.warning(f"卷 {volume.get('volume_title', '')} 没有生成章节大纲")
            return

        for ch in chapters:
            if "chapter_id" not in ch:
                self._logger.warning(f"章节缺少 chapter_id: {ch}")
            if "synopsis" not in ch and "chapter_title" not in ch:
                self._logger.warning(f"章节 {ch.get('chapter_id', '?')} 缺少概要和标题")

        self._logger.info(f"章节校验通过: {len(chapters)}章")

    def _review_loop(self, drafts: List[Dict], chapters: List[Dict],
                     style_guide: str) -> List[Dict]:
        """审核闭环迭代（集成死循环检测）"""
        max_attempts = self._config.review.max_rewrite_attempts
        rewrite_counts: Dict[int, int] = {}

        for attempt in range(max_attempts):
            review_result = self._reviewer.review(drafts)

            if review_result.passed:
                self._logger.info(f"Review passed on attempt {attempt + 1}")

                self._message_bus.publish(AgentEvent(
                    event_type=EventType.REVIEW_APPROVAL,
                    source_agent="reviewer",
                    data={"attempt": attempt + 1, "total_chapters": len(drafts)},
                ))

                return drafts

            rejections = review_result.rejections
            self._logger.warning(f"Review rejected {len(rejections)} chapters on attempt {attempt + 1}")

            self._message_bus.publish(AgentEvent(
                event_type=EventType.REVIEW_REJECTION,
                source_agent="reviewer",
                data={"attempt": attempt + 1, "rejection_count": len(rejections)},
            ))

            has_dead_loop = False
            for r in rejections:
                ch_id = r.get("chapter_id", 0)
                rewrite_counts[ch_id] = rewrite_counts.get(ch_id, 0) + 1

                # 死循环检测
                tracker = self._error_handler.track_rejection(ch_id, r.get("reason", ""))
                if tracker.is_dead_loop():
                    self._logger.warning(
                        f"Ch{ch_id} exceeded max rewrite attempts, marking for human edit"
                    )
                    r["_needs_human_edit"] = True
                    has_dead_loop = True

            # 如果所有被驳回的章节都进入死循环，终止审核循环
            if has_dead_loop and all(r.get("_needs_human_edit") for r in rejections):
                self._logger.warning("All rejected chapters are in dead loop, ending review cycle")
                break

            # 过滤掉死循环章节，只重写非死循环的
            rejections_to_rewrite = [r for r in rejections if not r.get("_needs_human_edit")]

            if rejections_to_rewrite:
                rewritten = self._writer.rewrite_chapters(rejections_to_rewrite, chapters, style_guide)
                rewritten_ids = {d.get("chapter_id") for d in rewritten}
                drafts = [d for d in drafts if d.get("chapter_id") not in rewritten_ids]
                drafts.extend(rewritten)
                drafts.sort(key=lambda d: d.get("chapter_id", 0))

                conflicts, _ = self._world_mgr.settle(drafts)
                if conflicts:
                    self._handle_conflicts(conflicts)

        # 保存审核报告
        self._save_review_report(rewrite_counts, drafts)

        return drafts

    def _handle_conflicts(self, conflicts: List[Conflict]):
        """处理冲突（暂停等待人类裁决）"""
        self._state = PipelineState.PAUSED
        for conflict in conflicts:
            self._logger.warning(f"CONFLICT: {conflict.description}")
            report_path = self._fm.tracking_path(f"conflict_ch{conflict.chapter_id}.json")
            self._fm.write_json(report_path, conflict.to_dict())

            self._error_handler.mark_unresolved_conflict(
                chapter_id=conflict.chapter_id,
                conflict_type=conflict.conflict_type,
                description=conflict.description,
            )

            self._tools.add_unresolved_conflict(conflict.to_dict())

            self._message_bus.publish(AgentEvent(
                event_type=EventType.CONFLICT_REPORT,
                source_agent="world_manager",
                data=conflict.to_dict(),
                priority=MessagePriority.HIGH if conflict.conflict_type in ("unique_item", "territory") else MessagePriority.NORMAL,
            ))

        self._save_checkpoint("conflict", {
            "conflicts": [c.to_dict() for c in conflicts],
            "current_volume_idx": self._current_volume_idx,
        })

    def _finalize(self):
        """最终成书"""
        self._logger.info("--- Finalizing Book ---")
        import shutil

        draft_dir = os.path.join(self._fm.output_dir, "draft")
        final_dir = os.path.join(self._fm.output_dir, "final")
        os.makedirs(final_dir, exist_ok=True)

        if os.path.exists(draft_dir):
            for filename in os.listdir(draft_dir):
                if filename.endswith(".md"):
                    src = os.path.join(draft_dir, filename)
                    dst = os.path.join(final_dir, filename)
                    shutil.copy2(src, dst)

        # 保存最终世界状态快照
        snapshot = self._world.get_world_snapshot(
            max((int(f.replace(".md", "")) for f in os.listdir(final_dir) if f.endswith(".md")), default=0)
        )
        self._fm.write_json(
            self._fm.tracking_path("final_world_snapshot.json"),
            snapshot
        )

        self._logger.info(f"Book finalized in {final_dir}")

    def _save_review_report(self, rewrite_counts: Dict[int, int], drafts: List[Dict]):
        """保存审核报告"""
        report = {
            "timestamp": time.time(),
            "rewrite_counts": rewrite_counts,
            "total_chapters": len(drafts),
            "dead_loop_chapters": [
                ch_id for ch_id, count in rewrite_counts.items()
                if count >= self._config.review.max_rewrite_attempts
            ],
            "error_summary": self._error_handler.get_error_summary(),
        }
        report_path = self._fm.tracking_path("review_report.json")
        self._fm.write_json(report_path, report)

    def _restore_world_state(self):
        """断点恢复时重建世界状态"""
        try:
            self._world.load_from_files()
            self._logger.info("World state restored from tracking files")
        except Exception as e:
            self._logger.warning(f"Failed to restore world state: {e}")

    # ---- 断点恢复 ----
    def _save_checkpoint(self, stage: str, data: Dict = None):
        """保存断点"""
        checkpoint = {
            "state": self._state.value,
            "stage": stage,
            "timestamp": time.time(),
            "data": data or {},
            "current_volume_idx": self._current_volume_idx,
        }
        with open(self._checkpoint_path, 'w', encoding='utf-8') as f:
            json.dump(checkpoint, f, ensure_ascii=False, indent=2)

    def _try_resume(self) -> bool:
        """尝试从断点恢复"""
        if not os.path.exists(self._checkpoint_path):
            return False

        try:
            with open(self._checkpoint_path, 'r', encoding='utf-8') as f:
                checkpoint = json.load(f)

            state_str = checkpoint.get("state", "")
            if state_str in [s.value for s in PipelineState]:
                self._state = PipelineState(state_str)
                self._current_volume_idx = checkpoint.get("data", {}).get(
                    "current_volume_idx", checkpoint.get("current_volume_idx", 0)
                )
                return True
        except Exception as e:
            self._logger.warning(f"Failed to load checkpoint: {e}")

        return False

    # ---- 状态查询（供 Web UI 使用）----
    def get_status(self) -> Dict:
        """获取管线状态"""
        max_chapter_id = 0
        try:
            chapters = self._fm.read_jsonl(self._fm.chapters_path())
            if chapters:
                max_chapter_id = max(c.get("chapter_id", 0) for c in chapters)
        except Exception:
            pass

        return {
            "state": self._state.value,
            "current_volume_idx": self._current_volume_idx,
            "world_status": self._world_mgr.get_world_status(),
            "world_snapshot": self._world.get_world_snapshot(max_chapter_id),
            "pending_approvals": self._tools.get_pending_approvals(),
            "reviewable_results": len(self._tools.get_reviewable_results()),
            "unresolved_conflicts": self._error_handler.get_unresolved_conflicts(),
            "error_summary": self._error_handler.get_error_summary(),
            "message_bus_history": self._message_bus.get_history(limit=20),
        }
