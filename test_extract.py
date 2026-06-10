import sys
sys.path.insert(0, 'e:/RWKV_生态_并发式小说')
from src.core.character_import import extract_character_names

# 这个文本对应 log 中 text_to_scan length=78 的内容
texts = [
    "宋霄和钱开凤是一对夫妻，因为教育小孩吵得不可开交。一颗陨石撞上卧室，两个人穿越了。宋霄醒来发现自己是赶尸匠。钱开凤醒来发现自己是千机门千金。",
    "灵气枯竭的时代 宋霄和钱开凤是一对夫妻，因为教育小孩吵得不可开交。一颗陨石撞上卧室，两个人穿越了。宋霄醒来发现自己是赶尸匠。钱开凤醒来发现自己是千机门千金。",
]
for i, t in enumerate(texts):
    print(f"=== Test {i+1}: len={len(t)} ===")
    print(t[:100])
    print("...")
    names = extract_character_names(t)
    print("Extracted:", [n['name'] for n in names])
    print()
