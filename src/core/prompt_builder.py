"""Prompt 构造器 - 按任务类型构造格式化 Prompt

RWKV 对提示词结构高度敏感，严格区分:
- Instruction/Response 格式: 结构化提取任务（大纲生成、状态抽取等）
- User/Assistant 格式: 创作型续写任务

8K 上下文约束:
- 所有 Prompt 构建方法均接受 context_budget 参数
- 超出预算的组件会被自动截断
"""

import json
from typing import Dict, List, Optional, Any

from .world_state_engine import WorldStateSummary
from .genre_expander import ContextBudget


class PromptFormat:
    INSTRUCTION_RESPONSE = "instruction_response"
    USER_ASSISTANT = "user_assistant"


class PromptBuilder:
    """按任务类型构造格式化 Prompt（8K 上下文预算感知）"""

    _budget = ContextBudget()

    # ============================================================
    # 宏观规划层
    # ============================================================

    @staticmethod
    def build_outline_prompt(spec: str, style_guide: str = "") -> str:
        """全书大纲生成 - User/Assistant 格式（8K 预算感知）"""
        budget = PromptBuilder._budget
        spec_budget = budget.get_budget("outline_gen", "spec")
        spec = budget.truncate_to_budget(spec, spec_budget)

        parts = [
            "User: 你是一位资深小说总编。请基于以下世界观设定和写作风格，生成一部长篇小说的完整大纲。",
            "\n大纲必须包含以下结构（输出JSON格式）：",
            "- title: 书名",
            "- genre: 题材类型",
            "- volumes: 卷级结构数组（每卷含 volume_id, volume_title, theme, chapter_count, main_events）",
            "- main_conflict: 核心冲突描述",
            "- ending_direction: 结局方向",
            "- world_setting_summary: 世界观摘要",
            "- initial_characters: 主要角色初始状态列表",
            "- initial_factions: 主要势力初始状态列表",
            "\n## 世界观设定",
            spec,
        ]
        if style_guide:
            style_budget = budget.get_budget("outline_gen", "style")
            style_guide = budget.truncate_to_budget(style_guide, style_budget)
            parts.append("\n## 写作风格约束")
            parts.append(style_guide)
        parts.append("\nAssistant: ")
        return "\n".join(parts)

    @staticmethod
    def build_volume_prompt(outline_json: str) -> str:
        """各卷大纲生成 - Instruction/Response 格式"""
        return (
            "Instruction: 基于以下全书大纲，为每一卷生成详细大纲。"
            "每卷大纲需包含：卷编号、卷标题、章节数、核心事件列表（含事件描述、涉及章节、重要性）、"
            "角色出场计划（角色ID、首次出场章节、角色定位）、伏笔埋设计划。\n"
            "输出JSONL格式，每行一个卷的JSON对象。\n"
            f"Input: {outline_json}\n"
            "Response: "
        )

    # ============================================================
    # 超级并发创作层
    # ============================================================

    @staticmethod
    def build_chapter_outline_prompt(volume_outline: str, chapter_idx: int, total_chapters: int) -> str:
        """章节大纲并行生成 - Instruction/Response 格式"""
        return (
            f"Instruction: 基于以下卷大纲，生成第{chapter_idx}章（共{total_chapters}章）的详细大纲。"
            "章节大纲需包含：chapter_id（全局编号）、volume_id、chapter_title、synopsis（200字概要）、"
            "involved_characters（涉及角色ID列表）、involved_factions（涉及势力ID列表）、"
            "foreshadowing（本章伏笔：plant埋设列表+resolve回收列表）。\n"
            "输出JSON格式。\n"
            f"Input: {volume_outline}\n"
            "Response: "
        )

    @staticmethod
    def build_chapter_content_prompt(
        chapter_outline: str,
        state_summary: WorldStateSummary,
        previous_summary: str = "",
        style_guide: str = "",
    ) -> str:
        """章节正文并行创作 - User/Assistant 续写格式（8K 预算感知）

        核心特征：注入世界状态摘要，确保AI基于最新事实创作
        8K约束：各组件按预算截断，优先保证章节大纲和输出格式完整
        """
        budget = PromptBuilder._budget
        task = "chapter_content"

        parts = [
            "User: 你是一位才华横溢的小说作家。请基于以下信息续写本章内容。",
            "\n## 本章大纲",
            chapter_outline,
        ]

        # 注入世界状态摘要（压缩版）
        state_text = state_summary.format_for_prompt()
        if state_text:
            state_text = budget.format_state_summary_compact(state_text, task)
            parts.append("\n## 当前世界状态")
            parts.append(state_text)

        if previous_summary:
            prev_budget = budget.get_budget(task, "previous_summary")
            previous_summary = budget.truncate_to_budget(previous_summary, prev_budget)
            parts.append("\n## 前情提要")
            parts.append(previous_summary)

        if style_guide:
            style_budget = budget.get_budget(task, "style")
            style_guide = budget.truncate_to_budget(style_guide, style_budget)
            parts.append("\n## 写作风格约束")
            parts.append(style_guide)

        parts.append(
            "\n## 输出要求\n"
            "1. 先输出本章正文（Markdown格式）\n"
            "2. 在正文末尾用 ```json ``` 代码块附带状态变更JSON，格式如下：\n"
            "```json\n"
            "{\n"
            '  "chapter_id": 章节编号,\n'
            '  "character_changes": [{"character_id": "", "attribute": "", "old_value": "", "new_value": "", "reason": ""}],\n'
            '  "faction_changes": [],\n'
            '  "economy_changes": [],\n'
            '  "new_foreshadowing": [{"id": "", "description": "", "expected_resolve_chapter": 0}],\n'
            '  "resolved_foreshadowing": [{"id": "", "method": ""}]\n'
            "}\n"
            "```\n"
        )
        parts.append("\nAssistant: ")
        return "\n".join(parts)

    # ============================================================
    # Roleplay Agent 专用
    # ============================================================

    @staticmethod
    def build_roleplay_prompt(
        character_id: str,
        character_state: str,
        scene_context: str,
        user_input: str,
        dialogue_history: str = "",
    ) -> str:
        """Roleplay Agent Prompt - 角色扮演对话生成

        Roleplay Agent 与作家 Agent 分离：
        - 作家Agent: 负责章节正文批量创作（超级并发）
        - RoleplayAgent: 负责角色对话/内心独白/行为演绎（串行，有状态）
        """
        parts = [
            f"User: 你正在扮演角色 {character_id}。",
            "\n## 角色当前状态",
            character_state,
            "\n## 场景上下文",
            scene_context,
        ]
        if dialogue_history:
            parts.append("\n## 对话历史")
            parts.append(dialogue_history)
        parts.append(f"\n## 用户输入\n{user_input}")
        parts.append("\n请以该角色的身份和语气进行回应，保持角色性格一致性。")
        parts.append("\nAssistant: ")
        return "\n".join(parts)

    # ============================================================
    # 审核层
    # ============================================================

    @staticmethod
    def build_fact_check_prompt(drafts_summary: str, world_state_summary: str) -> str:
        """事实校验 Prompt - Instruction/Response 格式"""
        return (
            "Instruction: 请对以下章节内容进行事实一致性校验。"
            "检查范围：角色属性是否与世界状态一致、势力归属是否正确、"
            "经济数值是否合理、时间线是否连贯、唯一物品归属是否冲突。\n"
            "输出JSON格式：{\"passed\": true/false, \"issues\": [{\"chapter_id\": 0, \"type\": \"\", \"description\": \"\"}]}\n"
            f"Input:\n章节摘要: {drafts_summary}\n世界状态: {world_state_summary}\n"
            "Response: "
        )

    @staticmethod
    def build_narrative_review_prompt(drafts_summary: str, world_state_summary: str) -> str:
        """叙事一致性审查 Prompt - User/Assistant 格式"""
        return (
            "User: 你是一位严格的小说审核编辑。请对以下章节内容进行叙事一致性审查。\n"
            "审查范围：\n"
            "1. 前后文是否有矛盾\n"
            "2. 伏笔是否遗漏或错配\n"
            "3. 人物弧光是否断裂\n"
            "4. 时间线是否错乱\n"
            "5. 叙事节奏是否合理\n\n"
            f"## 章节摘要\n{drafts_summary}\n\n"
            f"## 世界状态\n{world_state_summary}\n\n"
            "输出JSON格式："
            "{\"passed\": true/false, "
            "\"rejections\": [{\"chapter_id\": 0, \"reason\": \"\", \"suggestion\": \"\"}]}\n"
            "Assistant: "
        )

    # ============================================================
    # 状态变更提取
    # ============================================================

    @staticmethod
    def build_state_extract_prompt(chapter_content: str) -> str:
        """从章节正文中提取状态变更 - Instruction/Response 格式"""
        return (
            "Instruction: 从以下章节正文中提取状态变更信息。"
            "输出JSON格式，包含 character_changes, faction_changes, economy_changes, "
            "new_foreshadowing, resolved_foreshadowing。\n"
            f"Input: {chapter_content}\n"
            "Response: "
        )

    # ============================================================
    # 批量 Prompt 构造
    # ============================================================

    @staticmethod
    def build_batch_chapter_outline_prompts(
        volume_outline: str,
        chapter_count: int,
        start_chapter_id: int = 1,
    ) -> List[str]:
        """批量构造章节大纲生成 Prompt"""
        return [
            PromptBuilder.build_chapter_outline_prompt(volume_outline, i, chapter_count)
            for i in range(start_chapter_id, start_chapter_id + chapter_count)
        ]

    @staticmethod
    def build_batch_chapter_content_prompts(
        chapter_outlines: List[Dict],
        state_summaries: List[WorldStateSummary],
        previous_summaries: List[str] = None,
        style_guide: str = "",
    ) -> List[str]:
        """批量构造章节正文创作 Prompt"""
        prompts = []
        for i, outline in enumerate(chapter_outlines):
            summary = state_summaries[i] if i < len(state_summaries) else WorldStateSummary()
            prev = previous_summaries[i] if previous_summaries and i < len(previous_summaries) else ""
            prompts.append(
                PromptBuilder.build_chapter_content_prompt(
                    json.dumps(outline, ensure_ascii=False),
                    summary,
                    prev,
                    style_guide,
                )
            )
        return prompts
