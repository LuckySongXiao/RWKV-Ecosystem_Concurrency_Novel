"""题材扩展器 - 根据用户设定的题材自动补全世界观设定

核心职责:
- 识别题材类型（仙侠/玄幻/科幻/都市/历史/武侠/悬疑/奇幻等）
- 基于题材模板自动补全：修行体系、势力格局、经济体系、世界法则
- 将用户简略设定扩展为完整世界观文档
- 上下文预算管理：确保扩展后的设定不超过 token 预算
"""

import re
from typing import Dict, List, Optional, Tuple


# ============================================================
# 题材模板库
# ============================================================

GENRE_TEMPLATES = {
    "仙侠": {
        "world_law": "天地灵气循环，三界（天界/人界/冥界）各有法则。天道运转以劫数为纪，万载一轮回。灵气浓度决定修行上限，天地大劫时灵气枯竭。",
        "cultivation_system": (
            "境界划分：\n"
            "1. 炼气期（1-9层）→ 感气、引气、御气\n"
            "2. 筑基期（初/中/后/大圆满）→ 凝聚道基\n"
            "3. 金丹期 → 结丹成婴之前提\n"
            "4. 元婴期 → 神魂出窍，可夺舍\n"
            "5. 化虚期 → 空间法则初窥\n"
            "6. 合体期 → 天人合一\n"
            "7. 大乘期 → 渡劫飞升之前\n"
            "8. 渡劫期 → 天劫洗礼\n"
            "突破条件：需灵气积累+悟道契机+天劫考验（金丹以上）"
        ),
        "faction_pattern": "宗门（正道/魔道/散修）、世家大族、天庭/冥府、妖族势力",
        "economy_system": "灵石（下/中/上/极品）为通用货币，丹药/法器/灵材为交易核心，宗门掌控灵矿资源",
        "conflict_types": "道争（正魔之争）、资源争夺（灵矿/秘境）、天劫危机、上古秘辛、种族矛盾（人妖）",
        "power_ceiling": "渡劫期飞升为上限，天界仙人不可轻易下界",
    },
    "玄幻": {
        "world_law": "多元位面共存，有主物质位面与无数附属位面。位面之间有壁障，强者可破界而行。每个位面有独立法则但底层规则统一。",
        "cultivation_system": (
            "境界划分：\n"
            "1. 斗者 → 斗气初生\n"
            "2. 斗师 → 斗气化形\n"
            "3. 大斗师 → 斗气凝物\n"
            "4. 斗灵 → 斗气化翼\n"
            "5. 斗王 → 斗气化海\n"
            "6. 斗皇 → 斗气凝域\n"
            "7. 斗宗 → 空间之力\n"
            "8. 斗尊 → 天地共鸣\n"
            "9. 斗圣 → 半步超脱\n"
            "10. 斗帝 → 超脱位面\n"
            "突破条件：斗气积累+异火/天材地宝+悟性突破"
        ),
        "faction_pattern": "帝国/王国、宗族、佣兵团、商会、远古八族",
        "economy_system": "金币为凡人货币，灵石/丹药为修行者货币，异火/天材地宝为顶级资源",
        "conflict_types": "帝国争霸、宗族恩怨、异火争夺、远古秘辛、位面入侵",
        "power_ceiling": "斗帝为位面极限，超脱后进入大千世界",
    },
    "科幻": {
        "world_law": "物理定律为基础，可能存在超光速/暗物质/维度折叠等扩展物理。文明等级决定科技上限，AI/基因改造/纳米技术为核心科技树。",
        "cultivation_system": (
            "能力体系：\n"
            "1. 基因改造（1-5阶）→ 身体强化\n"
            "2. 神经接驳 → 脑机融合\n"
            "3. 纳米改造 → 体内纳米网络\n"
            "4. 意识升维 → 纯数据生命\n"
            "5. 维度跃迁 → 超越三维限制\n"
            "进阶条件：技术突破+资源投入+基因适配度"
        ),
        "faction_pattern": "星际联邦/帝国、巨型企业、AI集体、反叛军、外星文明",
        "economy_system": "信用点为通用货币，能源/算力/稀有矿物为硬通货，专利/技术为最高价值",
        "conflict_types": "星际战争、AI觉醒、基因歧视、资源枯竭、文明碰撞、技术失控",
        "power_ceiling": "维度跃迁为已知极限，更高维度存在未知",
    },
    "都市": {
        "world_law": "现代都市为背景，可能叠加异能/修真/系统等超自然元素。社会规则与现实一致，超自然元素有隐藏机制。",
        "cultivation_system": (
            "能力体系（可选）：\n"
            "1. 异能觉醒（F-A-S-SS级）\n"
            "2. 古武传承 → 内劲/外功\n"
            "3. 修真入世 → 炼气/筑基（隐藏于都市）\n"
            "4. 系统绑定 → 任务/升级/商城\n"
            "进阶条件：觉醒+历练+机缘"
        ),
        "faction_pattern": "豪门世家、地下势力、官方组织（异能局/修真司）、跨国集团、隐世门派",
        "economy_system": "人民币为凡人货币，灵石/贡献点为修行者货币，人脉/信息为隐性资源",
        "conflict_types": "商战、家族恩怨、正邪对抗、都市异能争斗、隐世与现实冲突",
        "power_ceiling": "筑基/金丹为都市极限，更高境界需入名山大川",
    },
    "武侠": {
        "world_law": "江湖为法外之地，朝廷与江湖并存。内力为力量根基，武学境界决定实力。门派传承千年，江湖规矩自成体系。",
        "cultivation_system": (
            "武学境界：\n"
            "1. 三流 → 初窥门径\n"
            "2. 二流 → 小有所成\n"
            "3. 一流 → 名动一方\n"
            "4. 绝顶 → 开宗立派\n"
            "5. 宗师 → 自创武学\n"
            "6. 化境 → 天人合一\n"
            "进阶条件：内力积累+武学悟性+实战磨砺"
        ),
        "faction_pattern": "名门正派、魔教、朝廷六扇门、江湖帮会、隐世高人",
        "economy_system": "白银为通用货币，武功秘籍/神兵利器为最高价值，门派掌控产业",
        "conflict_types": "正邪之争、夺宝之争、灭门之仇、朝廷与江湖、武林盟主之争",
        "power_ceiling": "破境为武学极限，传说中可达破碎虚空",
    },
    "历史": {
        "world_law": "遵循历史大势，可适度虚构人物和事件。朝堂/战场/江湖三线并行，天命与人力交织。",
        "cultivation_system": (
            "能力体系：\n"
            "1. 谋略 → 运筹帷幄\n"
            "2. 武勇 → 冲锋陷阵\n"
            "3. 治政 → 安邦定国\n"
            "4. 权术 → 纵横捭阖\n"
            "进阶条件：功勋+声望+机缘"
        ),
        "faction_pattern": "皇室/宗室、文官集团、武将勋贵、地方藩镇、异族政权",
        "economy_system": "铜钱/白银/黄金为货币，盐铁/土地/人口为核心资源",
        "conflict_types": "党争、夺嫡、外患、民变、藩镇割据、权臣篡位",
        "power_ceiling": "皇权为权力极限，但受制于天命与群臣",
    },
    "悬疑": {
        "world_law": "现实世界为基底，可能叠加超自然元素。线索链式展开，真相层层剥开。逻辑严密，伏笔深远。",
        "cultivation_system": (
            "能力体系：\n"
            "1. 推理 → 逻辑分析\n"
            "2. 洞察 → 微表情/行为分析\n"
            "3. 专业知识 → 法医/刑侦/心理\n"
            "4. 特殊能力（可选）→ 共情/预知/通灵\n"
            "进阶条件：经验积累+知识拓展+直觉训练"
        ),
        "faction_pattern": "警方/检方、犯罪组织、神秘组织、民间侦探、受害者联盟",
        "economy_system": "现代货币体系，信息/证据/人脉为隐性资源",
        "conflict_types": "侦破与反侦破、组织追杀、真相与掩盖、正义与私刑",
        "power_ceiling": "人类智力极限，超自然元素需有合理解释或留白",
    },
    "奇幻": {
        "world_law": "魔法与自然共存，世界有创世神话。种族多样（人/精灵/矮人/兽人等），魔法体系有明确规则和代价。",
        "cultivation_system": (
            "魔法体系：\n"
            "1. 学徒 → 基础法术\n"
            "2. 初级法师 → 单系精通\n"
            "3. 中级法师 → 多系兼修\n"
            "4. 高级法师 → 法术创新\n"
            "5. 大法师 → 领域展开\n"
            "6. 传奇法师 → 改写法则\n"
            "进阶条件：魔力积累+法术理解+元素亲和"
        ),
        "faction_pattern": "王国/帝国、魔法学院、骑士团、商会、龙族、暗夜势力",
        "economy_system": "金币为通用货币，魔法材料/附魔装备为高价值品，龙晶/圣物为传说级",
        "conflict_types": "种族战争、魔王入侵、王位继承、魔法失控、远古封印松动",
        "power_ceiling": "传奇法师为凡人极限，半神/神明为超凡存在",
    },
}


