"""端到端测试 - 设置创作设定（使用正确的 spec/fields API）"""
import json
import urllib.request

spec_data = {
    "fields": [
        {"key": "genre", "value": "仙侠"},
        {"key": "world_law", "value": (
            "修仙者依靠吸纳天地灵气突破境界，但当世灵气枯竭，修仙资源紧缺。"
            "散修生存艰难，正邪两道冲突不断。"
            "天材地宝几乎被几大宗门垄断，散修唯有另辟蹊径才能立足。"
        )},
        {"key": "background", "value": (
            "灵气枯竭、修真界动荡不安的末法时代。"
            "一颗来自天外的陨石击中了现代都市的一对普通夫妻的卧室，两人同时穿越到这个修仙世界。"
            "修真资源极度稀缺，各大宗门为争夺最后几条灵脉明争暗斗。"
        )},
        {"key": "cultivation_system", "value": (
            "炼气期（九层）→ 筑基期 → 金丹期 → 元婴期 → 化神期 → 炼虚期 → 合体期 → 大乘期 → 渡劫期。"
            "每个境界分为初期、中期、后期、大圆满四个小阶段。"
        )},
        {"key": "characters", "value": (
            "宋霄：男主，原是现代都市的普通丈夫，穿越后成为穷困潦倒的赶尸匠，性格稳重机警、深爱妻子。"
            "钱开凤：女主，原是现代都市的普通妻子，穿越后成为因被催婚自尽的千机门千金，性格倔强聪慧、深爱丈夫。"
        )},
        {"key": "faction_pattern", "value": (
            "正邪两道对峙。正道以'千机门''青云宗''太虚宫'三大宗门为首，"
            "另有诸多小门派和散修联盟。邪道以'幽冥殿''血煞宗'为主，"
            "行事诡秘残忍。正邪之间表面和平，实则暗流涌动。"
        )},
        {"key": "core_conflict", "value": (
            "末法时代的资源争夺、宋霄与钱开凤在异世界的生存困境、"
            "两人身份背后的秘密（宋霄赶尸匠身份、钱开凤千机门千金身份）、"
            "穿越的真相——陨石究竟是什么？"
        )},
        {"key": "storyline", "value": (
            "宋霄和钱开凤是一对夫妻，在家因为教育小孩吵得不可开交。"
            "一颗陨石在猝不及防之下撞上了处于楼顶夹层的卧室中。"
            "两个人整整齐齐的穿越了。\n"
            "宋霄醒来发现自己成为了穷困潦倒的赶尸匠，正路过一队送葬队伍，"
            "看规模应该是个大户人家，本来已经躲在一旁的他突然听到送葬队伍中的棺椁里传来了一阵若有似无的声音。\n"
            "钱开凤醒来发现自己成为了因被催婚自尽的千机门千金，此刻正躺在一个密闭的长方体之中，"
            "因为逐渐缺氧，她正逐渐虚弱的拍打着内壁。"
        )},
        {"key": "style", "value": (
            "第三人称有限视角 + 主角内心独白；"
            "细腻的环境描写 + 张力十足的情节推进；"
            "现代人的吐槽与修仙世界格格不入的对比感；"
            "动作场面干脆利落，心理描写细腻。"
        )},
    ]
}

url = "http://localhost:5000/api/context/spec/fields"
req = urllib.request.Request(
    url,
    data=json.dumps(spec_data, ensure_ascii=False).encode('utf-8'),
    headers={"Content-Type": "application/json; charset=utf-8"},
    method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=60) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        print(json.dumps(result, ensure_ascii=False, indent=2))
except Exception as e:
    print(f"Error: {e}")
