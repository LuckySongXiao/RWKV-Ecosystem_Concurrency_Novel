import sys
sys.path.insert(0, 'e:/RWKV_生态_并发式小说')
from src.core.json_utils import robust_json_parse

# Test 1: "角色: [...]" format
text1 = '角色: [{"name":"林枫","gender":"男","identity":"主角","personality":["沉稳","坚韧","善良"],"background":"曾是青云宗的外门弟子","initial_power":"筑基后期","role_type":"主角"},{"name":"苏婉儿","gender":"女","identity":"重要配角","personality":["聪慧","果敢"],"background":"青云宗内门弟子","initial_power":"金丹初期","role_type":"重要配角"}]'
print("=== Test 1: 角色: [...] ===")
print("Input length:", len(text1))
result, status = robust_json_parse(text1, first_only=True)
print("Status:", status)
print("Type:", type(result).__name__)
if isinstance(result, list):
    print("List length:", len(result))
    print("First item keys:", list(result[0].keys()) if result else "empty")
elif isinstance(result, dict):
    print("Dict keys:", list(result.keys()))

# Test 2: Direct JSON array (no prefix)
text2 = '[{"name":"林枫"}]'
print()
print("=== Test 2: direct array ===")
result2, status2 = robust_json_parse(text2, first_only=True)
print("Status:", status2, "Type:", type(result2).__name__)

# Test 3: Markdown code block with array
text3 = '```json\n[{"name":"林枫"}]\n```'
print()
print("=== Test 3: markdown code block ===")
result3, status3 = robust_json_parse(text3, first_only=True)
print("Status:", status3, "Type:", type(result3).__name__)

# Test 4: Object with characters key
text4 = '{"characters": [{"name":"林枫"}]}'
print()
print("=== Test 4: object with characters key ===")
result4, status4 = robust_json_parse(text4, first_only=True)
print("Status:", status4, "Type:", type(result4).__name__)
if isinstance(result4, dict):
    print("Has characters:", "characters" in result4)
