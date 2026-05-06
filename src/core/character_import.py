"""角色批量导入 - 用户批量导入角色名称和性别，AI按需补全详细设定

数据格式:
- 输入: [{"name": "林孤云", "gender": "男"}, {"name": "苏芸", "gender": "女"}, ...]
- 输出: 每个角色补全 identity, personality, background, initial_power, role_type

AI补全策略:
- 单个角色补全: 用户点击某角色的"补全"按钮
- 批量并发补全: 所有未补全角色一次性发送到 /big_batch/completions
- 模板预填: 根据题材+性别预填默认身份模板
"""

import json
import re
import time
from typing import Dict, List, Optional, Tuple

from .config import PipelineConfig, SamplingParams
from .rwkv_client import RWKVClient
from .json_utils import robust_json_parse
from .logger import Logger


# ============================================================
# 角色数据模型
# ============================================================

# 角色必需字段
REQUIRED_FIELDS = ["name"]
# 角色可AI补全字段
AI_FIELDS = ["identity", "personality", "background", "initial_power", "role_type"]
# 所有字段
ALL_FIELDS = ["name", "gender"] + AI_FIELDS

# 性别括号匹配: 名称(男) 或 名称（女）等
_GENDER_PAREN_RE = re.compile(
    r"^(.+?)\s*[\u3008\u3009\uFF08(]\s*([男女])\s*[\u3009\u300A\uFF09)]\s*$"
)
# 逗号/顿号分隔
_COMMA_SPLIT_RE = re.compile(r"[\uFF0C,]\s*")


def character_to_dict(char: Dict) -> Dict:
    """标准化角色字典，确保所有字段存在"""
    result = {"name": char.get("name", ""), "gender": char.get("gender", "")}
    for f in AI_FIELDS:
        result[f] = char.get(f, "")
    result["is_complete"] = all(char.get(f, "") for f in AI_FIELDS)
    return result


def parse_character_list(text: str) -> List[Dict]:
    """从文本中解析角色列表

    支持格式:
    1. JSON: [{"name": "林孤云", "gender": "男"}, ...]
    2. 每行一个: 林孤云 男
    3. 逗号分隔: 林孤云(男), 苏芸(女)
    """
    text = text.strip()
    if not text:
        return []

    # 尝试 JSON 解析
    parsed, _ = robust_json_parse(text)
    if parsed and isinstance(parsed, list):
        return [c for c in parsed if isinstance(c, dict) and c.get("name")]

    if parsed and isinstance(parsed, dict) and "characters" in parsed:
        chars = parsed["characters"]
        if isinstance(chars, list):
            return [c for c in chars if isinstance(c, dict) and c.get("name")]

    # 尝试每行一个角色
    characters = []

    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # 先尝试按逗号/顿号拆分（处理 "林孤云(男), 苏芸(女)" 格式）
        sub_parts = _COMMA_SPLIT_RE.split(line)
        if len(sub_parts) > 1:
            for part in sub_parts:
                part = part.strip()
                if not part:
                    continue
                m = _GENDER_PAREN_RE.match(part)
                if m:
                    characters.append({"name": m.group(1).strip(), "gender": m.group(2)})
                elif part:
                    characters.append({"name": part, "gender": ""})
            continue

        # 格式: 名称(性别) 或 名称（性别）
        m = _GENDER_PAREN_RE.match(line)
        if m:
            characters.append({"name": m.group(1).strip(), "gender": m.group(2)})
            continue

        # 格式: 名称 性别 (空格分隔)
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip() in ("男", "女"):
            characters.append({"name": parts[0].strip(), "gender": parts[1].strip()})
        elif len(parts) >= 1:
            name = parts[0].strip().rstrip("\uFF0C,")
            if name:
                characters.append({"name": name, "gender": ""})

    return characters


# ============================================================
# 性别-题材默认身份模板
# ============================================================

GENDER_ROLE_TEMPLATES = {
    "仙侠": {
        "男": ["宗门弟子", "散修", "世家公子", "魔道修士", "隐世高人"],
        "女": ["宗门圣女", "世家千金", "散修女侠", "魔道妖女", "仙门师姐"],
    },
    "玄幻": {
        "男": ["帝国王子", "佣兵团长", "远古族后裔", "商会少主", "学院天才"],
        "女": ["帝国公主", "药剂师", "远古族圣女", "佣兵女王", "学院首席"],
    },
    "科幻": {
        "男": ["舰长", "机甲驾驶员", "科学家", "赏金猎人", "AI工程师"],
        "女": ["舰长", "生物学家", "特工", "黑客", "医疗官"],
    },
    "都市": {
        "男": ["总裁", "医生", "律师", "警察", "创业者"],
        "女": ["总裁", "医生", "律师", "记者", "设计师"],
    },
    "武侠": {
        "男": ["少侠", "掌门", "捕快", "镖师", "隐侠"],
        "女": ["女侠", "掌门千金", "女捕快", "医女", "暗卫"],
    },
    "历史": {
        "男": ["将军", "文臣", "皇子", "谋士", "藩王"],
        "女": ["皇后", "女将", "公主", "才女", "女官"],
    },
    "悬疑": {
        "男": ["刑警", "侦探", "法医", "嫌疑人", "心理学家"],
        "女": ["刑警", "侦探", "记者", "受害者家属", "心理医生"],
    },
    "奇幻": {
        "男": ["骑士", "法师", "游侠", "矮人工匠", "龙骑士"],
        "女": ["女骑士", "女法师", "精灵游侠", "女祭司", "龙族后裔"],
    },
}


