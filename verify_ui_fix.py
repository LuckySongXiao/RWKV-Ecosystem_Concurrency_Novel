"""验证 UI 修复 - 标签页 + 大纲/故事主线显示"""
import requests
import json
import sys
import io

# 强制 UTF-8 输出
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE = 'http://localhost:5000'
OK = '[OK]'
BAD = '[X]'

print('=' * 70)
print('[1] 检查 /create 页面是否包含新标签')
r = requests.get(f'{BASE}/create')
html = r.text
checks = [
    ('📝 创作设定 tab', '创作设定' in html),
    ('👥 角色表格 tab', '角色表格' in html),
    ('🧩 SKILL 配置 tab', 'SKILL 配置' in html),
    ('✍️ 写作风格 tab', '写作风格' in html),
    ('⚡ 优化版管线 tab', '优化版管线' in html),
    ('🚀 全自动管线 tab', '全自动管线' in html),
    ('📖 大纲/主线 tab', '大纲/主线' in html),
    ('tab-characters 容器', 'id="tab-characters"' in html),
    ('tab-skills 容器', 'id="tab-skills"' in html),
    ('tab-outline 容器', 'id="tab-outline"' in html),
    ('switchOutlineView 函数', 'switchOutlineView' in html),
    ('renderStoryline 函数', 'renderStoryline' in html),
    ('renderFullOutline 函数', 'renderFullOutline' in html),
    ('renderCharactersJson 函数', 'renderCharactersJson' in html),
    ('loadStoryline 函数', 'loadStoryline' in html),
]
for name, ok in checks:
    print(f'   {OK if ok else BAD} {name}')

print()
print('=' * 70)
print('[2] 检查 API 端点')
apis = [
    '/api/output/main_storyline',
    '/api/output/full_outline',
    '/api/output/storyline',
    '/api/output/characters',
    '/api/output/outline',
]
for api in apis:
    try:
        r = requests.get(f'{BASE}{api}')
        ok = r.status_code == 200
        sz = len(r.text)
        print(f'   {OK if ok else BAD} GET {api} -> {r.status_code} ({sz} bytes)')
    except Exception as e:
        print(f'   {BAD} {api} -> {e}')

print()
print('=' * 70)
print('[3] 检查 main_storyline.json 内容')
r = requests.get(f'{BASE}/api/output/main_storyline')
d = r.json()
if d.get('exists') is False:
    print('   [!] main_storyline.json 不存在 (尚未生成) - 这正常')
else:
    print(f'   {OK} 返回了 {len(json.dumps(d, ensure_ascii=False))} 字节')
    print(f'   {OK} 字段: title={d.get("title")}, stages={len(d.get("stages", []))}')

print()
print('=' * 70)
print('[4] 检查 full_outline.json 内容')
r = requests.get(f'{BASE}/api/output/full_outline')
d = r.json()
if d.get('exists') is False:
    print('   [!] full_outline.json 不存在 (尚未生成) - 这正常')
else:
    print(f'   {OK} 返回了 {len(json.dumps(d, ensure_ascii=False))} 字节')
    print(f'   {OK} 字段: title={d.get("title")}, volumes={len(d.get("volumes", []))}')

print()
print('=' * 70)
print('[5] 验证页面无 JS 语法错误（查找重复函数定义）')
import re
matches = re.findall(r'function renderOutline\(', html)
print(f'   renderOutline 出现 {len(matches)} 次 (期望 1) {OK if len(matches) == 1 else BAD}')
matches = re.findall(r'function toggleVolume\(', html)
print(f'   toggleVolume 出现 {len(matches)} 次 (期望 1) {OK if len(matches) == 1 else BAD}')
matches = re.findall(r'function loadStoryline\(', html)
print(f'   loadStoryline 出现 {len(matches)} 次 (期望 1) {OK if len(matches) == 1 else BAD}')
matches = re.findall(r'function renderCharTable\(', html)
print(f'   renderCharTable 出现 {len(matches)} 次 (期望 1) {OK if len(matches) == 1 else BAD}')

print()
print('=' * 70)
print('验证完成！')
