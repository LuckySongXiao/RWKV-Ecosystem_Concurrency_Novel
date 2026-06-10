import sys
sys.path.insert(0, 'e:/RWKV_生态_并发式小说')

# 通过 HTTP 调用实际接口
import urllib.request
import json

body = json.dumps({
    "genre": "仙侠",
    "count": 4,
    "spec_map": {
        "background": "灵气枯竭的时代",
        "storyline": "宋霄和钱开凤是一对夫妻，因为教育小孩吵得不可开交。一颗陨石撞上卧室，两个人穿越了。宋霄醒来发现自己是赶尸匠。钱开凤醒来发现自己是千机门千金。"
    }
}, ensure_ascii=False).encode('utf-8')

print(f"Body bytes: {len(body)}")
print(f"Body content: {body[:200].decode('utf-8')}")

req = urllib.request.Request(
    "http://localhost:5000/api/characters/generate",
    data=body,
    headers={"Content-Type": "application/json; charset=utf-8"},
    method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        print(f"\nseed_names: {result.get('seed_names', [])}")
        print(f"character names: {[c.get('name') for c in result.get('characters', [])]}")
except Exception as e:
    print(f"Error: {e}")
