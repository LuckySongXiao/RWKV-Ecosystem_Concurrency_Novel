"""全自动管线编排器

实现完整的自动化小说创作管线：
1. 批量角色设定 → 2. 剧情线路生成 → 3. 全书大纲生成 → 
4. 卷宗拆分 → 5. 章节规划 → 6. 模块级并行写作

支持用户自定义并发上限，自动规划任务分配。
"""

import json
import os
import time
from typing import Dict, List, Optional, Tuple
from src.core.config import SamplingParams, PipelineConfig, load_config
from src.core.rwkv_client import RWKVClient
from src.core.file_manager import FileManager
from src.core.world_state_engine import WorldStateEngine
from src.core.character_batch_generator import CharacterBatchGenerator
from src.core.storyline_generator import StorylineGenerator
from src.core.logger import Logger
from src.core.prompt_builder import PromptBuilder
from src.core.error_handler import ErrorHandler


class AutoPipelineOrchestrator:
    """全自动管线编排器"""

    def __init__(self, config_path: str, max_concurrency: int = 200):
        """
        Args:
            config_path: 配置文件路径
            max_concurrency: 最大并发数（用户可设定）
        """
        self._config = load_config(config_path)
        self._fm = FileManager(self._config.paths)
        self._logger = Logger.get(os.path.join(self._config.paths.project_root, "output", "logs"))
        self._client = RWKVClient(self._config.api, self._logger)
        self._world = WorldStateEngine(self._fm, self._logger)
        self._error_handler = ErrorHandler(self._logger)
        
        # 用户设定的并发上限
        self._max_concurrency = max_concurrency
        
        # 子生成器
        self._char_generator = CharacterBatchGenerator(self._client, self._config, self._logger)
        self._storyline_generator = StorylineGenerator(self._client, self._config, self._logger)

    def run_full_pipeline(
        self,
        theme: str,
        protagonist_names: List[str],
        antagonist_names: Optional[List[str]] = None,
        volume_count: int = 4,
        chapters_per_volume: int = 10,
        extra_context: str = "",
    ) -> Dict:
        """运行完整的全自动管线

        Args:
            theme: 小说主题
            protagonist_names: 主角名称列表（用户设定）
            antagonist_names: 反派名称列表（可选）
            volume_count: 卷数
            chapters_per_volume: 每卷章节数
            extra_context: 额外设定上下文

        Returns:
            管线执行结果
        """
        self._logger.info("=" * 80)
        self._logger.info("全自动管线启动")
        self._logger.info(f"主题: {theme}")
        self._logger.info(f"主角: {', '.join(protagonist_names)}")
        self._logger.info(f"卷数: {volume_count}, 每卷章节数: {chapters_per_volume}")
        self._logger.info(f"并发上限: {self._max_concurrency}")
        self._logger.info("=" * 80)

        start_time = time.time()
        result = {
            "theme": theme,
            "volumes": volume_count,
            "chapters_per_volume": chapters_per_volume,
            "total_chapters": volume_count * chapters_per_volume,
            "stages": {},
        }

        try:
            # 阶段1: 批量角色设定
            stage1_start = time.time()
            characters = self._char_generator.generate_characters(
                theme, protagonist_names, antagonist_names,
                volume_count, chapters_per_volume, extra_context
            )
            result["stages"]["character_generation"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage1_start) * 1000,
                "protagonists": len(characters["protagonists"]),
                "antagonists": len(characters["antagonists"]),
                "supporting": len(characters["supporting"]),
                "factions": len(characters["factions"]),
            }
            self._logger.info(f"[阶段1] 角色设定完成 - 耗时: {result['stages']['character_generation']['elapsed_ms']:.0f}ms")

            # 阶段2: 剧情线路生成
            stage2_start = time.time()
            storyline = self._storyline_generator.generate_storyline(
                theme, characters, volume_count, chapters_per_volume, extra_context
            )
            result["stages"]["storyline_generation"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage2_start) * 1000,
                "sub_plots": len(storyline.get("sub_plots", [])),
                "foreshadowings": len(storyline.get("foreshadowings", [])),
            }
            self._logger.info(f"[阶段2] 剧情线路完成 - 耗时: {result['stages']['storyline_generation']['elapsed_ms']:.0f}ms")

            # 阶段3: 全书大纲生成
            stage3_start = time.time()
            outline = self._generate_full_outline(theme, characters, storyline, volume_count, chapters_per_volume)
            result["stages"]["outline_generation"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage3_start) * 1000,
                "title": outline.get("title", ""),
                "volumes": len(outline.get("volumes", [])),
            }
            self._logger.info(f"[阶段3] 全书大纲完成 - 耗时: {result['stages']['outline_generation']['elapsed_ms']:.0f}ms")

            # 阶段4: 卷宗拆分与章节规划
            stage4_start = time.time()
            volumes = self._split_into_volumes(outline, volume_count, chapters_per_volume)
            chapters = self._plan_chapters(volumes)
            result["stages"]["volume_splitting"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage4_start) * 1000,
                "volumes": len(volumes),
                "chapters": len(chapters),
            }
            self._logger.info(f"[阶段4] 卷宗拆分完成 - 耗时: {result['stages']['volume_splitting']['elapsed_ms']:.0f}ms")

            # 阶段5: 章节剧情模块规划
            stage5_start = time.time()
            chapters_with_modules = self._plan_chapter_modules(chapters, storyline)
            result["stages"]["module_planning"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage5_start) * 1000,
                "chapters_with_modules": len(chapters_with_modules),
            }
            self._logger.info(f"[阶段5] 章节模块规划完成 - 耗时: {result['stages']['module_planning']['elapsed_ms']:.0f}ms")

            # 阶段6: 并发写正文（模块级并行）
            stage6_start = time.time()
            drafts = self._write_chapters_concurrently(chapters_with_modules)
            result["stages"]["concurrent_writinging"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage6_start) * 1000,
                "drafts": len(drafts),
            }
            self._logger.info(f"[阶段6] 并发写作完成 - 耗时: {result['stages']['concurrent_writinging']['elapsed_ms']:.0f}ms")

            # 保存结果
            self._save_pipeline_results(characters, storyline, outline, volumes, chapters_with_modules, drafts)

            result["total_elapsed_ms"] = (time.time() - start_time) * 1000
            result["status"] = "completed"

            self._logger.info("=" * 80)
            self._logger.info(f"管线执行完成 - 总耗时: {result['total_elapsed_ms']:.0f}ms")
            self._logger.info("=" * 80)

        except Exception as e:
            self._logger.error(f"管线执行失败: {e}")
            result["status"] = "failed"
            result["error"] = str(e)
            result["total_elapsed_ms"] = (time.time() - start_time) * 1000

        return result

    def _generate_full_outline(
        self,
        theme: str,
        characters: Dict,
        storyline: Dict,
        volume_count: int,
        chapters_per_volume: int,
    ) -> Dict:
        """生成全书大纲"""
        self._logger.info("生成全书大纲...")

        prompt = f"""你是一位资深小说总编。请基于以下信息，生成一部长篇小说的完整大纲。

## 基本信息
- 题材类型: {theme}
- 总卷数: {volume_count} 卷
- 每卷章节数: {chapters_per_volume} 章

## 角色体系
{json.dumps(characters, ensure_ascii=False, indent=2)}

## 剧情线路
{json.dumps(storyline, ensure_ascii=False, indent=2)}

## 输出格式
```json
{{
  "title": "书名",
  "genre": "{theme}",
  "volumes": [
    {{
      "volume_id": 1,
      "volume_title": "卷标题",
      "theme": "本卷主题概述",
      "chapter_count": {chapters_per_volume},
      "events": "本卷关键事件，用分号分隔",
      "character_arcs": "本卷角色发展概述"
    }}
  ],
  "main_conflict": "核心冲突描述",
  "ending_direction": "结局方向概述",
  "foreshadowing_plan": "跨卷伏笔规划概述"
}}
```

请直接输出JSON，不要包含额外说明。"""

        sampling = SamplingParams(temperature=0.9, top_p=0.85, max_tokens=4096)
        result = self._client.openai_chat_completions(
            messages=[{"role": "user", "content": prompt}],
            sampling=sampling,
            stream=False,
        )

        return self._parse_json_result(result)

    def _split_into_volumes(self, outline: Dict, volume_count: int, chapters_per_volume: int) -> List[Dict]:
        """将大纲拆分为卷宗"""
        self._logger.info(f"将大纲拆分为 {volume_count} 卷...")
        
        volumes = outline.get("volumes", [])
        
        # 确保卷数匹配
        while len(volumes) < volume_count:
            volumes.append({
                "volume_id": len(volumes) + 1,
                "volume_title": f"第{len(volumes) + 1}卷",
                "theme": "待定",
                "chapter_count": chapters_per_volume,
                "main_events": [],
            })
        
        # 调整章节数
        for vol in volumes[:volume_count]:
            vol["chapter_count"] = chapters_per_volume
        
        return volumes[:volume_count]

    def _plan_chapters(self, volumes: List[Dict]) -> List[Dict]:
        """规划所有章节"""
        self._logger.info("规划章节...")
        
        chapters = []
        chapter_id = 1
        
        for volume in volumes:
            vol_id = volume.get("volume_id", 1)
            chapter_count = volume.get("chapter_count", 10)
            
            for i in range(chapter_count):
                chapters.append({
                    "chapter_id": chapter_id,
                    "volume_id": vol_id,
                    "chapter_title": f"第{chapter_id}章",
                    "synopsis": "",
                    "involved_characters": [],
                    "involved_factions": [],
                    "foreshadowing": {"plant": [], "resolve": []},
                })
                chapter_id += 1
        
        return chapters

    def _plan_chapter_modules(self, chapters: List[Dict], storyline: Dict) -> List[Dict]:
        """为每个章节规划剧情模块"""
        self._logger.info("规划章节剧情模块...")
        
        # 从剧情线路中提取模块类型
        sub_plots = storyline.get("sub_plots", [])
        main_plot = storyline.get("main_plot", {})
        
        for chapter in chapters:
            # 为每个章节分配剧情模块
            modules = []
            
            # 主线模块（每个章节都有）
            modules.append({
                "module_id": f"ch{chapter['chapter_id']}_main",
                "module_type": "main_plot",
                "module_name": "主线剧情",
                "description": f"推进主线剧情",
                "priority": 1,
            })
            
            # 根据章节位置分配支线模块
            chapter_idx = chapter["chapter_id"] - 1
            for plot_idx, sub_plot in enumerate(sub_plots[:3]):  # 最多3个支线
                if chapter_idx % (plot_idx + 2) == 0:  # 交错分配
                    modules.append({
                        "module_id": f"ch{chapter['chapter_id']}_{sub_plot.get('name', 'sub')}",
                        "module_type": "sub_plot",
                        "module_name": sub_plot.get("name", "支线"),
                        "description": sub_plot.get("description", ""),
                        "priority": 2 + plot_idx,
                    })
            
            chapter["modules"] = modules
        
        return chapters

    def _write_chapters_concurrently(self, chapters: List[Dict]) -> List[Dict]:
        """并发写作所有章节（模块级并行）"""
        self._logger.info(f"开始并发写作 {len(chapters)} 个章节...")
        
        # 构造所有模块的prompts
        all_prompts = []
        module_mapping = []  # 记录每个prompt对应的章节和模块
        
        for chapter in chapters:
            for module in chapter.get("modules", []):
                prompt = self._build_module_prompt(chapter, module)
                all_prompts.append(prompt)
                module_mapping.append({
                    "chapter_id": chapter["chapter_id"],
                    "module_id": module["module_id"],
                    "module_type": module["module_type"],
                })
        
        # 分批并发
        drafts = {}
        batch_size = min(self._max_concurrency, len(all_prompts))
        
        for i in range(0, len(all_prompts), batch_size):
            batch = all_prompts[i:i + batch_size]
            batch_mapping = module_mapping[i:i + batch_size]
            
            self._logger.info(f"批次 {i // batch_size + 1}: {len(batch)} 个模块")
            
            sampling = SamplingParams(temperature=0.85, top_p=0.9, max_tokens=2048)
            
            try:
                results = self._client.big_batch_completions(
                    contents=batch,
                    sampling=sampling,
                    stream=False,
                )
                
                # 将结果按章节组织
                for j, result in enumerate(results):
                    mapping = batch_mapping[j]
                    ch_id = mapping["chapter_id"]
                    
                    if ch_id not in drafts:
                        drafts[ch_id] = {
                            "chapter_id": ch_id,
                            "modules": {},
                            "content": "",
                        }
                    
                    drafts[ch_id]["modules"][mapping["module_id"]] = result
                    
            except Exception as e:
                self._logger.error(f"批次 {i // batch_size + 1} 失败: {e}")
        
        # 合并各模块内容为完整章节
        final_drafts = []
        for ch_id in sorted(drafts.keys()):
            draft = drafts[ch_id]
            merged_content = []
            
            # 按优先级合并模块
            chapter = next((c for c in chapters if c["chapter_id"] == ch_id), None)
            if chapter:
                for module in chapter.get("modules", []):
                    module_id = module["module_id"]
                    if module_id in draft["modules"]:
                        merged_content.append(f"## {module['module_name']}\n")
                        merged_content.append(draft["modules"][module_id])
                        merged_content.append("")
            
            draft["content"] = "\n".join(merged_content)
            final_drafts.append(draft)
        
        self._logger.info(f"并发写作完成 - 共 {len(final_drafts)} 章")
        return final_drafts

    def _build_module_prompt(self, chapter: Dict, module: Dict) -> str:
        """为单个模块构造写作prompt"""
        return (
            f"User: 请基于以下信息，撰写小说的{module['module_name']}部分。\n"
            f"\n## 章节信息\n"
            f"章节编号: 第{chapter['chapter_id']}章\n"
            f"章节标题: {chapter.get('chapter_title', '')}\n"
            f"章节概要: {chapter.get('synopsis', '')}\n"
            f"\n## 模块信息\n"
            f"模块类型: {module['module_type']}\n"
            f"模块名称: {module['module_name']}\n"
            f"模块描述: {module.get('description', '')}\n"
            f"\n## 写作要求\n"
            f"1. 请使用Markdown格式\n"
            f"2. 保持叙事连贯性\n"
            f"3. 注重角色塑造和情节推进\n"
            f"4. 符合题材类型的写作风格\n"
            f"\nAssistant: "
        )

    def _save_pipeline_results(
        self,
        characters: Dict,
        storyline: Dict,
        outline: Dict,
        volumes: List[Dict],
        chapters: List[Dict],
        drafts: List[Dict],
    ):
        """保存管线执行结果"""
        self._logger.info("保存管线结果...")
        
        # 保存角色设定
        char_path = os.path.join(self._fm.output_dir, "characters.json")
        self._fm.write_json(char_path, characters)
        
        # 保存剧情线路
        storyline_path = os.path.join(self._fm.output_dir, "storyline.json")
        self._fm.write_json(storyline_path, storyline)
        
        # 保存大纲
        outline_path = self._fm.outline_path()
        self._fm.write_json(outline_path, outline)
        
        # 保存卷宗
        volumes_path = self._fm.volumes_path()
        self._fm.write_jsonl(volumes_path, volumes)
        
        # 保存章节
        chapters_path = self._fm.chapters_path()
        self._fm.write_jsonl(chapters_path, chapters)
        
        # 保存初稿
        draft_dir = os.path.join(self._fm.output_dir, "draft")
        os.makedirs(draft_dir, exist_ok=True)
        for draft in drafts:
            ch_id = draft.get("chapter_id", 0)
            draft_path = os.path.join(draft_dir, f"{ch_id:04d}.md")
            self._fm.write_markdown(draft_path, draft.get("content", ""))
        
        self._logger.info("管线结果保存完成")

    def _parse_json_result(self, result: str) -> Dict:
        """解析JSON结果（带错误恢复）"""
        try:
            # 尝试提取JSON代码块
            if "```json" in result:
                json_str = result.split("```json")[1].split("```")[0].strip()
            elif "```" in result:
                json_str = result.split("```")[1].split("```")[0].strip()
            else:
                json_str = result
            
            # 尝试解析
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            self._logger.warning(f"JSON解析失败，尝试修复: {e}")
            try:
                # 尝试修复常见JSON错误
                fixed = self._fix_json_string(json_str)
                return json.loads(fixed)
            except Exception as e2:
                self._logger.error(f"JSON修复失败: {e2}")
                return {}
        except Exception as e:
            self._logger.error(f"解析JSON失败: {e}")
            return {}
    
    def _fix_json_string(self, json_str: str) -> str:
        """尝试修复常见的JSON格式错误"""
        import re
        
        # 修复未闭合的字符串
        lines = json_str.split('\n')
        fixed_lines = []
        for line in lines:
            # 检查是否有未闭合的字符串
            quote_count = line.count('"') - line.count('\\"')
            if quote_count % 2 != 0:
                # 尝试在行末添加引号
                if not line.rstrip().endswith('"'):
                    line = line.rstrip() + '"'
            fixed_lines.append(line)
        
        fixed = '\n'.join(fixed_lines)
        
        # 移除尾随逗号
        fixed = re.sub(r',\s*([}\]])', r'\1', fixed)
        
        return fixed
