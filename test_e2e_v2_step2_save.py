"""端到端测试 v2 - Step 2 save: 保存生成的角色到 spec"""
import json
import urllib.request

# Use the exact characters from generation result
req_data = {
    "characters": [
        {
            "name": "钱开凤",
            "gender": "女",
            "role_type": "主角",
            "identity": "原是现代都市的普通妻子，与丈夫宋霄因教育小孩吵架时被陨石击中穿越到修仙世界，附身于因被催婚自尽的千机门千金身上。",
            "personality": "倔强聪慧、果决冷静、深爱丈夫、现代女性的独立意识与古代闺秀的身份形成强烈反差。",
            "initial_power": "无修为（刚刚苏醒，附身于刚自尽未遂的千机门千金）",
            "background": "千机门门主之女，因不愿被安排婚姻、反抗催婚而自尽，宋霄与钱开凤的灵魂同时附身其上时，她正躺在密闭的棺椁中缺氧拍打内壁。",
            "fate": "苏醒后发现自己与丈夫身处异世界且身份错位，必须一边求生、一边寻找丈夫、揭开穿越之谜。",
        },
        {
            "name": "宋霄",
            "gender": "男",
            "role_type": "主角",
            "identity": "原是现代都市的普通丈夫，与妻子钱开凤因教育小孩吵架时被陨石击中穿越到修仙世界，附身于穷困潦倒的赶尸匠身上。",
            "personality": "稳重机警、悲天悯人、重情重义、坚韧不拔；现代人的吐槽与修仙底层散修的身份格格不入。",
            "initial_power": "炼气初期（赶尸匠身份能驱使低阶僵尸，但自身修为极低）",
            "background": "穷困潦倒的赶尸匠，本就游走于正邪边缘；穿越时正路过一队大户人家的送葬队伍，听到了棺椁中传来的敲击声。",
            "fate": "觉察到妻子的处境（棺椁中求救）后必须在送葬队伍的眼皮底下救人，同时还要应对自己身份背后的秘密。",
        },
        {
            "name": "墨羽",
            "gender": "男",
            "role_type": "重要配角",
            "identity": "青云宗长老，主管外门事务与世家交涉",
            "personality": "正直刚正、重视规矩、略显保守",
            "initial_power": "金丹初期",
            "background": "青云宗外门长老，长期负责与各大世家打交道；为调查近期多起灵脉枯竭事件下山，恰好撞上宋霄和送葬队伍。",
            "fate": "成为宋霄在修仙世界的引路人之一，但因宗派立场和保守作风而多次与宋霄产生冲突。",
        },
        {
            "name": "幽冥使者·冷无涯",
            "gender": "男",
            "role_type": "反派",
            "identity": "幽冥殿使者，专职为殿主搜罗'阴年阴月阴日'出生的奇女子作为祭品",
            "personality": "神秘莫测、冷酷无情、不择手段",
            "initial_power": "金丹中期",
            "background": "幽冥殿殿主心腹，此次亲自出马就是为了掳走钱开凤（原主）作为祭品；送葬队伍正是他所布下的局。",
            "fate": "与宋霄、钱开凤形成宿敌；他的存在直接推动了千机门千金自尽事件和后续穿越冲突。",
        },
    ]
}

url = "http://localhost:5000/api/characters/save"
req = urllib.request.Request(
    url,
    data=json.dumps(req_data, ensure_ascii=False).encode('utf-8'),
    headers={"Content-Type": "application/json; charset=utf-8"},
    method="POST"
)
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode('utf-8'))
        print(json.dumps(result, ensure_ascii=False, indent=2))
except Exception as e:
    print(f"Error: {e}")
