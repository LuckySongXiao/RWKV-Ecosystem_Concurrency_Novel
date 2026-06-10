"""并发设定补全器 - 利用 /big_batch/completions 超级并发自动填充设定

可并发补全的内容项:
1. 主要人物 - 基于题材+世界观，AI生成角色小传
2. 故事主线 - 基于题材+世界观+人物，AI生成核心冲突和主线走向
3. 写作风格 - 基于题材，AI生成适配的文笔风格约束

这三项互不依赖（人物和主线共享世界观上下文但各自独立生成），
可以一次性打包为 3 个 prompt 发送到 /big_batch/completions 并发执行。

大纲生成依赖前三项结果，因此串行在后（由 OutlineWorkflow 处理）。
"""

import json
import re
import time
from typing import Dict, List, Optional, Tuple

from .config import PipelineConfig, SamplingParams
from .rwkv_client import RWKVClient
from .genre_expander import GenreExpander, ContextBudget
from .json_utils import robust_json_parse
from .logger import Logger


# ============================================================
# 并发补全 Prompt 构造
# ============================================================

# 每条 prompt 的统一前置：强调"必须基于现有设定"，并列出已存在的字段清单
_BASE_CONSTRAINTS = (
    "\n【重要约束 - 请严格遵守】\n"
    "1. 你必须**严格基于**下方【已有设定】中的内容进行补全/生成。\n"
    "2. 已有设定中的题材、世界观、体系、势力、冲突等要素都是**定稿**，不得擅自更改或矛盾。\n"
    "3. 补全内容必须与已有设定**自洽连贯**（同一片大陆、同一套境界、同一阵营关系等）。\n"
    "4. 如果已有设定为空，请基于题材类型合理生成本次内容，不要凭空引入与后续章节冲突的设定。\n"
)


def _build_character_prompt(spec_context: str, genre: str) -> str:
    return (
        "User: 基于以下世界观设定，为这部长篇小说设计 5-8 个主要角色。\n"
        "每个角色需包含：name（姓名）、identity（身份/门派/职务）、personality（性格特点，3-5个）、"
        "background（百字背景）、initial_power（初始实力/修为）、role_type（主角/重要配角/反派/导师/路人）。\n"
        "角色之间应有明显差异：性别、年龄、阵营、立场、关系网，避免雷同。\n"
        f"题材类型: {genre}\n"
        f"【已有设定 - 角色必须严格遵循以下世界观与体系】:\n{spec_context}\n"
        "输出JSON格式: {\"characters\": [{\"name\":\"\", \"identity\":\"\", "
        "\"personality\":[], \"background\":\"\", \"initial_power\":\"\", \"role_type\":\"\"}]}\n"
        + _BASE_CONSTRAINTS
        + "\nAssistant: "
    )


def _build_storyline_prompt(spec_context: str, genre: str) -> str:
    return (
        "User: 基于以下世界观与角色设定，为这部长篇小说设计故事主线。\n"
        "需包含：main_conflict（核心冲突，50字内）、story_arc（起承转合各阶段概要）、"
        "key_turning_points（3-5个关键转折点）、ending_direction（结局方向）、"
        "themes（2-3个核心主题词）、foreshadowings（2-3个伏笔）。\n"
        "故事主线应**显式呼应**已有人物（特别是主角与反派），并使用已有的体系/势力/冲突作为舞台。\n"
        f"题材类型: {genre}\n"
        f"【已有设定 - 主线必须与这些人物、世界、势力、冲突保持一致】:\n{spec_context}\n"
        "输出JSON格式: {\"main_conflict\":\"\", \"story_arc\":{\"rise\":\"\", \"bear\":\"\", "
        "\"turn\":\"\", \"conclude\":\"\"}, \"key_turning_points\":[], "
        "\"ending_direction\":\"\", \"themes\":[], \"foreshadowings\":[]}\n"
        + _BASE_CONSTRAINTS
        + "\nAssistant: "
    )


def _build_style_prompt(genre: str, spec_context: str) -> str:
    return (
        "User: 基于以下题材类型和世界观/人物，生成适配的写作风格约束。\n"
        "需包含：narrative_pov（叙事视角）、tone（文风基调）、"
        "prose_style（文笔特点，3-5条）、dialogue_style（对话风格）、"
        "taboos（禁忌，3-5条）、chapter_structure（章节结构建议）、"
        "rhythm（节奏，slow/burn/climax-driven）、sensory_focus（感官侧重，3条）。\n"
        "文风必须服务于题材和世界观（仙侠≠都市≠科幻）。\n"
        f"题材类型: {genre}\n"
        f"【已有设定 - 风格需要匹配这些世界观与人物】:\n{spec_context}\n"
        "输出JSON格式: {\"narrative_pov\":\"\", \"tone\":\"\", "
        "\"prose_style\":[], \"dialogue_style\":\"\", "
        "\"taboos\":[], \"chapter_structure\":\"\", "
        "\"rhythm\":\"\", \"sensory_focus\":[]}\n"
        + _BASE_CONSTRAINTS
        + "\nAssistant: "
    )


