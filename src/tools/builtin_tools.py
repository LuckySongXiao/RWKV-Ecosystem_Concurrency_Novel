"""内置工具函数实现"""

import json
from typing import Any, Dict, Optional

from src.core.config import AutonomyLevel
from .tool_registry import Tool, ToolRegistry


def search_web(query: str) -> str:
    """网络搜索（预留接口，可接外部搜索API）"""
    # TODO: 接入实际搜索API
    return f"[Search result placeholder for: {query}]"


def query_world_state(entity: str, world_engine=None) -> str:
    """查询当前角色/势力/经济状态"""
    if world_engine is None:
        return "[World engine not available]"
    result = world_engine.query_entity(entity)
    if result:
        return json.dumps(result, ensure_ascii=False, indent=2)
    relations = world_engine.query_relations(entity)
    if relations:
        return json.dumps(relations, ensure_ascii=False, indent=2)
    return f"[Entity '{entity}' not found in world state]"


def propose_state_change(changes: Dict, world_engine=None) -> str:
    """提出状态变更请求"""
    return json.dumps({
        "status": "proposed",
        "changes": changes,
        "message": "State change proposal submitted for review",
    }, ensure_ascii=False)


def resolve_conflict(conflict: Dict) -> str:
    """尝试解决实体冲突（生成解决方案选项）"""
    return json.dumps({
        "conflict": conflict,
        "options": [
            {"id": 1, "description": "保留旧值，忽略新变更"},
            {"id": 2, "description": "采用新值，覆盖旧状态"},
            {"id": 3, "description": "合并两者，保留关键属性"},
        ],
        "message": "Please select a resolution option",
    }, ensure_ascii=False)


def check_narrative_consistency(scene: Dict) -> str:
    """叙事一致性检查"""
    # 基础规则检查
    issues = []
    if "characters" in scene:
        for char in scene["characters"]:
            if not char.get("id"):
                issues.append(f"Character missing ID: {char}")
    return json.dumps({
        "consistent": len(issues) == 0,
        "issues": issues,
    }, ensure_ascii=False)


def format_checker(text: str) -> str:
    """格式与语法修正"""
    # 基础格式修正
    result = text
    # 修正连续空行
    import re
    result = re.sub(r'\n{3,}', '\n\n', result)
    # 修正中文标点后的空格
    result = re.sub(r'([，。！？；：）】」】])\s+', r'\1', result)
    return result


def save_content(content: str, filepath: str, file_manager=None) -> str:
    """存储生成内容"""
    if file_manager:
        file_manager.write_text(filepath, content)
        return f"Content saved to {filepath}"
    return f"[File manager not available, content not saved to {filepath}]"


def register_builtin_tools(
    registry: ToolRegistry,
    world_engine=None,
    file_manager=None,
):
    """注册所有内置工具到工具注册中心"""

    # 绑定 world_engine 和 file_manager
    def _query_world_state(entity: str) -> str:
        return query_world_state(entity, world_engine)

    def _propose_state_change(changes: Dict) -> str:
        return propose_state_change(changes, world_engine)

    def _save_content(content: str, filepath: str) -> str:
        return save_content(content, filepath, file_manager)

    tools = [
        Tool(
            name="search_web",
            fn=search_web,
            description="网络搜索",
            autonomy_level=AutonomyLevel.FULL_AUTO,
        ),
        Tool(
            name="query_world_state",
            fn=_query_world_state,
            description="查询当前角色/势力/经济状态",
            autonomy_level=AutonomyLevel.FULL_AUTO,
        ),
        Tool(
            name="propose_state_change",
            fn=_propose_state_change,
            description="提出状态变更请求",
            autonomy_level=AutonomyLevel.SUGGESTED,
        ),
        Tool(
            name="resolve_conflict",
            fn=resolve_conflict,
            description="尝试解决实体冲突",
            autonomy_level=AutonomyLevel.MUST_CONFIRM,
        ),
        Tool(
            name="check_narrative_consistency",
            fn=check_narrative_consistency,
            description="叙事一致性检查",
            autonomy_level=AutonomyLevel.FULL_AUTO,
        ),
        Tool(
            name="format_checker",
            fn=format_checker,
            description="格式与语法修正",
            autonomy_level=AutonomyLevel.FULL_AUTO,
        ),
        Tool(
            name="save_content",
            fn=_save_content,
            description="存储生成内容",
            autonomy_level=AutonomyLevel.FULL_AUTO,
        ),
    ]

    for tool in tools:
        registry.register(tool)
