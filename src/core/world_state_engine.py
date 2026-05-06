"""世界状态引擎 - 保证唯一真值和串行一致性

核心职责:
- 管理角色/势力/经济/知识图谱的实时状态
- 按章节顺序串行结算状态变更
- 冲突检测（唯一物品、位置时间、势力领地、经济数值）
- 知识图谱查询
"""

import json
import os
import copy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .file_manager import FileManager
from .logger import Logger


# ============================================================
# 数据模型
# ============================================================

@dataclass
class CharacterState:
    character_id: str
    name: str
    attributes: Dict[str, Any] = field(default_factory=dict)
    location: str = ""
    status: str = "active"
    relationships: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "character_id": self.character_id,
            "name": self.name,
            "attributes": self.attributes,
            "location": self.location,
            "status": self.status,
            "relationships": self.relationships,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> 'CharacterState':
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class FactionState:
    faction_id: str
    name: str
    members: List[str] = field(default_factory=list)
    territory: str = ""
    resources: Dict[str, Any] = field(default_factory=dict)
    relationships: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "faction_id": self.faction_id,
            "name": self.name,
            "members": self.members,
            "territory": self.territory,
            "resources": self.resources,
            "relationships": self.relationships,
        }

    @classmethod
    def from_dict(cls, d: Dict) -> 'FactionState':
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class EconomySnapshot:
    currency_system: Dict[str, str] = field(default_factory=lambda: {"primary": "灵石"})
    price_level: Dict[str, float] = field(default_factory=dict)
    faction_economy: Dict[str, Dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return {
            "currency_system": self.currency_system,
            "price_level": self.price_level,
            "faction_economy": self.faction_economy,
        }


@dataclass
class Foreshadowing:
    id: str
    description: str
    status: str = "planted"  # planted / resolved / abandoned
    planted_at: int = 0
    expected_resolve: int = 0
    resolved_at: Optional[int] = None
    resolve_method: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status,
            "planted_at": self.planted_at,
            "expected_resolve": self.expected_resolve,
            "resolved_at": self.resolved_at,
            "resolve_method": self.resolve_method,
        }


@dataclass
class TimelineEvent:
    chapter_id: int
    event: str
    timestamp: str = ""

    def to_dict(self) -> Dict:
        return {
            "chapter_id": self.chapter_id,
            "event": self.event,
            "timestamp": self.timestamp,
        }


@dataclass
class EntityStore:
    """知识图谱 - 实体关系网络"""
    entities: Dict[str, Dict] = field(default_factory=dict)
    relations: List[Dict] = field(default_factory=list)
    foreshadowings: List[Dict] = field(default_factory=list)
    timeline: List[Dict] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "entities": self.entities,
            "relations": self.relations,
            "foreshadowings": self.foreshadowings,
            "timeline": self.timeline,
        }


# ============================================================
# 状态变更请求
# ============================================================

@dataclass
class StateChange:
    chapter_id: int
    character_changes: List[Dict] = field(default_factory=list)
    faction_changes: List[Dict] = field(default_factory=list)
    economy_changes: List[Dict] = field(default_factory=list)
    new_foreshadowing: List[Dict] = field(default_factory=list)
    resolved_foreshadowing: List[Dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: Dict) -> 'StateChange':
        return cls(
            chapter_id=d.get("chapter_id", 0),
            character_changes=d.get("character_changes", []),
            faction_changes=d.get("faction_changes", []),
            economy_changes=d.get("economy_changes", []),
            new_foreshadowing=d.get("new_foreshadowing", []),
            resolved_foreshadowing=d.get("resolved_foreshadowing", []),
        )


# ============================================================
# 冲突
# ============================================================

@dataclass
class Conflict:
    chapter_id: int
    conflict_type: str  # unique_item / position_temporal / territory / economy
    description: str
    details: Dict = field(default_factory=dict)
    resolution: Optional[str] = None  # pending / resolved / skipped

    def to_dict(self) -> Dict:
        return {
            "chapter_id": self.chapter_id,
            "conflict_type": self.conflict_type,
            "description": self.description,
            "details": self.details,
            "resolution": self.resolution,
        }


