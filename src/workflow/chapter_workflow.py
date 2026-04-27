"""章节创作工作流 - 章节大纲并行生成 + 章节正文并行创作"""

import json
import time
from typing import Dict, List, Optional

from src.core.config import PipelineConfig, SamplingParams
from src.core.rwkv_client import RWKVClient
from src.core.prompt_builder import PromptBuilder
from src.core.file_manager import FileManager
from src.core.world_state_engine import WorldStateEngine, WorldStateSummary
from src.core.json_utils import robust_json_parse
from src.core.logger import Logger


class ChapterOutlineWorkflow:
    """章节大纲并行生成工作流 skill

    使用 /big_batch/completions 超级并发
    """

    def __init__(self, client: RWKVClient, config: PipelineConfig, fm: FileManager, logger: Logger = None):
        self._client = client
        self._config = config
        self._fm = fm
        self._logger = logger or Logger.get()

    def run(self, volume: Dict, start_chapter_id: int = 1) -> List[Dict]:
        """为单卷生成所有章节大纲

        Args:
            volume: 卷大纲字典
            start_chapter_id: 起始章节全局编号

        Returns:
            章节大纲列表
        """
        chapter_count = volume.get("chapter_count", 10)
        volume_json = json.dumps(volume, ensure_ascii=False)
        self._logger.info(f"ChapterOutlineWorkflow: Generating {chapter_count} chapter outlines for volume {volume.get('volume_id')}")

        # 构造批量 Prompt
        prompts = PromptBuilder.build_batch_chapter_outline_prompts(
            volume_json, chapter_count, start_chapter_id
        )

        sampling = self._config.get_sampling("chapter_outline")
        sampling.max_tokens = 1024  # 大纲不需要太长

        # 分批提交
        all_results = self._batch_generate(prompts, sampling)

        # 解析结果
        chapters = []
        for i, result in enumerate(all_results):
            ch_id = start_chapter_id + i
            parsed, status = robust_json_parse(result)
            if parsed and isinstance(parsed, dict):
                chapter = parsed
                chapter["chapter_id"] = ch_id
                chapter["volume_id"] = volume.get("volume_id", 1)
                chapters.append(chapter)
            else:
                chapters.append({
                    "chapter_id": ch_id,
                    "volume_id": volume.get("volume_id", 1),
                    "chapter_title": f"第{ch_id}章",
                    "synopsis": result[:200],
                    "raw_output": True,
                })

        # 持久化
        existing = []
        if self._fm.exists(self._fm.chapters_path()):
            existing = self._fm.read_jsonl(self._fm.chapters_path())
        existing.extend(chapters)
        self._fm.write_jsonl(self._fm.chapters_path(), existing)

        return chapters

    def _batch_generate(self, prompts: List[str], sampling: SamplingParams) -> List[str]:
        """分批调用超级并发API"""
        max_batch = self._config.concurrency.max_batch_size
        all_results = []

        for i in range(0, len(prompts), max_batch):
            batch = prompts[i:i + max_batch]
            self._logger.info(f"Batch {i // max_batch + 1}: {len(batch)} prompts")

            start = time.time()
            results = self._client.big_batch_completions(
                contents=batch,
                sampling=sampling,
                stream=False,
            )
            elapsed = (time.time() - start) * 1000
            self._logger.info(f"Batch completed in {elapsed:.0f}ms")

            all_results.extend(results)

        return all_results


class ChapterContentWorkflow:
    """章节正文并行创作工作流 skill

    使用 /big_batch/completions 超级并发
    注入世界状态摘要到每个章节的 Prompt
    """

    def __init__(self, client: RWKVClient, config: PipelineConfig, fm: FileManager,
                 world_engine: WorldStateEngine, logger: Logger = None):
        self._client = client
        self._config = config
        self._fm = fm
        self._world = world_engine
        self._logger = logger or Logger.get()

    def run(self, chapters: List[Dict], style_guide: str = "") -> List[Dict]:
        """并行创作所有章节正文

        Args:
            chapters: 章节大纲列表
            style_guide: 写作风格约束

        Returns:
            章节初稿列表，每个包含 chapter_id, content, state_changes
        """
        self._logger.info(f"ChapterContentWorkflow: Generating content for {len(chapters)} chapters")

        # 为每个章节提取世界状态摘要
        state_summaries = []
        for ch in chapters:
            involved_chars = ch.get("involved_characters", [])
            involved_factions = ch.get("involved_factions", [])
            summary = self._world.extract_summary(
                involved_chars, involved_factions, ch.get("chapter_id", 0)
            )
            state_summaries.append(summary)

        # 构造批量 Prompt
        prompts = PromptBuilder.build_batch_chapter_content_prompts(
            chapters, state_summaries, style_guide=style_guide
        )

        sampling = self._config.get_sampling("chapter_content")

        # 分批提交
        all_results = self._batch_generate(prompts, sampling)

        # 解析结果，分离正文和状态变更
        from src.core.state_change_parser import split_content_and_state_change
        drafts = []
        for i, result in enumerate(all_results):
            ch_id = chapters[i].get("chapter_id", i + 1)
            content, state_change = split_content_and_state_change(result)
            drafts.append({
                "chapter_id": ch_id,
                "content": content or result,
                "state_changes": state_change,
                "raw_result": result,
            })
            # 持久化每章初稿
            self._fm.write_markdown(self._fm.draft_path(ch_id), result)

        return drafts

    def _batch_generate(self, prompts: List[str], sampling: SamplingParams) -> List[str]:
        """分批调用超级并发API"""
        max_batch = self._config.concurrency.max_batch_size
        all_results = []

        for i in range(0, len(prompts), max_batch):
            batch = prompts[i:i + max_batch]
            self._logger.info(f"Content batch {i // max_batch + 1}: {len(batch)} chapters")

            start = time.time()
            results = self._client.big_batch_completions(
                contents=batch,
                sampling=sampling,
                stream=False,
            )
            elapsed = (time.time() - start) * 1000
            self._logger.info(f"Content batch completed in {elapsed:.0f}ms")

            all_results.extend(results)

        return all_results
