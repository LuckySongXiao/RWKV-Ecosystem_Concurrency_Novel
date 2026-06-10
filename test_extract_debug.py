import sys
sys.path.insert(0, 'e:/RWKV_生态_并发式小说')
from src.core.character_import import _NAME_PATTERNS, _CN_SURNAMES, extract_character_names

# 模拟 API 实际 text_to_scan（多字段拼接）
text = (
    "灵气枯竭、修真界动荡不安的末法时代。一颗来自天外的陨石击中了现代都市的一对普通夫妻的卧室，两人同时穿越到这个修仙世界。"
    " 修仙者依靠吸纳天地灵气突破境界，当世灵气枯竭，修仙资源紧缺。"
    " 末法时代的资源争夺、宋霄与钱开凤的生存困境、穿越的真相"
    " 宋霄和钱开凤是一对夫妻，在家因为教育小孩吵得不可开交。"
    "一颗陨石在猝不及防之下撞上了处于楼顶夹层的卧室中。"
    "两个人整整齐齐的穿越了。"
    "宋霄醒来发现自己成为了穷困潦倒的赶尸匠，正路过一队送葬队伍。"
    "钱开凤醒来发现自己成为了因被催婚自尽的千机门千金，此刻正躺在一个密闭的长方体之中。"
)

print("Direct pattern match debug (full text):")
for i, pat in enumerate(_NAME_PATTERNS):
    print(f"\nPattern {i}: {pat.pattern}")
    for m in pat.finditer(text):
        for g in m.groups():
            if g and len(g) >= 2 and len(g) <= 4:
                first_char = g[0]
                is_surname = first_char in _CN_SURNAMES
                print(f"  Match: '{g}' (first='{first_char}', is_surname={is_surname})")

print("\nFinal extraction:")
names = extract_character_names(text)
for n in names:
    print(f"  - {n['name']}")
