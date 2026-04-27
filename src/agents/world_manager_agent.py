"""世界管理 Agent - 状态变更收集、排序、冲突校验、合并"""

from typing import Dict, List, Optional, Tuple

from src.core.config import PipelineConfig
from src.core.rwkv_client import RWKVClient
from src.core.world_state_engine import WorldStateEngine, Conflict
from src.tools.tool_registry import ToolRegistry
from src.workflow.state_workflow import StateSettlementWorkflow
from .base_agent import BaseAgent
from src.core.logger import Logger


class WorldManagerAgent(BaseAgent):
    """世界管理 Agent

    职责: 收集各章状态变更请求，按章排序、冲突校验、合并更新世界状态档案
    自主权: 中
    State: reviewer_factcheck.st (复用事实校验State)
    API: /openai/v1/chat/completions (串行)
    """

    agent_type = "world_manager"
    state_file_key = "reviewer_factcheck"

    def __init__(self, client: RWKVClient, config: PipelineConfig,
                 world_engine: WorldStateEngine, tools: ToolRegistry,
                 logger: Logger = None):
        super().__init__(client, config, tools, logger)
        self._workflow = StateSettlementWorkflow(world_engine, client, config, logger)
        self._world = world_engine

    def settle(self, drafts: List[Dict]) -> Tuple[List[Conflict], List[Dict]]:
        """执行状态串行结算

        Args:
            drafts: 章节初稿列表

        Returns:
            (conflicts, settlement_log)
        """
        self._logger.info("WorldManagerAgent: Starting state settlement...")
        return self._workflow.run(drafts)

    def handle_conflict(self, conflict: Conflict, resolution: str, details: Dict = None):
        """处理冲突（人类裁决后调用）"""
        self._world.resolve_conflict(conflict, resolution, details)
        self._world.persist()
        self._logger.info(f"WorldManagerAgent: Conflict resolved - {resolution}")

    def get_world_status(self) -> Dict:
        """获取当前世界状态概览"""
        return {
            "characters": len(self._world.characters),
            "factions": len(self._world.factions),
            "foreshadowings": {
                "planted": len(self._world.query_foreshadowings("planted")),
                "resolved": len(self._world.query_foreshadowings("resolved")),
            },
            "timeline_events": len(self._world.entity_store.timeline),
        }
