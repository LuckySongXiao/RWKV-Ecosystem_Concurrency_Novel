"""中央调度器 - 管线编排核心

主管线流程:
1. 宏观规划层（全串行）: 全书大纲 → 各卷大纲 → 初始化世界状态
2. 超级并发创作层（逐卷）: 章节大纲并行 → 章节正文并行
3. 状态串行结算层（全串行）: 收集变更 → 排序 → 冲突检测 → 合并
4. 质量审核（全串行）: 事实校验 → 叙事审查 → 通过/驳回
5. 闭环迭代: 驳回重写 → 重新结算+审核
6. 最终成书
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
from src.core.logger import Logger
from src.tools.tool_registry import ToolRegistry
from src.tools.builtin_tools import register_builtin_tools
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


class Orchestrator:
    """中央调度器"""

    def __init__(self, config_path: str):
        # 加载配置
        self._config = load_config(config_path)
        self._fm = FileManager(self._config.paths)
        self._logger = Logger.get(os.path.join(self._config.paths.project_root, "output", "logs"))

        # 初始化 API 客户端
        self._client = RWKVClient(self._config.api, self._logger)

        # 初始化世界状态引擎
        self._world = WorldStateEngine(self._fm, self._logger)

        # 初始化工具注册中心
        self._tools = ToolRegistry(self._logger)
        register_builtin_tools(self._tools, self._world, self._fm)

        # 初始化各 Agent
        self._editor = EditorAgent(self._client, self._config, self._fm, self._tools, self._logger)
        self._writer = WriterAgent(self._client, self._config, self._fm, self._world, self._tools, self._logger)
        self._roleplay = RoleplayAgent(self._client, self._config, self._world, self._tools, self._logger)
        self._world_mgr = WorldManagerAgent(self._client, self._config, self._world, self._tools, self._logger)
        self._reviewer = ReviewerAgent(self._client, self._config, self._world, self._tools, self._logger)

        # 管线状态
        self._state = PipelineState.INIT
        self._checkpoint_path = os.path.join(self._config.paths.project_root, ".checkpoint.json")

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

        # 断点恢复
        if self._try_resume():
            self._logger.info(f"Resumed from checkpoint: {self._state.value}")
        else:
            self._state = PipelineState.MACRO_PLANNING

        try:
            # ---- 阶段1: 宏观规划层 ----
            if self._state == PipelineState.MACRO_PLANNING:
                outline, volumes = self._macro_planning()
                self._save_checkpoint("outline_done", {"volume_count": len(volumes)})
                self._state = PipelineState.SUPER_CONCURRENT
            else:
                # 从断点恢复，加载已有数据
                outline_path = self._fm.outline_path()
                volumes_path = self._fm.volumes_path()

                if not self._fm.exists(outline_path) or not self._fm.exists(volumes_path):
                    # 断点文件存在但输出文件缺失，回退到宏观规划
                    self._logger.warning("Checkpoint references missing output files, restarting from macro_planning")
                    self._state = PipelineState.MACRO_PLANNING
                    outline, volumes = self._macro_planning()
                    self._save_checkpoint("outline_done", {"volume_count": len(volumes)})
                    self._state = PipelineState.SUPER_CONCURRENT
                else:
                    outline = self._fm.read_json(outline_path)
                    volumes = self._fm.read_jsonl(volumes_path)

            # ---- 阶段2-4: 逐卷创作+结算+审核 ----
            for vol_idx, volume in enumerate(volumes):
                self._logger.info(f"\n{'='*60}\nVolume {vol_idx + 1}/{len(volumes)}: {volume.get('volume_title', '')}\n{'='*60}")

                # 计算起始章节ID
                start_chapter_id = sum(
                    v.get("chapter_count", 0) for v in volumes[:vol_idx]
                ) + 1

                # 阶段2: 超级并发创作
                chapters = self._writer.generate_chapter_outlines(volume, start_chapter_id)
                style_guide = self._fm.read_style_guide()
                drafts = self._writer.generate_chapter_content(chapters, style_guide)

                # 阶段3: 状态串行结算
                conflicts, settlement_log = self._world_mgr.settle(drafts)
                if conflicts:
                    self._handle_conflicts(conflicts)

                # 阶段4: 质量审核 + 闭环迭代
                drafts = self._review_loop(drafts, chapters, style_guide)

                # 保存断点
                self._save_checkpoint(f"volume_{vol_idx + 1}_done", {
                    "volume_id": volume.get("volume_id"),
                    "chapters": len(chapters),
                })

            # ---- 阶段5: 最终成书 ----
            self._finalize()
            self._state = PipelineState.COMPLETED
            self._logger.info("Pipeline completed! Book is ready in output/final/")

        except KeyboardInterrupt:
            self._state = PipelineState.PAUSED
            self._save_checkpoint("interrupted", {"state": self._state.value})
            self._logger.info("Pipeline paused by user. Use --resume to continue.")
        except Exception as e:
            self._state = PipelineState.PAUSED
            self._save_checkpoint("error", {"error": str(e), "state": self._state.value})
            self._logger.error(f"Pipeline error: {e}")
            raise

    def _macro_planning(self):
        """宏观规划层：全书大纲 → 各卷大纲 → 初始化世界状态"""
        self._logger.info("--- Macro Planning Layer ---")

        # 读取设定
        spec = self._fm.read_specification()
        style_guide = self._fm.read_style_guide()

        # 生成全书大纲
        outline = self._editor.generate_outline(spec, style_guide)

        # 生成各卷大纲
        volumes = self._editor.generate_volumes(outline)

        # 初始化世界状态
        self._editor.init_world_state_from_outline(outline, self._world)

        return outline, volumes

    def _review_loop(self, drafts: List[Dict], chapters: List[Dict],
                     style_guide: str) -> List[Dict]:
        """审核闭环迭代"""
        max_attempts = self._config.review.max_rewrite_attempts
        rewrite_counts: Dict[int, int] = {}

        for attempt in range(max_attempts):
            # 审核
            review_result = self._reviewer.review(drafts)

            if review_result.passed:
                self._logger.info(f"Review passed on attempt {attempt + 1}")
                return drafts

            # 驳回重写
            rejections = review_result.rejections
            self._logger.warning(f"Review rejected {len(rejections)} chapters on attempt {attempt + 1}")

            # 检查死循环
            for r in rejections:
                ch_id = r.get("chapter_id", 0)
                rewrite_counts[ch_id] = rewrite_counts.get(ch_id, 0) + 1
                if rewrite_counts[ch_id] >= max_attempts:
                    self._logger.warning(f"Ch{ch_id} exceeded max rewrite attempts, marking for human edit")
                    r["_needs_human_edit"] = True

            # 重写
            rewritten = self._writer.rewrite_chapters(rejections, chapters, style_guide)

            # 替换被驳回的章节
            rewritten_ids = {d.get("chapter_id") for d in rewritten}
            drafts = [d for d in drafts if d.get("chapter_id") not in rewritten_ids]
            drafts.extend(rewritten)
            drafts.sort(key=lambda d: d.get("chapter_id", 0))

            # 重新结算
            conflicts, _ = self._world_mgr.settle(drafts)
            if conflicts:
                self._handle_conflicts(conflicts)

        return drafts

    def _handle_conflicts(self, conflicts: List[Conflict]):
        """处理冲突（暂停等待人类裁决）"""
        self._state = PipelineState.PAUSED
        for conflict in conflicts:
            self._logger.warning(f"CONFLICT: {conflict.description}")
            # 生成冲突报告
            report_path = self._fm.tracking_path(f"conflict_ch{conflict.chapter_id}.json")
            self._fm.write_json(report_path, conflict.to_dict())

        self._save_checkpoint("conflict", {
            "conflicts": [c.to_dict() for c in conflicts],
        })

    def _finalize(self):
        """最终成书"""
        self._logger.info("--- Finalizing Book ---")
        import shutil

        # 将所有审核通过的初稿移入 final/
        draft_dir = os.path.join(self._fm.output_dir, "draft")
        final_dir = os.path.join(self._fm.output_dir, "final")
        os.makedirs(final_dir, exist_ok=True)

        if os.path.exists(draft_dir):
            for filename in os.listdir(draft_dir):
                if filename.endswith(".md"):
                    src = os.path.join(draft_dir, filename)
                    dst = os.path.join(final_dir, filename)
                    shutil.copy2(src, dst)

        self._logger.info(f"Book finalized in {final_dir}")

    # ---- 断点恢复 ----
    def _save_checkpoint(self, stage: str, data: Dict = None):
        """保存断点"""
        checkpoint = {
            "state": self._state.value,
            "stage": stage,
            "timestamp": time.time(),
            "data": data or {},
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
                return True
        except Exception:
            pass

        return False

    # ---- 状态查询（供 Web UI 使用）----
    def get_status(self) -> Dict:
        """获取管线状态"""
        return {
            "state": self._state.value,
            "world_status": self._world_mgr.get_world_status(),
            "pending_approvals": self._tools.get_pending_approvals(),
            "reviewable_results": len(self._tools.get_reviewable_results()),
        }
