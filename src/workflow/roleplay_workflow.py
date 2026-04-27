"""Roleplay 工作流 - 角色扮演对话生成

Roleplay Agent 与作家 Agent 分离:
- 作家Agent: 负责章节正文批量创作（超级并发，无状态）
- RoleplayAgent: 负责角色对话/内心独白/行为演绎（串行，有状态缓存）
"""

import json
import time
from typing import Dict, List, Optional

from src.core.rwkv_client import RWKVClient
from src.core.config import PipelineConfig
from src.core.prompt_builder import PromptBuilder
from src.core.world_state_engine import WorldStateEngine
from src.core.logger import Logger


class RoleplayWorkflow:
    """Roleplay 工作流 skill

    特点:
    - 使用 /state/chat/completions 有状态缓存端点
    - 每个角色有独立的 session_id
    - 挂载 roleplay.st State 文件
    - 串行执行，保持对话连贯性
    """

    def __init__(self, client: RWKVClient, config: PipelineConfig,
                 world_engine: WorldStateEngine, logger: Logger = None):
        self._client = client
        self._config = config
        self._world = world_engine
        self._logger = logger or Logger.get()
        self._sessions: Dict[str, str] = {}  # character_id -> session_id

    def run(
        self,
        character_id: str,
        scene_context: str,
        user_input: str,
        dialogue_history: str = "",
    ) -> str:
        """执行角色扮演对话

        Args:
            character_id: 角色ID
            scene_context: 场景上下文
            user_input: 用户输入
            dialogue_history: 对话历史

        Returns:
            角色回应文本
        """
        self._logger.info(f"RoleplayWorkflow: {character_id} responding...")

        # 获取角色状态
        char_state = self._world.query_entity(character_id)
        char_state_str = json.dumps(char_state, ensure_ascii=False) if char_state else "未知角色"

        # 构造 Prompt
        prompt = PromptBuilder.build_roleplay_prompt(
            character_id, char_state_str, scene_context, user_input, dialogue_history
        )

        # 获取或创建 session
        session_id = self._sessions.get(character_id, f"roleplay_{character_id}")
        self._sessions[character_id] = session_id

        sampling = self._config.get_sampling("roleplay")

        start = time.time()
        result = self._client.state_chat_completions(
            contents=[prompt],
            sampling=sampling,
            session_id=session_id,
            stream=False,
        )
        elapsed = (time.time() - start) * 1000

        response = result[0] if isinstance(result, list) and result else str(result)

        self._logger.log_agent_call(
            "roleplay", f"dialogue_{character_id}", prompt[:200],
            {"temperature": sampling.temperature, "top_p": sampling.top_p},
            response[:200], elapsed,
        )

        return response

    def reset_session(self, character_id: str):
        """重置角色的对话会话"""
        session_id = self._sessions.pop(character_id, None)
        if session_id:
            try:
                self._client.state_delete(session_id)
            except Exception as e:
                self._logger.warning(f"Failed to delete session {session_id}: {e}")

    def multi_character_dialogue(
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
        dialogue_log = []
        history = ""

        for round_num in range(rounds):
            for char_id in character_ids:
                response = self.run(
                    character_id=char_id,
                    scene_context=scene_context,
                    user_input=topic if round_num == 0 else f"继续讨论: {topic}",
                    dialogue_history=history,
                )
                entry = {
                    "round": round_num + 1,
                    "character_id": char_id,
                    "response": response,
                }
                dialogue_log.append(entry)
                history += f"\n{char_id}: {response}"

        return dialogue_log
