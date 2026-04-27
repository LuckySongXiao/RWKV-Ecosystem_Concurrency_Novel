"""状态串行结算工作流"""

import json
import time
from typing import Dict, List, Optional, Tuple

from src.core.world_state_engine import WorldStateEngine, StateChange, Conflict
from src.core.state_change_parser import parse_state_change_from_draft
from src.core.rwkv_client import RWKVClient
from src.core.config import PipelineConfig
from src.core.prompt_builder import PromptBuilder
from src.core.logger import Logger


class StateSettlementWorkflow:
    """状态串行结算工作流 skill

    核心流程:
    1. 收集所有章节的状态变更请求
    2. 按章节顺序严格排序
    3. 逐章串行结算（冲突检测 → 合并 → 知识图谱更新）
    4. 返回冲突列表（如有）
    """

    def __init__(self, world_engine: WorldStateEngine, client: RWKVClient,
                 config: PipelineConfig, logger: Logger = None):
        self._world = world_engine
        self._client = client
        self._config = config
        self._logger = logger or Logger.get()

    def run(self, drafts: List[Dict]) -> Tuple[List[Conflict], List[Dict]]:
        """执行状态串行结算

        Args:
            drafts: 章节初稿列表，每个包含 chapter_id, content, state_changes

        Returns:
            (conflicts, settlement_log) - 冲突列表和结算日志
        """
        self._logger.info(f"StateSettlementWorkflow: Settling state for {len(drafts)} chapters")

        # 1. 收集并解析状态变更
        changes = []
        for draft in drafts:
            ch_id = draft.get("chapter_id", 0)
            state_change = draft.get("state_changes")

            if state_change is None:
                # 尝试从原始内容中解析
                raw = draft.get("raw_result", draft.get("content", ""))
                state_change, status = parse_state_change_from_draft(raw, ch_id)
                if status != "ok":
                    self._logger.warning(f"Ch{ch_id}: State change parse status={status}")
                    if state_change is None:
                        # 尝试用模型提取
                        state_change = self._extract_state_with_model(raw, ch_id)

            if state_change:
                changes.append(state_change)

        # 2. 按章节顺序严格排序
        changes.sort(key=lambda c: c.chapter_id)

        # 3. 逐章串行结算
        conflicts = []
        settlement_log = []

        for change in changes:
            self._logger.info(f"Settling ch{change.chapter_id}...")
            conflict = self._world.apply_change(change)

            if conflict:
                conflicts.append(conflict)
                settlement_log.append({
                    "chapter_id": change.chapter_id,
                    "status": "conflict",
                    "conflict": conflict.to_dict(),
                })
                # 冲突未解决时阻塞后续章节
                # （实际由调度器处理，这里先记录）
            else:
                settlement_log.append({
                    "chapter_id": change.chapter_id,
                    "status": "settled",
                })

        self._logger.info(f"StateSettlementWorkflow: {len(settlement_log)} settled, {len(conflicts)} conflicts")
        return conflicts, settlement_log

    def _extract_state_with_model(self, content: str, chapter_id: int) -> Optional[StateChange]:
        """使用模型从正文中提取状态变更（当JSON解析失败时的降级方案）"""
        prompt = PromptBuilder.build_state_extract_prompt(content[:2000])
        sampling = self._config.get_sampling("fact_check")
        sampling.max_tokens = 1024

        try:
            result = self._client.openai_chat_completions(
                messages=[{"role": "user", "content": prompt}],
                sampling=sampling,
                stream=False,
            )
            import re
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                data["chapter_id"] = chapter_id
                return StateChange.from_dict(data)
        except Exception as e:
            self._logger.warning(f"Model state extraction failed for ch{chapter_id}: {e}")

        return None
