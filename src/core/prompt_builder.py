"""Prompt 构造器 - 按任务类型构造格式化 Prompt

RWKV 对提示词结构高度敏感，严格区分:
- Instruction/Response 格式: 结构化提取任务（大纲生成、状态抽取等）
- User/Assistant 格式: 创作型续写任务

8K 上下文约束:
- 所有 Prompt 构建方法均接受 context_budget 参数
- 超出预算的组件会被自动截断
"""

import json
import re
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
            f"User: 你正在扮演角色「{character_id}」。你必须完全沉浸在这个角色中，以第一人称思考和回应。",
            "\n## 角色信息",
            character_state,
            "\n## 扮演规则",
            "1. 严格保持角色性格、语气和行为方式的一致性",
            "2. 使用角色特有的说话方式和口头禅",
            "3. 回应内容必须符合角色的身份、背景和动机",
            "4. 根据角色的能力范围做出反应，不要做出超出角色能力的事情",
            "5. 情感反应要符合角色的性格特征",
            "6. 直接输出角色的回应，不要输出任何旁白、解说或元信息",
            "7. 不要使用引号包裹对话内容",
        ]

        if scene_context:
            parts.append("\n## 当前场景")
            parts.append(scene_context)

        if dialogue_history:
            parts.append("\n## 对话历史")
            parts.append(dialogue_history)

        parts.append(f"\n## 用户输入\n{user_input}")
        parts.append(f"\n{character_id}: ")
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
        earlier_slices_summary: str = "",
        style_guide: str = "",
        original_storyline: str = "",
        allowed_names: List[str] = None,
    ) -> str:
        """章节切片写作 Prompt - 纯净续写格式

        核心改进：
        - 将章节概要拆分为具体情节要点，每个切片有明确的写作任务
        - 续写格式：给出前文末尾，模型直接续写
        - 严格要求只输出小说正文，不输出任何指令/标签/大纲
        - 通过情节要点差异化确保各切片内容不雷同
        - 强化角色唯一性约束，禁止凭空创造新角色
        - 第一切片必须从原故事线的开篇场景开始
        """
        synopsis = chapter_info.get('synopsis', '')
        plot_points = PromptBuilder._split_synopsis_into_plot_points(synopsis, total_slices)

        # 提取允许的角色名（用于硬约束）
        existing_names = []
        if characters:
            for ch in characters:
                if isinstance(ch, dict):
                    n = ch.get("name", "").strip()
                    if n:
                        existing_names.append(n)

        # 如果没有传 allowed_names，从 characters 中提取
        if not allowed_names:
            allowed_names = existing_names

        # 在最前部强插「允许的角色名」白名单（让模型最先看到）
        parts = [
            "User: 你是一位小说作家。请续写以下章节的指定部分。",
        ]

        # 🔴🔴🔴 最前部强插角色白名单 + 严令
        if allowed_names:
            parts.append(
                "\n## 🔴 角色白名单（绝对唯一可用的角色名）\n"
                f"**本作只能使用以下角色名：{', '.join(allowed_names)}**\n"
                f"**禁止凭空创造任何不在此名单上的角色，禁止使用\"林逸风\"\"苏音音\"\"宋一鸣\"\"秦师傅\"\"老陈\"\"奶奶\"\"王婆\"等任何新名字！**\n"
                f"**违反此条 = 整个内容作废！**\n"
            )

        parts.extend([
            f"\n## 章节概况",
            f"第{chapter_info.get('chapter_id', '?')}章「{chapter_info.get('chapter_title', '未命名')}」",
            f"章节概要: {synopsis}",
        ])

        involved = chapter_info.get("involved_characters", [])
        if involved:
            parts.append(f"涉及角色: {', '.join(involved)}")

        parts.append(f"\n## 本切片写作任务（第{slice_idx + 1}/{total_slices}部分）")
        parts.append(f"阶段: {slice_description}")

        if slice_idx < len(plot_points):
            parts.append(f"具体内容要求: {plot_points[slice_idx]}")
        else:
            parts.append(f"具体内容要求: 承接前文，推进情节发展")

        # 显式禁止重复
        if slice_idx == 0:
            parts.append(
                "\n**本切片写作要求**：开篇必须严格按【原始故事线】给定的开篇场景开始，"
                "建立人物关系/处境/冲突起点，**不要重写任何前文**（因为本切片就是第一章第一节）。"
            )
        elif slice_idx == total_slices - 1:
            parts.append(
                "\n**本切片写作要求**：作为本章结尾，呼应开篇，**绝对不要重写已写摘要中的任何场景、对话、动作**，"
                "必须推进到新的状态（危机升级/转折/收束）。"
            )
        else:
            parts.append(
                "\n**本切片写作要求**：严格紧接前文末尾，**绝对不要重复已写摘要中的场景、对话、心理、动作**。"
                "本切片只负责推进新的情节和状态，不允许复述前文任何部分。"
            )

        if characters:
            relevant_names = set(involved) if involved else set()
            shown = list(characters[:8])
            if relevant_names:
                shown = [ch for ch in characters if ch.get("name") in relevant_names] + \
                        [ch for ch in characters if ch.get("name") not in relevant_names]
                shown = shown[:8]

            parts.append("\n## 角色参考（人称必须严格对应下列性别）")
            for ch in shown:
                if isinstance(ch, dict):
                    name = ch.get("name", "未知")
                    role = ch.get("role_type", "")
                    identity = ch.get("identity", "")
                    personality = ch.get("personality", "")
                    # 推断人称
                    pronoun = PromptBuilder._infer_pronoun(name, identity, personality)
                    parts.append(f"- {name}({role}, {pronoun}): {identity}" + (f"，{personality}" if personality else ""))
                else:
                    parts.append(f"- {ch}")

        # 在第一个切片中强插原始故事线作为开篇依据
        if original_storyline:
            if slice_idx == 0:
                parts.append("\n## 📖 原始故事线（开篇必须以该场景开始）")
                parts.append(original_storyline)
            elif slice_idx == total_slices - 1:
                parts.append("\n## 📖 原始故事线（结尾必须与之呼应）")
                parts.append(original_storyline)

        if main_storyline:
            parts.append("\n## 故事主线")
            core_conflict = main_storyline.get("core_conflict", "")
            if core_conflict:
                parts.append(f"核心冲突: {core_conflict}")
            stages = main_storyline.get("stages", [])
            if stages:
                vol_id = chapter_info.get("volume_id", 1)
                for stage in stages:
                    if stage.get("volume_id") == vol_id:
                        parts.append(f"本卷阶段: {stage.get('stage_name', '')} - {stage.get('description', '')}")
                        break

        if earlier_slices_summary:
            parts.append("\n## 已写内容摘要（严禁重复）")
            parts.append(earlier_slices_summary)

        if previous_slice_content:
            parts.append("\n## 前文（请自然续写）")
            parts.append(previous_slice_content[-800:])

        # ⚠️ 硬性约束
        parts.append("\n## ⚠️ 硬性写作约束（违反即为失败）")
        if characters:
            existing_names = [ch.get("name", "") for ch in characters if isinstance(ch, dict) and ch.get("name")]
            if existing_names:
                parts.append(f"1. 【禁止新增角色】所有出场人物必须从【角色白名单】中点名：{', '.join(existing_names)}。绝不允许凭空创造\"林逸风\"、\"苏音音\"、\"宋一鸣\"、\"秦师傅\"、\"老陈\"、\"奶奶\"、\"王婆\"等不存在的角色。如果需要对话配角，必须复用白名单中的角色。")
        parts.append("2. 【人称必须正确】严格按角色性别使用\"他/她/它\"，女性角色永远用\"她\"，绝不允许写成\"他\"。")
        parts.append("3. 【首段不省略】禁止出现\"（此处省略X字）\"、\"......\"等占位符，必须写出完整句子。")
        parts.append("4. 【禁止元文本】禁止出现\"（第X/Y部分）\"、\"（前文接续）\"、\"（本切片写作任务完成）\"等元说明，这些必须只输出在控制台，不能出现在正文中。")
        parts.append("5. 【严禁循环重复】禁止反复重写相同或相似段落、相同对话、相同打斗动作。本切片只写本切片的内容，绝对不要重写【已写内容摘要】或【前文】中的任何句子。")
        if slice_idx == 0 and original_storyline:
            parts.append("6. 【开篇严格还原】本切片是本章第一部分，开场必须严格按【原始故事线】所给的开篇场景开始（如\"宋霄和钱开凤因教育孩子吵架→陨石撞向卧室→两人穿越\"），不允许直接跳到后续场景。")
        else:
            parts.append("6. 【续写自然】必须紧接前文末尾续写，不要重述前文、不要改变前文已确定的人物身份/处境。")

        # ⚠️ 题材硬约束 - 禁止任何非仙侠元素
        parts.append("\n## ⚠️ 题材与世界观硬约束（仙侠专属）")
        parts.append("7. 【题材严格锁定：仙侠/修仙/穿越/古代】本作品属于中国古代仙侠世界，")
        parts.append("   严禁出现以下元素，一旦出现即视为失败：")
        parts.append("   - 严禁现代科技物品：测量仪器、显示屏、电路、传感器、急救包、止血贴、防护服、防毒面具、面具、金属靴、防弹衣、手电筒、对讲机、电脑、监控器、监控摄像头、监控屏幕")
        parts.append("   - 严禁科幻机械装置：齿轮、轴承、传动结构、传送阵结构、能量传递效率、能量读数爆表、过载保护装置、安全模式、倒计时装置、信号、数据流、二进制、代码、程序、芯片、电容、电阻、电压、电流、频率、共振频率、转速、rpm、应力节点、间隙、毫米、百分之X等工程/物理参数")
        parts.append("   - 严禁现代职业身份：工程师、科学家、程序员、设计师、医生（西医手术）")
        parts.append("   - 严禁现代场景：办公室、自动售货机、电梯、高楼、阳台、卧室落地窗、空调、电梯井")
        parts.append("   - 所有\"机关\"必须以仙侠形式表达（符文、阵法、灵纹、灵力、禁制、法器、灵器、灵石、符箓、灵兽等），绝不允许用\"机械系统\"\"传动结构\"等工程术语")
        parts.append("   - 所有\"破解机关\"的描写应使用灵力、阵法、法术、神识等仙侠手段，不得使用\"共振频率\"\"应力节点\"等物理学术语")
        parts.append("   - 钱开凤虽是现代人穿越，但她的现代知识只能以\"直觉\"\"猜测\"\"模糊记忆\"形式表达，且最终要回归仙侠解释")
        parts.append("8. 【修为能力严格按角色设定】")
        parts.append("   - 钱开凤：明确无修为。绝不允许她使用任何灵力、神识、破阵、施法、画符等修真能力")
        parts.append("   - 宋霄：炼气初期。只具备最基础的灵觉和微弱灵力，行为符合凡人偏多")
        parts.append("   - 墨羽：金丹初期。青云宗外门长老，应展现一定修士风范")
        parts.append("   - 冷无涯：金丹中期。反派boss，实力强大但不可碾压全场")
        parts.append("9. 【场景描写需符合古代背景】")
        parts.append("   - 服饰：古装汉服、布衣、锦衣、儒衫、道袍、法袍等")
        parts.append("   - 建筑：木楼、青砖瓦房、宫殿、寺庙、山门、洞府等")
        parts.append("   - 器物：油灯、蜡烛、铜镜、木剑、桃木剑、玉佩、灵器、飞剑、符箓、玉简、铜铃等")
        parts.append("   - 称呼：公子、小姐、夫人、夫人、陛下、前辈、道友、师兄、师姐、师父、师叔等")
        parts.append("   - 货币：白银、铜钱、金子、灵石等")
        parts.append("   - 不可出现：手机、照片、银行卡、身份证、信用卡、合同、律师、医生（西医）、警察局等现代事物")

        parts.append("\n## 写作规则")
        parts.append("1. 【绝对禁止思考过程】不要输出任何分析、构思、推理、回顾、决定等思考性文字。直接开始小说正文写作。")
        parts.append("2. 禁止以\"嗯\"、\"啊\"、\"等等\"、\"再仔细想想\"、\"回顾提示\"、\"先确认\"、\"需要注意\"、\"可以这样构思\"、\"决定采用\"、\"合理推断\"、\"用户可能\"、\"根据约束\"等分析性词汇开头。")
        parts.append("3. 禁止出现\"Assistant:\"、\"让我\"、\"好的\"等AI对话痕迹")
        parts.append("4. 只输出小说正文，不要输出任何标题、标签、大纲、说明、注释、思考过程")
        parts.append("5. 直接续写故事，不要重述前文内容")
        parts.append("6. 严格按照「具体内容要求」写作，不要偏题")
        parts.append("7. 600-1000字，注重动作、对话、心理、环境的细节描写")
        if slice_idx > 0:
            parts.append("8. 必须紧接前文末尾续写，保持叙事连贯")
        if slice_idx > 1:
            parts.append("9. 严禁重复已写摘要中的任何情节和描写，本切片必须推进新的状态")
        if slice_idx < total_slices - 1:
            parts.append("10. 在结尾处留下过渡点，不要写完结")

        if previous_slice_content:
            last_line = previous_slice_content.strip().split('\n')[-1] if previous_slice_content.strip() else ""
            if last_line:
                parts.append(f"\nAssistant: {last_line[-50:]}")
            else:
                parts.append("\nAssistant:")
        else:
            parts.append("\nAssistant:")

        return "\n".join(parts)

    @staticmethod
    def _infer_pronoun(name: str, identity: str, personality: str) -> str:
        """根据角色身份/性格文本推断人称（他/她/它）"""
        text = f"{identity or ''} {personality or ''}".lower()
        # 明确的女性关键词
        female_kw = ["女", "姑娘", "少女", "妻子", "母亲", "婆婆", "千金", "师姐", "师妹", "娘子", "妾", "她", "姐", "妹", "妇"]
        male_kw = ["男", "公子", "少侠", "丈夫", "父亲", "父亲", "儿子", "郎", "君", "兄长", "师兄", "师弟", "老", "哥", "弟", "爷"]
        # 优先看名字
        if "凤" in name or "燕" in name or "莲" in name or "菊" in name or "月" in name or "娘" in name:
            return "女"
        if "霄" in name or "云" in name or "龙" in name or "虎" in name or "山" in name or "江" in name:
            if not any(kw in text for kw in female_kw):
                return "男"
        for kw in female_kw:
            if kw in text:
                return "女"
        for kw in male_kw:
            if kw in text:
                return "男"
        return "未知性别"

    @staticmethod
    def _split_synopsis_into_plot_points(synopsis: str, total_slices: int) -> List[str]:
        """将章节概要拆分为具体情节要点，每个切片对应一个

        策略：
        1. 按句号/分号/逗号拆分概要
        2. 均匀分配到各切片，每个切片有独立的「明确动作/对话/状态变化」
        3. 为每个切片补充阶段性的写作方向指导
        4. 注入「禁止重复前切片」指令
        """
        if not synopsis or not synopsis.strip():
            return []

        sentences = re.split(r'[。；！？\n]', synopsis)
        sentences = [s.strip() for s in sentences if s.strip()]

        if not sentences:
            return []

        stage_directions = [
            "开篇阶段：建立场景、出场人物、当前处境，",
            "发展阶段：推进情节、引入冲突、展现互动，",
            "高潮阶段：冲突爆发、关键转折、情感爆发，",
            "收尾阶段：收束情节、埋设伏笔、留下过渡，",
        ]

        plot_points = []
        if len(sentences) >= total_slices:
            chunk_size = len(sentences) / total_slices
            for i in range(total_slices):
                start = int(i * chunk_size)
                end = int((i + 1) * chunk_size)
                chunk = sentences[start:end]
                direction = stage_directions[min(i, len(stage_directions) - 1)]
                plot_points.append(f"{direction}本切片只写：{'。'.join(chunk)}。**严禁重复前切片已经描写过的任何场景/对话/动作**")
        else:
            for i in range(total_slices):
                direction = stage_directions[min(i, len(stage_directions) - 1)]
                if i < len(sentences):
                    plot_points.append(f"{direction}本切片只写：{sentences[i]}。**严禁重复前切片已经描写过的任何场景/对话/动作**")
                else:
                    plot_points.append(f"{direction}承接前文继续推进新的情节")

        return plot_points

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
                if isinstance(ch, dict):
                    name = ch.get("name", "未知")
                    role = ch.get("role_type", "")
                    identity = ch.get("identity", "")
                    parts.append(f"- {name}({role}): {identity}")
                else:
                    parts.append(f"- {ch}")

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
            "involved_characters": ["角色1", "角色2"],
            "foreshadowing": {"plant": ["伏笔1"], "resolve": ["回收1"]},
        }, ensure_ascii=False, indent=2))
        parts.append("```")
        parts.append("\n注意：直接输出JSON，不要输出任何其他文字。")
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
            parts.append(f"\n## 额外设定（含用户原始故事线，必须严格遵循）\n{extra_context}")

        # 关键约束：禁止新增角色、禁止偏离原始故事线
        parts.append("\n## ⚠️ 硬性约束（必须严格遵守，违反即为失败）")
        parts.append("1. 【禁止新增角色】所有出场人物必须使用【角色体系】和【额外设定】中已存在的名字。")
        parts.append("   若额外设定中提到某具尸体/棺椁里的人物，必须是【额外设定】中已点名的角色本人（例如原故事线说\"钱开凤躺在棺椁里\"，则棺椁中的人物就是钱开凤本人，不允许凭空创造\"苏音音\"等新角色）。")
        parts.append("2. 【禁止篡改原始故事线】额外设定中已写明的关键事件（穿越方式、初始处境、人物身份）必须原样保留，不得替换或添加原作中不存在的情节。")
        parts.append("3. 【性别/人称一致】所有角色的性别必须严格按【角色体系】和【额外设定】中的描述使用\"他/她/它\"，不得互换。")
        parts.append("4. 【开场严格还原】如果额外设定给出了开篇场景（如\"陨石撞击卧室\"），则故事开篇必须以该场景开始，不允许直接跳到后续场景。")

        parts.append("\n## 输出要求")
        parts.append("直接输出JSON对象，不要输出任何其他文字、解释或markdown代码块标记。")
        parts.append("JSON格式如下：")
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
        parts.append("\n请确保：1. 每卷都有明确的阶段目标 2. 角色发展弧线完整 3. 伏笔与回收对应 4. 冲突层层递进")
        parts.append("\nAssistant: {")
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

        parts.append("\n## 输出要求")
        parts.append("直接输出JSON对象，不要输出任何其他文字、解释或markdown代码块标记。")
        parts.append("JSON格式如下：")
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
        parts.append("\n请确保：1. 每卷事件分布合理 2. 伏笔埋设与回收跨卷对应 3. 角色弧线贯穿全书")
        parts.append("\nAssistant: {")
        return "\n".join(parts)

    @staticmethod
    def build_polish_prompt(
        original_text: str,
        polish_instructions: str,
        chapter_title: str = "",
        style_guide: str = "",
    ) -> str:
        """构建逐句润色 Prompt

        Args:
            original_text: 原始文本（初步草稿）
            polish_instructions: 用户的润色条件/指令
            chapter_title: 章节标题
            style_guide: 写作风格指南
        """
        parts = [
            "User: 你是一位精通文学润色的资深编辑。请根据用户的润色要求，对给定的文本进行精细润色。",
        ]

        if chapter_title:
            parts.append(f"\n## 章节: {chapter_title}")

        parts.append("\n## 原始文本")
        parts.append(original_text)

        parts.append("\n## 润色要求")
        parts.append(polish_instructions)

        if style_guide:
            parts.append("\n## 风格约束")
            parts.append(style_guide)

        parts.append("\n## 润色规则")
        parts.append("1. 严格按照用户的润色要求进行修改")
        parts.append("2. 保持原文的叙事结构和情节走向不变")
        parts.append("3. 只修改用户指定需要润色的部分，未提及的部分保持原样")
        parts.append("4. 润色后的文本必须比原文更流畅、更有文学性")
        parts.append("5. 输出完整的润色后文本，不要只输出修改的部分")
        parts.append("6. 使用Markdown格式输出")

        parts.append("\nAssistant: ")
        return "\n".join(parts)
