"""工具注册中心 - 工具注册、权限校验、调用分发"""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from src.core.config import AutonomyLevel
from src.core.logger import Logger


@dataclass
class ToolCallResult:
    success: bool
    result: Any = None
    error: str = ""
    autonomy_level: AutonomyLevel = AutonomyLevel.FULL_AUTO
    reviewable: bool = False  # 建议执行级标记为可撤回
    pending_approval: bool = False  # 必确认级标记为待审批


@dataclass
class Tool:
    name: str
    fn: Callable
    description: str = ""
    autonomy_level: AutonomyLevel = AutonomyLevel.FULL_AUTO
    parameters_schema: Dict = field(default_factory=dict)


class ToolRegistry:
    """工具注册与调用分发"""

    def __init__(self, logger: Optional[Logger] = None):
        self._tools: Dict[str, Tool] = {}
        self._logger = logger or Logger.get()
        self._pending_approvals: List[Dict] = []
        self._reviewable_results: List[Dict] = []

    def register(self, tool: Tool):
        self._tools[tool.name] = tool
        self._logger.info(f"Tool registered: {tool.name} (autonomy={tool.autonomy_level.value})")

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_tools(self) -> List[Dict]:
        return [
            {"name": t.name, "description": t.description, "autonomy": t.autonomy_level.value}
            for t in self._tools.values()
        ]

    def call(self, tool_name: str, params: Dict, config_autonomy: Dict = None) -> ToolCallResult:
        """调用工具，根据自主权级别决定执行方式

        Args:
            tool_name: 工具名称
            params: 工具参数
            config_autonomy: 从配置中获取的工具-自主权映射
        """
        tool = self._tools.get(tool_name)
        if not tool:
            return ToolCallResult(success=False, error=f"Tool not found: {tool_name}")

        # 确定实际自主权级别（配置可覆盖工具默认级别）
        level = tool.autonomy_level
        if config_autonomy:
            for lvl_name, tool_list in config_autonomy.items():
                if tool_name in tool_list:
                    level = AutonomyLevel(lvl_name)
                    break

        try:
            if level == AutonomyLevel.FULL_AUTO:
                # 全自动级：直接执行
                result = tool.fn(**params)
                return ToolCallResult(
                    success=True,
                    result=result,
                    autonomy_level=level,
                )

            elif level == AutonomyLevel.SUGGESTED:
                # 建议执行级：执行后标记可撤回
                result = tool.fn(**params)
                review_entry = {
                    "tool": tool_name,
                    "params": params,
                    "result": result,
                    "timestamp": __import__("datetime").datetime.now().isoformat(),
                }
                self._reviewable_results.append(review_entry)
                return ToolCallResult(
                    success=True,
                    result=result,
                    autonomy_level=level,
                    reviewable=True,
                )

            elif level == AutonomyLevel.MUST_CONFIRM:
                # 必确认级：暂停，等待人类裁决
                approval_entry = {
                    "tool": tool_name,
                    "params": params,
                    "timestamp": __import__("datetime").datetime.now().isoformat(),
                    "status": "pending",
                }
                self._pending_approvals.append(approval_entry)
                return ToolCallResult(
                    success=False,
                    result=None,
                    autonomy_level=level,
                    pending_approval=True,
                    error="Operation requires human approval",
                )

        except Exception as e:
            self._logger.error(f"Tool call failed: {tool_name} - {e}")
            return ToolCallResult(success=False, error=str(e))

    def approve_pending(self, approval_id: int, approved: bool = True, modification: Dict = None):
        """审批待确认操作"""
        if 0 <= approval_id < len(self._pending_approvals):
            entry = self._pending_approvals[approval_id]
            if approved:
                tool = self._tools.get(entry["tool"])
                if tool:
                    params = modification or entry["params"]
                    result = tool.fn(**params)
                    entry["status"] = "approved"
                    entry["result"] = result
                    return ToolCallResult(success=True, result=result)
            else:
                entry["status"] = "rejected"
                return ToolCallResult(success=False, error="Operation rejected by human")
        return ToolCallResult(success=False, error="Invalid approval ID")

    def get_pending_approvals(self) -> List[Dict]:
        return [e for e in self._pending_approvals if e["status"] == "pending"]

    def get_reviewable_results(self) -> List[Dict]:
        return self._reviewable_results

    def revoke_reviewable(self, result_id: int):
        """撤回建议执行级操作"""
        if 0 <= result_id < len(self._reviewable_results):
            self._reviewable_results[result_id]["revoked"] = True
            self._logger.info(f"Reviewable result {result_id} revoked")
