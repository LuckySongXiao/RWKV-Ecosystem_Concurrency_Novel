"""角色信息表模板和并行填表生成器

使用固定的角色信息表模板，AI并行填表生成用户指定数量的角色信息。
优先使用 big_batch_completions 续写端点批量生成，失败时回退到 openai_chat_completions。
"""

import json
import os
import re
import time
from typing import Dict, List, Optional
from src.core.config import SamplingParams, PipelineConfig
from src.core.rwkv_client import RWKVClient
from src.core.logger import Logger
from src.core.json_utils import robust_json_parse


CHARACTER_TEMPLATE = {
    "name": "",
    "identity": "",
    "personality": "",
    "ability": "",
    "background": "",
    "motivation": "",
    "role_type": "",
}


class CharacterTableGenerator:
    """角色信息表并行填表生成器"""

    def __init__(self, client: RWKVClient, config: PipelineConfig, logger: Logger = None):
        self._client = client
        self._config = config
        self._logger = logger or Logger.get()

    def generate_characters_batch(
        self,
        theme: str,
        character_count: int,
        protagonist_names: List[str] = None,
        antagonist_names: List[str] = None,
        extra_context: str = "",
        concurrency: int = 6,
    ) -> List[Dict]:
        self._logger.info(f"开始批量生成 {character_count} 个角色 - 主题: {theme}")

        user_defined_count = len(protagonist_names or []) + len(antagonist_names or [])
        ai_generate_count = character_count - user_defined_count

        characters = []

        if protagonist_names:
            for name in protagonist_names:
                char = CHARACTER_TEMPLATE.copy()
                char["name"] = name
                char["role_type"] = "protagonist"
                characters.append(char)

        if antagonist_names:
            for name in antagonist_names:
                char = CHARACTER_TEMPLATE.copy()
                char["name"] = name
                char["role_type"] = "antagonist"
                characters.append(char)

        if ai_generate_count > 0:
            ai_characters = self._generate_ai_characters(
                theme, ai_generate_count, extra_context, characters, concurrency=concurrency
            )
            characters.extend(ai_characters)

        self._logger.info(f"角色生成完成 - 总计: {len(characters)}")
        return characters

    def _generate_ai_characters(
        self,
        theme: str,
        count: int,
        extra_context: str,
        existing_characters: List[Dict] = None,
        max_retries: int = 2,
        concurrency: int = 6,
    ) -> List[Dict]:
        """使用 big_batch_completions 批量端点并发生成AI角色

        并发模型: 1并发 = 1bsz请求，concurrency 控制单次批量请求的 bsz 数
        big_batch_completions 是续写端点，prompt 需要使用续写格式
        """
        self._logger.info(f"批量生成 {count} 个AI角色 (bsz={concurrency})...")

        prompts = []
        for i in range(count):
            prompt = self._build_continuation_prompt(theme, i + 1, count, extra_context, existing_characters)
            prompts.append(prompt)

        sampling = SamplingParams(temperature=0.9, top_p=0.85, max_tokens=1024)

        all_results = [None] * count
        pending_indices = list(range(count))

        for attempt in range(max_retries + 1):
            if not pending_indices:
                break

            still_pending = []
            bsz = min(concurrency, len(pending_indices))

            for batch_start in range(0, len(pending_indices), bsz):
                batch_indices = pending_indices[batch_start:batch_start + bsz]
                batch_prompts = [prompts[idx] for idx in batch_indices]

                try:
                    results = self._client.big_batch_completions(
                        contents=batch_prompts,
                        sampling=sampling,
                        stream=False,
                        chunk_size=8,
                    )

                    for j, idx in enumerate(batch_indices):
                        if j < len(results):
                            char = self._parse_character_table(results[j], prompt_suffix='{"name":')
                            if char:
                                all_results[idx] = char
                            else:
                                still_pending.append(idx)
                                if attempt < max_retries:
                                    self._logger.warning(f"角色 #{idx + 1} 解析失败，将重试")
                        else:
                            still_pending.append(idx)

                except Exception as e:
                    self._logger.error(f"角色批量生成失败 (attempt {attempt + 1}): {e}")
                    still_pending.extend(batch_indices)

            pending_indices = still_pending

            if pending_indices and attempt < max_retries:
                time.sleep(1)

        if pending_indices:
            self._logger.warning(f"big_batch端点失败 {len(pending_indices)} 个角色，回退到openai_chat端点")
            fallback_results = self._fallback_openai_chat(
                prompts, pending_indices, theme, extra_context, existing_characters
            )
            for idx, char in fallback_results.items():
                if char and all_results[idx] is None:
                    all_results[idx] = char

        characters = [c for c in all_results if c is not None]
        failed = count - len(characters)
        if failed > 0:
            self._logger.warning(f"AI角色生成完成 - 成功: {len(characters)}/{count}，失败 {failed} 个将使用占位模板")
            for i in range(count):
                if all_results[i] is None:
                    placeholder = CHARACTER_TEMPLATE.copy()
                    placeholder["name"] = f"配角{chr(0x41 + i)}"
                    placeholder["role_type"] = "supporting"
                    placeholder["identity"] = f"{theme}世界配角"
                    characters.append(placeholder)
        else:
            self._logger.info(f"AI角色生成完成 - 全部成功: {len(characters)}/{count}")

        return characters

    def _fallback_openai_chat(
        self,
        prompts: List[str],
        pending_indices: List[int],
        theme: str,
        extra_context: str,
        existing_characters: List[Dict] = None,
    ) -> Dict[int, Optional[Dict]]:
        """回退到 openai_chat_completions 逐个生成"""
        results = {}
        sampling = SamplingParams(temperature=0.9, top_p=0.85, max_tokens=1024)

        for idx in pending_indices:
            chat_prompt = self._build_chat_prompt(theme, idx + 1, len(prompts), extra_context, existing_characters)
            try:
                result = self._client.openai_chat_completions(
                    messages=[{"role": "user", "content": chat_prompt}],
                    sampling=sampling,
                    stream=False,
                )
                char = self._parse_character_table(result)
                results[idx] = char
            except Exception as e:
                self._logger.error(f"角色 #{idx + 1} openai_chat回退也失败: {e}")
                results[idx] = None

        return results

    def _build_continuation_prompt(
        self,
        theme: str,
        index: int,
        total: int,
        extra_context: str,
        existing_characters: List[Dict] = None,
    ) -> str:
        """构造续写格式的Prompt（适配 big_batch_completions 续写端点）

        使用 few-shot 格式引导模型输出单行JSON，模型输出}后自然停止
        """
        existing_info = ""
        if existing_characters:
            names = [c.get("name", "?") for c in existing_characters]
            existing_info = f"已有角色: {', '.join(names)}，新角色需与已有角色互补。"

        role_hint = "主角" if index == 1 else ("反派" if index == total else "配角")

        return f"""User: 请为仙侠题材小说创建第1个角色，角色定位: 主角。严格按JSON格式输出，不要添加任何其他文字。
{{"name":"林逸风","identity":"青云宗首席弟子","personality":"正直坚毅、重情重义","ability":"天生剑骨，悟性极高","background":"出身寒门，幼年被青云宗收养","motivation":"守护苍生，证道成仙","role_type":"主角"}}

User: 请为仙侠题材小说创建第2个角色，角色定位: 反派。严格按JSON格式输出，不要添加任何其他文字。
{{"name":"魔尊血煞","identity":"血魔宗宗主","personality":"阴险狡诈、野心勃勃","ability":"血魔大法，操控血雾","background":"原为正道天才，因爱入魔","motivation":"颠覆正道，统治修真界","role_type":"反派"}}

User: 请为{theme}题材小说创建第{index}个角色（共{total}个），角色定位: {role_hint}。{existing_info}{extra_context + "。" if extra_context else ""}严格按JSON格式输出，不要添加任何其他文字。

Assistant: {{"name":"""

    def _build_chat_prompt(
        self,
        theme: str,
        index: int,
        total: int,
        extra_context: str,
        existing_characters: List[Dict] = None,
    ) -> str:
        """构造对话格式的Prompt（适配 openai_chat_completions）"""
        existing_info = ""
        if existing_characters:
            names = [c.get("name", "?") for c in existing_characters]
            existing_info = f"已有角色: {', '.join(names)}，新角色需与已有角色互补。"

        role_hint = "主角" if index == 1 else ("反派" if index == total else "配角")

        return f"""请为{theme}题材小说创建第{index}个角色（共{total}个），角色定位: {role_hint}。
{existing_info}{extra_context}

请严格按以下JSON格式输出，不要添加任何其他文字:
{{"name":"角色名","identity":"身份","personality":"性格特点","ability":"能力","background":"背景","motivation":"动机","role_type":"{role_hint}"}}"""

    def _parse_character_table(self, result: str, prompt_suffix: str = None) -> Optional[Dict]:
        """解析角色信息表（使用鲁棒JSON解析）

        Args:
            result: 模型续写输出
            prompt_suffix: prompt末尾的引导文本（如 '{"name":'），需要拼接到result前面
        """
        if not result or not result.strip():
            self._logger.error("解析角色信息表失败: AI返回空内容")
            return None

        text = result.strip()

        if prompt_suffix:
            text = prompt_suffix + text

        if not text.startswith("{") and not text.startswith("["):
            for marker in ["{", "["]:
                pos = text.find(marker)
                if pos > 0:
                    text = text[pos:]
                    break

        parsed, status = robust_json_parse(text, first_only=True)

        if parsed is None:
            self._logger.warning("角色信息表JSON解析失败，尝试正则提取字段")
            parsed = self._extract_character_fields_regex(text)

        if parsed is None:
            self._logger.error(f"解析角色信息表失败: 无法解析JSON (原始内容前100字: {result[:100]})")
            return None

        if status == "repaired":
            self._logger.info("角色信息表JSON已自动修复")

        if isinstance(parsed, list) and len(parsed) > 0:
            parsed = parsed[0]

        if not isinstance(parsed, dict):
            self._logger.error(f"解析角色信息表失败: 结果不是字典 (type={type(parsed).__name__})")
            return None

        char = CHARACTER_TEMPLATE.copy()
        for key in CHARACTER_TEMPLATE:
            if key in parsed:
                val = parsed[key]
                if isinstance(val, list):
                    val = "、".join(str(v) for v in val)
                elif isinstance(val, dict):
                    val = "、".join(f"{k}:{v}" for k, v in val.items())
                char[key] = val

        if not char.get("name"):
            name_keys = ["角色名", "姓名", "character_name"]
            for nk in name_keys:
                if nk in parsed and parsed[nk]:
                    char["name"] = str(parsed[nk])
                    break

        if not char.get("name"):
            self._logger.error("解析角色信息表失败: 缺少name字段")
            return None

        if not char.get("role_type"):
            char["role_type"] = "supporting"

        return char

    def _extract_character_fields_regex(self, text: str) -> Optional[Dict]:
        """当JSON解析完全失败时，使用正则表达式提取角色字段"""
        result = {}
        field_patterns = {
            "name": [r'"name"\s*:\s*"([^"]*)"', r'"姓名"\s*:\s*"([^"]*)"'],
            "identity": [r'"identity"\s*:\s*"([^"]*)"', r'"身份"\s*:\s*"([^"]*)"'],
            "personality": [r'"personality"\s*:\s*"([^"]*)"', r'"性格"\s*:\s*"([^"]*)"'],
            "ability": [r'"ability"\s*:\s*"([^"]*)"', r'"能力"\s*:\s*"([^"]*)"'],
            "background": [r'"background"\s*:\s*"([^"]*)"', r'"背景"\s*:\s*"([^"]*)"'],
            "motivation": [r'"motivation"\s*:\s*"([^"]*)"', r'"动机"\s*:\s*"([^"]*)"'],
            "role_type": [r'"role_type"\s*:\s*"([^"]*)"', r'"角色定位"\s*:\s*"([^"]*)"'],
        }

        for field, patterns in field_patterns.items():
            for pattern in patterns:
                m = re.search(pattern, text)
                if m:
                    result[field] = m.group(1)
                    break

        if not result.get("name"):
            return None

        return result
