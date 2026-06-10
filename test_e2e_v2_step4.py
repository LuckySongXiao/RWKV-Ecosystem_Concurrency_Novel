"""端到端测试 v2 - Step 4: 启动全自动流水线，生成第 1 章"""
import json
import urllib.request
import time

req_data = {
    "theme": "仙侠",
    "character_count": 4,
    "protagonist_names": ["宋霄", "钱开凤"],
    "antagonist_names": ["幽冥使者·冷无涯"],
    "volume_count": 2,
    "chapters_per_volume": 3,
    "slices_per_chapter": 5,
    "extra_context": (
        "# 故事设定\n"
        "宋霄和钱开凤是一对夫妻，在家因为教育小孩吵得不可开交。一颗陨石在猝不及防之下撞上了处于楼顶夹层的卧室中。两个人整整齐齐的穿越了。\n"
        "宋霄醒来发现自己成为了穷困潦倒的赶尸匠，正路过一队送葬队伍，看规模应该是个大户人家，本来已经躲在一旁的他突然听到送葬队伍中的棺椁里传来了一阵若有似无的声音。\n"
        "钱开凤醒来发现自己成为了因被催婚自尽的千机门千金，此刻正躺在一个密闭的长方体之中，因为逐渐缺氧，她正逐渐虚弱的拍打着内壁。\n\n"
        "# 主要角色\n"
        "- 宋霄（男，主角）：原是现代都市的普通丈夫，穿越后附身于穷困潦倒的赶尸匠，性格稳重机警。修为：炼气初期。\n"
        "- 钱开凤（女，主角）：原是现代都市的普通妻子，穿越后附身于因被催婚自尽的千机门千金。性格倔强聪慧。无修为。\n"
        "- 墨羽（男，配角）：青云宗外门长老，金丹初期，负责调查灵脉枯竭事件。\n"
        "- 幽冥使者·冷无涯（男，反派）：幽冥殿使者，金丹中期，专职为殿主搜罗'阴年阴月阴日'出生的奇女子作为祭品。送葬队伍正是他所布下的局。\n\n"
        "# 题材要求\n"
        "仙侠 / 穿越 / 重生 / 玄幻复仇 / 末法时代。\n"
        "基调：紧张刺激的现代灵魂与古代修仙世界的冲突。\n"
        "叙事：第三人称有限视角 + 主角内心独白，现代人的吐槽与修仙世界格格不入的对比感。\n"
    ),
    "concurrency_config": {
        "character_concurrency": 4,
        "outline_concurrency": 3,
        "chapter_concurrency": 4,
        "batch_size": 4,
    },
}

url = "http://localhost:5000/api/pipeline/optimized"
req = urllib.request.Request(
    url,
    data=json.dumps(req_data, ensure_ascii=False).encode('utf-8'),
    headers={"Content-Type": "application/json; charset=utf-8"},
    method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        print(json.dumps(result, ensure_ascii=False, indent=2))
except Exception as e:
    print(f"Error: {e}")
