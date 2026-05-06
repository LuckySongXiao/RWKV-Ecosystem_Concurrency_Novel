"""优化版全自动管线编排器

支持：
1. 角色信息表并行填表
2. 故事主线生成（基于主题+角色）
3. 全书大纲生成（基于主线）
4. 章节大纲规划（基于全书大纲）
5. 章节切片并行写作（跨章节并发 + 章节内切片串行依赖）
6. 实时进度监测

并行策略:
- 跨章节: 多个章节同时进行切片写作，受 max_concurrency 限制
- 章节内: 切片按顺序依赖（开场→发展→高潮→结尾），前一切片完成后才写下一个
- 批量API: 同一时刻所有就绪的切片合并为一次 big_batch_completions 调用
"""

import json
import os
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from src.core.config import SamplingParams, PipelineConfig, load_config
from src.core.rwkv_client import RWKVClient
from src.core.file_manager import FileManager
from src.core.world_state_engine import WorldStateEngine, WorldStateSummary
from src.core.character_table_generator import CharacterTableGenerator
from src.core.prompt_builder import PromptBuilder
from src.core.json_utils import robust_json_parse, parse_storyline_output, parse_outline_output, extract_volumes_from_truncated
from src.core.logger import Logger


class OptimizedPipelineOrchestrator:
    """优化版全自动管线编排器

    并发策略:
    - 每个阶段使用独立并发数，一个任务对应一个并发
    - 角色生成: character_count 个并发同时创建不同角色
    - 章节大纲: chapters_per_volume 个并发同时规划章节
    - 章节写作: chapter_count 个并发同时写不同章节
    - batch_size 控制单次 API 调用合并的请求数
    """

    def __init__(self, config_path: str, concurrency_config: Dict = None, progress_callback=None):
        self._config = load_config(config_path)
        self._fm = FileManager(self._config.paths)
        self._logger = Logger.get(os.path.join(self._config.paths.project_root, "output", "logs"))
        self._client = RWKVClient(self._config.api, self._logger)
        self._world = WorldStateEngine(self._fm, self._logger)

        default_cc = {
            "character_concurrency": 6,
            "outline_concurrency": 5,
            "chapter_concurrency": 4,
            "batch_size": 8,
        }
        if concurrency_config:
            default_cc.update(concurrency_config)
        self._concurrency_config = default_cc

        self._char_generator = CharacterTableGenerator(self._client, self._config, self._logger)
        self._progress_callback = progress_callback
        
        # 进度监测
        self._progress = {
            "status": "idle",
            "current_stage": "",
            "total_tasks": 0,
            "completed_tasks": 0,
            "chapter_matrix": [],
        }
        self._progress_lock = threading.Lock()

    def run_pipeline(
        self,
        theme: str,
        character_count: int,
        protagonist_names: List[str] = None,
        antagonist_names: List[str] = None,
        volume_count: int = 4,
        chapters_per_volume: int = 10,
        slices_per_chapter: int = 20,
        extra_context: str = "",
    ) -> Dict:
        """运行优化版管线

        Args:
            slices_per_chapter: 基准切片数，实际每章切片数在5~20之间根据剧情自动规划
        """
        self._logger.info("=" * 80)
        self._logger.info("优化版全自动管线启动")
        self._logger.info(f"主题: {theme}")
        self._logger.info(f"角色数量: {character_count}")
        self._logger.info(f"卷数: {volume_count}, 每卷章节数: {chapters_per_volume}")
        self._logger.info(f"每章节切片数: {slices_per_chapter}")
        self._logger.info(f"并发配置: {self._concurrency_config}")
        self._logger.info("=" * 80)

        start_time = time.time()
        result = {
            "theme": theme,
            "character_count": character_count,
            "volumes": volume_count,
            "chapters_per_volume": chapters_per_volume,
            "slices_per_chapter": slices_per_chapter,
            "total_chapters": volume_count * chapters_per_volume,
            "stages": {},
        }

        try:
            # 阶段1: 角色信息表并行填表
            stage1_start = time.time()
            self._update_progress("character_generation", 0, 0)
            characters = self._char_generator.generate_characters_batch(
                theme, character_count, protagonist_names, antagonist_names, extra_context,
                concurrency=self._concurrency_config.get("character_concurrency", 6),
            )
            result["stages"]["character_generation"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage1_start) * 1000,
                "character_count": len(characters),
            }
            self._logger.info(f"[阶段1] 角色信息表完成 - {len(characters)}个角色")

            # 阶段2: 故事主线生成
            stage2_start = time.time()
            self._update_progress("main_storyline", 0, 0)
            main_storyline = self._generate_main_storyline(
                theme, characters, volume_count, chapters_per_volume, extra_context
            )
            result["stages"]["main_storyline"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage2_start) * 1000,
            }
            self._logger.info(f"[阶段2] 故事主线完成")

            # 阶段3: 全书大纲生成
            stage3_start = time.time()
            self._update_progress("full_outline", 0, 0)
            full_outline = self._generate_full_outline(
                theme, characters, main_storyline, volume_count, chapters_per_volume
            )
            result["stages"]["full_outline"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage3_start) * 1000,
            }
            self._logger.info(f"[阶段3] 全书大纲完成")

            # 阶段4: 章节大纲规划
            stage4_start = time.time()
            self._update_progress("chapter_outlines", 0, 0)
            chapter_outlines = self._plan_chapter_outlines(
                full_outline, volume_count, chapters_per_volume,
                characters, main_storyline
            )
            result["stages"]["chapter_outlines"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage4_start) * 1000,
                "chapter_count": len(chapter_outlines),
            }
            self._logger.info(f"[阶段4] 章节大纲完成 - {len(chapter_outlines)}章")

            # 检查章节大纲是否为空
            if not chapter_outlines:
                self._logger.warning("章节大纲为空，使用规则分配生成基本大纲")
                chapter_outlines = self._generate_fallback_chapter_outlines(
                    volume_count, chapters_per_volume, characters, main_storyline
                )
                if not chapter_outlines:
                    self._logger.error("规则分配也失败，无法继续写作")
                    raise ValueError("章节大纲生成失败，请检查全书大纲格式是否正确")

            # 阶段5: 章节切片并行写作
            stage5_start = time.time()
            self._update_progress("chapter_writing", 0, 0)
            chapters = self._write_chapters_with_slices(
                chapter_outlines, characters, main_storyline, slices_per_chapter
            )
            result["stages"]["chapter_writing"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage5_start) * 1000,
                "chapter_count": len(chapters),
            }
            self._logger.info(f"[阶段5] 章节写作完成 - {len(chapters)}章")

            # 保存结果
            self._save_results(characters, main_storyline, full_outline, chapter_outlines, chapters)

            result["total_elapsed_ms"] = (time.time() - start_time) * 1000
            result["status"] = "completed"

            self._logger.info("=" * 80)
            self._logger.info(f"管线执行完成 - 总耗时: {result['total_elapsed_ms']:.0f}ms")
            self._logger.info("=" * 80)

        except Exception as e:
            self._logger.error(f"管线执行失败: {e}")
            import traceback
            self._logger.error(traceback.format_exc())
            result["status"] = "failed"
            result["error"] = str(e)
            result["total_elapsed_ms"] = (time.time() - start_time) * 1000

        with self._progress_lock:
            self._progress["status"] = result.get("status", "completed")
            self._notify_progress()

        return result

    def _generate_main_storyline(
        self,
        theme: str,
        characters: List[Dict],
        volume_count: int,
        chapters_per_volume: int,
        extra_context: str,
    ) -> Dict:
        """生成故事主线（基于主题+角色）- 使用 PromptBuilder"""
        self._logger.info("生成故事主线...")

        chars_summary = self._format_characters_summary(characters)

        prompt = PromptBuilder.build_storyline_prompt(
            theme=theme,
            characters_summary=chars_summary,
            volume_count=volume_count,
            chapters_per_volume=chapters_per_volume,
            extra_context=extra_context,
        )

        sampling = SamplingParams(temperature=0.9, top_p=0.85, max_tokens=3072)
        results = self._client.big_batch_completions(
            contents=[prompt],
            sampling=sampling,
            stream=False,
        )
        result = results[0] if results else ""

        parsed, status = parse_storyline_output(result)
        if parsed:
            self._logger.info(f"故事主线解析成功 (status={status})")
            return parsed

        self._logger.warning(f"故事主线JSON解析失败 (status={status})，尝试通用解析")
        return self._parse_json_result(result)

    def _generate_full_outline(
        self,
        theme: str,
        characters: List[Dict],
        main_storyline: Dict,
        volume_count: int,
        chapters_per_volume: int,
    ) -> Dict:
        """生成全书大纲（基于主线）- 使用 PromptBuilder 增强版"""
        self._logger.info("生成全书大纲...")

        chars_summary = self._format_characters_summary(characters)
        storyline_text = json.dumps(main_storyline, ensure_ascii=False, indent=2)

        prompt = PromptBuilder.build_full_outline_prompt_v2(
            theme=theme,
            characters_summary=chars_summary,
            main_storyline=storyline_text,
            volume_count=volume_count,
            chapters_per_volume=chapters_per_volume,
        )

        sampling = SamplingParams(temperature=0.9, top_p=0.85, max_tokens=4096)
        results = self._client.big_batch_completions(
            contents=[prompt],
            sampling=sampling,
            stream=False,
        )
        result = results[0] if results else ""

        parsed, status = parse_outline_output(result)
        if parsed and isinstance(parsed, dict):
            self._logger.info(f"全书大纲解析成功 (status={status})")
            return parsed

        self._logger.warning(f"全书大纲JSON解析失败 (status={status})，尝试从截断内容提取卷信息")
        volumes = extract_volumes_from_truncated(result)
        if volumes:
            self._logger.info(f"从截断内容中提取到 {len(volumes)} 个卷")
            return {
                "title": "自动提取大纲",
                "genre": theme,
                "volumes": volumes,
                "main_conflict": "",
                "ending_direction": "",
            }

        self._logger.warning("全书大纲所有解析策略均失败，返回空大纲")
        return {"title": "", "genre": theme, "volumes": [], "main_conflict": "", "ending_direction": ""}

    def _plan_chapter_outlines(
        self,
        full_outline: Dict,
        volume_count: int,
        chapters_per_volume: int,
        characters: List[Dict] = None,
        main_storyline: Dict = None,
    ) -> List[Dict]:
        """规划章节大纲（基于全书大纲）- AI增强版

        优先使用AI生成高质量章节大纲，失败时回退到规则分配。
        """
        self._logger.info("规划章节大纲（AI增强模式）...")

        if not full_outline:
            self._logger.warning("全书大纲为空，使用规则分配生成章节大纲")
            return self._generate_fallback_chapter_outlines(
                volume_count, chapters_per_volume, characters, main_storyline
            )

        volumes = full_outline.get("volumes", [])
        if not volumes:
            self._logger.warning("全书大纲中没有卷信息，使用规则分配生成章节大纲")
            return self._generate_fallback_chapter_outlines(
                volume_count, chapters_per_volume, characters, main_storyline
            )

        characters = characters or []
        main_storyline = main_storyline or {}

        chapter_outlines = []
        chapter_id = 1

        for volume in volumes[:volume_count]:
            vol_id = volume.get("volume_id", 1)
            vol_title = volume.get("volume_title", f"第{vol_id}卷")

            ai_outlines = self._ai_generate_chapter_outlines(
                volume, chapter_id, chapters_per_volume, characters, main_storyline
            )

            if ai_outlines:
                chapter_outlines.extend(ai_outlines)
                chapter_id += len(ai_outlines)
                self._logger.info(f"卷 {vol_title} AI生成 {len(ai_outlines)} 章大纲")
            else:
                self._logger.warning(f"卷 {vol_title} AI生成失败，使用规则分配")
                fallback = self._fallback_chapter_outlines(
                    volume, chapter_id, chapters_per_volume
                )
                chapter_outlines.extend(fallback)
                chapter_id += len(fallback)

        self._logger.info(f"章节大纲规划完成 - 共{len(chapter_outlines)}章")
        return chapter_outlines

    def _plan_slice_counts(
        self,
        chapter_outlines: List[Dict],
        base_slices: int = 20,
    ) -> Dict[int, int]:
        """基于章节大纲自动规划每个章节的切片数量（5~20）

        规划依据:
        - synopsis 长度：概要越长说明剧情越复杂，需要更多切片
        - 涉及角色数：出场角色越多需要更多切片
        - 伏笔数量：伏笔多需要更多切片展开
        - 卷首/卷尾章：通常需要更多切片做铺垫或收束

        Returns:
            {chapter_id: slice_count} 映射
        """
        MIN_SLICES = 5
        MAX_SLICES = 20

        if not chapter_outlines:
            return {}

        synopsis_lengths = []
        for ch in chapter_outlines:
            synopsis = ch.get("synopsis", "")
            synopsis_lengths.append(len(synopsis))

        max_syn = max(synopsis_lengths) if synopsis_lengths else 1
        min_syn = min(synopsis_lengths) if synopsis_lengths else 0
        syn_range = max(max_syn - min_syn, 1)

        result = {}
        for i, ch in enumerate(chapter_outlines):
            ch_id = ch.get("chapter_id", i + 1)
            synopsis = ch.get("synopsis", "")
            involved = ch.get("involved_characters", [])
            foreshadow = ch.get("foreshadowing", {})
            vol_id = ch.get("volume_id", 1)

            score = 0.0

            syn_ratio = (len(synopsis) - min_syn) / syn_range
            score += syn_ratio * 4.0

            char_count = len(involved) if isinstance(involved, list) else 0
            score += min(char_count / 5.0, 1.0) * 2.0

            plant_count = len(foreshadow.get("plant", [])) if isinstance(foreshadow, dict) else 0
            resolve_count = len(foreshadow.get("resolve", [])) if isinstance(foreshadow, dict) else 0
            score += min((plant_count + resolve_count) / 3.0, 1.0) * 2.0

            ch_idx_in_vol = ch_id - (vol_id - 1) * 5
            if ch_idx_in_vol <= 1 or ch_idx_in_vol >= 4:
                score += 1.0

            normalized = min(score / 9.0, 1.0)
            slice_count = MIN_SLICES + int(normalized * (MAX_SLICES - MIN_SLICES))
            slice_count = max(MIN_SLICES, min(MAX_SLICES, slice_count))

            result[ch_id] = slice_count

        total = sum(result.values())
        avg = total / len(result) if result else 0
        self._logger.info(f"切片规划完成 - 共{len(result)}章, 切片范围: {min(result.values())}~{max(result.values())}, 平均: {avg:.1f}")

        return result

    def _ai_generate_chapter_outlines(
        self,
        volume: Dict,
        start_chapter_id: int,
        chapters_per_volume: int,
        characters: List[Dict],
        main_storyline: Dict,
    ) -> List[Dict]:
        """使用AI为单卷生成章节大纲（并发）"""
        vol_id = volume.get("volume_id", 1)

        prompts = []
        for i in range(chapters_per_volume):
            ch_id = start_chapter_id + i
            prompt = PromptBuilder.build_chapter_outline_from_volume_prompt(
                volume_info=volume,
                chapter_idx=ch_id,
                total_chapters=chapters_per_volume,
                characters=characters,
                main_storyline=main_storyline,
            )
            prompts.append(prompt)

        batch_size = min(self._concurrency_config.get("outline_concurrency", 5), len(prompts))
        if batch_size <= 0:
            batch_size = 1

        all_results = []

        for batch_idx in range(0, len(prompts), batch_size):
            batch = prompts[batch_idx:batch_idx + batch_size]
            batch_chapter_ids = [
                start_chapter_id + batch_idx + j for j in range(len(batch))
            ]

            sampling = SamplingParams(temperature=0.85, top_p=0.9, max_tokens=1024)

            try:
                results = self._client.big_batch_completions(
                    contents=batch,
                    sampling=sampling,
                    stream=False,
                )

                for j, result in enumerate(results):
                    ch_id = batch_chapter_ids[j]
                    parsed, status = robust_json_parse(result)

                    if parsed and isinstance(parsed, dict):
                        parsed.setdefault("chapter_id", ch_id)
                        parsed.setdefault("volume_id", vol_id)
                        parsed.setdefault("chapter_title", f"第{ch_id}章")
                        parsed.setdefault("synopsis", "")
                        parsed.setdefault("involved_characters", [])
                        parsed.setdefault("involved_factions", [])
                        parsed.setdefault("foreshadowing", {"plant": [], "resolve": []})
                        all_results.append(parsed)
                    else:
                        self._logger.warning(f"章节 {ch_id} AI大纲解析失败 (status={status})，生成基本大纲")
                        all_results.append({
                            "chapter_id": ch_id,
                            "volume_id": vol_id,
                            "chapter_title": f"第{ch_id}章",
                            "synopsis": result[:200] if result else "",
                            "involved_characters": [],
                            "involved_factions": [],
                            "foreshadowing": {"plant": [], "resolve": []},
                        })

            except Exception as e:
                self._logger.error(f"章节大纲AI生成批次失败: {e}")

        if len(all_results) < chapters_per_volume:
            self._logger.warning(
                f"AI生成章节大纲不完整 ({len(all_results)}/{chapters_per_volume})，用规则分配补全"
            )
            existing_ids = {r.get("chapter_id") for r in all_results}
            for i in range(chapters_per_volume):
                ch_id = start_chapter_id + i
                if ch_id not in existing_ids:
                    all_results.append({
                        "chapter_id": ch_id,
                        "volume_id": vol_id,
                        "chapter_title": f"第{ch_id}章",
                        "synopsis": "",
                        "involved_characters": [],
                        "involved_factions": [],
                        "foreshadowing": {"plant": [], "resolve": []},
                    })

        all_results.sort(key=lambda x: x.get("chapter_id", 0))
        return all_results

    def _fallback_chapter_outlines(
        self,
        volume: Dict,
        start_chapter_id: int,
        chapters_per_volume: int,
    ) -> List[Dict]:
        """规则分配章节大纲（AI生成失败时的回退方案）"""
        vol_id = volume.get("volume_id", 1)
        vol_title = volume.get("volume_title", f"第{vol_id}卷")
        events = volume.get("main_events", [])
        chapter_outlines = []

        for i in range(chapters_per_volume):
            if events:
                event_idx = min(i * len(events) // chapters_per_volume, len(events) - 1)
                event = events[event_idx]
            else:
                event = {}

            chapter_outlines.append({
                "chapter_id": start_chapter_id + i,
                "volume_id": vol_id,
                "chapter_title": event.get("event_name", f"第{start_chapter_id + i}章"),
                "synopsis": event.get("description", f"{vol_title}第{i+1}章"),
                "involved_characters": [],
                "involved_factions": [],
                "foreshadowing": {"plant": [], "resolve": []},
            })

        return chapter_outlines

    def _generate_fallback_chapter_outlines(
        self,
        volume_count: int,
        chapters_per_volume: int,
        characters: List[Dict],
        main_storyline: Dict,
    ) -> List[Dict]:
        """全书大纲为空时的规则分配回退方案"""
        self._logger.info(f"使用规则分配生成 {volume_count}卷×{chapters_per_volume}章 的基本大纲")
        chapter_outlines = []
        chapter_id = 1

        for vol_idx in range(volume_count):
            vol_id = vol_idx + 1
            for ch_idx in range(chapters_per_volume):
                chapter_outlines.append({
                    "chapter_id": chapter_id,
                    "volume_id": vol_id,
                    "chapter_title": f"第{chapter_id}章",
                    "synopsis": f"第{vol_id}卷第{ch_idx+1}章",
                    "involved_characters": [],
                    "involved_factions": [],
                    "foreshadowing": {"plant": [], "resolve": []},
                })
                chapter_id += 1

        return chapter_outlines

    def _write_chapters_with_slices(
        self,
        chapter_outlines: List[Dict],
        characters: List[Dict],
        main_storyline: Dict,
        slices_per_chapter: int,
    ) -> List[Dict]:
        """章节切片并行写作 - 跨章节并发 + 章节内切片串行依赖

        每个章节的切片数量由 _plan_slice_counts 自动规划（5~20），
        slices_per_chapter 仅作为基准参考值。
        """
        total_chapters = len(chapter_outlines)

        slice_count_map = self._plan_slice_counts(chapter_outlines, slices_per_chapter)

        total_slices = sum(slice_count_map.values())

        self._logger.info(
            f"开始章节切片并行写作 - {total_chapters}章, 总切片={total_slices}, "
            f"章节并发={self._concurrency_config.get('chapter_concurrency', 4)}"
        )

        self._init_chapter_matrix_variable(total_chapters, slice_count_map)

        if total_chapters == 0:
            self._logger.warning("章节大纲为空，跳过章节写作")
            return []

        MIN_SLICE_CHARS = 200

        style_guide = self._read_style_guide()

        slice_results: Dict[int, Dict[int, str]] = {}
        slice_lock = threading.Lock()

        for chapter in chapter_outlines:
            ch_id = chapter["chapter_id"]
            slice_results[ch_id] = {}

        max_workers = min(self._concurrency_config.get("chapter_concurrency", 4), total_chapters)
        if max_workers <= 0:
            max_workers = 1

        self._logger.info(f"启动 {max_workers} 个并行工作线程")

        def _write_chapter_slices(chapter: Dict) -> Tuple[int, Dict[int, str]]:
            ch_id = chapter["chapter_id"]
            ch_slice_count = slice_count_map.get(ch_id, slices_per_chapter)
            chapter_slice_results: Dict[int, str] = {}

            slice_types = self._build_slice_types(ch_slice_count)

            involved_chars = chapter.get("involved_characters", [])
            involved_factions = chapter.get("involved_factions", [])

            world_state_text = self._get_world_state_text(
                involved_chars, involved_factions, ch_id
            )

            relevant_characters = self._get_relevant_characters(
                characters, involved_chars
            )

            for slice_idx in range(ch_slice_count):
                slice_type = slice_types[slice_idx % len(slice_types)]

                previous_content = ""
                if slice_idx > 0 and (slice_idx - 1) in chapter_slice_results:
                    previous_content = chapter_slice_results[slice_idx - 1]

                prompt = PromptBuilder.build_slice_writing_prompt(
                    chapter_info=chapter,
                    slice_type=slice_type["name"],
                    slice_description=slice_type["description"],
                    slice_idx=slice_idx,
                    total_slices=ch_slice_count,
                    characters=relevant_characters,
                    main_storyline=main_storyline,
                    world_state_text=world_state_text,
                    previous_slice_content=previous_content,
                    style_guide=style_guide,
                )

                self._update_slice_progress(ch_id, slice_idx, "in_progress")

                sampling = SamplingParams(temperature=0.85, top_p=0.9, max_tokens=1024)

                slice_content = ""
                for slice_attempt in range(2):
                    try:
                        results = self._client.big_batch_completions(
                            contents=[prompt],
                            sampling=sampling,
                            stream=False,
                        )
                        result = results[0] if results else ""

                        if len(result.strip()) < MIN_SLICE_CHARS and slice_attempt == 0:
                            self._logger.warning(
                                f"章节{ch_id}切片{slice_idx}内容不足{MIN_SLICE_CHARS}字"
                                f"({len(result.strip())}字)，重试..."
                            )
                            prompt += f"\n\n[重要] 上一次生成内容过短，请确保本次输出不少于{MIN_SLICE_CHARS}个字。"
                            continue

                        slice_content = result
                        break

                    except Exception as e:
                        self._logger.error(f"章节{ch_id}切片{slice_idx}写作失败: {e}")
                        if slice_attempt == 0:
                            continue
                        slice_content = ""

                chapter_slice_results[slice_idx] = slice_content
                self._update_slice_progress(ch_id, slice_idx, "completed" if slice_content else "error", slice_content)

            return ch_id, chapter_slice_results

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chapter = {
                executor.submit(_write_chapter_slices, chapter): chapter
                for chapter in chapter_outlines
            }

            for future in as_completed(future_to_chapter):
                chapter = future_to_chapter[future]
                try:
                    ch_id, chapter_slice_results = future.result()
                    with slice_lock:
                        slice_results[ch_id] = chapter_slice_results
                    self._logger.info(f"章节{ch_id}全部切片写作完成")
                except Exception as e:
                    ch_id = chapter["chapter_id"]
                    self._logger.error(f"章节{ch_id}写作线程异常: {e}")
                    with slice_lock:
                        slice_results[ch_id] = {}

        final_chapters = self._merge_slices_variable(
            chapter_outlines, slice_results, slice_count_map
        )

        self._logger.info(f"章节并行写作完成 - {len(final_chapters)}章")
        return final_chapters

    def _get_world_state_text(
        self,
        involved_characters: List[str],
        involved_factions: List[str],
        chapter_id: int,
    ) -> str:
        """获取与当前章节相关的世界状态文本"""
        try:
            summary = self._world.extract_summary(
                involved_characters, involved_factions, chapter_id
            )
            return summary.format_for_prompt()
        except Exception as e:
            self._logger.warning(f"获取世界状态失败: {e}")
            return ""

    def _get_relevant_characters(
        self,
        all_characters: List[Dict],
        involved_ids: List[str],
    ) -> List[Dict]:
        """获取与当前章节相关的角色信息"""
        if not involved_ids:
            return all_characters[:8]

        relevant = []
        id_set = set(involved_ids)
        for ch in all_characters:
            ch_name = ch.get("name", "")
            ch_id = ch.get("character_id", "")
            if ch_name in id_set or ch_id in id_set:
                relevant.append(ch)

        if len(relevant) < 3:
            for ch in all_characters:
                if ch not in relevant:
                    relevant.append(ch)
                if len(relevant) >= 8:
                    break

        return relevant

    def _merge_slices(
        self,
        chapter_outlines: List[Dict],
        slice_results: Dict,
        slices_per_chapter: int,
        slice_types: List[Dict],
    ) -> List[Dict]:
        """合并切片为完整章节 - 智能过渡衔接"""
        final_chapters = []

        for chapter in chapter_outlines:
            ch_id = chapter["chapter_id"]
            slices = slice_results.get(ch_id, {})

            merged_parts = []

            for slice_idx in range(slices_per_chapter):
                content = slices.get(slice_idx, "")

                if not content:
                    continue

                content = content.strip()

                if slice_idx > 0 and merged_parts:
                    content = self._smooth_transition(merged_parts[-1], content)

                slice_type = slice_types[slice_idx % len(slice_types)]
                merged_parts.append(content)

            merged_content = "\n\n".join(merged_parts)

            if merged_content:
                merged_content = self._clean_merged_content(merged_content)

            final_chapters.append({
                "chapter_id": ch_id,
                "volume_id": chapter["volume_id"],
                "chapter_title": chapter.get("chapter_title", f"第{ch_id}章"),
                "content": merged_content,
                "slices": slices,
                "slice_count": len([s for s in slices.values() if s]),
            })

        return final_chapters

    def _merge_slices_variable(
        self,
        chapter_outlines: List[Dict],
        slice_results: Dict,
        slice_count_map: Dict[int, int],
    ) -> List[Dict]:
        """合并切片为完整章节（支持每章不同切片数）"""
        final_chapters = []

        for chapter in chapter_outlines:
            ch_id = chapter["chapter_id"]
            slices = slice_results.get(ch_id, {})
            ch_slice_count = slice_count_map.get(ch_id, 10)

            slice_types = self._build_slice_types(ch_slice_count)

            merged_parts = []

            for slice_idx in range(ch_slice_count):
                content = slices.get(slice_idx, "")

                if not content:
                    continue

                content = content.strip()

                if slice_idx > 0 and merged_parts:
                    content = self._smooth_transition(merged_parts[-1], content)

                merged_parts.append(content)

            merged_content = "\n\n".join(merged_parts)

            if merged_content:
                merged_content = self._clean_merged_content(merged_content)

            final_chapters.append({
                "chapter_id": ch_id,
                "volume_id": chapter["volume_id"],
                "chapter_title": chapter.get("chapter_title", f"第{ch_id}章"),
                "content": merged_content,
                "slices": slices,
                "slice_count": len([s for s in slices.values() if s]),
            })

        return final_chapters

    def _smooth_transition(self, prev_content: str, next_content: str) -> str:
        """平滑切片间的过渡衔接

        检测并移除重复内容，确保叙事连贯。
        """
        if not prev_content or not next_content:
            return next_content

        prev_last_line = prev_content.strip().split("\n")[-1] if prev_content.strip() else ""
        next_first_line = next_content.strip().split("\n")[0] if next_content.strip() else ""

        if prev_last_line and next_first_line:
            similarity = self._text_similarity(prev_last_line, next_first_line)
            if similarity > 0.6:
                lines = next_content.strip().split("\n")
                next_content = "\n".join(lines[1:])

        next_content = re.sub(r'^#{1,3}\s+', '', next_content, count=1)

        return next_content

    def _text_similarity(self, text1: str, text2: str) -> float:
        """简单文本相似度计算（基于字符重叠）"""
        if not text1 or not text2:
            return 0.0

        set1 = set(text1)
        set2 = set(text2)
        intersection = set1 & set2
        union = set1 | set2

        return len(intersection) / len(union) if union else 0.0

    def _clean_merged_content(self, content: str) -> str:
        """清理合并后的内容"""
        content = re.sub(r'\n{3,}', '\n\n', content)
        content = re.sub(r'#{1,6}\s*#{1,6}', '#', content)
        content = content.strip()

        return content

    def _read_style_guide(self) -> str:
        """读取写作风格指南"""
        try:
            return self._fm.read_style_guide()
        except Exception:
            return ""

    def _build_slice_types(self, slices_per_chapter: int) -> List[Dict]:
        """根据切片数量构建切片类型列表

        20切片布局: 4行 × 5列
        - 第1行(0-4): 开场阶段 (场景引入/角色登场/氛围营造/背景铺垫/悬念设置)
        - 第2行(5-9): 发展阶段 (情节推进/冲突升级/关系变化/线索展开/危机逼近)
        - 第3行(10-14): 高潮阶段 (冲突爆发/关键转折/生死抉择/真相揭露/命运交汇)
        - 第4行(15-19): 结尾阶段 (冲突解决/情感升华/伏笔收束/余韵营造/承前启后)
        """
        rows = 4
        cols = max(1, (slices_per_chapter + rows - 1) // rows)

        row_templates = [
            ["场景引入", "角色登场", "氛围营造", "背景铺垫", "悬念设置"],
            ["情节推进", "冲突升级", "关系变化", "线索展开", "危机逼近"],
            ["冲突爆发", "关键转折", "生死抉择", "真相揭露", "命运交汇"],
            ["冲突解决", "情感升华", "伏笔收束", "余韵营造", "承前启后"],
        ]

        row_names = ["开场", "发展", "高潮", "结尾"]

        slice_types = []
        for row_idx in range(rows):
            for col_idx in range(cols):
                slice_idx = row_idx * cols + col_idx
                if slice_idx >= slices_per_chapter:
                    break
                template = row_templates[row_idx]
                name = template[col_idx % len(template)]
                description = f"{row_names[row_idx]}阶段 - {name}"
                slice_types.append({
                    "name": name,
                    "description": description,
                    "row": row_idx,
                    "col": col_idx,
                    "row_name": row_names[row_idx],
                })

        while len(slice_types) < slices_per_chapter:
            idx = len(slice_types)
            row_idx = idx // cols
            col_idx = idx % cols
            row_name = row_names[min(row_idx, len(row_names) - 1)]
            template = row_templates[min(row_idx, len(row_templates) - 1)]
            name = template[col_idx % len(template)]
            slice_types.append({
                "name": name,
                "description": f"{row_name}阶段 - {name}",
                "row": row_idx,
                "col": col_idx,
                "row_name": row_name,
            })

        return slice_types[:slices_per_chapter]

    def _init_chapter_matrix(self, total_chapters: int, slices_per_chapter: int):
        """初始化章节矩阵"""
        with self._progress_lock:
            self._progress["chapter_matrix"] = []
            for ch_id in range(1, total_chapters + 1):
                chapter_row = {
                    "chapter_id": ch_id,
                    "slices": [],
                    "status": "pending",
                }
                for slice_idx in range(slices_per_chapter):
                    chapter_row["slices"].append({
                        "slice_idx": slice_idx,
                        "status": "pending",
                        "content": "",
                    })
                self._progress["chapter_matrix"].append(chapter_row)

    def _init_chapter_matrix_variable(self, total_chapters: int, slice_count_map: Dict[int, int]):
        """初始化章节矩阵（支持每章不同切片数）"""
        with self._progress_lock:
            self._progress["chapter_matrix"] = []
            for ch_id in range(1, total_chapters + 1):
                ch_slice_count = slice_count_map.get(ch_id, 10)
                chapter_row = {
                    "chapter_id": ch_id,
                    "slices": [],
                    "status": "pending",
                    "slice_count": ch_slice_count,
                }
                for slice_idx in range(ch_slice_count):
                    chapter_row["slices"].append({
                        "slice_idx": slice_idx,
                        "status": "pending",
                        "content": "",
                    })
                self._progress["chapter_matrix"].append(chapter_row)

    def _update_slice_progress(self, chapter_id: int, slice_idx: int, status: str, content: str = ""):
        """更新切片进度"""
        with self._progress_lock:
            for chapter_row in self._progress["chapter_matrix"]:
                if chapter_row["chapter_id"] == chapter_id:
                    if slice_idx < len(chapter_row["slices"]):
                        chapter_row["slices"][slice_idx]["status"] = status
                        if content:
                            chapter_row["slices"][slice_idx]["content"] = content
                    
                    completed_count = sum(
                        1 for s in chapter_row["slices"] if s["status"] == "completed"
                    )
                    if completed_count == len(chapter_row["slices"]):
                        chapter_row["status"] = "completed"
                    elif completed_count > 0:
                        chapter_row["status"] = "in_progress"
                    break
            self._notify_progress()

    def _update_progress(self, stage: str, completed: int, total: int):
        """更新全局进度"""
        with self._progress_lock:
            self._progress["current_stage"] = stage
            self._progress["completed_tasks"] = completed
            self._progress["total_tasks"] = total
            self._progress["status"] = "running"
            self._notify_progress()

    def _notify_progress(self):
        """通知进度变更（在 _progress_lock 内调用）"""
        if self._progress_callback:
            try:
                snapshot = self._progress.copy()
                self._progress_callback(snapshot)
            except Exception:
                pass

    def get_progress(self) -> Dict:
        """获取当前进度"""
        with self._progress_lock:
            return self._progress.copy()

    def _format_characters_summary(self, characters: List[Dict]) -> str:
        """格式化角色摘要"""
        parts = []
        for ch in characters:
            name = ch.get("name", "未知")
            role = ch.get("role_type", "")
            identity = ch.get("identity", "")
            parts.append(f"- {name} ({role}): {identity}")
        return "\n".join(parts)

    def _parse_json_result(self, result: str) -> Dict:
        """解析JSON结果（使用鲁棒解析工具）"""
        parsed, status = robust_json_parse(result)
        if parsed is not None:
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                return parsed[0]
        return {}

    def _save_results(self, characters, main_storyline, full_outline, chapter_outlines, chapters):
        """保存结果"""
        self._logger.info("保存管线结果...")

        # 保存角色
        char_path = os.path.join(self._fm.output_dir, "characters.json")
        self._fm.write_json(char_path, characters)

        # 保存主线
        storyline_path = os.path.join(self._fm.output_dir, "main_storyline.json")
        self._fm.write_json(storyline_path, main_storyline)

        # 保存大纲
        outline_path = self._fm.outline_path()
        self._fm.write_json(outline_path, full_outline)

        # 保存章节大纲
        chapters_path = self._fm.chapters_path()
        self._fm.write_jsonl(chapters_path, chapter_outlines)

        # 保存章节正文
        draft_dir = os.path.join(self._fm.output_dir, "draft")
        os.makedirs(draft_dir, exist_ok=True)
        for chapter in chapters:
            ch_id = chapter.get("chapter_id", 0)
            draft_path = os.path.join(draft_dir, f"{ch_id:04d}.md")
            self._fm.write_markdown(draft_path, chapter.get("content", ""))

        self._logger.info("管线结果保存完成")
