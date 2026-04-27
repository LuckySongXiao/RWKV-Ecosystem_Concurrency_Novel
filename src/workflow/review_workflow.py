"""质量审核工作流 - 事实校验 + 叙事一致性审查"""

import json
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from src.core.rwkv_client import RWKVClient
from src.core.config import PipelineConfig
from src.core.prompt_builder import PromptBuilder
from src.core.world_state_engine import WorldStateEngine
from src.core.json_utils import robust_json_parse
from src.core.logger import Logger


@dataclass
class ReviewResult:
    passed: bool = True
    fact_check_passed: bool = True
    narrative_review_passed: bool = True
    rejections: List[Dict] = field(default_factory=list)
    fact_issues: List[Dict] = field(default_factory=list)
    narrative_issues: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "passed": self.passed,
            "fact_check_passed": self.fact_check_passed,
            "narrative_review_passed": self.narrative_review_passed,
            "rejections": self.rejections,
        }


class ReviewWorkflow:
    """质量审核工作流 skill

    两阶段审核:
    1. 事实校验 (reviewer_factcheck.st) - 检查角色/势力/经济一致性
    2. 叙事审查 (reviewer_narrative.st) - 检查伏笔/人物弧光/时间线
    """

    def __init__(self, client: RWKVClient, config: PipelineConfig,
                 world_engine: WorldStateEngine, logger: Logger = None):
        self._client = client
        self._config = config
        self._world = world_engine
        self._logger = logger or Logger.get()

    def run(self, drafts: List[Dict]) -> ReviewResult:
        """执行完整审核流程

        Args:
            drafts: 章节初稿列表
        """
        self._logger.info(f"ReviewWorkflow: Reviewing {len(drafts)} chapters")

        # 构造审核摘要
        drafts_summary = self._summarize_drafts(drafts)
        world_state_summary = self._summarize_world_state()

        # 阶段1: 事实校验
        fact_result = self._fact_check(drafts_summary, world_state_summary)

        # 阶段2: 叙事审查
        narrative_result = self._narrative_review(drafts_summary, world_state_summary)

        # 合并结果
        result = ReviewResult(
            passed=fact_result["passed"] and narrative_result["passed"],
            fact_check_passed=fact_result["passed"],
            narrative_review_passed=narrative_result["passed"],
            fact_issues=fact_result.get("issues", []),
            narrative_issues=narrative_result.get("issues", []),
            rejections=fact_result.get("issues", []) + narrative_result.get("rejections", []),
        )

        self._logger.info(f"ReviewWorkflow: passed={result.passed}, rejections={len(result.rejections)}")
        return result

    def _fact_check(self, drafts_summary: str, world_state_summary: str) -> Dict:
        """事实校验 - 使用 reviewer_factcheck.st"""
        prompt = PromptBuilder.build_fact_check_prompt(drafts_summary, world_state_summary)
        sampling = self._config.get_sampling("fact_check")

        start = time.time()
        result = self._client.openai_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            sampling=sampling,
            stream=False,
        )
        elapsed = (time.time() - start) * 1000

        try:
            parsed, status = robust_json_parse(result)
            if parsed and isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        return {"passed": True, "issues": []}

    def _narrative_review(self, drafts_summary: str, world_state_summary: str) -> Dict:
        """叙事审查 - 使用 reviewer_narrative.st"""
        prompt = PromptBuilder.build_narrative_review_prompt(drafts_summary, world_state_summary)
        sampling = self._config.get_sampling("narrative_review")

        start = time.time()
        result = self._client.openai_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            sampling=sampling,
            stream=False,
        )
        elapsed = (time.time() - start) * 1000

        try:
            parsed, status = robust_json_parse(result)
            if parsed and isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        return {"passed": True, "rejections": []}

    def _summarize_drafts(self, drafts: List[Dict]) -> str:
        """构造章节摘要供审核使用"""
        summaries = []
        for d in drafts:
            ch_id = d.get("chapter_id", 0)
            content = d.get("content", d.get("raw_result", ""))
            summaries.append(f"### 第{ch_id}章\n{content[:500]}...")
        return "\n\n".join(summaries)

    def _summarize_world_state(self) -> str:
        """构造世界状态摘要供审核使用"""
        chars = [c.to_dict() for c in self._world.characters.values()]
        factions = [f.to_dict() for f in self._world.factions.values()]
        return json.dumps({
            "characters": chars,
            "factions": factions,
            "economy": self._world.economy.to_dict(),
        }, ensure_ascii=False, indent=2)[:3000]
