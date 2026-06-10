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

# 通用约束：确保补全内容与已有设定一致
_BASE_CONSTRAINTS = (
    "\n【重要约束 - 请严格遵守】\n"
    "1. 必须**严格基于**下方【已有设定】补全角色，遵守其世界观、体系、势力与冲突。\n"
    "2. 角色的身份、实力、阵营必须与已有势力格局、境界体系自洽。\n"
    "3. 角色之间的姓名/身份/实力不要雷同；性格关键词避免重复。\n"
    "4. 如果已有设定为空，请基于题材+性别合理生成本角色，不要凭空引入与后续章节冲突的设定。\n"
)


# 严格单字姓氏（用于人名抽取，避免多字复姓把中间字也算进去）
# 资料来源：常见百家姓 + 武侠/修仙常用姓氏
_SINGLE_CN_SURNAMES = set(
    # 常见大姓
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐"
    "费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卡齐康伍余元卜顾孟平黄穆萧尹"
    "姚邵湛汪祁毛禹狄米贝明臧计伏成戴谈宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季"
    "麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管卢莫"
    "经房裘缪干解应宗丁宣贲邓郁单杭洪包诸左石崔吉钮龚程嵇邢滑裴陆荣翁"
    "甄芮羿储靳汲糜松井段富巫焦弓牧隗山谷车侯宓蓬全郗班仰秋仲伊宫"
    "宁仇栾暴甘钭厉戎祖武符刘景詹束龙叶幸司韶郜黎蓟薄印宿白怀蒲邰从鄂索咸籍"
    "赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿"
    "通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庚终暨居"
    "衡步都耿满弘匡国文寇广禄阙殴殳沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚"
    "那简饶空曾毋沙乜养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓"
    # 武侠/修仙常见冷僻姓氏
    "慕容司马欧阳诸葛上官皇甫尉迟公孙轩辕令狐宇文长孙司徒司空"
    "端木皇甫南宫"
)

# 常见中文姓氏（用于人名抽取）
_CN_SURNAMES = _SINGLE_CN_SURNAMES  # 别名，保持向后兼容

# 用来从已有文字中识别"主角/主视角"的人名
_NAME_PATTERNS = [
    re.compile(r"([\u4e00-\u9fff]{2,3})(?:是|成为|成为的|成为了|变成了|叫|名为|名叫|名为)"),
    re.compile(r"(?:主角|男主|女主|主人公|主视角|用户)[\u4e00-\u9fff]{0,4}?[\s:：是为]?([\u4e00-\u9fff]{2,3})"),
    re.compile(r"([\u4e00-\u9fff]{2,3})(?:和|与)([\u4e00-\u9fff]{2,3})"),
    re.compile(r"([\u4e00-\u9fff]{2,3})(?:醒来|穿越|来到|进入)"),
]


def extract_character_names(text: str, max_count: int = 5) -> List[Dict[str, str]]:
    """从已有文本中提取可能的人名，返回 [{"name": "宋霄", "gender": ""}, ...]

    启发式策略：
    1. 优先提取"X是..." / "X成为..." / "X叫..." / "X和Y" 等强信号模式
    2. 跳过明显非人名的词（已知题材术语、常见动词等）
    3. 根据姓氏白名单过滤

    Args:
        text:      待提取的文本（如 storyline、background 字段）
        max_count: 最多返回多少个候选

    Returns:
        [{"name": "宋霄", "gender": ""}, {"name": "钱开凤", "gender": ""}, ...]
    """
    if not text:
        return []

    # 常见停用词（不能当人名）
    STOP_WORDS = {
        "一个", "这个", "那个", "我们", "你们", "他们", "什么", "怎么", "这样", "那样",
        "因为", "所以", "于是", "然后", "突然", "已经", "正在", "已经", "并非", "不止",
        "故事", "世界", "时代", "历史", "传说", "传闻", "听说", "发现", "感到", "知道",
        "看着", "声音", "意识", "身体", "感觉", "自己", "他人", "人们", "大家",
        "自己", "直接", "立即", "马上", "迅速", "缓缓", "慢慢", "深深", "轻轻", "重重",
        "应该", "可能", "或许", "大概", "大约", "似乎", "好像", "仿佛", "犹如", "如同",
        "但是", "然而", "不过", "可是", "只是", "甚至", "尤其", "特别", "非常", "极其",
        "非常", "比较", "更加", "越发", "更为", "更", "最", "太", "很", "挺", "蛮",
        "穿越", "醒来", "发现", "成为", "变成", "进入", "来到", "处于", "位于",
        "夫妻", "父母", "子女", "孩子", "丈夫", "妻子", "家庭", "家中", "卧室", "客厅",
        "楼顶", "夹层", "陨石", "一行", "一动", "不久", "一段", "一下", "一直", "一样",
        "齐齐的", "慢慢的", "轻轻的", "重重的", "迅速", "缓缓", "匆匆", "默默", "静静",
        "一对", "两个人", "每个人", "某个人", "此人", "其人", "本人", "他人", "别人",
        "人同时", "人一起", "人一同", "人们", "人皆", "众人", "人人", "众人", "人皆", "一众人",
        "两颗", "两颗", "三人", "四人", "五人", "两人", "一人", "某个", "某种", "某个",
    }

    # 名字中不能包含的常见助词/虚词（结尾或中间）
    NAME_FORBIDDEN_CHARS = set("的了着过是嘛呢吧啊呀哦哈")

    candidates = []
    seen = set()

    def _try_add(name: str):
        if not name or len(name) < 2 or len(name) > 4:
            return
        if name in STOP_WORDS:
            return
        if name in seen:
            return
        # 必须以常见姓氏开头
        if name[0] not in _CN_SURNAMES:
            return
        # 名字里不能含"的了着过"等虚词
        for ch in name:
            if ch in NAME_FORBIDDEN_CHARS:
                return
        seen.add(name)
        candidates.append({"name": name, "gender": ""})

    # 模式 1: 强信号 - "X是..." / "X成为..." / "X和Y"
    for pat in _NAME_PATTERNS:
        for m in pat.finditer(text):
            for g in m.groups():
                if g and len(g) >= 2 and len(g) <= 4:
                    _try_add(g)

    # 模式 2: 简单姓氏开头 - 任何以姓氏开头、长度2-3的中文串
    # 仅在前面所有模式没找到时使用
    if not candidates:
        for m in re.finditer(r"[\u4e00-\u9fff]{2,3}", text):
            name = m.group(0)
            _try_add(name)
            if len(candidates) >= max_count:
                break

    return candidates[:max_count]