def get_default_identity(genre: str, gender: str, index: int = 0) -> str:
    """根据题材和性别获取默认身份"""
    genre_templates = GENDER_ROLE_TEMPLATES.get(genre, GENDER_ROLE_TEMPLATES["仙侠"])
    gender_roles = genre_templates.get(gender, genre_templates.get("男", []))
    if gender_roles:
        return gender_roles[index % len(gender_roles)]
    return ""


# ============================================================
# AI 补全 Prompt
# ============================================================

def _build_single_character_prompt(
    name: str, gender: str, genre: str, spec_context: str
) -> str:
    """构造单个角色补全 Prompt"""
    gender_hint = f"，性别{gender}" if gender else ""
    return (
        f"User: 基于以下世界观设定，为角色「{name}」{gender_hint}补全详细设定。\n"
        "需补全：identity（身份/门派）、personality（性格特点，3-5个关键词）、"
        "background（百字背景故事）、initial_power（初始实力/修为）、"
        "role_type（主角/重要配角/反派/导师/路人）。\n"
        f"题材: {genre}\n"
        f"世界观:\n{spec_context[:1500]}\n"
        '输出JSON: {"identity":"", "personality":[], "background":"", '
        '"initial_power":"", "role_type":""}\n'
        "\nAssistant: "
    )


def _build_batch_character_prompt(
    characters: List[Dict], genre: str, spec_context: str
) -> str:
    names = ", ".join(f"「{c['name']}」({c.get('gender', '?')})" for c in characters)
    return (
        "User: 基于以下世界观设定，为以下角色补全详细设定。\n"
        f"角色列表: {names}\n"
        "每个角色需补全：identity, personality(3-5个), background(百字), "
        "initial_power, role_type(主角/重要配角/反派/导师)。\n"
        f"题材: {genre}\n"
        f"世界观:\n{spec_context[:1500]}\n"
        '输出JSON: {"characters": [{"name":"", "identity":"", '
        '"personality":[], "background":"", "initial_power":"", "role_type":""}]}\n'
        "\nAssistant: "
    )


# ============================================================
# 角色补全器
# ============================================================

class CharacterFiller:
    """角色补全器 - 支持批量导入 + AI按需补全"""

    def __init__(self, client: RWKVClient, config: PipelineConfig, logger: Logger = None):
        self._client = client
        self._config = config
        self._logger = logger or Logger.get()

    def fill_single(
        self, name: str, gender: str, genre: str, spec_context: str
    ) -> Optional[Dict]:
        """补全单个角色（串行调用）"""
        prompt = _build_single_character_prompt(name, gender, genre, spec_context)
        sampling = SamplingParams(temperature=1.0, top_p=0.1, max_tokens=1024)

        try:
            results = self._client.big_batch_completions(
                contents=[prompt],
                sampling=sampling,
                stream=False,
            )
            result = results[0] if results else ""
            parsed, status = robust_json_parse(result, first_only=True)
            if parsed and isinstance(parsed, dict):
                parsed["name"] = name
                parsed["gender"] = gender
                return parsed
        except Exception as e:
            self._logger.warning(f"CharacterFiller: fill_single failed for {name}: {e}")
        return None

    def fill_batch_concurrent(
        self, characters: List[Dict], genre: str, spec_context: str
    ) -> List[Dict]:
        """并发补全多个角色（使用 /big_batch/completions）

        每个未补全的角色生成一个独立 prompt，一次性并发执行。
        """
        incomplete = [c for c in characters if not all(c.get(f) for f in AI_FIELDS)]
        if not incomplete:
            return characters

        # 为每个角色构造独立 prompt
        prompts = []
        for c in incomplete:
            prompts.append(_build_single_character_prompt(
                c["name"], c.get("gender", ""), genre, spec_context
            ))

        sampling = SamplingParams(temperature=1.0, top_p=0.1, max_tokens=1024)

        try:
            results = self._client.big_batch_completions(
                contents=prompts,
                sampling=sampling,
                stream=False,
            )

            if isinstance(results, str):
                results = [results]
            elif not isinstance(results, list):
                results = [str(results)]

            # 解析结果并合并回角色列表
            char_map = {c["name"]: c for c in characters}

            for i, c in enumerate(incomplete):
                if i >= len(results):
                    break
                parsed, status = robust_json_parse(results[i])
                if parsed and isinstance(parsed, dict):
                    name = c["name"]
                    if name in char_map:
                        for f in AI_FIELDS:
                            if parsed.get(f):
                                char_map[name][f] = parsed[f]

            return list(char_map.values())

        except Exception as e:
            self._logger.warning(f"CharacterFiller: fill_batch_concurrent failed: {e}")
            return characters

    def prefill_from_template(
        self, characters: List[Dict], genre: str
    ) -> List[Dict]:
        """根据题材模板预填角色身份（无需AI调用）"""
        for i, c in enumerate(characters):
            if not c.get("identity"):
                c["identity"] = get_default_identity(genre, c.get("gender", ""), i)
        return characters


def characters_to_markdown(characters: List[Dict]) -> str:
    """将角色列表格式化为 Markdown（用于 specification.md 的主要人物节）"""
    lines = []
    for c in characters:
        name = c.get("name", "未知")
        gender = c.get("gender", "")
        identity = c.get("identity", "")
        personality = c.get("personality", [])
        if isinstance(personality, list):
            personality = "、".join(str(p) for p in personality)
        background = c.get("background", "")
        power = c.get("initial_power", "")
        role = c.get("role_type", "")

        gender_str = f"（{gender}）" if gender else ""
        role_str = f" [{role}]" if role else ""
        lines.append(f"**{name}**{gender_str}{role_str}")
        if identity:
            lines.append(f"  身份: {identity}")
        if personality:
            lines.append(f"  性格: {personality}")
        if power:
            lines.append(f"  实力: {power}")
        if background:
            lines.append(f"  背景: {background}")
        lines.append("")

    return "\n".join(lines)
