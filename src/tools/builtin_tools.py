"""内置工具函数实现

工具分类:
- 信息查询: search_web, query_world_state, query_foreshadowings, query_timeline
- 状态操作: propose_state_change, resolve_conflict
- 质量检查: check_narrative_consistency, validate_character_presence, format_checker
- 内容操作: save_content, extract_state_changes, summarize_text
- 管线操作: generate_outline, generate_storyline
"""

import json
import re
from typing import Any, Dict, List, Optional

from src.core.config import AutonomyLevel
from .tool_registry import Tool, ToolRegistry


def search_web(query: str) -> str:
    """网络搜索（预留接口，可接外部搜索API）"""
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


def query_foreshadowings(status: str = "", world_engine=None) -> str:
    """查询伏笔状态

    Args:
        status: 伏笔状态过滤 (planted/resolved/abandoned)，空字符串表示全部
    """
    if world_engine is None:
        return "[World engine not available]"
    result = world_engine.query_foreshadowings(status if status else None)
    return json.dumps(result, ensure_ascii=False, indent=2)


def query_timeline(from_chapter: int = 0, to_chapter: int = 99999, world_engine=None) -> str:
    """查询时间线事件

    Args:
        from_chapter: 起始章节
        to_chapter: 结束章节
    """
    if world_engine is None:
        return "[World engine not available]"
    result = world_engine.query_timeline(from_chapter, to_chapter)
    return json.dumps(result, ensure_ascii=False, indent=2)


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
    issues = []
    if "characters" in scene:
        for char in scene["characters"]:
            if not char.get("id"):
                issues.append(f"Character missing ID: {char}")
    return json.dumps({
        "consistent": len(issues) == 0,
        "issues": issues,
    }, ensure_ascii=False)


def validate_character_presence(
    chapter_content: str,
    expected_characters: List[str],
) -> str:
    """验证章节中角色出场情况

    Args:
        chapter_content: 章节正文
        expected_characters: 预期出场的角色名列表
    """
    missing = []
    appeared = []
    for char_name in expected_characters:
        if char_name in chapter_content:
            appeared.append(char_name)
        else:
            missing.append(char_name)

    return json.dumps({
        "appeared": appeared,
        "missing": missing,
        "coverage": len(appeared) / len(expected_characters) if expected_characters else 1.0,
    }, ensure_ascii=False)


def format_checker(text: str) -> str:
    """格式与语法修正"""
    result = text
    result = re.sub(r'\n{3,}', '\n\n', result)
    result = re.sub(r'([，。！？；：）】」】])\s+', r'\1', result)
    return result


def save_content(content: str, filepath: str, file_manager=None) -> str:
    """存储生成内容"""
    if file_manager:
        file_manager.write_text(filepath, content)
        return f"Content saved to {filepath}"
    return f"[File manager not available, content not saved to {filepath}]"


def extract_state_changes(chapter_content: str) -> str:
    """从章节正文中提取状态变更

    尝试从Markdown代码块中提取JSON格式的状态变更。
    """
    json_pattern = r'```json\s*(.*?)\s*```'
    matches = re.findall(json_pattern, chapter_content, re.DOTALL)

    if matches:
        try:
            data = json.loads(matches[-1])
            return json.dumps(data, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            pass

    return json.dumps({
        "status": "no_state_changes_found",
        "message": "No valid state change JSON found in chapter content",
    }, ensure_ascii=False)


def summarize_text(text: str, max_length: int = 500) -> str:
    """文本摘要（截断式）

    Args:
        text: 原始文本
        max_length: 最大长度
    """
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."


def generate_outline(spec: str, client=None, config=None) -> str:
    """生成全书大纲

    Args:
        spec: 世界观设定文本
    """
    if client is None:
        return "[RWKV client not available]"

    from src.core.prompt_builder import PromptBuilder
    from src.core.config import SamplingParams

    prompt = PromptBuilder.build_outline_prompt(spec)
    sampling = SamplingParams(temperature=0.9, top_p=0.85, max_tokens=4096)

    try:
        result = client.openai_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            sampling=sampling,
            stream=False,
        )
        return result
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def generate_storyline(theme: str, characters_summary: str, volume_count: int = 4,
                       chapters_per_volume: int = 10, client=None) -> str:
    """生成故事主线

    Args:
        theme: 题材类型
        characters_summary: 角色摘要
        volume_count: 卷数
        chapters_per_volume: 每卷章节数
    """
    if client is None:
        return "[RWKV client not available]"

    from src.core.prompt_builder import PromptBuilder
    from src.core.config import SamplingParams

    prompt = PromptBuilder.build_storyline_prompt(
        theme=theme,
        characters_summary=characters_summary,
        volume_count=volume_count,
        chapters_per_volume=chapters_per_volume,
    )
    sampling = SamplingParams(temperature=0.9, top_p=0.85, max_tokens=3072)

    try:
        result = client.openai_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            sampling=sampling,
            stream=False,
        )
        return result
    except Exception as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)


def register_builtin_tools(
    registry: ToolRegistry,
    world_engine=None,
    file_manager=None,
    client=None,
    config=None,
):
    """注册所有内置工具到工具注册中心"""

    def _query_world_state(entity: str) -> str:
        return query_world_state(entity, world_engine)

    def _query_foreshadowings(status: str = "") -> str:
        return query_foreshadowings(status, world_engine)

    def _query_timeline(from_chapter: int = 0, to_chapter: int = 99999) -> str:
        return query_timeline(from_chapter, to_chapter, world_engine)

    def _propose_state_change(changes: Dict) -> str:
        return propose_state_change(changes, world_engine)

    def _save_content(content: str, filepath: str) -> str:
        return save_content(content, filepath, file_manager)

    def _generate_outline(spec: str) -> str:
        return generate_outline(spec, client, config)

    def _generate_storyline(theme: str, characters_summary: str,
                            volume_count: int = 4, chapters_per_volume: int = 10) -> str:
        return generate_storyline(theme, characters_summary, volume_count,
                                  chapters_per_volume, client)

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
            name="query_foreshadowings",
            fn=_query_foreshadowings,
            description="查询伏笔状态（planted/resolved/abandoned）",
            autonomy_level=AutonomyLevel.FULL_AUTO,
        ),
        Tool(
            name="query_timeline",
            fn=_query_timeline,
            description="查询时间线事件",
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
            name="validate_character_presence",
            fn=validate_character_presence,
            description="验证章节中角色出场情况",
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
        Tool(
            name="extract_state_changes",
            fn=extract_state_changes,
            description="从章节正文中提取状态变更JSON",
            autonomy_level=AutonomyLevel.FULL_AUTO,
        ),
        Tool(
            name="summarize_text",
            fn=summarize_text,
            description="文本摘要（截断式）",
            autonomy_level=AutonomyLevel.FULL_AUTO,
        ),
        Tool(
            name="generate_outline",
            fn=_generate_outline,
            description="生成全书大纲",
            autonomy_level=AutonomyLevel.SUGGESTED,
        ),
        Tool(
            name="generate_storyline",
            fn=_generate_storyline,
            description="生成故事主线",
            autonomy_level=AutonomyLevel.SUGGESTED,
        ),
    ]

    for tool in tools:
        registry.register(tool)
