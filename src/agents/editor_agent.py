"""总编 Agent - 全书/卷大纲结构化生成与宏观叙事决策"""

from typing import Dict, List, Optional

from src.core.config import PipelineConfig
from src.core.rwkv_client import RWKVClient
from src.core.file_manager import FileManager
from src.tools.tool_registry import ToolRegistry
from src.workflow.outline_workflow import OutlineWorkflow
from .base_agent import BaseAgent
from src.core.logger import Logger


class EditorAgent(BaseAgent):
    """总编 Agent

    职责: 全书/卷大纲结构化生成、宏观叙事决策
    自主权: 高
    State: editor_planning.st
    API: /openai/v1/chat/completions (串行)
    """

    agent_type = "editor"
    state_file_key = "editor_planning"

    def __init__(self, client: RWKVClient, config: PipelineConfig,
                 fm: FileManager, tools: ToolRegistry, logger: Logger = None):
        super().__init__(client, config, tools, logger)
        self._workflow = OutlineWorkflow(client, config, fm, logger)

    def generate_outline(self, spec: str, style_guide: str = "") -> Dict:
        """生成全书大纲"""
        self._logger.info("EditorAgent: Generating book outline...")
        return self._workflow.run(spec, style_guide)

    def generate_volumes(self, outline: Dict) -> List[Dict]:
        """生成各卷详细大纲"""
        self._logger.info("EditorAgent: Generating volume outlines...")
        return self._workflow.generate_volumes(outline)

    def init_world_state_from_outline(self, outline: Dict, world_engine) -> None:
        """从大纲中初始化世界状态"""
        # 初始化角色
        for char_data in outline.get("initial_characters", []):
            from src.core.world_state_engine import CharacterState
            char = CharacterState(
                character_id=char_data.get("id", ""),
                name=char_data.get("name", ""),
                attributes=char_data.get("attributes", {}),
                location=char_data.get("location", ""),
                status="active",
                relationships=char_data.get("relationships", []),
            )
            world_engine.characters[char.character_id] = char
            world_engine.entity_store.entities[char.character_id] = {
                "type": "character",
                "label": char.name,
            }

        # 初始化势力
        for faction_data in outline.get("initial_factions", []):
            from src.core.world_state_engine import FactionState
            faction = FactionState(
                faction_id=faction_data.get("id", ""),
                name=faction_data.get("name", ""),
                members=faction_data.get("members", []),
                territory=faction_data.get("territory", ""),
                resources=faction_data.get("resources", {}),
            )
            world_engine.factions[faction.faction_id] = faction
            world_engine.entity_store.entities[faction.faction_id] = {
                "type": "faction",
                "label": faction.name,
            }

        world_engine.persist()
        self._logger.info(f"EditorAgent: Initialized world state with {len(world_engine.characters)} chars, {len(world_engine.factions)} factions")