# ============================================================
# 题材识别
# ============================================================

# 题材关键词映射（支持模糊匹配）
GENRE_KEYWORDS = {
    "仙侠": ["仙侠", "修仙", "修真", "仙道", "飞升", "渡劫", "灵气", "道法", "仙界"],
    "玄幻": ["玄幻", "斗气", "异火", "位面", "大千世界", "斗破"],
    "科幻": ["科幻", "星际", "赛博", "机甲", "AI", "基因", "纳米", "太空", "外星"],
    "都市": ["都市", "现代", "异能", "系统", "重生都市", "都市修真"],
    "武侠": ["武侠", "江湖", "内力", "武功", "门派", "侠客", "轻功"],
    "历史": ["历史", "朝堂", "架空历史", "穿越历史", "争霸", "权谋"],
    "悬疑": ["悬疑", "推理", "侦探", "刑侦", "灵异", "恐怖", "惊悚"],
    "奇幻": ["奇幻", "魔法", "精灵", "矮人", "龙族", "骑士", "魔王", "西幻"],
}


def detect_genre(text: str) -> Tuple[str, float]:
    """从文本中识别题材类型

    Returns:
        (genre_name, confidence) - 题材名和置信度
    """
    scores: Dict[str, int] = {}
    text_lower = text.lower()

    for genre, keywords in GENRE_KEYWORDS.items():
        score = 0
        for kw in keywords:
            count = text_lower.count(kw.lower())
            score += count * (3 if len(kw) >= 2 else 1)  # 长关键词权重更高
        if score > 0:
            scores[genre] = score

    if not scores:
        return ("仙侠", 0.3)  # 默认仙侠，低置信度

    best = max(scores, key=scores.get)
    total = sum(scores.values())
    confidence = scores[best] / total if total > 0 else 0.0
    return (best, min(confidence, 1.0))


