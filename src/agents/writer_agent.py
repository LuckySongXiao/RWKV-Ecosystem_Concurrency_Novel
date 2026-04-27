"""作家 Agent - 章节正文并行创作

与 RoleplayAgent 分离:
- WriterAgent: 批量章节正文创作（超级并发，无状态）
- RoleplayAgent: 角色对话演绎（串行，有状态缓存）
"""

from typing import Dict, List, Optional

from src.core.config import PipelineConfig
from src.core.rwkv_client import RWKVClient
from src.core.file_manager import FileManager
from src.core.world_state_engine import WorldStateEngine
from src.tools.tool_registry import ToolRegistry
from src.workflow.chapter_workflow import ChapterOutlineWorkflow, ChapterContentWorkflow
from .base_agent import BaseAgent
from src.core.logger import Logger


class WriterAgent(BaseAgent):
    """作家 Agent

    职责: 基于章节大纲和世界状态卡，并行创作章节正文
    自主权: 中
    State: writer_novel.st
    API: /big_batch/completions (超级并发)
    """

    agent_type = "writer"
    state_file_key = "writer_novel"

    def __init__(self, client: RWKVClient, config: PipelineConfig,
                 fm: FileManager, world_engine: WorldStateEngine,
                 tools: ToolRegistry, logger: Logger = None):
        super().__init__(client, config, tools, logger)
        self._outline_workflow = ChapterOutlineWorkflow(client, config, fm, logger)
        self._content_workflow = ChapterContentWorkflow(client, config, fm, world_engine, logger)
        self._world = world_engine

    def generate_chapter_outlines(self, volume: Dict, start_chapter_id: int = 1) -> List[Dict]:
        """章节大纲并行生成"""
        self._logger.info(f"WriterAgent: Generating chapter outlines for volume {volume.get('volume_id')}")
        return self._outline_workflow.run(volume, start_chapter_id)

    def generate_chapter_content(self, chapters: List[Dict], style_guide: str = "") -> List[Dict]:
        """章节正文并行创作"""
        self._logger.info(f"WriterAgent: Generating content for {len(chapters)} chapters")
        return self._content_workflow.run(chapters, style_guide)

    def rewrite_chapters(self, rejections: List[Dict], chapters: List[Dict],
                         style_guide: str = "") -> List[Dict]:
        """根据审核驳回重写指定章节

        Args:
            rejections: 驳回列表，每个包含 chapter_id, reason, suggestion
            chapters: 原章节大纲列表
        """
        rejected_ids = {r.get("chapter_id") for r in rejections}
        rejected_chapters = [ch for ch in chapters if ch.get("chapter_id") in rejected_ids]

        if not rejected_chapters:
            return []

        self._logger.info(f"WriterAgent: Rewriting {len(rejected_chapters)} rejected chapters")

        # 在大纲中注入驳回原因
        for ch in rejected_chapters:
            matching = [r for r in rejections if r.get("chapter_id") == ch.get("chapter_id")]
            if matching:
                ch["_rewrite_context"] = {
                    "reason": matching[0].get("reason", ""),
                    "suggestion": matching[0].get("suggestion", ""),
                }

        return self._content_workflow.run(rejected_chapters, style_guide)
