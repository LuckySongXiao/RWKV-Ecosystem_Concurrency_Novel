"""状态变更 JSON 解析器 - 从章节正文 Markdown 末尾提取状态变更"""

import json
import re
from typing import Optional, Tuple

from .world_state_engine import StateChange


def parse_state_change_from_draft(draft_content: str, chapter_id: int = 0) -> Tuple[Optional[StateChange], str]:
    """从章节正文 Markdown 中提取状态变更 JSON

    Returns:
        (StateChange, status) - status 为 "ok" / "parse_error" / "not_found"
    """
    # 尝试提取 ```json ... ``` 代码块
    pattern = r'```json\s*(\{.*?\})\s*```'
    matches = re.findall(pattern, draft_content, re.DOTALL)

    if not matches:
        # 尝试提取 --- 分隔符后的 JSON
        parts = draft_content.rsplit("---", 1)
        if len(parts) > 1:
            try:
                data = json.loads(parts[1].strip())
                return StateChange.from_dict(data), "ok"
            except json.JSONDecodeError:
                pass
        return None, "not_found"

    # 取最后一个匹配（最可能是状态变更）
    for match in reversed(matches):
        try:
            data = json.loads(match)
            if "chapter_id" in data or "character_changes" in data:
                if not data.get("chapter_id"):
                    data["chapter_id"] = chapter_id
                return StateChange.from_dict(data), "ok"
        except json.JSONDecodeError:
            continue

    return None, "parse_error"


def split_content_and_state_change(draft_content: str) -> Tuple[str, Optional[StateChange]]:
    """将章节正文与状态变更JSON分离

    Returns:
        (pure_content, state_change_or_none)
    """
    # 查找最后一个 ```json ``` 代码块
    pattern = r'```json\s*\{.*?\}\s*```'
    matches = list(re.finditer(pattern, draft_content, re.DOTALL))

    if not matches:
        # 尝试 --- 分隔符
        parts = draft_content.rsplit("---", 1)
        if len(parts) > 1:
            try:
                data = json.loads(parts[1].strip())
                return parts[0].strip(), StateChange.from_dict(data)
            except json.JSONDecodeError:
                pass
        return draft_content, None

    last_match = matches[-1]
    pure_content = draft_content[:last_match.start()].strip()

    json_str = re.search(r'\{.*\}', last_match.group(), re.DOTALL)
    if json_str:
        try:
            data = json.loads(json_str.group())
            return pure_content, StateChange.from_dict(data)
        except json.JSONDecodeError:
            pass

    return pure_content, None