# ============================================================
# 世界状态摘要（注入Prompt用）
# ============================================================

@dataclass
class WorldStateSummary:
    characters: List[CharacterState] = field(default_factory=list)
    factions: List[FactionState] = field(default_factory=list)
    economy: Optional[EconomySnapshot] = None
    pending_foreshadowings: List[Dict] = field(default_factory=list)
    recent_timeline: List[Dict] = field(default_factory=list)
    active_conflicts: List[Dict] = field(default_factory=list)

    def format_for_prompt(self) -> str:
        """格式化为可注入Prompt的文本

        输出结构:
        1. 角色状态卡 - 包含属性、位置、关系
        2. 势力状态卡 - 包含成员、领地、资源
        3. 经济快照 - 货币体系和价格水平
        4. 待回收伏笔 - 含紧急程度标记
        5. 近期时间线 - 最近3章的重要事件
        6. 活跃冲突 - 未解决的冲突
        """
        parts = []

        if self.characters:
            parts.append("## 角色状态卡")
            for c in self.characters:
                attr_parts = []
                for k, v in c.attributes.items():
                    if k in ("描述",):
                        continue
                    attr_parts.append(f"{k}={v}")
                attrs_str = ", ".join(attr_parts)

                desc = c.attributes.get("描述", "")
                parts.append(f"- {c.name}({c.character_id}): {desc}")
                if attrs_str:
                    parts.append(f"  属性: {attrs_str}")
                parts.append(f"  位置={c.location}, 状态={c.status}")

                if c.relationships:
                    rel_strs = []
                    for r in c.relationships[:5]:
                        rtype = r.get("type", "")
                        rtarget = r.get("target", "")
                        if rtype and rtarget:
                            rel_strs.append(f"{rtype}→{rtarget}")
                    if rel_strs:
                        parts.append(f"  关系: {', '.join(rel_strs)}")

        if self.factions:
            parts.append("\n## 势力状态卡")
            for f in self.factions:
                parts.append(f"- {f.name}({f.faction_id}): 成员={f.members}, 领地={f.territory}")
                if f.resources:
                    res_strs = [f"{k}={v}" for k, v in f.resources.items() if k != "描述"]
                    if res_strs:
                        parts.append(f"  资源: {', '.join(res_strs)}")

        if self.economy:
            parts.append("\n## 经济快照")
            currency = self.economy.currency_system
            parts.append(f"- 货币体系: {currency.get('primary', '灵石')}")
            if self.economy.price_level:
                price_strs = [f"{k}={v}" for k, v in list(self.economy.price_level.items())[:8]]
                parts.append(f"- 价格水平: {', '.join(price_strs)}")
            if self.economy.faction_economy:
                for fid, econ in list(self.economy.faction_economy.items())[:3]:
                    econ_strs = [f"{k}={v}" for k, v in list(econ.items())[:4]]
                    parts.append(f"- {fid}经济: {', '.join(econ_strs)}")

        if self.pending_foreshadowings:
            parts.append("\n## 待回收伏笔")
            for fs in self.pending_foreshadowings:
                fs_id = fs.get("id", "?")
                fs_desc = fs.get("description", "")
                expected = fs.get("expected_resolve", "?")
                planted = fs.get("planted_at", "?")
                urgency = ""
                if isinstance(expected, int) and isinstance(planted, int):
                    span = expected - planted
                    if span > 20:
                        urgency = " [长期]"
                    elif span > 10:
                        urgency = " [中期]"
                    else:
                        urgency = " [短期]"
                parts.append(f"- [{fs_id}] {fs_desc}{urgency} (埋设第{planted}章, 预计第{expected}章回收)")

        if self.recent_timeline:
            parts.append("\n## 近期事件")
            for ev in self.recent_timeline[-5:]:
                ch_id = ev.get("chapter_id", "?")
                event_text = ev.get("event", "")
                parts.append(f"- 第{ch_id}章: {event_text}")

        if self.active_conflicts:
            parts.append("\n## 活跃冲突")
            for conflict in self.active_conflicts[:5]:
                ctype = conflict.get("type", "")
                desc = conflict.get("description", "")
                parts.append(f"- [{ctype}] {desc}")

        return "\n".join(parts)


