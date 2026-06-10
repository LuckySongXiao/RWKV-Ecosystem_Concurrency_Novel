"""Step 2: AI 自动生成角色 - 验证是否保留已有主角名"""
import json
import urllib.request

req_data = {
    "genre": "仙侠",
    "count": 4,
    "spec_map": {
        "genre": "仙侠",
        "background": (
            "灵气枯竭、修真界动荡不安的末法时代。"
            "一颗来自天外的陨石击中了现代都市的一对普通夫妻的卧室，两人同时穿越到这个修仙世界。"
        ),
        "world_law": "修仙者依靠吸纳天地灵气突破境界，当世灵气枯竭，修仙资源紧缺。",
        "cultivation_system": "炼气→筑基→金丹→元婴→化神",
        "faction_pattern": "正邪两道对峙，千机门/青云宗/太虚宫/幽冥殿/血煞宗等",
        "core_conflict": "末法时代的资源争夺、宋霄与钱开凤的生存困境、穿越的真相",
        "storyline": (
            "宋霄和钱开凤是一对夫妻，在家因为教育小孩吵得不可开交。"
            "一颗陨石在猝不及防之下撞上了处于楼顶夹层的卧室中。"
            "两个人整整齐齐的穿越了。"
            "宋霄醒来发现自己成为了穷困潦倒的赶尸匠，正路过一队送葬队伍。"
            "钱开凤醒来发现自己成为了因被催婚自尽的千机门千金，此刻正躺在一个密闭的长方体之中。"
        ),
    }
}

url = "http://localhost:5000/api/characters/generate"
req = urllib.request.Request(
    url,
    data=json.dumps(req_data, ensure_ascii=False).encode('utf-8'),
    headers={"Content-Type": "application/json; charset=utf-8"},
    method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=180) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        print("Status:", result.get("status"))
        print("Count:", result.get("count"))
        print("Seed names extracted from storyline:", result.get("seed_names"))
        print()
        print("Generated characters:")
        for c in result.get("characters", []):
            print(f"  - {c.get('name')} ({c.get('gender')}) - {c.get('role_type')}")
            print(f"    Identity: {c.get('identity', '')[:60]}")
            print(f"    Personality: {c.get('personality', '')[:60]}")
            print(f"    Initial power: {c.get('initial_power', '')[:60]}")
            print()
except Exception as e:
    print(f"Error: {e}")
