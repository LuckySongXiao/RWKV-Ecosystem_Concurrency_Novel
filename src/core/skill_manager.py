"""SKILL.md 写作技能文件管理器

支持多份 SKILL 文件，每份可独立激活/停用。
激活的 SKILL 会在 prompt 构建阶段被引用，遵循 Anthropic SKILL.md 规范。

存储路径: <context_dir>/skills/*.md
激活状态: <context_dir>/skills/.active.json (列表)
"""
import json
import os
import re
import shutil
from typing import List, Dict, Optional, Tuple


class SkillManager:
    """SKILL.md 文件管理器

    每个 SKILL.md 文件的格式遵循：
    ---
    name: skill-name
    description: 简短描述（用于展示和 LLM 引用）
    ---

    # 技能正文
    ...
    """

    def __init__(self, context_dir: str):
        self.context_dir = context_dir
        self.skills_dir = os.path.join(context_dir, "skills")
        self.active_file = os.path.join(self.skills_dir, ".active.json")
        os.makedirs(self.skills_dir, exist_ok=True)

    # ============== 文件管理 ==============
    def list_skills(self) -> List[Dict]:
        """列出所有 SKILL 文件

        Returns:
            [{'name': 'wuxia.md', 'display_name': 'wuxia', 'description': '...', 'size': 1024, 'modified': '2024-...'}, ...]
        """
        skills = []
        if not os.path.isdir(self.skills_dir):
            return skills
        for fname in sorted(os.listdir(self.skills_dir)):
            if not fname.lower().endswith(".md"):
                continue
            if fname.startswith("."):
                continue
            fpath = os.path.join(self.skills_dir, fname)
            try:
                stat = os.stat(fpath)
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                meta = self._parse_frontmatter(content)
                skills.append({
                    "name": fname,
                    "display_name": fname[:-3],
                    "title": meta.get("name", fname[:-3]),
                    "description": meta.get("description", ""),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                })
            except Exception:
                continue
        return skills

    def read_skill(self, name: str) -> Optional[str]:
        """读取 SKILL 文件全文"""
        fpath = self._safe_path(name)
        if fpath is None or not os.path.isfile(fpath):
            return None
        with open(fpath, "r", encoding="utf-8") as f:
            return f.read()

    def save_skill(self, name: str, content: str) -> bool:
        """保存 SKILL 文件"""
        fpath = self._safe_path(name)
        if fpath is None:
            return False
        try:
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception:
            return False

    def delete_skill(self, name: str) -> bool:
        """删除 SKILL 文件"""
        fpath = self._safe_path(name)
        if fpath is None or not os.path.isfile(fpath):
            return False
        try:
            os.remove(fpath)
            # 同时清理激活列表
            active = self.get_active_skill_names()
            if name in active:
                self.set_active([n for n in active if n != name])
            return True
        except Exception:
            return False

    # ============== 激活状态管理 ==============
    def get_active_skill_names(self) -> List[str]:
        """获取激活的 SKILL 文件名列表"""
        if not os.path.isfile(self.active_file):
            return []
        try:
            with open(self.active_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def set_active(self, names: List[str]) -> None:
        """设置激活的 SKILL 列表"""
        if not isinstance(names, list):
            names = []
        cleaned = []
        for n in names:
            if isinstance(n, str):
                if not n.lower().endswith(".md"):
                    n = n + ".md"
                cleaned.append(n)
        with open(self.active_file, "w", encoding="utf-8") as f:
            json.dump(cleaned, f, ensure_ascii=False, indent=2)

    # ============== 引用整合 ==============
    def get_active_content(self) -> str:
        """读取所有激活的 SKILL 全文，拼接返回

        格式：每个文件用 # 标题分隔，方便注入到 system prompt。
        """
        active = self.get_active_skill_names()
        if not active:
            return ""

        parts = []
        for name in active:
            content = self.read_skill(name)
            if not content:
                continue
            display = name[:-3] if name.lower().endswith(".md") else name
            parts.append(f"## SKILL: {display}\n\n{content.strip()}\n")
        return "\n---\n\n".join(parts)

    def get_active_summaries(self) -> List[Dict]:
        """获取激活 SKILL 的元数据（name + description）"""
        active = self.get_active_skill_names()
        summaries = []
        for name in active:
            content = self.read_skill(name)
            if not content:
                continue
            meta = self._parse_frontmatter(content)
            summaries.append({
                "name": name,
                "title": meta.get("name", name[:-3] if name.endswith(".md") else name),
                "description": meta.get("description", ""),
            })
        return summaries

    # ============== 内部工具 ==============
    def _parse_frontmatter(self, content: str) -> Dict:
        """解析 SKILL.md 顶部的 YAML frontmatter（简易版）"""
        meta = {}
        m = re.match(r"^---\s*\n(.+?)\n---\s*\n", content, re.DOTALL)
        if not m:
            return meta
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"').strip("'")
        return meta

    def _safe_path(self, name: str) -> Optional[str]:
        """安全路径校验：禁止跳出 skills_dir"""
        if not name or "/" in name or "\\" in name or ".." in name:
            return None
        if not name.lower().endswith(".md"):
            name = name + ".md"
        return os.path.join(self.skills_dir, name)

    # ============== 技能包导入 ==============
    def import_pack(self, source_dir: str, overwrite: bool = False,
                    activate: bool = True) -> Dict:
        """从外部目录导入一个技能包

        将 source_dir 下的所有 .md 文件复制到 skills_dir，并自动激活。

        Args:
            source_dir: 源目录（包含 .md 文件）
            overwrite:   是否覆盖同名已存在文件
            activate:    导入后是否自动激活

        Returns:
            {
                "imported":  ["a.md", "b.md", ...],  # 成功导入的文件名
                "skipped":   ["c.md", ...],           # 跳过（已存在未覆盖）
                "errors":    ["d.md: 原因", ...],     # 失败的
                "active":    ["a.md", "b.md", ...],   # 当前激活列表
                "source":    "...",
                "dest":      "...",
            }
        """
        result = {
            "imported": [],
            "skipped": [],
            "errors": [],
            "active": [],
            "source": source_dir,
            "dest": self.skills_dir,
        }
        if not source_dir or not os.path.isdir(source_dir):
            result["errors"].append(f"源目录不存在或不是目录: {source_dir}")
            return result

        try:
            md_files = sorted(
                f for f in os.listdir(source_dir)
                if f.lower().endswith(".md") and not f.startswith(".")
            )
        except Exception as e:
            result["errors"].append(f"读取源目录失败: {e}")
            return result

        for fname in md_files:
            src_path = os.path.join(source_dir, fname)
            if not os.path.isfile(src_path):
                continue
            dst_path = os.path.join(self.skills_dir, fname)
            try:
                if os.path.isfile(dst_path) and not overwrite:
                    result["skipped"].append(fname)
                    continue
                shutil.copy2(src_path, dst_path)
                result["imported"].append(fname)
            except Exception as e:
                result["errors"].append(f"{fname}: {e}")

        # 自动激活新导入的
        if activate and result["imported"]:
            active = self.get_active_skill_names()
            for name in result["imported"]:
                if name not in active:
                    active.append(name)
            self.set_active(active)
        result["active"] = self.get_active_skill_names()
        return result
