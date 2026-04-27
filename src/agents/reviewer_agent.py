"""审核 Agent - 事实校验 + 叙事一致性审查"""

from typing import Dict, List, Optional

from src.core.config import PipelineConfig
from src.core.rwkv_client import RWKVClient
from src.core.world_state_engine import WorldStateEngine
from src.tools.tool_registry import ToolRegistry
from src.workflow.review_workflow import ReviewWorkflow, ReviewResult
from .base_agent import BaseAgent
from src.core.logger import Logger


class ReviewerAgent(BaseAgent):
    """审核 Agent

    职责: 事实校验 + 叙事一致性审查，发现问题可驳回并触发重写
    自主权: 高
    State: reviewer_factcheck.st / reviewer_narrative.st
    API: /openai/v1/chat/completions (串行)
    """

    agent_type = "reviewer"
    state_file_key = "reviewer_narrative"

    def __init__(self, client: RWKVClient, config: PipelineConfig,
                 world_engine: WorldStateEngine, tools: ToolRegistry,
                 logger: Logger = None):
        super().__init__(client, config, tools, logger)
        self._workflow = ReviewWorkflow(client, config, world_engine, logger)

    def review(self, drafts: List[Dict]) -> ReviewResult:
        """执行完整审核（事实校验 + 叙事审查）"""
        self._logger.info("ReviewerAgent: Starting review...")
        return self._workflow.run(drafts)

    def review_and_get_rejections(self, drafts: List[Dict]) -> List[Dict]:
        """审核并返回驳回列表（供调度器触发重写）"""
        result = self.review(drafts)
        if result.passed:
            return []
        return result.rejections
