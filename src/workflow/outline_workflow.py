"""大纲生成工作流 - 全书大纲 + 各卷大纲

集成 GenreExpander: 在生成大纲前自动扩展用户简略设定
集成 ConcurrentSpecFiller: 并发补全人物/主线/风格
"""

import json
import time
from typing import Dict, List, Optional

from src.core.config import PipelineConfig, SamplingParams
from src.core.rwkv_client import RWKVClient
from src.core.prompt_builder import PromptBuilder
from src.core.file_manager import FileManager
from src.core.json_utils import parse_outline_output, parse_volumes_output
from src.core.genre_expander import GenreExpander
from src.core.concurrent_spec_filler import ConcurrentSpecFiller
from src.core.logger import Logger


class OutlineWorkflow:
    """大纲生成工作流 skill

    用法:
        workflow = OutlineWorkflow(client, config, fm)
        outline = workflow.run(spec, style_guide)
        volumes = workflow.generate_volumes(outline)
    """

    def __init__(self, client: RWKVClient, config: PipelineConfig, fm: FileManager, logger: Logger = None):
        self._client = client
        self._config = config
        self._fm = fm
        self._logger = logger or Logger.get()
        self._expander = GenreExpander(token_budget=3000)
        self._filler = ConcurrentSpecFiller(client, config, logger)

    def run(self, spec: str, style_guide: str = "", genre_override: str = "",
            auto_fill: bool = True) -> Dict:
        """生成全书大纲

        串行调用 /openai/v1/chat/completions，使用 editor_planning.st
        自动扩展简略设定：如果用户只填了题材关键词，自动补全世界观
        并发补全：人物/主线/风格通过 /big_batch/completions 并发生成
        """
        self._logger.info("OutlineWorkflow: Generating book outline...")

        # Step 1: 题材扩展 - 自动补全用户未填写的设定项
        expanded_spec, detected_genre, report = self._expander.expand(spec, genre_override)
        if report["expanded_sections"]:
            self._logger.info(
                f"OutlineWorkflow: Genre detected: {detected_genre} (confidence: {report['confidence']:.2f}), "
                f"expanded sections: {report['expanded_sections']}"
            )
        spec = expanded_spec

        # Step 2: 并发补全 - 人物/主线/风格
        if auto_fill:
            fill_result = self._filler.fill(spec, detected_genre)
            if fill_result["filled_items"]:
                self._logger.info(
                    f"OutlineWorkflow: Concurrent fill completed: {fill_result['filled_items']} "
                    f"in {fill_result['elapsed_ms']:.0f}ms"
                )
                # 合并补全结果到设定文档
                spec = self._filler.merge_to_spec(spec, fill_result)

                # 如果补全了风格，更新 style_guide
                if fill_result.get("style_md"):
                    style_guide = fill_result["style_md"]

                # 保存合并后的设定
                self._fm.write_markdown(
                    self._fm.context_dir + "/specification_filled.md",
                    spec
                )
                if fill_result.get("style_md"):
                    self._fm.write_markdown(
                        self._fm.context_dir + "/style-guide.md",
                        fill_result["style_md"]
                    )

        # 保存扩展后的设定供后续使用
        self._fm.write_markdown(
            self._fm.context_dir + "/specification_expanded.md",
            spec
        )

        prompt = PromptBuilder.build_outline_prompt(spec, style_guide)
        sampling = self._config.get_sampling("outline_gen")

        start = time.time()
        result = self._client.openai_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            sampling=sampling,
            stream=False,
        )
        elapsed = (time.time() - start) * 1000

        # 鲁棒解析结果
        outline, status = parse_outline_output(result)
        if outline is None:
            outline = {"raw_output": result, "title": "解析失败", "genre": "未知"}
            self._logger.error("OutlineWorkflow: Failed to parse outline JSON after all repair attempts")
        elif status == "repaired":
            self._logger.warning("OutlineWorkflow: Outline JSON repaired (not perfectly formatted)")
        else:
            self._logger.info("OutlineWorkflow: Outline JSON parsed successfully")

        # 持久化
        self._fm.write_json(self._fm.outline_path(), outline)
        self._logger.log_agent_call(
            "editor", "outline_gen", prompt[:200],
            {"temperature": sampling.temperature, "top_p": sampling.top_p},
            str(outline)[:200], elapsed,
        )

        return outline

    def generate_volumes(self, outline: Dict) -> List[Dict]:
        """生成各卷详细大纲

        串行调用 /openai/v1/chat/completions，使用 editor_planning.st
        """
        self._logger.info("OutlineWorkflow: Generating volume outlines...")

        outline_json = json.dumps(outline, ensure_ascii=False)
        prompt = PromptBuilder.build_volume_prompt(outline_json)
        sampling = self._config.get_sampling("volume_gen")

        start = time.time()
        result = self._client.openai_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            sampling=sampling,
            stream=False,
        )
        elapsed = (time.time() - start) * 1000

        # 鲁棒解析结果
        volumes, status = parse_volumes_output(result)
        if not volumes:
            volumes = [{"raw_output": result, "volume_id": 1, "volume_title": "解析失败"}]
            self._logger.error("OutlineWorkflow: Failed to parse volumes JSON")
        elif status == "repaired":
            self._logger.warning("OutlineWorkflow: Volumes JSON repaired")
        else:
            self._logger.info("OutlineWorkflow: Volumes JSON parsed successfully")

        # 持久化
        self._fm.write_jsonl(self._fm.volumes_path(), volumes)
        self._logger.log_agent_call(
            "editor", "volume_gen", prompt[:200],
            {"temperature": sampling.temperature, "top_p": sampling.top_p},
            str(volumes)[:200], elapsed,
        )

        return volumes
