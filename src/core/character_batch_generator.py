"""批量角色设定生成器

根据主题和用户设定的主角名称，自动生成完整的角色体系。
支持：
- 主角设定（用户指定名称）
- 配角自动生成（AI补全）
- 反派角色生成
- 势力相关角色生成
"""

import json
from typing import Dict, List, Optional
from src.core.config import SamplingParams, PipelineConfig
from src.core.rwkv_client import RWKVClient
from src.core.logger import Logger


class CharacterBatchGenerator:
    """批量角色设定生成器"""

    def __init__(self, client: RWKVClient, config: PipelineConfig, logger: Logger = None):
        self._client = client
        self._config = config
        self._logger = logger or Logger.get()

    def generate_characters(
        self,
        theme: str,
        protagonist_names: List[str],
        antagonist_names: Optional[List[str]] = None,
        volume_count: int = 4,
        chapters_per_volume: int = 10,
        extra_context: str = "",
    ) -> Dict:
        """批量生成角色设定

        Args:
            theme: 小说主题/题材
            protagonist_names: 主角名称列表（用户设定）
            antagonist_names: 反派名称列表（可选，用户设定）
            volume_count: 卷数
            chapters_per_volume: 每卷章节数
            extra_context: 额外设定上下文

        Returns:
            角色设定字典，包含：
            - protagonists: 主角列表
            - antagonists: 反派列表
            - supporting: 配角列表
            - factions: 势力列表
        """
        self._logger.info(f"开始批量生成角色设定 - 主题: {theme}")

        # 构造角色生成Prompt
        prompt = self._build_character_generation_prompt(
            theme, protagonist_names, antagonist_names,
            volume_count, chapters_per_volume, extra_context
        )

        sampling = SamplingParams(
            temperature=0.9,
            top_p=0.85,
            max_tokens=4096,
        )

        # 调用AI生成角色
        self._logger.info("调用AI生成角色设定...")
        result = self._client.openai_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            sampling=sampling,
            stream=False,
        )

        # 解析结果
        characters = self._parse_character_result(result)

        # 确保用户设定的主角名称被使用
        for i, name in enumerate(protagonist_names):
            if i < len(characters["protagonists"]):
                characters["protagonists"][i]["name"] = name
            else:
                characters["protagonists"].append({
                    "name": name,
                    "role": "protagonist",
                    "description": f"主角 - {name}",
                })

        # 确保用户设定的反派名称被使用
        if antagonist_names:
            for i, name in enumerate(antagonist_names):
                if i < len(characters["antagonists"]):
                    characters["antagonists"][i]["name"] = name
                else:
                    characters["antagonists"].append({
                        "name": name,
                        "role": "antagonist",
                        "description": f"反派 - {name}",
                    })

        self._logger.info(f"角色生成完成 - 主角: {len(characters['protagonists'])}, "
                         f"反派: {len(characters['antagonists'])}, "
                         f"配角: {len(characters['supporting'])}")

        return characters

    def _build_character_generation_prompt(
        self,
        theme: str,
        protagonist_names: List[str],
        antagonist_names: Optional[List[str]],
        volume_count: int,
        chapters_per_volume: int,
        extra_context: str,
    ) -> str:
        """构造角色生成Prompt"""
        total_chapters = volume_count * chapters_per_volume

        protagonist_str = "、".join(protagonist_names) if protagonist_names else "待设定"
        antagonist_str = "、".join(antagonist_names) if antagonist_names else "待设定"

        prompt = f"""你是一位资深小说角色设计师。请基于以下信息，为一部长篇小说设计完整的角色体系。

## 基本信息
- 题材类型: {theme}
- 总卷数: {volume_count} 卷
- 每卷章节数: {chapters_per_volume} 章
- 总章节数: {total_chapters} 章
- 已设定主角名称: {protagonist_str}
- 已设定反派名称: {antagonist_str}

{extra_context if extra_context else ""}

## 角色设计要求

请设计以下类型的角色（输出JSON格式）：

### 1. 主角团队 (protagonists)
- 包含用户已设定的主角名称
- 为每位主角设计详细的角色档案
- 包含：性格、背景、能力、成长弧线

### 2. 反派团队 (antagonists)
- 包含用户已设定的反派名称（如有）
- 设计主要反派和次要反派
- 包含：动机、能力、与主角的冲突点

### 3. 配角团队 (supporting)
- 盟友、导师、朋友、恋人等
- 各势力代表人物
- 功能性角色（推动剧情）

### 4. 势力列表 (factions)
- 主要势力/组织
- 势力之间的关系
- 各势力的核心人物

## 输出格式
```json
{{
  "protagonists": [
    {{
      "name": "角色名",
      "gender": "男/女",
      "age": "年龄",
      "identity": "身份/职业",
      "personality": ["性格特点1", "性格特点2"],
      "ability": "能力/技能",
      "background": "背景故事",
      "growth_arc": "成长弧线",
      "relationships": {{"角色名": "关系描述"}}
    }}
  ],
  "antagonists": [
    {{
      "name": "角色名",
      "gender": "男/女",
      "motivation": "动机",
      "ability": "能力",
      "conflict_with_protagonist": "与主角的冲突点"
    }}
  ],
  "supporting": [
    {{
      "name": "角色名",
      "role": "角色定位",
      "relationship_to_protagonist": "与主角的关系"
    }}
  ],
  "factions": [
    {{
      "name": "势力名",
      "type": "势力类型",
      "leader": "首领",
      "members": ["成员列表"],
      "relationship": "与其他势力的关系"
    }}
  ]
}}
```

请直接输出JSON，不要包含额外说明。"""

        return prompt

    def _parse_character_result(self, result: str) -> Dict:
        """解析AI生成的角色结果"""
        try:
            # 尝试提取JSON
            if "```json" in result:
                json_str = result.split("```json")[1].split("```")[0].strip()
            elif "```" in result:
                json_str = result.split("```")[1].split("```")[0].strip()
            else:
                json_str = result

            data = json.loads(json_str)

            return {
                "protagonists": data.get("protagonists", []),
                "antagonists": data.get("antagonists", []),
                "supporting": data.get("supporting", []),
                "factions": data.get("factions", []),
            }
        except json.JSONDecodeError as e:
            self._logger.warning(f"角色JSON解析失败，尝试修复: {e}")
            try:
                fixed = self._fix_json_string(json_str)
                data = json.loads(fixed)
                return {
                    "protagonists": data.get("protagonists", []),
                    "antagonists": data.get("antagonists", []),
                    "supporting": data.get("supporting", []),
                    "factions": data.get("factions", []),
                }
            except Exception as e2:
                self._logger.error(f"角色JSON修复失败: {e2}")
        except Exception as e:
            self._logger.error(f"解析角色结果失败: {e}")
        
        return {
            "protagonists": [],
            "antagonists": [],
            "supporting": [],
            "factions": [],
        }
    
    def _fix_json_string(self, json_str: str) -> str:
        """尝试修复常见的JSON格式错误"""
        import re
        
        # 修复未闭合的字符串
        lines = json_str.split('\n')
        fixed_lines = []
        for line in lines:
            quote_count = line.count('"') - line.count('\\"')
            if quote_count % 2 != 0:
                if not line.rstrip().endswith('"'):
                    line = line.rstrip() + '"'
            fixed_lines.append(line)
        
        fixed = '\n'.join(fixed_lines)
        fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
        
        return fixed