def _build_generate_characters_prompt(
    genre: str, spec_context: str, count: int = 4, seed_characters: List[Dict] = None
) -> str:
    """构造"AI自主生成角色"Prompt - 不需要用户预先提供角色名

    根据已有创作条件（世界观/体系/势力/冲突/主线/故事背景等），
    自动生成 count 个角色，覆盖主角/配角/反派/导师等不同角色定位。

    如果提供了 seed_characters（已存在的人名），则**优先使用这些人名作为主要角色**，
    AI 只补充剩余的角色（如反派/配角/导师）和为已有名字填充完整字段。
    """
    seed_characters = seed_characters or []
    seed_info = ""
    if seed_characters:
        names = "、".join(c.get("name", "?") for c in seed_characters if c.get("name"))
        seed_info = (
            f"\n【已存在的主角 - 必须保留这些人名】:\n{names}\n"
            "你必须为这些已存在的人名生成完整字段（identity/personality/background/initial_power/role_type），"
            "不得改名或遗漏；然后再补充反派/导师/其他配角凑齐总数。\n"
        )

    return (
        f"User: 任务：基于下方【已有设定】和【已存在的主角】直接输出 JSON 数组，不要任何解释/前言/后记。\n"
        f"题材: {genre}\n"
        f"需要凑齐 {count} 个角色（1主角+1-2重要配角+1反派+0-1导师）。\n"
        f"{seed_info}"
        "【输出要求 - 严格遵守】\n"
        "1. **只输出一段 JSON**，不要 ```json``` 标记，不要任何中文/英文说明。\n"
        '2. JSON 格式: {"characters":[{"name":"姓名","gender":"男或女",'
        '"identity":"身份/门派/职务","personality":["性格词1","性格词2","性格词3"],'
        '"background":"百字以内的背景故事","initial_power":"初始实力/修为",'
        '"role_type":"主角/重要配角/反派/导师"}]}\n'
        "3. 姓名风格匹配题材；身份落在已有势力中；实力匹配境界体系；性格词不重复。\n"
        f"【已有设定 - 角色必须严格遵守】:\n{spec_context}\n"
        + _BASE_CONSTRAINTS
        + "\nAssistant: "
    )


def _build_single_character_prompt(
    name: str, gender: str, genre: str, spec_context: str
) -> str:
    """构造单个角色补全 Prompt"""
    gender_hint = f"，性别{gender}" if gender else ""
    return (
        f"User: 基于以下世界观设定，为角色「{name}」{gender_hint}补全详细设定。\n"
        "需补全：identity（身份/门派/职务）、personality（性格特点，3-5个关键词）、"
        "background（百字背景故事）、initial_power（初始实力/修为）、"
        "role_type（主角/重要配角/反派/导师/路人）。\n"
        f"题材: {genre}\n"
        f"【已有设定 - 角色必须与以下设定一致】:\n{spec_context}\n"
        '输出JSON: {"identity":"", "personality":[], "background":"", '
        '"initial_power":"", "role_type":""}\n'
        + _BASE_CONSTRAINTS
        + "\nAssistant: "
    )


