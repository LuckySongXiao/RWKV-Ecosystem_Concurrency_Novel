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
        """各卷大纲生成 - User/Assistant 格式"""
        return (
            "User: 基于以下全书大纲，为每一卷生成详细大纲。"
            "每卷大纲需包含：卷编号、卷标题、章节数、核心事件列表（含事件描述、涉及章节、重要性）、"
            "角色出场计划（角色ID、首次出场章节、角色定位）、伏笔埋设计划。\n"
            "输出JSONL格式，每行一个卷的JSON对象。\n"
            f"\n{outline_json}\n"
            "\nAssistant: "
        )

    # ============================================================
    # 超级并发创作层
    # ============================================================

    @staticmethod
    def build_chapter_outline_prompt(volume_outline: str, chapter_idx: int, total_chapters: int) -> str:
        """章节大纲并行生成 - User/Assistant 格式"""
        return (
            f"User: 基于以下卷大纲，生成第{chapter_idx}章（共{total_chapters}章）的详细大纲。"
            "章节大纲需包含：chapter_id（全局编号）、volume_id、chapter_title、synopsis（200字概要）、"
            "involved_characters（涉及角色ID列表）、involved_factions（涉及势力ID列表）、"
            "foreshadowing（本章伏笔：plant埋设列表+resolve回收列表）。\n"
            "输出JSON格式。\n"
            f"\n{volume_outline}\n"
            "\nAssistant: "
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
        """事实校验 Prompt - User/Assistant 格式"""
        return (
            "User: 请对以下章节内容进行事实一致性校验。"
            "检查范围：角色属性是否与世界状态一致、势力归属是否正确、"
            "经济数值是否合理、时间线是否连贯、唯一物品归属是否冲突。\n"
            "输出JSON格式：{\"passed\": true/false, \"issues\": [{\"chapter_id\": 0, \"type\": \"\", \"description\": \"\"}]}\n"
            f"\n章节摘要: {drafts_summary}\n世界状态: {world_state_summary}\n"
            "\nAssistant: "
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
        """从章节正文中提取状态变更 - User/Assistant 格式"""
        return (
            "User: 从以下章节正文中提取状态变更信息。"
            "输出JSON格式，包含 character_changes, faction_changes, economy_changes, "
            "new_foreshadowing, resolved_foreshadowing。\n"
            f"\n{chapter_content}\n"
            "\nAssistant: "
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

    # ============================================================
    # 优化管线专用 - 切片级 Prompt
    # ============================================================

    @staticmethod
    def build_slice_writing_prompt(
        chapter_info: Dict,
        slice_type: str,
        slice_description: str,
        slice_idx: int,
        total_slices: int,
        characters: List[Dict],
        main_storyline: Dict,
        world_state_text: str = "",
        previous_slice_content: str = "",
        style_guide: str = "",
    ) -> str:
        """章节切片写作 Prompt - User/Assistant 续写格式

        核心特征：
        - 注入世界状态摘要，确保叙事一致性
        - 注入前一切片内容，保证切片间连贯性
        - 明确切片类型和位置，指导写作方向
        """
        parts = [
            "User: 你是一位才华横溢的小说作家。请基于以下信息，撰写小说章节的指定切片部分。",
            f"\n## 章节信息",
            f"- 章节编号: 第{chapter_info.get('chapter_id', '?')}章",
            f"- 章节标题: {chapter_info.get('chapter_title', '未命名')}",
            f"- 章节概要: {chapter_info.get('synopsis', '')}",
        ]

        involved = chapter_info.get("involved_characters", [])
        if involved:
            parts.append(f"- 涉及角色: {', '.join(involved)}")

        parts.append(f"\n## 切片信息")
        parts.append(f"- 切片类型: {slice_type}")
        parts.append(f"- 切片描述: {slice_description}")
        parts.append(f"- 切片位置: 第{slice_idx + 1}/{total_slices}切片")

        if characters:
            parts.append("\n## 角色信息卡")
            for ch in characters[:8]:
                name = ch.get("name", "未知")
                role = ch.get("role_type", "")
                identity = ch.get("identity", "")
                personality = ch.get("personality", "")
                parts.append(f"- **{name}** ({role}): {identity}")
                if personality:
                    parts.append(f"  性格: {personality}")

        if main_storyline:
            parts.append("\n## 故事主线")
            title = main_storyline.get("title", "")
            desc = main_storyline.get("description", "")
            core_conflict = main_storyline.get("core_conflict", "")
            if title:
                parts.append(f"- 主线: {title}")
            if desc:
                parts.append(f"- 概述: {desc}")
            if core_conflict:
                parts.append(f"- 核心冲突: {core_conflict}")

            stages = main_storyline.get("stages", [])
            if stages:
                ch_id = chapter_info.get("chapter_id", 0)
                vol_id = chapter_info.get("volume_id", 1)
                for stage in stages:
                    if stage.get("volume_id") == vol_id:
                        parts.append(f"- 本卷阶段: {stage.get('stage_name', '')}")
                        parts.append(f"- 阶段描述: {stage.get('description', '')}")
                        key_events = stage.get("key_events", [])
                        if key_events:
                            parts.append(f"- 关键事件: {', '.join(key_events[:5])}")
                        break

        if world_state_text:
            parts.append("\n## 当前世界状态")
            parts.append(world_state_text)

        if previous_slice_content:
            parts.append("\n## 前一切片内容（请保持连贯）")
            parts.append(previous_slice_content[:1500])

        if style_guide:
            parts.append("\n## 写作风格约束")
            parts.append(style_guide)

        slice_guidance = {
            "开场": "重点描写场景氛围、引入关键角色、设定故事基调。注意环境描写和角色出场的自然过渡。",
            "发展": "推进情节、深化冲突、展现角色互动。注意节奏把控，避免平铺直叙。",
            "高潮": "冲突爆发、关键转折、情感爆发。注意张力营造和节奏加速，让读者身临其境。",
            "结尾": "收束冲突、埋设伏笔、承上启下。注意留白和悬念，为下一章做铺垫。",
        }

        parts.append("\n## 写作要求")
        parts.append(f"1. 这是章节的「{slice_type}」部分，{slice_guidance.get(slice_type, '请保持叙事连贯性')}")
        parts.append("2. 使用Markdown格式，自然段落分明")
        parts.append("3. 保持与角色设定和世界观的严格一致")
        parts.append("4. 注重细节描写（动作、对话、心理、环境）")
        parts.append("5. 字数控制在600-1000字")
        if slice_idx > 0:
            parts.append("6. 必须与前一切片内容自然衔接，避免重复或跳跃")
        if slice_idx < total_slices - 1:
            parts.append("7. 在结尾处留下自然的过渡点，便于后续切片续写")

        parts.append("\nAssistant: ")
        return "\n".join(parts)

    @staticmethod
    def build_chapter_outline_from_volume_prompt(
        volume_info: Dict,
        chapter_idx: int,
        total_chapters: int,
        characters: List[Dict],
        main_storyline: Dict,
    ) -> str:
        """基于卷大纲AI生成章节大纲 - 简化JSON格式"""
        parts = [
            f"User: 基于以下卷大纲和角色信息，为第{chapter_idx}章（共{total_chapters}章）生成详细大纲。",
            "\n## 卷信息",
            f"- 卷编号: 第{volume_info.get('volume_id', 1)}卷",
            f"- 卷标题: {volume_info.get('volume_title', '')}",
            f"- 卷主题: {volume_info.get('theme', '')}",
        ]

        events = volume_info.get("main_events", [])
        if not events:
            events_raw = volume_info.get("events", "")
            if events_raw:
                events = [{"event_name": e.strip()} for e in events_raw.split(";") if e.strip()]
        if events:
            parts.append("\n## 本卷关键事件")
            for i, event in enumerate(events):
                ename = event.get("event_name", f"事件{i+1}")
                edesc = event.get("description", "")
                parts.append(f"- {ename}" + (f": {edesc}" if edesc else ""))

        if characters:
            parts.append("\n## 可用角色")
            for ch in characters[:10]:
                name = ch.get("name", "未知")
                role = ch.get("role_type", "")
                identity = ch.get("identity", "")
                parts.append(f"- {name}({role}): {identity}")

        if main_storyline:
            stages = main_storyline.get("stages", [])
            vol_id = volume_info.get("volume_id", 1)
            for stage in stages:
                if stage.get("volume_id") == vol_id:
                    parts.append(f"\n## 本卷主线阶段")
                    parts.append(f"- 阶段名: {stage.get('stage_name', '')}")
                    parts.append(f"- 描述: {stage.get('description', '')}")
                    parts.append(f"- 关键事件: {', '.join(stage.get('key_events', []))}")
                    break

        parts.append(f"\n## 输出格式（JSON）")
        parts.append("```json")
        parts.append(json.dumps({
            "chapter_id": chapter_idx,
            "volume_id": volume_info.get("volume_id", 1),
            "chapter_title": "章节标题",
            "synopsis": "200字章节概要",
            "characters": "出场角色，用逗号分隔",
            "foreshadowing_plant": "本章埋设的伏笔",
            "foreshadowing_resolve": "本章回收的伏笔",
            "emotional_arc": "情感弧线（如：紧张→爆发→释然）",
            "key_scenes": "关键场景，用分号分隔",
        }, ensure_ascii=False, indent=2))
        parts.append("```")
        parts.append("\nAssistant: ")

        return "\n".join(parts)

    @staticmethod
    def build_storyline_prompt(
        theme: str,
        characters_summary: str,
        volume_count: int,
        chapters_per_volume: int,
        extra_context: str = "",
    ) -> str:
        """故事主线生成 Prompt - User/Assistant 格式"""
        parts = [
            "User: 你是一位资深小说编剧。请基于以下信息，设计完整的故事主线剧情。",
            f"\n## 基本信息",
            f"- 题材类型: {theme}",
            f"- 总卷数: {volume_count} 卷",
            f"- 每卷章节数: {chapters_per_volume} 章",
            f"- 总章节数: {volume_count * chapters_per_volume} 章",
            "\n## 角色体系",
            characters_summary,
        ]

        if extra_context:
            parts.append(f"\n## 额外设定\n{extra_context}")

        parts.append("\n## 输出格式（JSON）")
        parts.append("```json")
        parts.append(json.dumps({
            "title": "主线名称",
            "description": "主线剧情概述（200字以内）",
            "stages": [
                {
                    "volume_id": 1,
                    "stage_name": "阶段名称",
                    "description": "阶段剧情描述",
                    "key_events": ["关键事件1", "关键事件2"],
                    "character_focus": ["本阶段主要角色"],
                }
            ],
            "core_conflict": "核心冲突描述",
            "sub_conflicts": ["支线冲突1", "支线冲突2"],
            "ending": "结局方向",
        }, ensure_ascii=False, indent=2))
        parts.append("```")
        parts.append("\n请确保：1. 每卷都有明确的阶段目标 2. 角色发展弧线完整 3. 伏笔与回收对应 4. 冲突层层递进")
        parts.append("\nAssistant: ")
        return "\n".join(parts)

    @staticmethod
    def build_full_outline_prompt_v2(
        theme: str,
        characters_summary: str,
        main_storyline: str,
        volume_count: int,
        chapters_per_volume: int,
    ) -> str:
        """全书大纲生成（增强版）- 基于主线和角色信息，简化JSON格式"""
        parts = [
            "User: 你是一位资深小说总编。请基于以下故事主线和角色信息，生成完整的全书大纲。",
            f"\n## 基本信息",
            f"- 题材类型: {theme}",
            f"- 总卷数: {volume_count} 卷",
            f"- 每卷章节数: {chapters_per_volume} 章",
            "\n## 故事主线",
            main_storyline,
            "\n## 角色体系",
            characters_summary,
        ]

        parts.append("\n## 输出格式（JSON）")
        parts.append("```json")
        parts.append(json.dumps({
            "title": "书名",
            "genre": theme,
            "volumes": [
                {
                    "volume_id": 1,
                    "volume_title": "卷标题",
                    "theme": "本卷主题概述",
                    "chapter_count": chapters_per_volume,
                    "events": "本卷关键事件，用分号分隔",
                    "character_arcs": "本卷角色发展概述",
                }
            ],
            "main_conflict": "核心冲突描述",
            "ending_direction": "结局方向概述",
            "foreshadowing_plan": "跨卷伏笔规划概述",
        }, ensure_ascii=False, indent=2))
        parts.append("```")
        parts.append("\n请确保：1. 每卷事件分布合理 2. 伏笔埋设与回收跨卷对应 3. 角色弧线贯穿全书")
        parts.append("\nAssistant: ")
        return "\n".join(parts)
