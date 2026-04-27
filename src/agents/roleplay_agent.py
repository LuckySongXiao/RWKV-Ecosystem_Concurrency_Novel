"""Roleplay Agent - 角色扮演对话生成

与 WriterAgent 分离:
- WriterAgent: 批量章节正文创作（超级并发，无状态）
- RoleplayAgent: 角色对话/内心独白/行为演绎（串行，有状态缓存）

RoleplayAgent 使用 /state/chat/completions 端点，
利用 L1/L2/L3 三级缓存保持对话连贯性。
"""

from typing import Dict, List, Optional

from src.core.config import PipelineConfig
from src.core.rwkv_client import RWKVClient
from src.core.world_state_engine import WorldStateEngine
from src.tools.tool_registry import ToolRegistry
from src.workflow.roleplay_workflow import RoleplayWorkflow
from .base_agent import BaseAgent
from src.core.logger import Logger


class RoleplayAgent(BaseAgent):
    """Roleplay Agent

    职责: 角色扮演对话生成、内心独白、行为演绎
    自主权: 中
    State: roleplay.st
    API: /state/chat/completions (有状态缓存，串行)
    """

    agent_type = "roleplay"
    state_file_key = "roleplay"

    def __init__(self, client: RWKVClient, config: PipelineConfig,
                 world_engine: WorldStateEngine, tools: ToolRegistry,
                 logger: Logger = None):
        super().__init__(client, config, tools, logger)
        self._workflow = RoleplayWorkflow(client, config, world_engine, logger)

    def dialogue(
        self,
        character_id: str,
        scene_context: str,
        user_input: str,
        dialogue_history: str = "",
    ) -> str:
        """单角色对话

        Args:
            character_id: 角色ID
            scene_context: 场景上下文
            user_input: 用户输入
            dialogue_history: 对话历史

        Returns:
            角色回应文本
        """
        self._logger.info(f"RoleplayAgent: {character_id} dialogue")
        return self._workflow.run(character_id, scene_context, user_input, dialogue_history)

    def multi_dialogue(
        self,
        character_ids: List[str],
        scene_context: str,
        topic: str,
        rounds: int = 3,
    ) -> List[Dict]:
        """多角色对话场景

        Args:
            character_ids: 参与角色ID列表
            scene_context: 场景上下文
            topic: 对话主题
            rounds: 对话轮数

        Returns:
            对话记录列表
        """
        self._logger.info(f"RoleplayAgent: Multi-dialogue with {len(character_ids)} characters")
        return self._workflow.multi_character_dialogue(character_ids, scene_context, topic, rounds)

    def inner_monologue(self, character_id: str, situation: str) -> str:
        """角色内心独白

        Args:
            character_id: 角色ID
            situation: 当前处境描述

        Returns:
            内心独白文本
        """
        self._logger.info(f"RoleplayAgent: {character_id} inner monologue")
        return self._workflow.run(
            character_id=character_id,
            scene_context=situation,
            user_input="请描述你此刻的内心想法和感受。",
        )

    def reset_session(self, character_id: str):
        """重置角色的对话会话"""
        self._workflow.reset_session(character_id)
