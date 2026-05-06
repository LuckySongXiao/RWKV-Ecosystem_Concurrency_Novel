"""质量审核工作流 - 事实校验 + 叙事一致性审查

增强功能:
- 集成 ErrorHandler 死循环检测
- 逐章审核粒度
- 伏笔逾期检测
- 审核结果持久化
- 角色出场覆盖率检查
- 章节间连贯性检查
- 内容质量评分
"""

import json
import os
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field

from src.core.rwkv_client import RWKVClient
from src.core.config import PipelineConfig
from src.core.prompt_builder import PromptBuilder
from src.core.world_state_engine import WorldStateEngine
from src.core.error_handler import ErrorHandler
from src.core.json_utils import robust_json_parse
from src.core.logger import Logger


@dataclass
class ChapterReviewDetail:
    chapter_id: int = 0
    content_length: int = 0
    has_state_changes: bool = False
    new_foreshadowing_count: int = 0
    resolved_foreshadowing_count: int = 0
    character_coverage: float = 0.0
    quality_score: float = 0.0
    issues: List[Dict] = field(default_factory=list)
    coherence_with_previous: float = 0.0

    def to_dict(self) -> Dict:
        return {
            "chapter_id": self.chapter_id,
            "content_length": self.content_length,
            "has_state_changes": self.has_state_changes,
            "new_foreshadowing_count": self.new_foreshadowing_count,
            "resolved_foreshadowing_count": self.resolved_foreshadowing_count,
            "character_coverage": round(self.character_coverage, 2),
            "quality_score": round(self.quality_score, 2),
            "coherence_with_previous": round(self.coherence_with_previous, 2),
            "issues": self.issues,
        }


@dataclass
class ReviewResult:
    passed: bool = True
    fact_check_passed: bool = True
    narrative_review_passed: bool = True
    rejections: List[Dict] = field(default_factory=list)
    fact_issues: List[Dict] = field(default_factory=list)
    narrative_issues: List[Dict] = field(default_factory=list)
    chapter_details: Dict = field(default_factory=dict)
    overall_quality_score: float = 0.0
    review_timestamp: float = 0.0

    def __post_init__(self):
        if self.review_timestamp == 0.0:
            self.review_timestamp = time.time()

    def to_dict(self) -> Dict:
        return {
            "passed": self.passed,
            "fact_check_passed": self.fact_check_passed,
            "narrative_review_passed": self.narrative_review_passed,
            "rejections": self.rejections,
            "chapter_details": {
                str(k): v.to_dict() if isinstance(v, ChapterReviewDetail) else v
                for k, v in self.chapter_details.items()
            },
            "overall_quality_score": round(self.overall_quality_score, 2),
            "review_timestamp": self.review_timestamp,
        }