# ============================================================
# 结果解析与格式化
# ============================================================

def _parse_characters(result: str) -> Tuple[Optional[List[Dict]], str]:
    parsed, status = robust_json_parse(result, first_only=True)
    if parsed and isinstance(parsed, dict) and "characters" in parsed:
        return parsed["characters"], status
    if parsed and isinstance(parsed, list):
        return parsed, status
    return None, status


def _parse_storyline(result: str) -> Tuple[Optional[Dict], str]:
    parsed, status = robust_json_parse(result, first_only=True)
    if parsed and isinstance(parsed, dict):
        return parsed, status
    return None, status


def _parse_style(result: str) -> Tuple[Optional[Dict], str]:
    """解析写作风格生成结果"""
    parsed, status = robust_json_parse(result)
    if parsed and isinstance(parsed, dict):
        return parsed, status
    return None, status


def _format_characters_md(characters: List[Dict]) -> str:
    """将人物列表格式化为 Markdown"""
    lines = []
    for c in characters:
        name = c.get("name", "未知")
        identity = c.get("identity", "")
        personality = c.get("personality", [])
        if isinstance(personality, list):
            personality = "、".join(personality)
        background = c.get("background", "")
        power = c.get("initial_power", "")
        role = c.get("role_type", "")
        lines.append(f"**{name}** ({role})")
        lines.append(f"  身份: {identity}")
        lines.append(f"  性格: {personality}")
        if power:
            lines.append(f"  实力: {power}")
        if background:
            lines.append(f"  背景: {background}")
        lines.append("")
    return "\n".join(lines)


def _format_storyline_md(storyline: Dict) -> str:
    """将故事主线格式化为 Markdown"""
    lines = []
    if "main_conflict" in storyline:
        lines.append(f"**核心冲突**: {storyline['main_conflict']}")
        lines.append("")

    arc = storyline.get("story_arc", {})
    if arc:
        lines.append("**故事弧线**:")
        for key, label in [("rise", "起"), ("bear", "承"), ("turn", "转"), ("conclude", "合")]:
            if key in arc:
                lines.append(f"  {label}: {arc[key]}")
        lines.append("")

    points = storyline.get("key_turning_points", [])
    if points:
        lines.append("**关键转折**:")
        for i, p in enumerate(points, 1):
            lines.append(f"  {i}. {p}")
        lines.append("")

    if "ending_direction" in storyline:
        lines.append(f"**结局方向**: {storyline['ending_direction']}")

    themes = storyline.get("themes", [])
    if themes:
        lines.append(f"**核心主题**: {'、'.join(themes)}")

    return "\n".join(lines)


def _format_style_md(style: Dict) -> str:
    """将写作风格格式化为 Markdown"""
    lines = ["# 写作风格约束", ""]

    if "narrative_pov" in style:
        lines.append(f"## 叙事视角\n{style['narrative_pov']}\n")

    if "tone" in style:
        lines.append(f"## 文风基调\n{style['tone']}\n")

    prose = style.get("prose_style", [])
    if prose:
        lines.append("## 文笔特点")
        for p in prose:
            lines.append(f"- {p}")
        lines.append("")

    if "dialogue_style" in style:
        lines.append(f"## 对话风格\n{style['dialogue_style']}\n")

    taboos = style.get("taboos", [])
    if taboos:
        lines.append("## 禁忌")
        for t in taboos:
            lines.append(f"- {t}")
        lines.append("")

    if "chapter_structure" in style:
        lines.append(f"## 章节结构\n{style['chapter_structure']}\n")

    return "\n".join(lines)


# ============================================================
# 并发设定补全器
# ============================================================

