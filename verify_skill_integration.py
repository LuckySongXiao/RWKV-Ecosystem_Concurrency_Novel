"""验证 prompt 构建阶段能正确加载 SKILL 内容"""
import sys
import os
sys.path.insert(0, r'e:\RWKV_生态_并发式小说')

from src.core.file_manager import FileManager
from src.core.config import load_config

# 1. 通过 FileManager 读取激活的 SKILL
cfg = load_config(r'e:\RWKV_生态_并发式小说\pipeline.config.json')
fm = FileManager(cfg.paths)
print('[1] Active SKILL content via FileManager:')
content = fm.read_active_skills()
print(f'   Total length: {len(content)} chars')
print(f'   Starts with: {content[:100].strip()}')
print(f'   Ends with:   ...{content[-100:].strip()}')
print()

# 2. 列出所有激活的 SKILL
print('[2] Activated SKILL files:')
from src.core.skill_manager import SkillManager
sm = SkillManager(fm.context_dir)
print(f'   Active names: {sm.get_active_skill_names()}')
print(f'   Skills dir:   {sm.skills_dir}')
print()

# 3. 模拟 prompt_builder 集成
print('[3] Sample of integrated content (first 1500 chars):')
print('-' * 60)
print(content[:1500])
print('-' * 60)
