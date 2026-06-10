"""文件管理器 - 统一的文件读写接口，支持 JSON/JSONL/Markdown"""

import json
import os
from typing import Any, Dict, List, Optional


class FileManager:
    def __init__(self, config_paths):
        self.root = config_paths.project_root
        self.context_dir = self._resolve(config_paths.context_dir)
        self.output_dir = self._resolve(config_paths.output_dir)
        self.states_dir = self._resolve(config_paths.states_dir)

    def _resolve(self, path: str) -> str:
        if os.path.isabs(path):
            return path
        return os.path.join(self.root, path)

    def _ensure_dir(self, filepath: str):
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

    # ---- JSON ----
    def read_json(self, filepath: str) -> Dict:
        with open(self._resolve(filepath), 'r', encoding='utf-8') as f:
            return json.load(f)

    def write_json(self, filepath: str, data: Any, indent: int = 2):
        path = self._resolve(filepath)
        self._ensure_dir(path)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=indent)

    # ---- JSONL ----
    def read_jsonl(self, filepath: str) -> List[Dict]:
        result = []
        with open(self._resolve(filepath), 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    result.append(json.loads(line))
        return result

    def write_jsonl(self, filepath: str, records: List[Dict]):
        path = self._resolve(filepath)
        self._ensure_dir(path)
        with open(path, 'w', encoding='utf-8') as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + '\n')

    def append_jsonl(self, filepath: str, record: Dict):
        path = self._resolve(filepath)
        self._ensure_dir(path)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')

    # ---- Markdown ----
    def read_markdown(self, filepath: str) -> str:
        with open(self._resolve(filepath), 'r', encoding='utf-8') as f:
            return f.read()

    def write_markdown(self, filepath: str, content: str):
        path = self._resolve(filepath)
        self._ensure_dir(path)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

    # ---- Generic ----
    def exists(self, filepath: str) -> bool:
        return os.path.exists(self._resolve(filepath))

    def read_text(self, filepath: str, encoding: str = 'utf-8') -> str:
        with open(self._resolve(filepath), 'r', encoding=encoding) as f:
            return f.read()

    def write_text(self, filepath: str, content: str, encoding: str = 'utf-8'):
        path = self._resolve(filepath)
        self._ensure_dir(path)
        with open(path, 'w', encoding=encoding) as f:
            f.write(content)

    # ---- Context files ----
    def read_specification(self) -> str:
        return self.read_markdown(os.path.join(self.context_dir, "specification.md"))

    def read_style_guide(self) -> str:
        path = os.path.join(self.context_dir, "style-guide.md")
        if self.exists(path):
            return self.read_markdown(path)
        return ""

    def read_active_skills(self) -> str:
        """读取当前激活的 SKILL.md 文件内容（拼接）"""
        try:
            from src.core.skill_manager import SkillManager
        except ImportError:
            return ""
        sm = SkillManager(self.context_dir)
        return sm.get_active_content()

    # ---- Output paths ----
    def get_output_dir(self) -> str:
        """获取输出目录路径"""
        return self.output_dir

    def get_draft_dir(self) -> str:
        """获取草稿目录路径"""
        return os.path.join(self.output_dir, "draft")

    def outline_path(self) -> str:
        return os.path.join(self.output_dir, "outline.json")

    def volumes_path(self) -> str:
        return os.path.join(self.output_dir, "volumes.jsonl")

    def chapters_path(self) -> str:
        return os.path.join(self.output_dir, "chapters.jsonl")

    def draft_path(self, chapter_id: int) -> str:
        return os.path.join(self.output_dir, "draft", f"{chapter_id:04d}.md")

    def tracking_path(self, name: str) -> str:
        return os.path.join(self.output_dir, "tracking", name)

    def final_path(self, chapter_id: int) -> str:
        return os.path.join(self.output_dir, "final", f"{chapter_id:04d}.md")

    # ---- State files ----
    def state_file_path(self, state_file: str) -> str:
        return self._resolve(state_file)
