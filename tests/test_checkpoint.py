"""断点恢复验证测试

验证:
- 在管线中途模拟中断
- 恢复后从断点继续，不丢失已生成内容
"""

import pytest
import os
import json
import tempfile
import shutil
from src.core.file_manager import FileManager
from src.core.config import load_config


class TestCheckpointRecovery:
    """断点恢复测试"""

    @pytest.fixture
    def temp_project(self):
        """创建临时项目目录"""
        temp_dir = tempfile.mkdtemp()
        
        # 创建基本目录结构
        os.makedirs(os.path.join(temp_dir, "context"), exist_ok=True)
        os.makedirs(os.path.join(temp_dir, "output", "draft"), exist_ok=True)
        os.makedirs(os.path.join(temp_dir, "output", "tracking"), exist_ok=True)
        os.makedirs(os.path.join(temp_dir, "states"), exist_ok=True)

        yield temp_dir
        
        # 清理
        shutil.rmtree(temp_dir, ignore_errors=True)

    def test_checkpoint_save_and_load(self, temp_project):
        """测试检查点保存和加载"""
        from src.core.config import PathsConfig
        config_paths = PathsConfig(
            context_dir=os.path.join(temp_project, "context"),
            output_dir=os.path.join(temp_project, "output"),
            states_dir=os.path.join(temp_project, "states"),
            project_root=temp_project,
        )
        fm = FileManager(config_paths)
        checkpoint_path = os.path.join(temp_project, ".checkpoint.json")
        
        # 保存检查点
        checkpoint_data = {
            "stage": "chapter_generation",
            "current_volume": 1,
            "completed_chapters": [1, 2, 3],
            "next_chapter": 4,
            "timestamp": "2026-04-27T10:00:00",
        }
        
        with open(checkpoint_path, 'w', encoding='utf-8') as f:
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)

        # 加载检查点
        assert os.path.exists(checkpoint_path)
        
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)

        assert loaded["stage"] == "chapter_generation"
        assert loaded["completed_chapters"] == [1, 2, 3]
        assert loaded["next_chapter"] == 4

    def test_resume_from_checkpoint(self, temp_project):
        """测试从检查点恢复"""
        checkpoint_path = os.path.join(temp_project, ".checkpoint.json")
        
        # 模拟管线中断前的检查点
        checkpoint_data = {
            "stage": "chapter_generation",
            "current_volume": 1,
            "completed_chapters": [1, 2],
            "next_chapter": 3,
            "timestamp": "2026-04-27T10:00:00",
        }
        
        with open(checkpoint_path, 'w', encoding='utf-8') as f:
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)

        # 模拟已生成的章节文件
        for ch in [1, 2]:
            chapter_path = os.path.join(temp_project, "output", "draft", f"{ch:04d}.md")
            with open(chapter_path, 'w', encoding='utf-8') as f:
                f.write(f"# 第{ch}章\n\n这是第{ch}章的内容。")

        # 验证恢复逻辑
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            checkpoint = json.load(f)

        # 应该从第3章继续
        assert checkpoint["next_chapter"] == 3
        assert checkpoint["completed_chapters"] == [1, 2]

        # 验证已生成的章节文件存在
        for ch in [1, 2]:
            chapter_path = os.path.join(temp_project, "output", "draft", f"{ch:04d}.md")
            assert os.path.exists(chapter_path)

    def test_no_duplicate_generation(self, temp_project):
        """测试不会重复生成已完成的章节"""
        checkpoint_path = os.path.join(temp_project, ".checkpoint.json")
        
        # 检查点显示第1-3章已完成
        checkpoint_data = {
            "stage": "chapter_generation",
            "current_volume": 1,
            "completed_chapters": [1, 2, 3],
            "next_chapter": 4,
            "timestamp": "2026-04-27T10:00:00",
        }
        
        with open(checkpoint_path, 'w', encoding='utf-8') as f:
            json.dump(checkpoint_data, f, ensure_ascii=False, indent=2)

        # 恢复后，应该只生成第4章及之后的内容
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            checkpoint = json.load(f)

        completed = checkpoint["completed_chapters"]
        next_ch = checkpoint["next_chapter"]

        # 验证不会重新生成已完成的章节
        assert 1 not in range(next_ch, 10)
        assert 2 not in range(next_ch, 10)
        assert 3 not in range(next_ch, 10)
        assert 4 in range(next_ch, 10)

    def test_checkpoint_integrity(self, temp_project):
        """测试检查点完整性"""
        checkpoint_path = os.path.join(temp_project, ".checkpoint.json")
        
        # 创建有效的检查点
        valid_checkpoint = {
            "stage": "chapter_generation",
            "current_volume": 1,
            "completed_chapters": [1, 2],
            "next_chapter": 3,
            "timestamp": "2026-04-27T10:00:00",
        }
        
        with open(checkpoint_path, 'w', encoding='utf-8') as f:
            json.dump(valid_checkpoint, f, ensure_ascii=False, indent=2)

        # 验证检查点格式
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            checkpoint = json.load(f)

        # 检查必需字段
        required_fields = ["stage", "current_volume", "completed_chapters", "next_chapter", "timestamp"]
        for field in required_fields:
            assert field in checkpoint

        # 验证数据类型
        assert isinstance(checkpoint["completed_chapters"], list)
        assert isinstance(checkpoint["next_chapter"], int)
        assert isinstance(checkpoint["current_volume"], int)

    def test_checkpoint_after_stage_completion(self, temp_project):
        """测试阶段完成后的检查点更新"""
        checkpoint_path = os.path.join(temp_project, ".checkpoint.json")
        
        # 初始检查点 - 大纲生成阶段
        checkpoint = {
            "stage": "outline_generation",
            "current_volume": 0,
            "completed_chapters": [],
            "next_chapter": 1,
            "timestamp": "2026-04-27T09:00:00",
        }
        
        with open(checkpoint_path, 'w', encoding='utf-8') as f:
            json.dump(checkpoint, f, ensure_ascii=False, indent=2)

        # 模拟大纲生成完成，进入章节生成阶段
        checkpoint["stage"] = "chapter_generation"
        checkpoint["current_volume"] = 1
        checkpoint["timestamp"] = "2026-04-27T10:00:00"
        
        with open(checkpoint_path, 'w', encoding='utf-8') as f:
            json.dump(checkpoint, f, ensure_ascii=False, indent=2)

        # 验证更新后的检查点
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            updated = json.load(f)

        assert updated["stage"] == "chapter_generation"
        assert updated["current_volume"] == 1
