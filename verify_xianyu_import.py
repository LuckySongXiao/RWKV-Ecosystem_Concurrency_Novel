"""验证 xianyu-laobai-skill 技能包导入状态"""
import requests
import json

BASE = 'http://localhost:5000'

# 1. 列出所有 SKILL
r = requests.get(f'{BASE}/api/skills')
d = r.json()
print(f'[1] Total skills: {len(d["skills"])}')
print(f'    Active: {d["active"]}')
print()
for s in d['skills']:
    desc = s.get('description') or '(no description)'
    print(f'  - {s["name"]:<30s}  {round(s["size"]/1024, 1):>6.1f} KB  desc: {desc[:60]}')

# 2. 验证可读取每个
print()
print('[2] Read each skill first 80 chars:')
for s in d['skills']:
    rr = requests.get(f'{BASE}/api/skills/{s["name"]}')
    if rr.status_code == 200:
        c = rr.json().get('content', '')
        first_line = c.strip().split('\n')[0] if c else '(empty)'
        print(f'  - {s["name"]}: {first_line[:80]}')
    else:
        print(f'  - {s["name"]}: ERROR {rr.status_code}')

# 3. 验证激活列表 .active.json
print()
import os
af = r'e:\RWKV_生态_并发式小说\context\skills\.active.json'
with open(af, 'r', encoding='utf-8') as f:
    active = json.load(f)
print(f'[3] .active.json contents: {active}')

# 4. 验证 prompt builder 能读到内容
print()
print('[4] Skill content size summary:')
for s in d['skills']:
    rr = requests.get(f'{BASE}/api/skills/{s["name"]}')
    if rr.status_code == 200:
        c = rr.json().get('content', '')
        print(f'  - {s["name"]}: {len(c):>6d} chars')
