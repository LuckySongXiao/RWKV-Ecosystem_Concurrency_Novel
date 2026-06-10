import json
import re
from typing import Any, Optional, Tuple


def extract_json_from_text(text: str, first_only: bool = False) -> Optional[str]:
    pattern = r'```json\s*(.*?)\s*```'
    matches = re.findall(pattern, text, re.DOTALL)
    if matches:
        if first_only:
            return matches[0]
        for m in reversed(matches):
            try:
                json.loads(m)
                return m
            except json.JSONDecodeError:
                continue
        return matches[-1]

    return _extract_outermost_json(text, first_only)


def _extract_outermost_json(text: str, first_only: bool = False) -> Optional[str]:
    best = None
    best_len = 0

    for opener, closer in [('{', '}'), ('[', ']')]:
        depth = 0
        start = -1
        in_string = False
        escape_next = False

        for i, ch in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue

            if ch == opener:
                if depth == 0:
                    start = i
                depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidate = text[start:i + 1]
                    if first_only:
                        return candidate
                    if len(candidate) > best_len:
                        best = candidate
                        best_len = len(candidate)
                    start = -1

    return best


def extract_all_json_blocks(text: str) -> list:
    results = []

    pattern = r'```json\s*(.*?)\s*```'
    matches = re.findall(pattern, text, re.DOTALL)
    for m in matches:
        results.append(m.strip())

    depth = 0
    start = -1
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue

        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start >= 0:
                results.append(text[start:i + 1])
                start = -1

    return results


def sanitize_json_text(text: str) -> str:
    text = re.sub(r'…+（[^）]*）', '', text)
    text = re.sub(r'\.\.\.+（[^）]*）', '', text)
    text = re.sub(r'…+', '', text)
    text = re.sub(r'\.\.\.\s*$', '', text, flags=re.MULTILINE)

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

    text = re.sub(
        r'([{,]\s*)([a-zA-Z_][a-zA-Z0-9_]*)\s*:',
        r'\1"\2":',
        text
    )
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
    text = re.sub(
        r',\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*',
        r', "\1": ',
        text
    )

    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*]', ']', text)

    return text


def balance_brackets(text: str) -> str:
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

    for opener in reversed(stack):
        text += pairs[opener]

    return text


def _close_open_strings(text: str) -> str:
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if esc:
            esc = False
            continue
        if ch == '\\' and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
    if in_str:
        text = text + '"'
    return text


