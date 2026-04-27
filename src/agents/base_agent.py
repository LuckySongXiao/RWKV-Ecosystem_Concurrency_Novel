"""Agent 基类"""

from typing import Any, Dict, Optional

from src.core.config import PipelineConfig, AutonomyLevel
from src.core.rwkv_client import RWKVClient
from src.tools.tool_registry import ToolRegistry, ToolCallResult
from src.core.logger import Logger


class BaseAgent:
    """Agent 基类 - 所有 Agent 的公共接口"""

    agent_type: str = "base"
    state_file_key: str = ""  # 在 config.state_files 中的 key

    def __init__(
        self,
        client: RWKVClient,
        config: PipelineConfig,
        tool_registry: ToolRegistry,
        logger: Optional[Logger] = None,
    ):
        self._client = client
        self._config = config
        self._tools = tool_registry
        self._logger = logger or Logger.get()
        self._state_file = config.get_state_file(self.state_file_key)

    def call_tool(self, tool_name: str, params: Dict) -> ToolCallResult:
        """调用工具，根据自主权级别决定执行方式"""
        return self._tools.call(tool_name, params, self._config.autonomy)

    def get_info(self) -> Dict:
        """获取 Agent 信息"""
        return {
            "agent_type": self.agent_type,
            "state_file": self._state_file,
            "state_file_key": self.state_file_key,
        }