# ============================================================
# 设定扩展器
# ============================================================

class GenreExpander:
    """题材驱动设定扩展器

    工作流程:
    1. 读取用户 specification.md
    2. 识别题材
    3. 检测哪些设定项为空/占位符
    4. 用题材模板自动补全空项
    5. 保留用户已填写的项（不覆盖）
    6. 输出完整设定文档
    """

    # 占位符模式
    PLACEHOLDER_PATTERNS = [
        r"（请在此处填写[^）]*）",
        r"（[^）]*填写[^）]*）",
        r"\[请填写[^\]]*\]",
        r"待填写",
        r"TODO",
        r"TBD",
    ]

    def __init__(self, token_budget: int = 3000):
        """
        Args:
            token_budget: 设定文档的 token 预算（默认3000，约占8K上下文的37%）
        """
        self.token_budget = token_budget

    def _is_placeholder(self, text: str) -> bool:
        """检测文本是否为占位符"""
        stripped = text.strip()
        if not stripped:
            return True
        for pattern in self.PLACEHOLDER_PATTERNS:
            if re.search(pattern, stripped):
                return True
        return False

    def _estimate_tokens(self, text: str) -> int:
        """估算中文文本的 token 数（粗略：1.7字/token）"""
        return int(len(text) / 1.7)

    def _extract_sections(self, spec: str) -> Dict[str, str]:
        """从 specification.md 中提取各节内容（跳过一级标题）"""
        sections: Dict[str, str] = {}
        current_section = ""
        current_content: List[str] = []

        for line in spec.split("\n"):
            # 检测 Markdown 二级/三级标题（跳过一级标题，那是文档标题）
            match = re.match(r"^#{2,3}\s+(.+)$", line)
            if match:
                # 保存上一节
                if current_section:
                    sections[current_section] = "\n".join(current_content).strip()
                current_section = match.group(1).strip()
                current_content = []
            elif not re.match(r"^#\s+", line):
                # 跳过一级标题行
                current_content.append(line)

        # 保存最后一节
        if current_section:
            sections[current_section] = "\n".join(current_content).strip()

        return sections

    def _build_section(self, title: str, content: str) -> str:
        return f"## {title}\n{content}\n"

    def expand(self, spec: str, genre_override: str = "") -> Tuple[str, str, Dict]:
        """扩展世界观设定

        Args:
            spec: 原始 specification.md 内容
            genre_override: 手动指定题材（覆盖自动检测）

        Returns:
            (expanded_spec, detected_genre, expansion_report)
        """
        # 1. 识别题材
        if genre_override:
            genre = genre_override
            confidence = 1.0
        else:
            genre, confidence = detect_genre(spec)

        template = GENRE_TEMPLATES.get(genre, GENRE_TEMPLATES["仙侠"])

        # 2. 提取现有节
        sections = self._extract_sections(spec)

        # 3. 检测并补全空项
        report = {
            "genre": genre,
            "confidence": confidence,
            "expanded_sections": [],
            "preserved_sections": [],
            "token_estimate": 0,
        }

        # 需要确保存在的节及其模板映射
        required_sections = {
            "世界观": template["world_law"],
            "修行体系": template["cultivation_system"],
            "势力格局": template["faction_pattern"],
            "经济体系": template["economy_system"],
            "核心冲突类型": template["conflict_types"],
            "力量上限": template["power_ceiling"],
        }

        # 节名到模板的模糊匹配
        section_aliases = {
            "世界观": ["世界观", "世界设定", "天地法则", "世界法则"],
            "修行体系": ["修行体系", "修炼体系", "境界划分", "能力体系", "武学境界", "魔法体系"],
            "势力格局": ["势力格局", "势力", "门派", "阵营", "势力分布"],
            "经济体系": ["经济体系", "经济", "货币", "交易"],
            "核心冲突类型": ["核心冲突类型", "冲突", "矛盾", "核心冲突"],
            "力量上限": ["力量上限", "实力上限", "战力上限", "天花板"],
        }

        expanded_sections: Dict[str, str] = {}

        # 先处理用户已有的节
        for section_title, content in sections.items():
            # 检查是否匹配到某个必需节
            matched_key = None
            for key, aliases in section_aliases.items():
                if section_title in aliases or section_title == key:
                    matched_key = key
                    break

            if matched_key and self._is_placeholder(content):
                # 占位符 → 用模板补全
                expanded_sections[matched_key] = template[{
                    "世界观": "world_law",
                    "修行体系": "cultivation_system",
                    "势力格局": "faction_pattern",
                    "经济体系": "economy_system",
                    "核心冲突类型": "conflict_types",
                    "力量上限": "power_ceiling",
                }[matched_key]]
                report["expanded_sections"].append(section_title)
            else:
                # 保留用户内容
                expanded_sections[section_title] = content
                report["preserved_sections"].append(section_title)

        # 补全缺失的节
        for key, template_content in required_sections.items():
            if key not in expanded_sections:
                # 检查别名是否已存在
                found = False
                for alias in section_aliases.get(key, []):
                    if alias in expanded_sections:
                        found = True
                        break
                if not found:
                    expanded_sections[key] = template_content
                    report["expanded_sections"].append(key)

        # 4. 组装扩展后的 specification
        # 保持原始文档的标题
        title_match = re.search(r"^#\s+(.+)$", spec, re.MULTILINE)
        doc_title = title_match.group(1) if title_match else "世界观设定"

        # 按逻辑顺序排列
        section_order = [
            "题材", "世界观", "修行体系", "主要人物",
            "势力格局", "经济体系", "核心冲突类型", "力量上限",
            "故事主线",
        ]

        parts = [f"# {doc_title}\n"]

        # 先按顺序输出有内容的节
        for section_name in section_order:
            if section_name in expanded_sections:
                content = expanded_sections.pop(section_name)
                parts.append(self._build_section(section_name, content))

        # 输出剩余的节
        for section_name, content in expanded_sections.items():
            if section_name not in section_order:
                parts.append(self._build_section(section_name, content))

        expanded_spec = "\n".join(parts)

        # 5. Token 预算检查与压缩
        estimated_tokens = self._estimate_tokens(expanded_spec)
        if estimated_tokens > self.token_budget:
            expanded_spec = self._compress(expanded_spec, self.token_budget)
            estimated_tokens = self._estimate_tokens(expanded_spec)

        report["token_estimate"] = estimated_tokens

        return expanded_spec, genre, report

    def _compress(self, text: str, budget: int) -> str:
        """压缩设定文档以适应 token 预算

        策略：按节压缩，优先保留核心节（世界观/修行体系/主要人物），
        次要节截断到关键信息
        """
        target_chars = int(budget * 1.7)  # token → 字数

        # 核心节（不压缩）和次要节（可截断）
        core_sections = {"世界观", "修行体系", "主要人物", "题材"}
        truncatable_sections = {"势力格局", "经济体系", "核心冲突类型", "力量上限", "故事主线"}

        sections = self._extract_sections(text)
        title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        doc_title = title_match.group(1) if title_match else "世界观设定"

        # 先计算核心节总长
        core_len = len(f"# {doc_title}\n\n")
        for name in core_sections:
            if name in sections:
                core_len += len(self._build_section(name, sections[name]))

        # 剩余预算分配给次要节
        remaining = target_chars - core_len
        if remaining < 200:
            remaining = 200  # 保底

        # 次要节按比例分配
        truncatable_count = sum(1 for s in truncatable_sections if s in sections)
        if truncatable_count > 0:
            per_section = remaining // truncatable_count
        else:
            per_section = remaining

        # 组装
        parts = [f"# {doc_title}\n\n"]
        for name in core_sections:
            if name in sections:
                parts.append(self._build_section(name, sections[name]))

        for name in truncatable_sections:
            if name in sections:
                content = sections[name]
                if len(content) > per_section:
                    # 截断并加省略标记
                    content = content[:per_section - 3] + "..."
                parts.append(self._build_section(name, content))

        # 其他节
        for name, content in sections.items():
            if name not in core_sections and name not in truncatable_sections:
                parts.append(self._build_section(name, content))

        return "\n".join(parts)