class ConcurrentSpecFiller:
    """并发设定补全器

    利用 /big_batch/completions 一次性并发生成:
    - 主要人物
    - 故事主线
    - 写作风格

    用法:
        filler = ConcurrentSpecFiller(client, config, logger)
        result = filler.fill(spec, genre)
        # result 包含 characters, storyline, style 及其 Markdown 格式
    """

    def __init__(
        self,
        client: RWKVClient,
        config: PipelineConfig,
        logger: Logger = None,
    ):
        self._client = client
        self._config = config
        self._logger = logger or Logger.get()
        self._budget = ContextBudget()

    def fill(
        self,
        spec: str,
        genre: str,
        fill_characters: bool = True,
        fill_storyline: bool = True,
        fill_style: bool = True,
    ) -> Dict:
        """并发补全设定

        Args:
            spec: 已扩展的世界观设定（GenreExpander 输出）
            genre: 题材类型
            fill_characters: 是否补全人物
            fill_storyline: 是否补全故事主线
            fill_style: 是否补全写作风格

        Returns:
            {
                "characters": [...],       # 人物列表
                "characters_md": "...",    # 人物 Markdown
                "storyline": {...},        # 故事主线
                "storyline_md": "...",     # 故事主线 Markdown
                "style": {...},            # 写作风格
                "style_md": "...",         # 写作风格 Markdown
                "elapsed_ms": float,       # 总耗时
                "filled_items": [...],     # 实际补全的项
            }
        """
        self._logger.info(f"ConcurrentSpecFiller: Starting concurrent fill for genre={genre}")

        # 构造并发 prompts
        prompts = []
        task_names = []

        # 压缩 spec 到预算内
        spec_budget = self._budget.get_budget("outline_gen", "spec")
        spec_context = self._budget.truncate_to_budget(spec, spec_budget)

        if fill_characters:
            prompts.append(_build_character_prompt(spec_context, genre))
            task_names.append("characters")
        if fill_storyline:
            prompts.append(_build_storyline_prompt(spec_context, genre))
            task_names.append("storyline")
        if fill_style:
            prompts.append(_build_style_prompt(genre, spec_context))
            task_names.append("style")

        if not prompts:
            return {"elapsed_ms": 0, "filled_items": []}

        # 并发调用 /big_batch/completions
        sampling = SamplingParams(
            temperature=1.0,
            top_p=0.1,
            max_tokens=2048,
        )

        start = time.time()
        results = self._client.big_batch_completions(
            contents=prompts,
            sampling=sampling,
            stream=False,
        )
        elapsed = (time.time() - start) * 1000

        # 确保结果是列表
        if isinstance(results, str):
            results = [results]
        elif not isinstance(results, list):
            results = [str(results)]

        # 解析结果
        output = {
            "characters": None,
            "characters_md": "",
            "storyline": None,
            "storyline_md": "",
            "style": None,
            "style_md": "",
            "elapsed_ms": elapsed,
            "filled_items": [],
        }

        for i, task_name in enumerate(task_names):
            result_text = results[i] if i < len(results) else ""

            if task_name == "characters":
                chars, status = _parse_characters(result_text)
                if chars:
                    output["characters"] = chars
                    output["characters_md"] = _format_characters_md(chars)
                    output["filled_items"].append("characters")
                    self._logger.info(f"ConcurrentSpecFiller: Characters filled ({len(chars)} characters, {status})")
                else:
                    self._logger.warning(f"ConcurrentSpecFiller: Characters parse failed ({status})")

            elif task_name == "storyline":
                story, status = _parse_storyline(result_text)
                if story:
                    output["storyline"] = story
                    output["storyline_md"] = _format_storyline_md(story)
                    output["filled_items"].append("storyline")
                    self._logger.info(f"ConcurrentSpecFiller: Storyline filled ({status})")
                else:
                    self._logger.warning(f"ConcurrentSpecFiller: Storyline parse failed ({status})")

            elif task_name == "style":
                sty, status = _parse_style(result_text)
                if sty:
                    output["style"] = sty
                    output["style_md"] = _format_style_md(sty)
                    output["filled_items"].append("style")
                    self._logger.info(f"ConcurrentSpecFiller: Style filled ({status})")
                else:
                    self._logger.warning(f"ConcurrentSpecFiller: Style parse failed ({status})")

        self._logger.info(
            f"ConcurrentSpecFiller: Completed in {elapsed:.0f}ms, "
            f"filled: {output['filled_items']}"
        )

        return output

    def merge_to_spec(self, spec: str, fill_result: Dict) -> str:
        """将补全结果合并回 specification.md

        将人物、故事主线注入到设定文档的对应节中
        """
        sections = self._extract_sections(spec)

        # 合并人物
        if fill_result.get("characters_md"):
            sections["主要人物"] = fill_result["characters_md"]

        # 合并故事主线
        if fill_result.get("storyline_md"):
            sections["故事主线"] = fill_result["storyline_md"]

        # 重组文档
        title_match = re.search(r"^#\s+(.+)$", spec, re.MULTILINE)
        doc_title = title_match.group(1) if title_match else "世界观设定"

        section_order = [
            "题材", "世界观", "修行体系", "主要人物",
            "势力格局", "经济体系", "核心冲突类型", "力量上限",
            "故事主线",
        ]

        parts = [f"# {doc_title}\n"]
        used = set()

        for name in section_order:
            if name in sections:
                parts.append(f"## {name}\n{sections[name]}\n")
                used.add(name)

        for name, content in sections.items():
            if name not in used and content.strip():
                parts.append(f"## {name}\n{content}\n")

        return "\n".join(parts)

    def _extract_sections(self, spec: str) -> Dict[str, str]:
        """从 specification.md 中提取各节内容"""
        sections: Dict[str, str] = {}
        current_section = ""
        current_content: List[str] = []

        for line in spec.split("\n"):
            match = re.match(r"^#{2,3}\s+(.+)$", line)
            if match:
                if current_section:
                    sections[current_section] = "\n".join(current_content).strip()
                current_section = match.group(1).strip()
                current_content = []
            elif not re.match(r"^#\s+", line):
                current_content.append(line)

        if current_section:
            sections[current_section] = "\n".join(current_content).strip()

        return sections