def repair_truncated_json(text: str) -> Optional[str]:
    text = text.strip()

    if not text:
        return None

    if not text.startswith(('{', '[')):
        idx = -1
        for i, ch in enumerate(text):
            if ch in ('{', '['):
                idx = i
                break
        if idx > 0:
            prepended = '{' + text
            prepended_cleaned = sanitize_json_text(prepended)
            prepended_cleaned = _close_open_strings(prepended_cleaned)
            try:
                json.loads(prepended_cleaned)
                return prepended_cleaned
            except json.JSONDecodeError:
                pass
            balanced_prepended = balance_brackets(prepended_cleaned)
            balanced_prepended = _close_open_strings(balanced_prepended)
            try:
                json.loads(balanced_prepended)
                return balanced_prepended
            except json.JSONDecodeError:
                pass
            text = text[idx:]
        elif idx < 0:
            return None

    cleaned = sanitize_json_text(text)
    cleaned = _close_open_strings(cleaned)

    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        pass

    balanced = balance_brackets(cleaned)
    balanced = _close_open_strings(balanced)
    try:
        json.loads(balanced)
        return balanced
    except json.JSONDecodeError:
        pass

    opener = cleaned[0]
    closer = '}' if opener == '{' else ']'

    for pos in range(len(cleaned) - 1, max(len(cleaned) // 4, 1), -1):
        if cleaned[pos] == closer:
            candidate = cleaned[:pos + 1]

            depth = 0
            in_str = False
            esc = False
            for ch in candidate:
                if esc:
                    esc = False
                    continue
                if ch == '\\' and in_str:
                    esc = True
                    continue
                if ch == '"':
                    in_str = not in_str
                    continue
                if in_str:
                    continue
                if ch == '{' or ch == '[':
                    depth += 1
                elif ch == '}' or ch == ']':
                    depth -= 1

            if depth > 0:
                candidate = balance_brackets(candidate)

            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue

    if opener == '{':
        last_comma = -1
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(cleaned):
            if esc:
                esc = False
                continue
            if ch == '\\' and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == '{' or ch == '[':
                depth += 1
            elif ch == '}' or ch == ']':
                depth -= 1
            elif ch == ',' and depth == 1:
                last_comma = i

        if last_comma > 0:
            candidate = cleaned[:last_comma] + '}'
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        last_colon = -1
        depth = 0
        in_str = False
        esc = False
        for i, ch in enumerate(cleaned):
            if esc:
                esc = False
                continue
            if ch == '\\' and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == '{' or ch == '[':
                depth += 1
            elif ch == '}' or ch == ']':
                depth -= 1
            elif ch == ':' and depth == 1:
                last_colon = i

        if last_colon > 0:
            scan_start = last_colon + 1
            for pos in range(len(cleaned) - 1, scan_start, -1):
                ch = cleaned[pos]
                if ch in ('"', "'", '}', ']', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9', 'e', 'E', 'l', 's', 'n'):
                    candidate = cleaned[:pos + 1] + '}'
                    candidate = balance_brackets(candidate)
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        continue
                    break

    return None


def robust_json_parse(text: str, first_only: bool = False) -> Tuple[Optional[Any], str]:
    if not text or not text.strip():
        return None, "failed"

    try:
        return json.loads(text), "ok"
    except json.JSONDecodeError:
        pass

    extracted = extract_json_from_text(text, first_only=first_only)
    if extracted:
        try:
            return json.loads(extracted), "ok"
        except json.JSONDecodeError:
            pass

    if extracted:
        cleaned = sanitize_json_text(extracted)
    else:
        cleaned = sanitize_json_text(text)

    try:
        return json.loads(cleaned), "repaired"
    except json.JSONDecodeError:
        pass

    balanced = balance_brackets(cleaned)
    try:
        return json.loads(balanced), "repaired"
    except json.JSONDecodeError:
        pass

    if not extracted:
        outer = _extract_outermost_json(text, first_only=False)
        if outer and outer != text:
            try:
                return json.loads(outer), "ok"
            except json.JSONDecodeError:
                pass
            cleaned_outer = sanitize_json_text(outer)
            try:
                return json.loads(cleaned_outer), "repaired"
            except json.JSONDecodeError:
                pass

    repaired = repair_truncated_json(text)
    if repaired:
        try:
            return json.loads(repaired), "repaired"
        except json.JSONDecodeError:
            pass

    all_blocks = extract_all_json_blocks(text)
    for block in reversed(all_blocks):
        try:
            return json.loads(block), "ok"
        except json.JSONDecodeError:
            pass
        cleaned_block = sanitize_json_text(block)
        try:
            return json.loads(cleaned_block), "repaired"
        except json.JSONDecodeError:
            pass
        repaired_block = repair_truncated_json(block)
        if repaired_block:
            try:
                return json.loads(repaired_block), "repaired"
            except json.JSONDecodeError:
                pass

    return None, "failed"


def parse_outline_output(raw_text: str) -> Tuple[Optional[dict], str]:
    parsed, status = robust_json_parse(raw_text)

    if parsed is None:
        return None, "failed"

    if isinstance(parsed, list) and len(parsed) > 0:
        if all(isinstance(item, dict) and ("volume_id" in item or "volume_title" in item) for item in parsed):
            parsed = {"volumes": parsed}
        elif isinstance(parsed[0], dict) and "volume_id" not in parsed[0]:
            parsed = parsed[0]
        else:
            parsed = {"volumes": parsed}

    if not isinstance(parsed, dict):
        return None, "failed"

    parsed.setdefault("title", "未命名小说")
    parsed.setdefault("genre", "仙侠")
    parsed.setdefault("volumes", [])
    parsed.setdefault("main_conflict", "")
    parsed.setdefault("ending_direction", "")
    parsed.setdefault("world_setting_summary", "")
    parsed.setdefault("initial_characters", [])
    parsed.setdefault("initial_factions", [])

    return parsed, status


def parse_storyline_output(raw_text: str) -> Tuple[Optional[dict], str]:
    parsed, status = robust_json_parse(raw_text)

    if parsed is not None and isinstance(parsed, dict):
        parsed.setdefault("title", "")
        parsed.setdefault("description", "")
        parsed.setdefault("stages", [])
        parsed.setdefault("core_conflict", "")
        parsed.setdefault("sub_conflicts", [])
        parsed.setdefault("ending", "")
        return parsed, status

    if parsed is not None and isinstance(parsed, list):
        result = {
            "title": "自动生成主线",
            "description": "",
            "stages": parsed if all(isinstance(x, dict) for x in parsed) else [],
            "core_conflict": "",
            "sub_conflicts": [],
            "ending": "",
        }
        return result, "repaired"

    return None, "failed"


def parse_volumes_output(raw_text: str) -> Tuple[list, str]:
    parsed, status = robust_json_parse(raw_text)

    if parsed is None:
        return [], "failed"

    if isinstance(parsed, dict):
        return [parsed], status
    elif isinstance(parsed, list):
        return parsed, status

    return [], "failed"


def _deduplicate_events(events_text: str) -> str:
    seen = set()
    parts = events_text.split(';')
    deduped = []
    for p in parts:
        p = p.strip()
        if p and p not in seen:
            seen.add(p)
            deduped.append(p)
    return ';'.join(deduped)


def extract_volumes_from_truncated(raw_text: str) -> list:
    volumes = []
    seen_vol_ids = set()

    vol_pattern = r'\{\s*"volume_id"\s*:\s*(\d+)'
    for m in re.finditer(vol_pattern, raw_text):
        vol_id_str = m.group(1)
        if vol_id_str in seen_vol_ids:
            continue
        seen_vol_ids.add(vol_id_str)

        start = m.start()
        depth = 0
        in_str = False
        esc = False
        end = -1
        for i in range(start, len(raw_text)):
            ch = raw_text[i]
            if esc:
                esc = False
                continue
            if ch == '\\' and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i
                    break

        if end > 0:
            block = raw_text[start:end + 1]
            try:
                vol = json.loads(block)
                if isinstance(vol, dict):
                    events = vol.get("events", "")
                    if isinstance(events, str) and events:
                        vol["events"] = _deduplicate_events(events)
                    volumes.append(vol)
            except json.JSONDecodeError:
                repaired = repair_truncated_json(block)
                if repaired:
                    try:
                        vol = json.loads(repaired)
                        if isinstance(vol, dict):
                            events = vol.get("events", "")
                            if isinstance(events, str) and events:
                                vol["events"] = _deduplicate_events(events)
                            volumes.append(vol)
                    except json.JSONDecodeError:
                        pass

    if not volumes:
        volumes = _extract_volumes_from_text_blocks(raw_text)

    return volumes


def _extract_volumes_from_text_blocks(raw_text: str) -> list:
    volumes = []
    vol_sections = re.split(r'(?="volume_id"\s*:\s*\d+)', raw_text)

    for section in vol_sections:
        section = section.strip()
        if not section or 'volume_id' not in section:
            continue

        vol = {}
        vol_id_m = re.search(r'"volume_id"\s*:\s*(\d+)', section)
        if vol_id_m:
            vol["volume_id"] = int(vol_id_m.group(1))

        vol_title_m = re.search(r'"volume_title"\s*:\s*"([^"]*)"', section)
        if vol_title_m:
            vol["volume_title"] = vol_title_m.group(1)

        theme_m = re.search(r'"theme"\s*:\s*"([^"]*)"', section)
        if theme_m:
            vol["theme"] = theme_m.group(1)

        events_m = re.search(r'"events"\s*:\s*"([^"]*(?:"[^"]*"[^"]*)*)"', section)
        if events_m:
            vol["events"] = _deduplicate_events(events_m.group(1))

        ch_count_m = re.search(r'"chapter_count"\s*:\s*(\d+)', section)
        if ch_count_m:
            vol["chapter_count"] = int(ch_count_m.group(1))

        if vol.get("volume_id") is not None:
            vol.setdefault("volume_title", f"第{vol['volume_id']}卷")
            vol.setdefault("theme", "")
            vol.setdefault("chapter_count", 20)
            vol.setdefault("events", "")
            volumes.append(vol)

    return volumes
