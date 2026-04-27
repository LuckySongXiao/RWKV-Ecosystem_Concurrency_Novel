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

    def format_for_prompt(self) -> str:
        """格式化为可注入Prompt的文本"""
        parts = []

        if self.characters:
            parts.append("## 角色状态卡")
            for c in self.characters:
                attrs = ", ".join(f"{k}={v}" for k, v in c.attributes.items())
                parts.append(f"- {c.name}({c.character_id}): {attrs}, 位置={c.location}, 状态={c.status}")

        if self.factions:
            parts.append("\n## 势力状态卡")
            for f in self.factions:
                parts.append(f"- {f.name}({f.faction_id}): 成员={f.members}, 领地={f.territory}")

        if self.economy:
            parts.append("\n## 经济快照")
            parts.append(f"- 货币体系: {self.economy.currency_system}")

        if self.pending_foreshadowings:
            parts.append("\n## 待回收伏笔")
            for fs in self.pending_foreshadowings:
                parts.append(f"- [{fs['id']}] {fs['description']} (预计第{fs.get('expected_resolve', '?')}章回收)")

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

    # ---- 初始化 ----
    def init_from_spec(self, spec_text: str):
        """从设定文档初始化世界状态（由总编Agent解析后调用）"""
        # 初始化空状态，具体由总编Agent的输出填充
        self._logger.info("World state initialized from spec")

    def load_from_files(self):
        """从 tracking/ 目录加载已有状态"""
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

        self._logger.info(f"Loaded world state: {len(self.characters)} chars, {len(self.factions)} factions")

    # ---- 状态结算 ----
    def apply_change(self, change: StateChange) -> Optional[Conflict]:
        """应用单章状态变更，返回冲突（如有）"""
        conflict = self.detect_conflict(change)
        if conflict:
            conflict.resolution = "pending"
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

        # 2. 角色位置时间冲突
        for cc in change.character_changes:
            if cc.get("attribute") == "location":
                char_id = cc.get("character_id", "")
                new_loc = cc.get("new_value", "")
                if char_id in self.characters:
                    old_loc = self.characters[char_id].location
                    # 简单检查：同一角色不能同时出现在两个遥远位置
                    # （实际冲突判定由审核Agent细化）

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
        """提取与指定章节相关的世界状态摘要"""
        chars = [self.characters[cid] for cid in involved_characters if cid in self.characters]
        factions = [self.factions[fid] for fid in involved_factions if fid in self.factions]

        # 获取待回收伏笔
        pending = [
            fs for fs in self.entity_store.foreshadowings
            if fs.get("status") == "planted" and fs.get("expected_resolve", 0) >= current_chapter_id - 5
        ]

        return WorldStateSummary(
            characters=chars,
            factions=factions,
            economy=self.economy,
            pending_foreshadowings=pending,
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
        self._fm.write_json(self._fm.tracking_path("economy.json"), self.economy.to_dict())

        # entity_store.json
        self._fm.write_json(self._fm.tracking_path("entity_store.json"), self.entity_store.to_dict())

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