# ============================================================
# 世界状态引擎
# ============================================================

class WorldStateEngine:
    """世界状态引擎 - 保证唯一真值和串行一致性"""

    def __init__(self, file_manager: FileManager, logger: Optional[Logger] = None):
        self._fm = file_manager
        self._logger = logger or Logger.get()

        self.characters: Dict[str, CharacterState] = {}
        self.factions: Dict[str, FactionState] = {}
        self.economy: EconomySnapshot = EconomySnapshot()
        self.entity_store: EntityStore = EntityStore()
        self.changelog: List[Dict] = []
        self._conflicts: List[Conflict] = []

    # ---- 初始化 ----
    def init_from_spec(self, spec_text: str):
        """从设定文档初始化世界状态

        解析 specification.md 中的角色、势力、经济设定，
        生成初始 characters.jsonl、factions.jsonl、economy.json、entity_store.json
        """
        import re

        # 解析主要人物
        char_pattern = r'[-*]\s*(\S+)[：:]\s*([^\n]+)'
        char_section = re.search(r'##\s*主要人物[^\n]*\n(.*?)(?=##|$)', spec_text, re.DOTALL)
        if char_section:
            for match in re.finditer(char_pattern, char_section.group(1)):
                name = match.group(1).strip()
                desc = match.group(2).strip()
                
                # 解析性别
                gender = "未知"
                if "男" in desc:
                    gender = "男"
                elif "女" in desc:
                    gender = "女"

                char_id = name
                self.characters[char_id] = CharacterState(
                    character_id=char_id,
                    name=name,
                    attributes={"描述": desc, "性别": gender},
                    location="",
                    status="active",
                )

        # 解析势力
        faction_pattern = r'[-*]\s*(\S+)[：:]\s*([^\n]+)'
        faction_section = re.search(r'##\s*势力[^\n]*\n(.*?)(?=##|$)', spec_text, re.DOTALL)
        if faction_section:
            for match in re.finditer(faction_pattern, faction_section.group(1)):
                name = match.group(1).strip()
                desc = match.group(2).strip()
                
                faction_id = name
                self.factions[faction_id] = FactionState(
                    faction_id=faction_id,
                    name=name,
                    members=[],
                    territory="",
                    resources={"描述": desc},
                )

        # 解析经济体系
        economy_section = re.search(r'##\s*经济[^\n]*\n(.*?)(?=##|$)', spec_text, re.DOTALL)
        if economy_section:
            econ_text = economy_section.group(1).strip()
            self.economy.currency_system["primary"] = "灵石"
            self.economy.currency_system["description"] = econ_text

        # 初始化实体库
        for char_id, char in self.characters.items():
            self.entity_store.entities[char_id] = {
                "type": "character",
                "name": char.name,
                "gender": char.attributes.get("性别", "未知"),
            }

        for faction_id, faction in self.factions.items():
            self.entity_store.entities[faction_id] = {
                "type": "faction",
                "name": faction.name,
            }

        # 持久化初始状态
        self.persist()
        self._logger.info(f"World state initialized from spec: {len(self.characters)} chars, {len(self.factions)} factions")

    def load_from_files(self):
        """从 tracking/ 目录加载已有状态

        加载内容:
        - characters.jsonl → 角色状态
        - factions.jsonl → 势力状态
        - economy.json → 经济快照
        - entity_store.json → 实体库（含伏笔、时间线、关系）
        - conflicts.jsonl → 未解决冲突
        - changelog.jsonl → 变更日志
        """
        chars_path = self._fm.tracking_path("characters.jsonl")
        if self._fm.exists(chars_path):
            for rec in self._fm.read_jsonl(chars_path):
                c = CharacterState.from_dict(rec)
                self.characters[c.character_id] = c

        factions_path = self._fm.tracking_path("factions.jsonl")
        if self._fm.exists(factions_path):
            for rec in self._fm.read_jsonl(factions_path):
                f = FactionState.from_dict(rec)
                self.factions[f.faction_id] = f

        econ_path = self._fm.tracking_path("economy.json")
        if self._fm.exists(econ_path):
            data = self._fm.read_json(econ_path)
            self.economy = EconomySnapshot(
                currency_system=data.get("currency_system", {}),
                price_level=data.get("price_level", {}),
                faction_economy=data.get("faction_economy", {}),
            )

        entity_path = self._fm.tracking_path("entity_store.json")
        if self._fm.exists(entity_path):
            data = self._fm.read_json(entity_path)
            self.entity_store = EntityStore(
                entities=data.get("entities", {}),
                relations=data.get("relations", []),
                foreshadowings=data.get("foreshadowings", []),
                timeline=data.get("timeline", []),
            )

        conflicts_path = self._fm.tracking_path("conflicts.jsonl")
        if self._fm.exists(conflicts_path):
            for rec in self._fm.read_jsonl(conflicts_path):
                conflict = Conflict(
                    conflict_type=rec.get("conflict_type", ""),
                    description=rec.get("description", ""),
                    chapter_id=rec.get("chapter_id", 0),
                    resolution=rec.get("resolution"),
                    details=rec.get("details", {}),
                )
                if conflict.resolution is None or conflict.resolution == "pending":
                    self._conflicts.append(conflict)

        changelog_path = self._fm.tracking_path("changelog.jsonl")
        if self._fm.exists(changelog_path):
            for rec in self._fm.read_jsonl(changelog_path):
                self.changelog.append(rec)

        self._logger.info(f"Loaded world state: {len(self.characters)} chars, {len(self.factions)} factions, {len(self._conflicts)} pending conflicts")

    def add_character(self, character: CharacterState):
        """添加角色"""
        self.characters[character.character_id] = character

    def add_faction(self, faction: FactionState):
        """添加势力"""
        self.factions[faction.faction_id] = faction

    def get_character(self, character_id: str) -> Optional[CharacterState]:
        """获取角色"""
        return self.characters.get(character_id)

    def get_faction(self, faction_id: str) -> Optional[FactionState]:
        """获取势力"""
        return self.factions.get(faction_id)

    # ---- 状态结算 ----
    def apply_change(self, change: StateChange) -> Optional[Conflict]:
        """应用单章状态变更，返回冲突（如有）"""
        conflict = self.detect_conflict(change)
        if conflict:
            conflict.resolution = "pending"
            self._conflicts.append(conflict)
            self._logger.warning(f"Conflict detected at ch{change.chapter_id}: {conflict.description}")
            return conflict

        self._merge_change(change)
        self._update_entity_store(change)
        self._record_changelog(change)
        self.persist()
        return None

    def detect_conflict(self, change: StateChange) -> Optional[Conflict]:
        """冲突检测"""
        # 1. 唯一物品归属冲突
        for cc in change.character_changes:
            attr = cc.get("attribute", "")
            if "unique_item" in attr or "持有" in attr:
                new_val = cc.get("new_value", "")
                for cid, char in self.characters.items():
                    if cid != cc.get("character_id") and char.attributes.get(attr) == new_val and new_val:
                        return Conflict(
                            chapter_id=change.chapter_id,
                            conflict_type="unique_item",
                            description=f"唯一物品'{new_val}'同时被{char.name}和{cc.get('character_id')}持有",
                        )

        # 2. 角色位置时间冲突 - 同一章节同一角色不能出现在两个不同位置
        for cc in change.character_changes:
            if cc.get("attribute") == "location":
                char_id = cc.get("character_id", "")
                new_loc = cc.get("new_value", "")
                if char_id in self.characters:
                    old_loc = self.characters[char_id].location
                    if old_loc and old_loc != new_loc:
                        return Conflict(
                            chapter_id=change.chapter_id,
                            conflict_type="position_temporal",
                            description=f"角色{char_id}在同一章节中从'{old_loc}'变更为'{new_loc}'，可能存在位置冲突",
                        )

        # 3. 势力领地重叠
        for fc in change.faction_changes:
            if fc.get("attribute") == "territory":
                new_terr = fc.get("new_value", "")
                fid = fc.get("faction_id", "")
                for other_fid, other_f in self.factions.items():
                    if other_fid != fid and other_f.territory == new_terr and new_terr:
                        return Conflict(
                            chapter_id=change.chapter_id,
                            conflict_type="territory",
                            description=f"势力{fid}和{other_fid}领地重叠: {new_terr}",
                        )

        # 4. 经济数值矛盾 - 同一势力同一属性数值不合理变更
        for ec in change.economy_changes:
            fid = ec.get("faction_id", "")
            attr = ec.get("attribute", "")
            new_val = ec.get("new_value")
            if fid and fid in self.economy.faction_economy and attr:
                old_val = self.economy.faction_economy[fid].get(attr)
                if old_val is not None and new_val is not None:
                    try:
                        old_num = float(old_val)
                        new_num = float(new_val)
                        if old_num > 0 and abs(new_num - old_num) / old_num > 10.0:
                            return Conflict(
                                chapter_id=change.chapter_id,
                                conflict_type="economy",
                                description=f"势力{fid}经济属性'{attr}'数值异常变更: {old_val} → {new_val}（变化超过10倍）",
                            )
                    except (ValueError, TypeError, ZeroDivisionError):
                        pass

            if not fid and attr and attr in self.economy.price_level:
                old_price = self.economy.price_level[attr]
                try:
                    if new_val is not None and old_price is not None:
                        old_num = float(old_price)
                        new_num = float(new_val)
                        if old_num > 0 and abs(new_num - old_num) / old_num > 5.0:
                            return Conflict(
                                chapter_id=change.chapter_id,
                                conflict_type="economy",
                                description=f"物价'{attr}'异常波动: {old_price} → {new_val}（变化超过5倍）",
                            )
                except (ValueError, TypeError, ZeroDivisionError):
                    pass

        return None

    def resolve_conflict(self, conflict: Conflict, resolution: str, details: Dict = None):
        """解决冲突"""
        conflict.resolution = resolution
        if details:
            conflict.details.update(details)
        self._logger.info(f"Conflict resolved: {conflict.description} -> {resolution}")

    # ---- 状态摘要提取 ----
    def extract_summary(self, involved_characters: List[str], involved_factions: List[str],
                        current_chapter_id: int) -> WorldStateSummary:
        """提取与指定章节相关的世界状态摘要

        改进:
        - 包含关联角色的关系信息
        - 包含近期时间线事件（最近3章）
        - 包含活跃冲突
        - 伏笔筛选更智能：包含即将到期和已逾期的
        """
        chars = [self.characters[cid] for cid in involved_characters if cid in self.characters]
        factions = [self.factions[fid] for fid in involved_factions if fid in self.factions]

        pending = []
        for fs in self.entity_store.foreshadowings:
            if fs.get("status") != "planted":
                continue
            expected = fs.get("expected_resolve", 0)
            if not isinstance(expected, int):
                expected = 0
            if expected >= current_chapter_id - 5 or expected <= current_chapter_id + 3:
                pending.append(fs)
        overdue = self.query_overdue_foreshadowings(current_chapter_id, overdue_threshold=5)
        for od in overdue:
            if od not in pending:
                pending.append(od)

        recent_timeline = [
            ev for ev in self.entity_store.timeline
            if isinstance(ev.get("chapter_id", 0), int)
            and current_chapter_id - 3 <= ev.get("chapter_id", 0) <= current_chapter_id
        ]

        active_conflicts = []
        for conflict in self._conflicts:
            if conflict.resolution is None or conflict.resolution == "pending":
                active_conflicts.append({
                    "type": conflict.conflict_type,
                    "description": conflict.description,
                })

        return WorldStateSummary(
            characters=chars,
            factions=factions,
            economy=self.economy,
            pending_foreshadowings=pending,
            recent_timeline=recent_timeline,
            active_conflicts=active_conflicts,
        )

    # ---- 知识图谱查询 ----
    def query_entity(self, entity_id: str) -> Optional[Dict]:
        """查询实体信息"""
        return self.entity_store.entities.get(entity_id)

    def query_relations(self, entity_id: str) -> List[Dict]:
        """查询实体的所有关系"""
        return [
            r for r in self.entity_store.relations
            if r.get("source") == entity_id or r.get("target") == entity_id
        ]

    def query_foreshadowings(self, status: str = None) -> List[Dict]:
        """查询伏笔"""
        if status:
            return [fs for fs in self.entity_store.foreshadowings if fs.get("status") == status]
        return self.entity_store.foreshadowings

    def query_timeline(self, from_chapter: int = 0, to_chapter: int = 99999) -> List[Dict]:
        """查询时间线事件"""
        return [
            ev for ev in self.entity_store.timeline
            if from_chapter <= ev.get("chapter_id", 0) <= to_chapter
        ]

    def query_characters_by_attribute(self, attribute: str, min_value=None, max_value=None,
                                       exact_value=None) -> List[CharacterState]:
        """按角色属性条件查询（复杂条件查询）

        示例: query_characters_by_attribute("好感度", min_value=80)
        """
        results = []
        for char in self.characters.values():
            val = char.attributes.get(attribute)
            if val is None:
                continue
            if exact_value is not None and val == exact_value:
                results.append(char)
            elif min_value is not None or max_value is not None:
                try:
                    num_val = float(val)
                    if min_value is not None and num_val < float(min_value):
                        continue
                    if max_value is not None and num_val > float(max_value):
                        continue
                    results.append(char)
                except (ValueError, TypeError):
                    if exact_value is None:
                        continue
            elif exact_value is None and min_value is None and max_value is None:
                results.append(char)
        return results

    def query_characters_by_location(self, location: str) -> List[CharacterState]:
        """查询指定位置的所有角色"""
        return [c for c in self.characters.values() if c.location == location]

    def query_faction_members(self, faction_id: str) -> List[CharacterState]:
        """查询势力成员列表（返回完整角色状态）"""
        faction = self.factions.get(faction_id)
        if not faction:
            return []
        return [
            self.characters[mid] for mid in faction.members
            if mid in self.characters
        ]

    def query_overdue_foreshadowings(self, current_chapter_id: int, overdue_threshold: int = 10) -> List[Dict]:
        """查询逾期未回收的伏笔

        伏笔的 expected_resolve 章节已过但尚未回收
        """
        overdue = []
        for fs in self.entity_store.foreshadowings:
            if fs.get("status") == "planted":
                expected = fs.get("expected_resolve", 0)
                if expected > 0 and current_chapter_id - expected > overdue_threshold:
                    overdue.append(fs)
        return overdue

    def query_character_relationships(self, character_id: str, relation_type: str = None) -> List[Dict]:
        """查询角色的特定类型关系"""
        char = self.characters.get(character_id)
        if not char:
            return []
        if relation_type:
            return [r for r in char.relationships if r.get("type") == relation_type]
        return char.relationships

    def add_relation(self, source: str, target: str, relation_type: str, properties: Dict = None):
        """添加实体关系到知识图谱"""
        relation = {
            "source": source,
            "target": target,
            "type": relation_type,
        }
        if properties:
            relation.update(properties)
        self.entity_store.relations.append(relation)

    def get_world_snapshot(self, current_chapter_id: int) -> Dict:
        """获取完整世界状态快照（供Web UI和审核使用）"""
        return {
            "characters": {cid: c.to_dict() for cid, c in self.characters.items()},
            "factions": {fid: f.to_dict() for fid, f in self.factions.items()},
            "economy": self.economy.to_dict(),
            "foreshadowings": {
                "planted": len([fs for fs in self.entity_store.foreshadowings if fs.get("status") == "planted"]),
                "resolved": len([fs for fs in self.entity_store.foreshadowings if fs.get("status") == "resolved"]),
                "overdue": len(self.query_overdue_foreshadowings(current_chapter_id)),
            },
            "timeline_events": len(self.entity_store.timeline),
            "entity_count": len(self.entity_store.entities),
            "relation_count": len(self.entity_store.relations),
        }

    # ---- 持久化 ----
    def persist(self):
        """将当前状态写入文件"""
        # characters.jsonl
        chars = [c.to_dict() for c in self.characters.values()]
        self._fm.write_jsonl(self._fm.tracking_path("characters.jsonl"), chars)

        # factions.jsonl
        factions = [f.to_dict() for f in self.factions.values()]
        self._fm.write_jsonl(self._fm.tracking_path("factions.jsonl"), factions)

        # economy.json
        if self.economy:
            self._fm.write_json(self._fm.tracking_path("economy.json"), self.economy.to_dict())

        # entity_store.json
        self._fm.write_json(self._fm.tracking_path("entity_store.json"), self.entity_store.to_dict())

        # conflicts.jsonl
        conflicts = [c.to_dict() for c in self._conflicts]
        self._fm.write_jsonl(self._fm.tracking_path("conflicts.jsonl"), conflicts)

        # changelog.jsonl
        self._fm.write_jsonl(self._fm.tracking_path("changelog.jsonl"), self.changelog)

        # changelog.md
        log_path = self._fm.tracking_path("changelog.md")
        lines = ["# 世界状态变更日志\n"]
        for entry in self.changelog:
            lines.append(f"## 第{entry['chapter_id']}章 ({entry['timestamp']})\n")
            for cc in entry.get("character_changes", []):
                lines.append(f"- 角色 {cc.get('character_id')}: {cc.get('attribute')} {cc.get('old_value')} → {cc.get('new_value')}")
            for fc in entry.get("faction_changes", []):
                lines.append(f"- 势力 {fc.get('faction_id')}: {fc.get('attribute')} {fc.get('old_value')} → {fc.get('new_value')}")
            lines.append("")
        self._fm.write_markdown(log_path, "\n".join(lines))

    # ---- 内部方法 ----
    def _merge_change(self, change: StateChange):
        """原子化合并状态变更"""
        # 角色变更
        for cc in change.character_changes:
            cid = cc.get("character_id", "")
            if cid in self.characters:
                char = self.characters[cid]
                attr = cc.get("attribute", "")
                if attr == "location":
                    char.location = cc.get("new_value", "")
                elif attr == "status":
                    char.status = cc.get("new_value", "")
                else:
                    char.attributes[attr] = cc.get("new_value")

        # 势力变更
        for fc in change.faction_changes:
            fid = fc.get("faction_id", "")
            if fid in self.factions:
                faction = self.factions[fid]
                attr = fc.get("attribute", "")
                if attr == "territory":
                    faction.territory = fc.get("new_value", "")
                elif attr == "members":
                    faction.members = fc.get("new_value", [])
                else:
                    faction.resources[attr] = fc.get("new_value")

        # 经济变更
        for ec in change.economy_changes:
            fid = ec.get("faction_id", "")
            attr = ec.get("attribute", "")
            if fid and fid in self.economy.faction_economy:
                self.economy.faction_economy[fid][attr] = ec.get("new_value", 0)
            elif attr in self.economy.price_level:
                self.economy.price_level[attr] = ec.get("new_value", 0)

        # 新伏笔
        for fs in change.new_foreshadowing:
            self.entity_store.foreshadowings.append(fs)
            fs_id = fs.get("id", "")
            if fs_id:
                self.entity_store.entities[fs_id] = {
                    "type": "foreshadowing",
                    "label": fs.get("description", ""),
                }

        # 伏笔回收
        for rf in change.resolved_foreshadowing:
            fs_id = rf.get("id", "")
            for fs in self.entity_store.foreshadowings:
                if fs.get("id") == fs_id:
                    fs["status"] = "resolved"
                    fs["resolved_at"] = change.chapter_id
                    fs["resolve_method"] = rf.get("method", "")

    def _update_entity_store(self, change: StateChange):
        """更新知识图谱"""
        # 添加时间线事件
        for cc in change.character_changes:
            self.entity_store.timeline.append({
                "chapter_id": change.chapter_id,
                "event": f"{cc.get('character_id')}: {cc.get('attribute')} 变更为 {cc.get('new_value')}",
                "timestamp": datetime.now().isoformat(),
            })

    def _record_changelog(self, change: StateChange):
        """记录变更日志"""
        self.changelog.append({
            "chapter_id": change.chapter_id,
            "timestamp": datetime.now().isoformat(),
            "character_changes": change.character_changes,
            "faction_changes": change.faction_changes,
            "economy_changes": change.economy_changes,
        })