class ReviewWorkflow:
    """质量审核工作流 skill

    两阶段审核:
    1. 事实校验 (reviewer_factcheck.st) - 检查角色/势力/经济一致性
    2. 叙事审查 (reviewer_narrative.st) - 检查伏笔/人物弧光/时间线

    增强:
    - 死循环检测: 同一章节连续驳回超限自动标记
    - 逐章审核: 每章独立审核，返回详细问题列表
    - 伏笔逾期: 检查逾期未回收的伏笔
    """

    def __init__(self, client: RWKVClient, config: PipelineConfig,
                 world_engine: WorldStateEngine, logger: Logger = None,
                 error_handler: ErrorHandler = None):
        self._client = client
        self._config = config
        self._world = world_engine
        self._logger = logger or Logger.get()
        self._error_handler = error_handler or ErrorHandler(self._logger)

    def run(self, drafts: List[Dict], current_chapter_id: int = 0) -> ReviewResult:
        """执行完整审核流程

        Args:
            drafts: 章节初稿列表
            current_chapter_id: 当前章节ID（用于伏笔逾期检测）
        """
        self._logger.info(f"ReviewWorkflow: Reviewing {len(drafts)} chapters")

        drafts_summary = self._summarize_drafts(drafts)
        world_state_summary = self._summarize_world_state()

        # 伏笔逾期检测
        overdue_foreshadowings = self._world.query_overdue_foreshadowings(
            current_chapter_id or max(d.get("chapter_id", 1) for d in drafts) if drafts else 1
        )
        if overdue_foreshadowings:
            self._logger.warning(
                f"发现 {len(overdue_foreshadowings)} 个逾期未回收伏笔"
            )

        # 阶段1: 事实校验
        fact_result = self._fact_check(drafts_summary, world_state_summary)

        # 阶段2: 叙事审查
        narrative_result = self._narrative_review(
            drafts_summary, world_state_summary, overdue_foreshadowings
        )

        # 阶段3: 逐章审核
        chapter_details = self._per_chapter_review(drafts, world_state_summary)

        # 合并结果
        all_rejections = []
        for issue in fact_result.get("issues", []):
            all_rejections.append({
                "chapter_id": issue.get("chapter_id", 0),
                "reason": issue.get("description", ""),
                "suggestion": issue.get("suggestion", "请修正事实不一致处"),
                "type": "fact_check",
            })
        for rejection in narrative_result.get("rejections", []):
            all_rejections.append({
                "chapter_id": rejection.get("chapter_id", 0),
                "reason": rejection.get("reason", ""),
                "suggestion": rejection.get("suggestion", ""),
                "type": "narrative_review",
            })

        # 死循环检测
        for rejection in all_rejections:
            ch_id = rejection.get("chapter_id", 0)
            tracker = self._error_handler.track_rejection(ch_id, rejection.get("reason", ""))
            if tracker.is_dead_loop():
                self._logger.warning(
                    f"章节 {ch_id} 连续驳回 {tracker.rejection_count} 次，"
                    f"标记为需人工编辑"
                )
                rejection["_needs_human_edit"] = True

        result = ReviewResult(
            passed=fact_result.get("passed", True) and narrative_result.get("passed", True),
            fact_check_passed=fact_result.get("passed", True),
            narrative_review_passed=narrative_result.get("passed", True),
            fact_issues=fact_result.get("issues", []),
            narrative_issues=narrative_result.get("issues", []),
            rejections=all_rejections,
            chapter_details=chapter_details,
        )

        if chapter_details:
            scores = []
            for detail in chapter_details.values():
                if isinstance(detail, ChapterReviewDetail):
                    scores.append(detail.quality_score)
            if scores:
                result.overall_quality_score = sum(scores) / len(scores)

        self._persist_review_result(result)

        self._logger.info(
            f"ReviewWorkflow: passed={result.passed}, "
            f"fact_issues={len(result.fact_issues)}, "
            f"narrative_issues={len(result.narrative_issues)}, "
            f"rejections={len(result.rejections)}, "
            f"quality_score={result.overall_quality_score:.1f}"
        )
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
        self._logger.info(f"事实校验完成，耗时 {elapsed:.0f}ms")

        try:
            parsed, status = robust_json_parse(result)
            if parsed and isinstance(parsed, dict):
                return parsed
        except Exception as e:
            self._logger.warning(f"事实校验结果解析失败: {e}")

        return {"passed": True, "issues": []}

    def _narrative_review(self, drafts_summary: str, world_state_summary: str,
                          overdue_foreshadowings: List[Dict] = None) -> Dict:
        """叙事审查 - 使用 reviewer_narrative.st"""
        prompt = PromptBuilder.build_narrative_review_prompt(
            drafts_summary, world_state_summary
        )

        if overdue_foreshadowings:
            overdue_text = "\n".join(
                f"- [{fs.get('id', '?')}] {fs.get('description', '')} "
                f"(预计第{fs.get('expected_resolve', '?')}章回收)"
                for fs in overdue_foreshadowings
            )
            prompt += f"\n\n## 逾期未回收伏笔警告\n{overdue_text}\n请特别关注这些伏笔是否需要在本章回收。"

        sampling = self._config.get_sampling("narrative_review")

        start = time.time()
        result = self._client.openai_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            sampling=sampling,
            stream=False,
        )
        elapsed = (time.time() - start) * 1000
        self._logger.info(f"叙事审查完成，耗时 {elapsed:.0f}ms")

        try:
            parsed, status = robust_json_parse(result)
            if parsed and isinstance(parsed, dict):
                return parsed
        except Exception as e:
            self._logger.warning(f"叙事审查结果解析失败: {e}")

        return {"passed": True, "rejections": []}

    def _per_chapter_review(self, drafts: List[Dict], world_state_summary: str) -> Dict:
        """逐章审核 - 为每章生成独立审核结果

        检查维度:
        - 内容长度和完整性
        - 状态变更有效性
        - 角色出场覆盖率
        - 伏笔格式
        - 章节间连贯性
        - 内容质量评分
        """
        chapter_details = {}
        sorted_drafts = sorted(drafts, key=lambda d: d.get("chapter_id", 0))

        for idx, draft in enumerate(sorted_drafts):
            ch_id = draft.get("chapter_id", 0)
            content = draft.get("content", draft.get("raw_result", ""))
            state_changes = draft.get("state_changes")

            detail = ChapterReviewDetail(
                chapter_id=ch_id,
                content_length=len(content.strip()) if content else 0,
                has_state_changes=state_changes is not None,
            )

            if not content or len(content.strip()) < 100:
                detail.issues.append({
                    "type": "format",
                    "severity": "high",
                    "description": f"章节内容过短（{len(content.strip()) if content else 0}字），可能生成不完整",
                })

            if state_changes is None:
                detail.issues.append({
                    "type": "state_change",
                    "severity": "medium",
                    "description": "未检测到状态变更JSON，需人工确认",
                })
            elif isinstance(state_changes, dict):
                char_changes = state_changes.get("character_changes", [])
                for cc in char_changes:
                    char_id = cc.get("character_id", "")
                    if char_id and char_id not in self._world.characters:
                        detail.issues.append({
                            "type": "fact",
                            "severity": "high",
                            "description": f"引用了不存在的角色: {char_id}",
                        })

                new_fs = state_changes.get("new_foreshadowing", [])
                resolved_fs = state_changes.get("resolved_foreshadowing", [])
                detail.new_foreshadowing_count = len(new_fs) if isinstance(new_fs, list) else 0
                detail.resolved_foreshadowing_count = len(resolved_fs) if isinstance(resolved_fs, list) else 0

                for fs in (new_fs if isinstance(new_fs, list) else []):
                    if not fs.get("id") or not fs.get("description"):
                        detail.issues.append({
                            "type": "foreshadowing",
                            "severity": "low",
                            "description": "新伏笔缺少ID或描述",
                        })

            involved_chars = []
            if isinstance(state_changes, dict):
                for cc in state_changes.get("character_changes", []):
                    cid = cc.get("character_id", "")
                    if cid:
                        involved_chars.append(cid)

            if involved_chars:
                present_count = sum(1 for cid in involved_chars if cid in content)
                detail.character_coverage = present_count / len(involved_chars) if involved_chars else 0.0
                if detail.character_coverage < 0.5:
                    detail.issues.append({
                        "type": "coverage",
                        "severity": "medium",
                        "description": f"角色出场覆盖率低（{detail.character_coverage:.0%}），部分预期角色未在正文中出现",
                    })

            if idx > 0 and content:
                prev_content = sorted_drafts[idx - 1].get("content", sorted_drafts[idx - 1].get("raw_result", ""))
                if prev_content:
                    detail.coherence_with_previous = self._check_coherence(prev_content, content)
                    if detail.coherence_with_previous < 0.3:
                        detail.issues.append({
                            "type": "coherence",
                            "severity": "high",
                            "description": f"与前一章连贯性差（{detail.coherence_with_previous:.2f}），可能存在叙事断裂",
                        })

            detail.quality_score = self._compute_quality_score(detail)

            chapter_details[ch_id] = detail

        return chapter_details

    def _check_coherence(self, prev_content: str, current_content: str) -> float:
        """检查章节间连贯性

        基于以下指标:
        - 共同角色提及
        - 场景/地点延续
        - 关键词重叠
        """
        if not prev_content or not current_content:
            return 0.0

        prev_chars = set()
        current_chars = set()
        for cid, char in self._world.characters.items():
            name = char.name
            if name in prev_content:
                prev_chars.add(name)
            if name in current_content:
                current_chars.add(name)

        char_overlap = 0.0
        if prev_chars or current_chars:
            char_overlap = len(prev_chars & current_chars) / max(len(prev_chars | current_chars), 1)

        prev_words = set(prev_content[:2000])
        current_words = set(current_content[:500])
        word_overlap = len(prev_words & current_words) / max(len(prev_words | current_words), 1)

        coherence = char_overlap * 0.7 + word_overlap * 0.3
        return min(coherence, 1.0)

    def _compute_quality_score(self, detail: ChapterReviewDetail) -> float:
        """计算章节内容质量评分（0-10）

        评分维度:
        - 内容长度 (0-3分)
        - 状态变更完整性 (0-2分)
        - 角色覆盖率 (0-2分)
        - 连贯性 (0-2分)
        - 问题扣分 (每个high -1, medium -0.5, low -0.2)
        """
        score = 0.0

        if detail.content_length >= 2000:
            score += 3.0
        elif detail.content_length >= 1000:
            score += 2.0
        elif detail.content_length >= 500:
            score += 1.0

        if detail.has_state_changes:
            score += 2.0
        if detail.new_foreshadowing_count > 0:
            score += 0.5
        if detail.resolved_foreshadowing_count > 0:
            score += 0.5

        score += min(detail.character_coverage * 2.0, 2.0)

        score += min(detail.coherence_with_previous * 2.0, 2.0)

        for issue in detail.issues:
            severity = issue.get("severity", "low")
            if severity == "high":
                score -= 1.0
            elif severity == "medium":
                score -= 0.5
            elif severity == "low":
                score -= 0.2

        return max(0.0, min(10.0, score))

    def _summarize_drafts(self, drafts: List[Dict]) -> str:
        """构造章节摘要供审核使用"""
        summaries = []
        for d in drafts:
            ch_id = d.get("chapter_id", 0)
            content = d.get("content", d.get("raw_result", ""))
            state_changes = d.get("state_changes")
            summary = f"### 第{ch_id}章\n{content[:800]}"
            if state_changes:
                summary += f"\n[状态变更: {json.dumps(state_changes, ensure_ascii=False)[:200]}]"
            summaries.append(summary)
        return "\n\n".join(summaries)

    def _summarize_world_state(self) -> str:
        """构造世界状态摘要供审核使用"""
        chars = [c.to_dict() for c in self._world.characters.values()]
        factions = [f.to_dict() for f in self._world.factions.values()]
        pending_fs = self._world.query_foreshadowings("planted")

        return json.dumps({
            "characters": chars,
            "factions": factions,
            "economy": self._world.economy.to_dict(),
            "pending_foreshadowings": pending_fs[:10],
        }, ensure_ascii=False, indent=2)[:4000]

    def _persist_review_result(self, result: ReviewResult):
        """持久化审核结果到 tracking/ 目录"""
        try:
            review_dir = os.path.join(
                self._world._fm.get_output_dir(), "tracking"
            )
            os.makedirs(review_dir, exist_ok=True)

            timestamp_str = time.strftime(
                "%Y%m%d_%H%M%S", time.localtime(result.review_timestamp)
            )
            filename = f"review_{timestamp_str}.json"
            filepath = os.path.join(review_dir, filename)

            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)

            latest_path = os.path.join(review_dir, "review_latest.json")
            with open(latest_path, 'w', encoding='utf-8') as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)

            self._logger.info(f"审核结果已保存: {filepath}")
        except Exception as e:
            self._logger.warning(f"审核结果持久化失败: {e}")
