"""状态变更 JSON 解析器 - 从章节正文 Markdown 末尾提取状态变更

增强功能:
- 多种 JSON 提取策略（代码块、分隔符、尾部大括号）
- 鲁棒 JSON 解析（自动修复常见格式错误）
- 状态变更结构校验和补全
- 解析失败时生成待审标记
"""

import json
import re
from typing import Optional, Tuple, List, Dict

from .world_state_engine import StateChange
from .json_utils import robust_json_parse


def parse_state_change_from_draft(draft_content: str, chapter_id: int = 0) -> Tuple[Optional[StateChange], str]:
    """从章节正文 Markdown 中提取状态变更 JSON

    提取策略（按优先级）:
    1. ```json ... ``` 代码块
    2. --- 分隔符后的 JSON
    3. 文末最后一个完整的 {...} 块
    4. 包含 "state_change" 或 "状态变更" 标记的段落

    Returns:
        (StateChange, status) - status 为 "ok" / "parse_error" / "not_found" / "repaired"
    """
    if not draft_content or not draft_content.strip():
        return None, "not_found"

    strategies = [
        _extract_from_code_block,
        _extract_from_separator,
        _extract_from_trailing_brace,
        _extract_from_marker,
    ]

    for strategy in strategies:
        json_str = strategy(draft_content)
        if not json_str:
            continue

        parsed, status = robust_json_parse(json_str)
        if parsed is not None and isinstance(parsed, dict):
            if "chapter_id" not in parsed:
                parsed["chapter_id"] = chapter_id
            state_change = _validate_and_build_state_change(parsed, chapter_id)
            if state_change is not None:
                return state_change, status

    return None, "not_found"


def split_content_and_state_change(draft_content: str) -> Tuple[str, Optional[StateChange]]:
    """将章节正文与状态变更JSON分离

    Returns:
        (pure_content, state_change_or_none)
    """
    if not draft_content or not draft_content.strip():
        return draft_content or "", None

    pattern = r'```json\s*\{.*?\}\s*```'
    matches = list(re.finditer(pattern, draft_content, re.DOTALL))

    if matches:
        last_match = matches[-1]
        pure_content = draft_content[:last_match.start()].strip()

        json_str = re.search(r'\{.*\}', last_match.group(), re.DOTALL)
        if json_str:
            parsed, status = robust_json_parse(json_str.group())
            if parsed is not None and isinstance(parsed, dict):
                return pure_content, StateChange.from_dict(parsed)

        return pure_content, None

    parts = draft_content.rsplit("---", 1)
    if len(parts) > 1:
        try:
            parsed, status = robust_json_parse(parts[1].strip())
            if parsed is not None and isinstance(parsed, dict):
                return parts[0].strip(), StateChange.from_dict(parsed)
        except Exception:
            pass

    trailing = _extract_from_trailing_brace(draft_content)
    if trailing:
        parsed, status = robust_json_parse(trailing)
        if parsed is not None and isinstance(parsed, dict):
            brace_start = draft_content.rfind("{")
            if brace_start > 0:
                return draft_content[:brace_start].strip(), StateChange.from_dict(parsed)

    return draft_content, None


def _extract_from_code_block(content: str) -> Optional[str]:
    """策略1: 从 ```json ... ``` 代码块提取"""
    pattern = r'```json\s*(\{.*?\})\s*```'
    matches = re.findall(pattern, content, re.DOTALL)
    if not matches:
        return None

    for match in reversed(matches):
        if _looks_like_state_change(match):
            return match

    return matches[-1] if matches else None


def _extract_from_separator(content: str) -> Optional[str]:
    """策略2: 从 --- 分隔符后提取"""
    parts = content.rsplit("---", 1)
    if len(parts) <= 1:
        return None

    candidate = parts[1].strip()
    if candidate.startswith("{") or "chapter_id" in candidate or "character_changes" in candidate:
        return candidate

    return None


def _extract_from_trailing_brace(content: str) -> Optional[str]:
    """策略3: 从文末最后一个完整大括号块提取"""
    last_brace = content.rfind("}")
    if last_brace < 0:
        return None

    depth = 0
    start = -1
    for i in range(last_brace, -1, -1):
        if content[i] == "}":
            depth += 1
        elif content[i] == "{":
            depth -= 1
            if depth == 0:
                start = i
                break

    if start < 0:
        return None

    candidate = content[start:last_brace + 1]

    if _looks_like_state_change(candidate):
        return candidate

    return None


def _extract_from_marker(content: str) -> Optional[str]:
    """策略4: 从包含状态变更标记的段落提取"""
    markers = ["状态变更", "state_change", "State Change", "【状态变更】"]
    lines = content.split("\n")

    for marker in markers:
        for i, line in enumerate(lines):
            if marker in line:
                remaining = "\n".join(lines[i:])
                brace_start = remaining.find("{")
                if brace_start >= 0:
                    brace_end = remaining.rfind("}")
                    if brace_end > brace_start:
                        return remaining[brace_start:brace_end + 1]

    return None


def _looks_like_state_change(text: str) -> bool:
    """判断文本是否看起来像状态变更 JSON"""
    if not text:
        return False

    keywords = [
        "chapter_id", "character_changes", "faction_changes",
        "economy_changes", "new_foreshadowing", "resolved_foreshadowing",
    ]

    lower_text = text.lower()
    return any(kw in lower_text for kw in keywords)


def _validate_and_build_state_change(data: Dict, chapter_id: int) -> Optional[StateChange]:
    """校验并补全状态变更数据结构

    确保必要字段存在，缺失字段用默认值填充。
    """
    if not isinstance(data, dict):
        return None

    data.setdefault("chapter_id", chapter_id)
    data.setdefault("character_changes", [])
    data.setdefault("faction_changes", [])
    data.setdefault("economy_changes", [])
    data.setdefault("new_foreshadowing", [])
    data.setdefault("resolved_foreshadowing", [])

    for cc in data.get("character_changes", []):
        if not isinstance(cc, dict):
            continue
        cc.setdefault("character_id", "")
        cc.setdefault("attribute", "")
        cc.setdefault("old_value", "")
        cc.setdefault("new_value", "")

    for fc in data.get("faction_changes", []):
        if not isinstance(fc, dict):
            continue
        fc.setdefault("faction_id", "")
        fc.setdefault("attribute", "")
        fc.setdefault("old_value", "")
        fc.setdefault("new_value", "")

    for ec in data.get("economy_changes", []):
        if not isinstance(ec, dict):
            continue
        ec.setdefault("faction_id", "")
        ec.setdefault("attribute", "")
        ec.setdefault("new_value", 0)

    for fs in data.get("new_foreshadowing", []):
        if not isinstance(fs, dict):
            continue
        fs.setdefault("id", f"fs_ch{chapter_id}_{len(data['new_foreshadowing'])}")
        fs.setdefault("description", "")
        fs.setdefault("status", "planted")
        fs.setdefault("planted_at", chapter_id)
        fs.setdefault("expected_resolve", chapter_id + 10)

    for rf in data.get("resolved_foreshadowing", []):
        if not isinstance(rf, dict):
            continue
        rf.setdefault("id", "")
        rf.setdefault("method", "")

    try:
        return StateChange.from_dict(data)
    except Exception:
        return None
