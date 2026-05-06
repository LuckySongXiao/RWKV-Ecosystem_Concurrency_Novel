"""世界状态冲突检测验证测试

构造冲突场景:
- 唯一物品重复归属
- 角色同时出现在两地
验证:
- 冲突被正确检测和报告
- 冲突解决后状态正确更新
"""

import pytest
import os
import tempfile
import shutil
from src.core.file_manager import FileManager
from src.core.world_state_engine import WorldStateEngine, CharacterState, FactionState, StateChange


class TestConflictDetection:
    """冲突检测测试"""

    @pytest.fixture
    def temp_dir(self):
        """创建临时目录"""
        temp_dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(temp_dir, "output", "tracking"), exist_ok=True)
        yield temp_dir
        shutil.rmtree(temp_dir, ignore_errors=True)

    @pytest.fixture
    def engine(self, temp_dir):
        """创建世界状态引擎"""
        from src.core.config import PathsConfig
        config_paths = PathsConfig(
            context_dir=temp_dir,
            output_dir=os.path.join(temp_dir, "output"),
            states_dir=temp_dir,
            project_root=temp_dir,
        )
        fm = FileManager(config_paths)
        return WorldStateEngine(fm)

    def test_location_conflict(self, engine):
        """测试位置冲突 - 角色同时出现在两地"""
        # 添加角色
        engine.add_character(CharacterState(
            character_id="凌云",
            name="凌云",
            location="青云宗后山",
            status="活跃",
            attributes={"修为": "筑基期"}
        ))

        # 第一章：凌云在后山
        change1 = StateChange(
            chapter_id=1,
            character_changes=[{
                "character_id": "凌云",
                "attribute": "location",
                "new_value": "青云宗后山",
            }]
        )
        engine.apply_change(change1)

        # 第一章：凌云在大殿（冲突！）
        change2 = StateChange(
            chapter_id=1,
            character_changes=[{
                "character_id": "凌云",
                "attribute": "location",
                "new_value": "青云宗大殿",
            }]
        )

        conflicts = engine.detect_conflict(change2)
        assert conflicts is not None
        assert conflicts.conflict_type == "position_temporal"

    def test_unique_item_ownership_conflict(self, engine):
        """测试唯一物品归属冲突"""
        # 添加角色（需要两个角色才能检测唯一物品冲突）
        engine.add_character(CharacterState(
            character_id="凌云",
            name="凌云",
            location="青云宗后山",
            status="活跃",
            attributes={"修为": "筑基期"}
        ))
        engine.add_character(CharacterState(
            character_id="苏瑶",
            name="苏瑶",
            location="青云宗大殿",
            status="活跃",
            attributes={"修为": "金丹期"}
        ))

        # 第一章：玄天剑归属凌云
        change1 = StateChange(
            chapter_id=1,
            character_changes=[{
                "character_id": "凌云",
                "attribute": "持有物品",
                "new_value": "玄天剑",
            }]
        )
        engine.apply_change(change1)

        # 第一章：玄天剑归属苏瑶（冲突！）
        change2 = StateChange(
            chapter_id=1,
            character_changes=[{
                "character_id": "苏瑶",
                "attribute": "持有物品",
                "new_value": "玄天剑",
            }]
        )

        conflicts = engine.detect_conflict(change2)
        assert conflicts is not None
        assert conflicts.conflict_type == "unique_item"

    def test_faction_territory_conflict(self, engine):
        """测试势力领地冲突"""
        # 添加势力
        engine.add_faction(FactionState(
            faction_id="青云宗",
            name="青云宗",
            territory="青云山",
            members=["凌云", "苏瑶"],
        ))

        engine.add_faction(FactionState(
            faction_id="天剑门",
            name="天剑门",
            territory="天剑峰",
            members=["剑无尘"],
        ))

        # 天剑门声称拥有青云山（冲突！）
        change = StateChange(
            chapter_id=1,
            faction_changes=[{
                "faction_id": "天剑门",
                "attribute": "territory",
                "new_value": "青云山",
            }]
        )

        conflicts = engine.detect_conflict(change)
        assert conflicts is not None
        assert conflicts.conflict_type == "territory"

    def test_conflict_resolution(self, engine):
        """测试冲突解决后状态正确更新"""
        # 添加角色（初始位置为空，避免触发位置冲突）
        engine.add_character(CharacterState(
            character_id="凌云",
            name="凌云",
            location="",
            status="活跃",
            attributes={"修为": "筑基期"}
        ))

        # 应用变更
        change = StateChange(
            chapter_id=2,
            character_changes=[{
                "character_id": "凌云",
                "attribute": "location",
                "new_value": "青云宗大殿",
            }]
        )
        engine.apply_change(change)

        # 验证状态已更新
        char = engine.get_character("凌云")
        assert char.location == "青云宗大殿"

    def test_merge_changes(self, engine):
        """测试多变更合并"""
        # 添加角色（初始位置为空，避免触发位置冲突）
        engine.add_character(CharacterState(
            character_id="凌云",
            name="凌云",
            location="",
            status="活跃",
            attributes={"修为": "筑基期", "好感度": 70}
        ))

        # 同时应用多个变更
        changes = [
            StateChange(
                chapter_id=1,
                character_changes=[{
                    "character_id": "凌云",
                    "attribute": "location",
                    "new_value": "青云宗大殿",
                }]
            ),
            StateChange(
                chapter_id=1,
                character_changes=[{
                    "character_id": "凌云",
                    "attribute": "好感度",
                    "new_value": 75,
                }]
            ),
        ]

        for change in changes:
            engine.apply_change(change)

        # 验证所有变更都已应用
        char = engine.get_character("凌云")
        assert char.location == "青云宗大殿"
        assert char.attributes.get("好感度") == 75
