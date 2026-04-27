"""结构化设定模型 - 将 specification.md 拆分为逐条可编辑的字段

每条设定有:
- key: 唯一标识
- label: 显示名称
- value: 当前值
- placeholder: 占位提示
- auto_fillable: 是否可AI自动补全
- auto_fill_prompt: AI补全用的prompt构造函数
- multiline: 是否多行输入
"""

import re
from typing import Dict, List, Optional, Callable, Tuple
from dataclasses import dataclass, field


@dataclass
class SpecField:
    """单条设定字段"""
    key: str
    label: str
    value: str = ""
    placeholder: str = ""
    auto_fillable: bool = False
    multiline: bool = True
    order: int = 0


# ============================================================
# 设定字段定义（固定顺序）
# ============================================================

SPEC_FIELD_DEFS = [
    SpecField(key="genre", label="题材", placeholder="如：仙侠、玄幻、科幻、都市...",
              auto_fillable=False, multiline=False, order=1),
    SpecField(key="world_law", label="世界观", placeholder="天地法则、世界结构、运行规律...",
              auto_fillable=True, multiline=True, order=2),
    SpecField(key="cultivation_system", label="修行/能力体系",
              placeholder="境界划分、突破条件、功法体系...",
              auto_fillable=True, multiline=True, order=3),
    SpecField(key="characters", label="主要人物",
              placeholder="角色小传：姓名、身份、性格、初始实力...",
              auto_fillable=True, multiline=True, order=4),
    SpecField(key="faction_pattern", label="势力格局",
              placeholder="主要势力、阵营分布、势力关系...",
              auto_fillable=True, multiline=True, order=5),
    SpecField(key="economy_system", label="经济体系",
              placeholder="货币、交易核心、资源分配...",
              auto_fillable=True, multiline=True, order=6),
    SpecField(key="conflict_types", label="核心冲突类型",
              placeholder="主要矛盾类型、冲突来源...",
              auto_fillable=True, multiline=True, order=7),
    SpecField(key="power_ceiling", label="力量上限",
              placeholder="实力天花板、超凡限制...",
              auto_fillable=True, multiline=False, order=8),
    SpecField(key="storyline", label="故事主线",
              placeholder="核心冲突、起承转合、结局方向...",
              auto_fillable=True, multiline=True, order=9),
    # 写作风格（独立字段，从 style-guide.md 读取）
    SpecField(key="style", label="写作风格",
              placeholder="叙事视角、文风基调、文笔特点、禁忌...",
              auto_fillable=True, multiline=True, order=10),
    # 大纲（管线生成后的结果，可查看/编辑/重新生成）
    SpecField(key="outline", label="全书大纲",
              placeholder="管线运行后自动生成，也可AI补全...",
              auto_fillable=True, multiline=True, order=11),
]

# key → SpecField 映射
FIELD_MAP = {f.key: f for f in SPEC_FIELD_DEFS}

# specification.md 节标题 → key 映射
SECTION_TO_KEY = {
    "题材": "genre",
    "世界观": "world_law",
    "修行体系": "cultivation_system",
    "能力体系": "cultivation_system",
    "魔法体系": "cultivation_system",
    "武学境界": "cultivation_system",
    "主要人物": "characters",
    "势力格局": "faction_pattern",
    "势力": "faction_pattern",
    "经济体系": "economy_system",
    "经济": "economy_system",
    "核心冲突类型": "conflict_types",
    "冲突": "conflict_types",
    "力量上限": "power_ceiling",
    "实力上限": "power_ceiling",
    "故事主线": "storyline",
    "写作风格": "style",
    "写作风格约束": "style",
    "全书大纲": "outline",
    "大纲": "outline",
}

# key → specification.md 节标题
KEY_TO_SECTION = {
    "genre": "题材",
    "world_law": "世界观",
    "cultivation_system": "修行体系",
    "characters": "主要人物",
    "faction_pattern": "势力格局",
    "economy_system": "经济体系",
    "conflict_types": "核心冲突类型",
    "power_ceiling": "力量上限",
    "storyline": "故事主线",
    "style": "写作风格",
    "outline": "全书大纲",
}


# ============================================================
# 占位符检测
# ============================================================

PLACEHOLDER_PATTERNS = [
    r"（请在此处填写[^）]*）",
    r"（[^）]*填写[^）]*）",
    r"\[请填写[^\]]*\]",
    r"待填写", r"TODO", r"TBD",
]


def is_placeholder(text: str) -> bool:
    """检测文本是否为占位符或空"""
    stripped = text.strip()
    if not stripped:
        return True
    for pattern in PLACEHOLDER_PATTERNS:
        if re.search(pattern, stripped):
            return True
    return False


# ============================================================
# 解析与序列化
# ============================================================

def parse_spec_to_fields(spec_text: str, style_text: str = "", outline_text: str = "") -> List[SpecField]:
    """将 specification.md 文本解析为字段列表

    Args:
        spec_text: specification.md 内容
        style_text: style-guide.md 内容（可选）
        outline_text: outline.json 的格式化文本（可选）

    Returns:
        按顺序排列的 SpecField 列表，每个字段都有当前值
    """
    # 提取各节
    sections: Dict[str, str] = {}
    current_section = ""
    current_content: List[str] = []

    for line in spec_text.split("\n"):
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

    # 映射到字段
    fields = []
    for fdef in SPEC_FIELD_DEFS:
        f = SpecField(
            key=fdef.key,
            label=fdef.label,
            placeholder=fdef.placeholder,
            auto_fillable=fdef.auto_fillable,
            multiline=fdef.multiline,
            order=fdef.order,
        )

        # 尝试从 sections 中找值
        for section_title, key in SECTION_TO_KEY.items():
            if key == f.key and section_title in sections:
                f.value = sections[section_title]
                break

        # style 和 outline 从独立文件注入
        if f.key == "style" and style_text:
            f.value = style_text
        elif f.key == "outline" and outline_text:
            f.value = outline_text

        fields.append(f)

    return fields


def fields_to_spec(fields: List[SpecField]) -> Tuple[str, str, str]:
    """将字段列表序列化为文本

    Returns:
        (spec_text, style_text, outline_text)
        - spec_text: specification.md 内容
        - style_text: style-guide.md 内容
        - outline_text: 大纲文本
    """
    spec_parts = ["# 世界观设定\n"]
    style_text = ""
    outline_text = ""

    for f in sorted(fields, key=lambda x: x.order):
        section_title = KEY_TO_SECTION.get(f.key, f.label)
        value = f.value if f.value else f.placeholder

        if f.key == "style":
            style_text = value
        elif f.key == "outline":
            outline_text = value
        else:
            spec_parts.append(f"## {section_title}")
            spec_parts.append(value)
            spec_parts.append("")

    return "\n".join(spec_parts), style_text, outline_text

    return "\n".join(parts)


def fields_to_dict(fields: List[SpecField]) -> List[Dict]:
    """将字段列表转为 JSON 可序列化的字典列表"""
    return [
        {
            "key": f.key,
            "label": f.label,
            "value": f.value,
            "placeholder": f.placeholder,
            "auto_fillable": f.auto_fillable,
            "multiline": f.multiline,
            "order": f.order,
            "is_empty": is_placeholder(f.value),
        }
        for f in fields
    ]


def update_field(fields: List[SpecField], key: str, value: str) -> List[SpecField]:
    """更新指定字段的值"""
    for f in fields:
        if f.key == key:
            f.value = value
            break
    return fields