def _build_batch_character_prompt(
    characters: List[Dict], genre: str, spec_context: str
) -> str:
    names = ", ".join(f"「{c['name']}」({c.get('gender', '?')})" for c in characters)
    return (
        "User: 基于以下世界观设定，为以下角色补全详细设定。\n"
        f"角色列表: {names}\n"
        "每个角色需补全：identity, personality(3-5个), background(百字), "
        "initial_power, role_type(主角/重要配角/反派/导师/路人)。\n"
        "角色之间姓名/身份/实力/性格不要雷同。\n"
        f"题材: {genre}\n"
        f"【已有设定 - 所有角色必须与以下设定一致】:\n{spec_context}\n"
        '输出JSON: {"characters": [{"name":"", "identity":"", '
        '"personality":[], "background":"", "initial_power":"", "role_type":""}]}\n'
        + _BASE_CONSTRAINTS
        + "\nAssistant: "
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

    def generate_from_spec(
        self, genre: str, spec_context: str, count: int = 4,
        seed_characters: List[Dict] = None
    ) -> List[Dict]:
        """AI 根据已有创作条件自主生成角色（无需用户预先提供角色名）

        如果提供了 seed_characters（已存在的主角），会强制让 AI 保留这些人名作为主角色，
        并为它们填充完整字段（identity/personality/background/initial_power/role_type）；
        AI 只需补充剩余的配角/反派/导师凑齐总数。

        Args:
            genre:          题材（如"仙侠"、"玄幻"等）
            spec_context:   结构化已有设定上下文（来自 spec_map，自动排除 characters 字段）
            count:          生成角色总数，默认 4
            seed_characters: 已存在的主角列表 [{"name":"宋霄","gender":""}, ...]，
                            来自 storyline 等字段自动抽取或前端传入

        Returns:
            合并后的角色列表，AI 必须为 seed_characters 中的每个名字生成完整字段。
        """
        seed_characters = seed_characters or []
        prompt = _build_generate_characters_prompt(
            genre, spec_context, count=count, seed_characters=seed_characters
        )
        # 低温让 JSON 输出更稳定
        sampling = SamplingParams(temperature=0.7, top_p=0.1, max_tokens=2048)

        try:
            results = self._client.big_batch_completions(
                contents=[prompt],
                sampling=sampling,
                stream=False,
            )
            result = results[0] if results else ""
            # Debug: dump raw result for diagnosis
            self._logger.info(f"generate_from_spec raw result (first 1500 chars):\n{str(result)[:1500]}")

            # 尝试多种解析策略
            parsed = None

            # 策略1: 直接解析
            # 注意：AI 经常以 "角色: [...]" 或 "```json\n[...]\n```" 形式输出
            # robust_json_parse 可能错误地返回内部对象，需要严格校验
            if isinstance(result, str):
                p, _ = robust_json_parse(result, first_only=True)
                if isinstance(p, dict) and isinstance(p.get("characters"), list):
                    parsed = p
                elif isinstance(p, list) and p and isinstance(p[0], dict):
                    # AI 输出了纯数组，包装成 {"characters": [...]} 格式
                    parsed = {"characters": p}
                    self._logger.info("generate_from_spec: wrapped raw JSON array")
                # 其它情况（误解析为内部对象）一律忽略，让后续策略处理

            # 策略2: 如果返回是 list（多个候选），找第一个含 characters 的
            if parsed is None and isinstance(results, list):
                for candidate in results:
                    if not candidate or not isinstance(candidate, str):
                        continue
                    p, _ = robust_json_parse(candidate)
                    if isinstance(p, dict) and isinstance(p.get("characters"), list):
                        parsed = p
                        break
                    if isinstance(p, list) and p and isinstance(p[0], dict):
                        parsed = {"characters": p}
                        break

            # 策略3: 暴力 - 在 result 中找 {"characters":[ 或 [{ 的位置开始截取
            if parsed is None and isinstance(result, str):
                idx1 = result.find('{"characters"')
                idx2 = result.find('"characters"')
                idx_arr_lf = result.find('[\n')
                idx_arr_obj = result.find('[{')
                idx_arr_dquot = result.find('["')
                # 优先选最早出现的有效起点（先对象，后数组）
                candidates_idx = [i for i in (idx1, idx2, idx_arr_lf, idx_arr_obj, idx_arr_dquot) if i >= 0]
                if candidates_idx:
                    start = min(candidates_idx)
                    sub = result[start:]
                    # 简单尝试：用括号配对找最外层匹配
                    open_ch = sub[0]
                    close_ch = '}' if open_ch == '{' else ']'
                    depth = 0
                    end = -1
                    in_str = False
                    esc = False
                    for i, ch in enumerate(sub):
                        if esc:
                            esc = False
                            continue
                        if ch == '\\' and in_str:
                            esc = True
                            continue
                        if ch == '"' and not esc:
                            in_str = not in_str
                            continue
                        if in_str:
                            continue
                        if ch == open_ch:
                            depth += 1
                        elif ch == close_ch:
                            depth -= 1
                            if depth == 0:
                                end = i + 1
                                break
                    if end > 0:
                        candidate = sub[:end]
                        try:
                            import json as _json
                            p = _json.loads(candidate)
                            if isinstance(p, dict) and isinstance(p.get("characters"), list):
                                parsed = p
                                self._logger.info("generate_from_spec: recovered JSON via substring scan (object)")
                            elif isinstance(p, list) and p and isinstance(p[0], dict):
                                parsed = {"characters": p}
                                self._logger.info("generate_from_spec: recovered JSON via substring scan (array)")
                        except Exception:
                            pass

            # 策略4: 处理 "角色: [...]"、"下面是角色: [...]" 等带中文前缀的格式
            if parsed is None and isinstance(result, str):
                # 匹配 "中文[:：]\s*[" 后面到下一个 ] 的内容
                import re as _re
                m = _re.search(r'[：:]\s*(\[)', result)
                if m:
                    sub = result[m.start(1):]
                    depth = 0
                    end = -1
                    in_str = False
                    esc = False
                    for i, ch in enumerate(sub):
                        if esc:
                            esc = False
                            continue
                        if ch == '\\' and in_str:
                            esc = True
                            continue
                        if ch == '"' and not esc:
                            in_str = not in_str
                            continue
                        if in_str:
                            continue
                        if ch == '[':
                            depth += 1
                        elif ch == ']':
                            depth -= 1
                            if depth == 0:
                                end = i + 1
                                break
                    if end > 0:
                        candidate = sub[:end]
                        try:
                            import json as _json
                            p = _json.loads(candidate)
                            if isinstance(p, list) and p and isinstance(p[0], dict):
                                parsed = {"characters": p}
                                self._logger.info("generate_from_spec: recovered array after Chinese prefix")
                        except Exception:
                            pass

            if not parsed or not isinstance(parsed, dict):
                self._logger.warning("CharacterFiller.generate_from_spec: all parse strategies failed")
                return []

            chars = parsed.get("characters")
            if not isinstance(chars, list) or not chars:
                self._logger.warning("CharacterFiller.generate_from_spec: no characters in response")
                return []

            # 标准化每个角色
            ai_chars: List[Dict] = []
            for c in chars:
                if not isinstance(c, dict):
                    continue
                char = {
                    "name": str(c.get("name", "")).strip(),
                    "gender": str(c.get("gender", "")).strip(),
                    "identity": str(c.get("identity", "")),
                    "personality": c.get("personality", []),
                    "background": str(c.get("background", "")),
                    "initial_power": str(c.get("initial_power", "")),
                    "role_type": str(c.get("role_type", "")),
                }
                if isinstance(char["personality"], list):
                    char["personality"] = "、".join(str(p) for p in char["personality"])
                # 过滤掉没姓名的废条目
                if char["name"]:
                    ai_chars.append(char)

            # 合并 seed_characters：确保已存在的人名出现在最终列表中
            seed_names = {c.get("name", "") for c in seed_characters if c.get("name")}
            if seed_names:
                # 用 seed 替换 AI 生成的同名字（保留 AI 填充的字段）
                ai_map = {c["name"]: c for c in ai_chars}
                final_chars: List[Dict] = []
                used_names = set()
                for seed in seed_characters:
                    sname = seed.get("name", "")
                    if not sname:
                        continue
                    if sname in ai_map:
                        # AI 已经为这个名字生成了内容 → 用 AI 的，但合并 seed 的 gender
                        merged = dict(ai_map[sname])
                        if seed.get("gender"):
                            merged["gender"] = seed["gender"]
                        final_chars.append(merged)
                    else:
                        # AI 没有为这个名字生成 → 创建一个基础条目（仅姓名+性别）
                        final_chars.append({
                            "name": sname,
                            "gender": seed.get("gender", ""),
                            "identity": "",
                            "personality": "",
                            "background": "",
                            "initial_power": "",
                            "role_type": "主角" if len(final_chars) == 0 else "重要配角",
                        })
                    used_names.add(sname)
                # 追加 AI 生成的其他角色
                for c in ai_chars:
                    if c["name"] not in used_names:
                        final_chars.append(c)
                        if len(final_chars) >= count:
                            break
                return final_chars[:count]

            return ai_chars

        except Exception as e:
            self._logger.warning(f"CharacterFiller.generate_from_spec failed: {e}")
            return []


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
