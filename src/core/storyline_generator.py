"""剧情线路生成器

基于主题和角色信息，自动生成完整的剧情线路。
支持：
- 主线剧情生成
- 支线剧情生成
- 角色弧光设计
- 伏笔埋设规划
"""

import json
from typing import Dict, List, Optional
from src.core.config import SamplingParams, PipelineConfig
from src.core.rwkv_client import RWKVClient
from src.core.logger import Logger


class StorylineGenerator:
    """剧情线路生成器"""

    def __init__(self, client: RWKVClient, config: PipelineConfig, logger: Logger = None):
        self._client = client
        self._config = config
        self._logger = logger or Logger.get()

    def generate_storyline(
        self,
        theme: str,
        characters: Dict,
        volume_count: int = 4,
        chapters_per_volume: int = 10,
        extra_context: str = "",
    ) -> Dict:
        """生成完整剧情线路

        Args:
            theme: 小说主题
            characters: 角色设定字典（来自CharacterBatchGenerator）
            volume_count: 卷数
            chapters_per_volume: 每卷章节数
            extra_context: 额外上下文

        Returns:
            剧情线路字典，包含：
            - main_plot: 主线剧情
            - sub_plots: 支线剧情列表
            - character_arcs: 角色弧光
            - foreshadowings: 伏笔规划
        """
        self._logger.info(f"开始生成剧情线路 - 主题: {theme}")

        prompt = self._build_storyline_prompt(
            theme, characters, volume_count, chapters_per_volume, extra_context
        )

        sampling = SamplingParams(
            temperature=0.95,
            top_p=0.9,
            max_tokens=4096,
        )

        self._logger.info("调用AI生成剧情线路...")
        result = self._client.openai_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            sampling=sampling,
            stream=False,
        )

        storyline = self._parse_storyline_result(result)

        self._logger.info(f"剧情线路生成完成 - 主线: 1条, "
                         f"支线: {len(storyline.get('sub_plots', []))}条, "
                         f"伏笔: {len(storyline.get('foreshadowings', []))}个")

        return storyline

    def _build_storyline_prompt(
        self,
        theme: str,
        characters: Dict,
        volume_count: int,
        chapters_per_volume: int,
        extra_context: str,
    ) -> str:
        """构造剧情线路生成Prompt - 简化JSON格式"""
        total_chapters = volume_count * chapters_per_volume

        chars_str = self._format_characters(characters)

        return f"""你是一位资深小说编剧。请基于以下信息，为一部长篇小说设计完整的剧情线路。

## 基本信息
- 题材类型: {theme}
- 总卷数: {volume_count} 卷
- 每卷章节数: {chapters_per_volume} 章
- 总章节数: {total_chapters} 章

## 角色体系
{chars_str}

{extra_context if extra_context else ""}

## 输出格式（JSON）
```json
{{
  "title": "主线名称",
  "description": "主线剧情概述",
  "stages": [
    {{"volume_id": 1, "stage_name": "阶段名称", "description": "阶段剧情", "key_events": "关键事件，用分号分隔"}}
  ],
  "sub_plots": "支线剧情概述",
  "character_arcs": "角色成长弧线概述",
  "foreshadowings": "伏笔规划概述",
  "core_conflict": "核心冲突",
  "ending": "结局方向"
}}
```

请直接输出JSON，不要包含额外说明。"""

    def _format_characters(self, characters: Dict) -> str:
        """格式化角色信息"""
        parts = []

        # 主角
        if characters.get("protagonists"):
            parts.append("### 主角团队")
            for ch in characters["protagonists"]:
                parts.append(f"- {ch.get('name', '未知')}: {ch.get('identity', '')} - {ch.get('personality', '')}")

        # 反派
        if characters.get("antagonists"):
            parts.append("\n### 反派团队")
            for ch in characters["antagonists"]:
                parts.append(f"- {ch.get('name', '未知')}: {ch.get('motivation', '')}")

        # 配角
        if characters.get("supporting"):
            parts.append("\n### 配角团队")
            for ch in characters["supporting"][:10]:  # 限制数量
                parts.append(f"- {ch.get('name', '未知')}: {ch.get('role', '')}")

        # 势力
        if characters.get("factions"):
            parts.append("\n### 势力")
            for f in characters["factions"]:
                parts.append(f"- {f.get('name', '未知')}: 首领={f.get('leader', '')}")

        return "\n".join(parts)

    def _parse_storyline_result(self, result: str) -> Dict:
        """解析AI生成的剧情线路结果"""
        try:
            if "```json" in result:
                json_str = result.split("```json")[1].split("```")[0].strip()
            elif "```" in result:
                json_str = result.split("```")[1].split("```")[0].strip()
            else:
                json_str = result

            data = json.loads(json_str)

            main_plot = data.get("main_plot", {})
            if not main_plot and "title" in data:
                main_plot = data

            return {
                "main_plot": main_plot,
                "sub_plots": data.get("sub_plots", ""),
                "character_arcs": data.get("character_arcs", ""),
                "foreshadowings": data.get("foreshadowings", ""),
                "core_conflict": data.get("core_conflict", ""),
                "ending": data.get("ending", ""),
            }
        except json.JSONDecodeError as e:
            self._logger.warning(f"剧情JSON解析失败，尝试修复: {e}")
            try:
                fixed = self._fix_json_string(json_str)
                data = json.loads(fixed)
                main_plot = data.get("main_plot", {})
                if not main_plot and "title" in data:
                    main_plot = data
                return {
                    "main_plot": main_plot,
                    "sub_plots": data.get("sub_plots", ""),
                    "character_arcs": data.get("character_arcs", ""),
                    "foreshadowings": data.get("foreshadowings", ""),
                    "core_conflict": data.get("core_conflict", ""),
                    "ending": data.get("ending", ""),
                }
            except Exception as e2:
                self._logger.error(f"剧情JSON修复失败: {e2}")
        except Exception as e:
            self._logger.error(f"解析剧情线路结果失败: {e}")
        
        return {
            "main_plot": {},
            "sub_plots": "",
            "character_arcs": "",
            "foreshadowings": "",
            "core_conflict": "",
            "ending": "",
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
