"""鲁棒的 JSON 提取与修复工具

处理 RWKV 模型输出中常见的 JSON 格式问题:
- 键缺少引号: {chapter_id: 10} → {"chapter_id": 10}
- 尾随逗号: [1, 2, 3,] → [1, 2, 3]
- 省略号: ...（后续内容）→ 删除
- 不完整的 JSON: 截断处自动补全括号
- 混入中文标点: ：→ : ，→ ,
"""

import json
import re
from typing import Any, Optional, Tuple


def extract_json_from_text(text: str) -> Optional[str]:
    """从文本中提取最长的 JSON 块"""
    # 尝试找 ```json ``` 代码块
    pattern = r'```json\s*(.*?)\s*```'
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        return matches[-1]

    # 尝试找 { } 或 [ ] 包裹的 JSON
    # 找最外层的 { }
    depth = 0
    start = -1
    best = None

    for i, ch in enumerate(text):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start:i + 1]
                if best is None or len(candidate) > len(best):
                    best = candidate
                start = -1

    if best:
        return best

    # 尝试找 [ ]
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '[':
            if depth == 0:
                start = i
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0 and start >= 0:
                candidate = text[start:i + 1]
                if best is None or len(candidate) > len(best):
                    best = candidate
                start = -1

    return best


def sanitize_json_text(text: str) -> str:
    """清理和修复 JSON 文本中的常见问题"""

    # 1. 删除省略号和中文注释
    text = re.sub(r'…+（[^）]*）', '', text)
    text = re.sub(r'\.\.\.+（[^）]*）', '', text)
    text = re.sub(r'…+', '', text)
    text = re.sub(r'\.\.\.\s*$', '', text, flags=re.MULTILINE)

    # 2. 替换中文标点为英文标点（在 JSON 上下文中）
    text = text.replace('：', ':')
    text = text.replace('，', ',')
    text = text.replace('（', '(')
    text = text.replace('）', ')')
    text = text.replace('【', '[')
    text = text.replace('】', ']')
    text = text.replace('\u201c', '"')
    text = text.replace('\u201d', '"')
    text = text.replace('\u2018', "'")
    text = text.replace('\u2019', "'")

    # 3. 修复缺少引号的键: {key: value} → {"key": value}
    # 匹配 { 或 , 后面的裸键
    text = re.sub(
        r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:',
        r'\1"\2":',
        text
    )

    # 3b. 修复 "key: value," 模式（键值混在引号内）
    # 如 "chapter_id: 10," → "chapter_id": 10,
    text = re.sub(
        r'"([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*([0-9]+)\s*,\s*"',
        r'"\1": \2, "',
        text
    )
    text = re.sub(
        r'"([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*([^"]*?)\s*,\s*"',
        r'"\1": "\2", "',
        text
    )

    # 3c. 修复裸键:值模式（逗号后空格+裸键）
    # 如 ," event: "" → , "event": ""
    text = re.sub(
        r',\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*',
        r', "\1": ',
        text
    )

    # 4. 修复尾随逗号: ,} → }  ,] → ]
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)

    # 5. 修复单引号为双引号（简单情况）
    # 注意：这可能会破坏字符串内容中的单引号，所以只在键值对中处理
    # text = re.sub(r"'([^']*)'", r'"\1"', text)  # 太激进，暂不启用

    return text


def balance_brackets(text: str) -> str:
    """补全不匹配的括号"""
    stack = []
    pairs = {'{': '}', '[': ']', '(': ')'}
    openers = set(pairs.keys())
    closers = set(pairs.values())

    for ch in text:
        if ch in openers:
            stack.append(ch)
        elif ch in closers:
            if stack and pairs.get(stack[-1]) == ch:
                stack.pop()

    # 补全缺失的闭合括号
    for opener in reversed(stack):
        text += pairs[opener]

    return text


def robust_json_parse(text: str) -> Tuple[Optional[Any], str]:
    """鲁棒地解析 JSON 文本

    尝试多种策略解析，返回 (parsed_object, status)
    status: "ok" / "repaired" / "failed"
    """
    # 策略1: 直接解析
    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError:
        pass

    # 策略2: 提取 JSON 块后解析
    extracted = extract_json_from_text(text)
    if extracted:
        try:
            return json.loads(extracted), "ok"
        except json.JSONDecodeError:
            pass

    # 策略3: 清理修复后解析
    if extracted:
        cleaned = sanitize_json_text(extracted)
    else:
        cleaned = sanitize_json_text(text)

    try:
        return json.loads(cleaned), "repaired"
    except json.JSONDecodeError:
        pass

    # 策略4: 清理 + 补全括号后解析
    balanced = balance_brackets(cleaned)
    try:
        return json.loads(balanced), "repaired"
    except json.JSONDecodeError:
        pass

    # 策略5: 逐步截断尝试（处理尾部截断）
    if extracted:
        work_text = sanitize_json_text(extracted)
    else:
        work_text = sanitize_json_text(text)

    # 从最后一个完整的 } 或 ] 开始截断
    for pos in range(len(work_text) - 1, len(work_text) // 2, -1):
        if work_text[pos] in ('}', ']'):
            candidate = balance_brackets(work_text[:pos + 1])
            try:
                return json.loads(candidate), "repaired"
            except json.JSONDecodeError:
                continue

    return None, "failed"


def parse_outline_output(raw_text: str) -> Tuple[Optional[dict], str]:
    """专门解析大纲输出的 JSON

    Returns:
        (outline_dict, status)
    """
    parsed, status = robust_json_parse(raw_text)

    if parsed is None:
        return None, "failed"

    # 确保是字典
    if isinstance(parsed, list) and len(parsed) > 0:
        parsed = parsed[0] if isinstance(parsed[0], dict) else {"volumes": parsed}

    if not isinstance(parsed, dict):
        return None, "failed"

    # 补全必要字段
    parsed.setdefault("title", "未命名小说")
    parsed.setdefault("genre", "仙侠")
    parsed.setdefault("volumes", [])
    parsed.setdefault("main_conflict", "")
    parsed.setdefault("ending_direction", "")
    parsed.setdefault("world_setting_summary", "")
    parsed.setdefault("initial_characters", [])
    parsed.setdefault("initial_factions", [])

    return parsed, status


def parse_volumes_output(raw_text: str) -> Tuple[list, str]:
    """解析卷级大纲输出

    Returns:
        (volumes_list, status)
    """
    parsed, status = robust_json_parse(raw_text)

    if parsed is None:
        return [], "failed"

    if isinstance(parsed, dict):
        # 可能是单个卷
        return [parsed], status
    elif isinstance(parsed, list):
        return parsed, status

    return [], "failed"
