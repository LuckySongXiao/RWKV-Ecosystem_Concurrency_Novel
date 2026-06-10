"""测试 SKILL API 和新的 spec fields"""
import requests

BASE = 'http://localhost:5000'

# 1. 测试 SKILL 列表
r = requests.get(f'{BASE}/api/skills')
print(f'GET /api/skills: {r.status_code}, {r.json()}')

# 2. 创建 SKILL
r = requests.post(f'{BASE}/api/skills', json={
    'name': 'wuxia-classic',
    'content': '''---
name: wuxia-classic
description: 经典金庸武侠风格
---

# 经典武侠

## 叙事特点
- 章回体结构
- 武功招数描写细腻
- 江湖义气

## 禁忌
- 不要出现现代术语
'''
})
print(f'POST /api/skills: {r.status_code}, {r.json()}')

# 3. 获取刚创建的
r = requests.get(f'{BASE}/api/skills/wuxia-classic.md')
print(f'GET /api/skills/wuxia-classic.md: {r.status_code}, has content: {len(r.json().get("content", "")) > 0}')

# 4. 激活
r = requests.post(f'{BASE}/api/skills/active', json={'names': ['wuxia-classic.md']})
print(f'POST /api/skills/active: {r.status_code}, {r.json()}')

# 5. 列出确认
r = requests.get(f'{BASE}/api/skills')
d = r.json()
print(f'After activation: skills={len(d["skills"])}, active={d["active"]}')

# 6. 测试 spec/fields 包含 background
r = requests.get(f'{BASE}/api/context/spec/fields')
fields = r.json()
keys = [f['key'] for f in fields]
print(f'spec field keys: {keys}')
print(f'has background: {"background" in keys}')

# 7. 清理：删除测试 SKILL
r = requests.delete(f'{BASE}/api/skills/wuxia-classic.md')
print(f'DELETE: {r.json()}')