# ============================================================
# 上下文预算管理器
# ============================================================

class ContextBudget:
    """8K 上下文预算分配器

    将 8K 上下文按任务类型分配给不同组件:
    - 设定文档: ~30% (2400 tokens)
    - 任务指令: ~10% (800 tokens)
    - 世界状态摘要: ~15% (1200 tokens)
    - 前情提要: ~15% (1200 tokens)
    - 风格约束: ~5% (400 tokens)
    - 输出预留: ~25% (2000 tokens)

    不同任务类型有不同的分配比例
    """

    TOTAL_BUDGET = 8192  # 8K

    # 任务类型 → 各组件预算比例
    BUDGET_PROFILES = {
        "outline_gen": {
            "spec": 0.40, "instruction": 0.15, "style": 0.05, "output": 0.40,
        },
        "volume_gen": {
            "spec": 0.30, "instruction": 0.10, "outline": 0.10, "output": 0.50,
        },
        "chapter_outline": {
            "spec": 0.15, "instruction": 0.10, "volume_outline": 0.25, "output": 0.50,
        },
        "chapter_content": {
            "spec": 0.10, "instruction": 0.08, "state_summary": 0.15,
            "previous_summary": 0.12, "style": 0.05, "output": 0.50,
        },
        "roleplay": {
            "character_state": 0.15, "scene": 0.10, "history": 0.25,
            "instruction": 0.10, "output": 0.40,
        },
        "fact_check": {
            "drafts": 0.35, "world_state": 0.25, "instruction": 0.10, "output": 0.30,
        },
        "narrative_review": {
            "drafts": 0.35, "world_state": 0.25, "instruction": 0.10, "output": 0.30,
        },
    }

    def get_budget(self, task_type: str, component: str) -> int:
        """获取指定任务类型下某组件的 token 预算"""
        profile = self.BUDGET_PROFILES.get(task_type, self.BUDGET_PROFILES["chapter_content"])
        ratio = profile.get(component, 0.1)
        return int(self.TOTAL_BUDGET * ratio)

    def truncate_to_budget(self, text: str, budget: int) -> str:
        """将文本截断到 token 预算内"""
        max_chars = int(budget * 1.7)  # token → 字数
        if len(text) <= max_chars:
            return text
        return text[:max_chars - 3] + "..."

    def format_state_summary_compact(self, state_summary_text: str, task_type: str) -> str:
        """压缩世界状态摘要以适应预算

        策略：保留角色名+关键属性，删除详细描述
        """
        budget = self.get_budget(task_type, "state_summary")
        max_chars = int(budget * 1.7)

        if len(state_summary_text) <= max_chars:
            return state_summary_text

        # 压缩：每角色只保留一行核心信息
        lines = state_summary_text.split("\n")
        compact_lines = []
        for line in lines:
            if line.startswith("##"):
                compact_lines.append(line)
            elif line.startswith("-"):
                # 保留角色行但截断过长属性
                if len(line) > 80:
                    compact_lines.append(line[:77] + "...")
                else:
                    compact_lines.append(line)
            # 跳过空行和详细描述

        result = "\n".join(compact_lines)
        if len(result) > max_chars:
            result = result[:max_chars - 3] + "..."

        return result
