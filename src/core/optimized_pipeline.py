"""优化版全自动管线编排器

支持：
1. 角色信息表并行填表
2. 故事主线生成（基于主题+角色）
3. 全书大纲生成（基于主线）
4. 章节大纲规划（基于全书大纲）
5. 章节切片轮次批量并发写作
6. 实时进度监测

并行策略:
- 轮次批量: 每一轮收集所有活跃章节的当前切片，合并为一次 chat_completions_v2 批量请求
- 章节内: 切片按顺序依赖（开场→发展→高潮→结尾），前一切片完成后才写下一个
- 采样参数: 切片写作启用 alpha_presence/alpha_frequency + alpha_decay=0.996 防止重复输出
- 批量分组: 相同采样参数的切片合并为一组请求，减少 API 调用次数
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
            "chapter_concurrency": 8,
            "batch_size": 8,
        }
        if concurrency_config:
            default_cc.update(concurrency_config)
        self._concurrency_config = default_cc

        self._char_generator = CharacterTableGenerator(self._client, self._config, self._logger)
        self._progress_callback = progress_callback
        
        self._progress = {
            "status": "idle",
            "current_stage": "",
            "total_tasks": 0,
            "completed_tasks": 0,
            "chapter_matrix": [],
        }
        self._progress_lock = threading.Lock()
        self._last_notify_time = 0.0
        self._notify_min_interval = 0.3

    def _checkpoint_path(self) -> str:
        return os.path.join(self._fm.output_dir, ".cache", "pipeline_checkpoint.json")

    def _save_checkpoint(self, stage: str, data: Dict):
        try:
            cache_dir = os.path.join(self._fm.output_dir, ".cache")
            os.makedirs(cache_dir, exist_ok=True)
            checkpoint = {
                "stage": stage,
                "timestamp": time.time(),
                "data": data,
            }
            with open(self._checkpoint_path(), 'w', encoding='utf-8') as f:
                json.dump(checkpoint, f, ensure_ascii=False, indent=2)
            self._logger.info(f"检查点已保存: {stage}")
        except Exception as e:
            self._logger.warning(f"保存检查点失败: {e}")

    def _load_checkpoint(self) -> Optional[Dict]:
        try:
            cp_path = self._checkpoint_path()
            if not os.path.exists(cp_path):
                return None
            with open(cp_path, 'r', encoding='utf-8') as f:
                checkpoint = json.load(f)
            stage = checkpoint.get("stage", "")
            data = checkpoint.get("data", {})
            self._logger.info(f"发现检查点: 阶段={stage}, 时间={checkpoint.get('timestamp', 0)}")
            return checkpoint
        except Exception as e:
            self._logger.warning(f"加载检查点失败: {e}")
            return None

    def _load_cached_stage(self, name: str):
        try:
            cache_dir = os.path.join(self._fm.output_dir, ".cache")
            path = os.path.join(cache_dir, f"{name}.json")
            if not os.path.exists(path):
                return None
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            self._logger.info(f"从缓存加载: {name}")
            return data
        except Exception as e:
            self._logger.warning(f"加载缓存失败 ({name}): {e}")
            return None

    def _clear_checkpoint(self):
        try:
            cp_path = self._checkpoint_path()
            if os.path.exists(cp_path):
                os.remove(cp_path)
                self._logger.info("检查点已清除")
        except Exception:
            pass

    def run_pipeline(
        self,
        theme: str,
        character_count: int,
        protagonist_names: List[str] = None,
        antagonist_names: List[str] = None,
        volume_count: int = 4,
        chapters_per_volume: int = 10,
        slices_per_chapter: int = 10,
        extra_context: str = "",
        resume: bool = False,
    ) -> Dict:
        """运行优化版管线

        Args:
            slices_per_chapter: 最大切片数，实际每章切片数在3~slices_per_chapter之间根据剧情自动规划
            resume: 是否从断点恢复，自动加载已缓存阶段的结果
        """
        self._logger.info("=" * 80)
        self._logger.info("优化版全自动管线启动")
        if resume:
            self._logger.info("模式: 断点恢复")
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

        pipeline_params = {
            "theme": theme,
            "character_count": character_count,
            "protagonist_names": protagonist_names,
            "antagonist_names": antagonist_names,
            "volume_count": volume_count,
            "chapters_per_volume": chapters_per_volume,
            "slices_per_chapter": slices_per_chapter,
            "extra_context": extra_context,
        }

        checkpoint = self._load_checkpoint() if resume else None
        resume_from = ""
        if checkpoint and resume:
            resume_from = checkpoint.get("stage", "")
            cp_data = checkpoint.get("data", {})
            cp_params = cp_data.get("pipeline_params", {})
            if cp_params.get("theme") != theme or cp_params.get("volume_count") != volume_count:
                self._logger.warning("检查点参数不匹配，从头开始")
                resume_from = ""
                checkpoint = None
            else:
                self._logger.info(f"将从阶段 '{resume_from}' 之后恢复")

        try:
            # 阶段1: 角色信息表并行填表
            characters = None
            if resume and resume_from and resume_from not in ("character_generation",):
                characters = self._load_cached_stage("characters")
                if characters:
                    self._logger.info(f"[阶段1] 从缓存恢复 - {len(characters)}个角色")
                    result["stages"]["character_generation"] = {"status": "resumed", "character_count": len(characters)}

            if characters is None:
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
                self._cache_interim("characters", characters)
                self._save_checkpoint("character_generation", {"pipeline_params": pipeline_params})

            # 阶段2: 故事主线生成
            main_storyline = None
            if resume and resume_from and resume_from not in ("character_generation", "main_storyline"):
                main_storyline = self._load_cached_stage("main_storyline")
                if main_storyline:
                    self._logger.info("[阶段2] 从缓存恢复故事主线")
                    result["stages"]["main_storyline"] = {"status": "resumed"}

            if main_storyline is None:
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
                self._cache_interim("main_storyline", main_storyline)
                self._save_checkpoint("main_storyline", {"pipeline_params": pipeline_params})

            # 阶段3: 全书大纲生成
            full_outline = None
            if resume and resume_from and resume_from not in ("character_generation", "main_storyline", "full_outline"):
                full_outline = self._load_cached_stage("full_outline")
                if full_outline:
                    self._logger.info("[阶段3] 从缓存恢复全书大纲")
                    result["stages"]["full_outline"] = {"status": "resumed"}

            if full_outline is None:
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
                self._cache_interim("full_outline", full_outline)
                self._save_checkpoint("full_outline", {"pipeline_params": pipeline_params})

            # 阶段4: 章节大纲规划
            chapter_outlines = None
            if resume and resume_from and resume_from not in ("character_generation", "main_storyline", "full_outline", "chapter_outlines"):
                chapter_outlines = self._load_cached_stage("chapter_outlines")
                if chapter_outlines:
                    self._logger.info(f"[阶段4] 从缓存恢复章节大纲 - {len(chapter_outlines)}章")
                    result["stages"]["chapter_outlines"] = {"status": "resumed", "chapter_count": len(chapter_outlines)}

            if chapter_outlines is None:
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
                self._cache_interim("chapter_outlines", chapter_outlines)
                self._save_checkpoint("chapter_outlines", {"pipeline_params": pipeline_params})

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
            chapters, slice_count_map = self._write_chapters_with_slices(
                chapter_outlines, characters, main_storyline, slices_per_chapter,
                extra_context=extra_context,
            )
            result["stages"]["chapter_writing"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage5_start) * 1000,
                "chapter_count": len(chapters),
            }
            self._logger.info(f"[阶段5] 章节写作完成 - {len(chapters)}章")

            # 阶段5b: 切片自检与补全
            stage5b_start = time.time()
            style_guide = self._read_style_guide()
            chapters = self._self_check_and_fix_slices(
                chapters, chapter_outlines, characters, main_storyline,
                slice_count_map, style_guide,
            )
            result["stages"]["slice_self_check"] = {
                "status": "completed",
                "elapsed_ms": (time.time() - stage5b_start) * 1000,
            }

            # 保存结果
            self._save_results(characters, main_storyline, full_outline, chapter_outlines, chapters)

            result["total_elapsed_ms"] = (time.time() - start_time) * 1000
            result["status"] = "completed"

            self._clear_checkpoint()

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
            self._save_checkpoint("error", {
                "pipeline_params": pipeline_params,
                "error": str(e),
                "completed_stages": list(result.get("stages", {}).keys()),
            })

        with self._progress_lock:
            self._progress["status"] = result.get("status", "completed")
            self._last_notify_time = 0.0
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

        sampling = SamplingParams(temperature=0.9, top_p=0.85, max_tokens=4096)
        results = self._client.big_batch_completions(
            contents=[prompt],
            sampling=sampling,
            stream=False,
        )
        result = results[0] if results else ""

        self._logger.debug(f"故事主线原始输出(前500字): {result[:500]}")

        parsed, status = parse_storyline_output(result)
        if parsed:
            self._logger.info(f"故事主线解析成功 (status={status})")
            return parsed

        self._logger.warning(f"故事主线JSON解析失败 (status={status})，尝试通用解析")
        self._logger.warning(f"故事主线原始输出(前800字): {result[:800]}")
        return self._parse_json_result(result)

    def _generate_full_outline(
        self,
        theme: str,
        characters: List[Dict],
        main_storyline: Dict,
        volume_count: int,
        chapters_per_volume: int,
    ) -> Dict:
        """生成全书大纲（基于主线）- 使用 PromptBuilder 增强版

        策略：
        1. 尝试一次性生成全书大纲
        2. 解析失败时，从截断内容提取卷信息
        3. 提取也失败时，按卷分段生成大纲
        """
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

        sampling = SamplingParams(temperature=0.9, top_p=0.85, max_tokens=8192)
        results = self._client.big_batch_completions(
            contents=[prompt],
            sampling=sampling,
            stream=False,
        )
        result = results[0] if results else ""

        self._logger.debug(f"全书大纲原始输出(前500字): {result[:500]}")

        parsed, status = parse_outline_output(result)
        if parsed is not None and isinstance(parsed, dict):
            volumes = parsed.get("volumes", [])
            if volumes and len(volumes) >= volume_count:
                self._logger.info(f"全书大纲解析成功 (status={status})")
                return parsed
            elif volumes:
                self._logger.info(f"全书大纲解析成功但卷数不足 ({len(volumes)}/{volume_count})，补全缺失卷")
                return self._supplement_volumes(parsed, volume_count, chapters_per_volume, main_storyline)

        self._logger.warning(f"全书大纲JSON解析失败 (status={status})，尝试从截断内容提取卷信息")
        self._logger.warning(f"全书大纲原始输出(前800字): {result[:800]}")
        volumes = extract_volumes_from_truncated(result)
        if volumes:
            self._logger.info(f"从截断内容中提取到 {len(volumes)} 个卷")
            outline = {
                "title": "自动提取大纲",
                "genre": theme,
                "volumes": volumes,
                "main_conflict": main_storyline.get("core_conflict", "") if main_storyline else "",
                "ending_direction": main_storyline.get("ending", "") if main_storyline else "",
            }
            if len(volumes) < volume_count:
                self._logger.info(f"截断提取卷数不足 ({len(volumes)}/{volume_count})，补全缺失卷")
                outline = self._supplement_volumes(outline, volume_count, chapters_per_volume, main_storyline)
            return outline

        self._logger.warning("全书大纲所有解析策略均失败，尝试按卷分段生成大纲")
        return self._generate_full_outline_by_volumes(
            theme, characters, main_storyline, volume_count, chapters_per_volume
        )

    def _supplement_volumes(
        self,
        outline: Dict,
        target_volume_count: int,
        chapters_per_volume: int,
        main_storyline: Optional[Dict] = None,
    ) -> Dict:
        """补全大纲中缺失的卷（当模型输出卷数不足时）"""
        volumes = outline.get("volumes", [])
        existing_count = len(volumes)
        if existing_count >= target_volume_count:
            return outline

        last_vol = volumes[-1] if volumes else {}
        last_vol_id = last_vol.get("volume_id", existing_count)
        try:
            last_vol_id = int(last_vol_id)
        except (ValueError, TypeError):
            last_vol_id = existing_count

        for vol_idx in range(existing_count, target_volume_count):
            vol_id = last_vol_id + (vol_idx - existing_count + 1)
            volumes.append({
                "volume_id": vol_id,
                "volume_title": f"第{vol_id}卷",
                "theme": f"第{vol_id}卷主题",
                "chapter_count": chapters_per_volume,
                "events": "",
                "character_arcs": "",
            })

        outline["volumes"] = volumes
        return outline

    def _generate_full_outline_by_volumes(
        self,
        theme: str,
        characters: List[Dict],
        main_storyline: Dict,
        volume_count: int,
        chapters_per_volume: int,
    ) -> Dict:
        """按卷分段生成全书大纲（当一次性生成失败时的回退策略）

        每次只生成一卷的大纲，降低单次输出长度要求，避免截断和重复。
        """
        chars_summary = self._format_characters_summary(characters)
        storyline_text = json.dumps(main_storyline, ensure_ascii=False, indent=2)

        all_volumes = []
        for vol_idx in range(volume_count):
            vol_id = vol_idx + 1
            self._logger.info(f"按卷生成大纲 - 第{vol_id}卷 ({vol_id}/{volume_count})...")

            prompt = (
                f"User: 你是一位资深小说总编。请为以下小说生成第{vol_id}卷（共{volume_count}卷）的详细大纲。\n"
                f"\n## 基本信息\n"
                f"- 题材类型: {theme}\n"
                f"- 第{vol_id}卷，每卷{chapters_per_volume}章\n"
                f"\n## 故事主线\n"
                f"{storyline_text}\n"
                f"\n## 角色体系\n"
                f"{chars_summary}\n"
            )

            if all_volumes:
                prev_vols_text = ""
                for pv in all_volumes:
                    prev_vols_text += f"- 第{pv.get('volume_id', '?')}卷「{pv.get('volume_title', '')}」: {pv.get('theme', '')}\n"
                prompt += f"\n## 已生成的前序卷\n{prev_vols_text}\n"

            prompt += (
                f"\n## 输出要求\n"
                f"直接输出单个JSON对象，不要输出任何其他文字、解释或markdown代码块标记。\n"
                f"JSON格式如下：\n"
                f'{{"volume_id": {vol_id}, "volume_title": "卷标题", "theme": "本卷主题概述", '
                f'"chapter_count": {chapters_per_volume}, '
                f'"events": "本卷关键事件，用分号分隔", '
                f'"character_arcs": "本卷角色发展概述"}}\n'
                f"\n请确保：1. 事件分布合理 2. 与前序卷衔接 3. 角色弧线连贯\n"
                f"\nAssistant: {{"
            )

            sampling = SamplingParams(temperature=0.85, top_p=0.85, max_tokens=2048)
            try:
                results = self._client.big_batch_completions(
                    contents=[prompt],
                    sampling=sampling,
                    stream=False,
                )
                result = results[0] if results else ""
            except Exception as e:
                self._logger.error(f"按卷生成大纲 - 第{vol_id}卷请求失败: {e}")
                result = ""

            parsed, status = robust_json_parse(result)
            if parsed and isinstance(parsed, dict):
                parsed.setdefault("volume_id", vol_id)
                parsed.setdefault("volume_title", f"第{vol_id}卷")
                parsed.setdefault("theme", "")
                parsed.setdefault("chapter_count", chapters_per_volume)
                parsed.setdefault("events", "")
                all_volumes.append(parsed)
                self._logger.info(f"按卷生成大纲 - 第{vol_id}卷成功")
            else:
                self._logger.warning(f"按卷生成大纲 - 第{vol_id}卷解析失败，使用基本模板")
                all_volumes.append({
                    "volume_id": vol_id,
                    "volume_title": f"第{vol_id}卷",
                    "theme": f"第{vol_id}卷主题",
                    "chapter_count": chapters_per_volume,
                    "events": "",
                    "character_arcs": "",
                })

        return {
            "title": main_storyline.get("title", "分段生成大纲") if main_storyline else "分段生成大纲",
            "genre": theme,
            "volumes": all_volumes,
            "main_conflict": main_storyline.get("core_conflict", "") if main_storyline else "",
            "ending_direction": main_storyline.get("ending", "") if main_storyline else "",
        }

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
        base_slices: int = 10,
    ) -> Dict[int, int]:
        """基于章节大纲自动规划每个章节的切片数量

        base_slices 为用户设定的最大切片数，实际切片数在
        MIN_SLICES~base_slices 之间根据剧情复杂度自动分配。

        规划依据:
        - synopsis 长度：概要越长说明剧情越复杂，需要更多切片
        - 涉及角色数：出场角色越多需要更多切片
        - 伏笔数量：伏笔多需要更多切片展开
        - 卷首/卷尾章：通常需要更多切片做铺垫或收束

        Returns:
            {chapter_id: slice_count} 映射
        """
        MAX_SLICES = base_slices
        MIN_SLICES = max(3, base_slices // 3)

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

            try:
                ch_id = int(ch_id)
            except (ValueError, TypeError):
                ch_id = i + 1
            try:
                vol_id = int(vol_id)
            except (ValueError, TypeError):
                vol_id = 1

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

                    stripped = result
                    if result:
                        first_brace = -1
                        for idx, ch in enumerate(result):
                            if ch in ('{', '['):
                                first_brace = idx
                                break
                        if first_brace > 0:
                            stripped = result[first_brace:]

                    parsed, status = robust_json_parse(stripped)
                    if parsed is None and stripped != result:
                        parsed, status = robust_json_parse(result)

                    if isinstance(parsed, list) and len(parsed) > 0:
                        parsed = parsed[0] if isinstance(parsed[0], dict) else None

                    if parsed is not None and isinstance(parsed, dict):
                        parsed = self._normalize_chapter_outline(parsed, ch_id, vol_id)
                        all_results.append(parsed)
                    else:
                        raw_preview = result[:300].replace('\n', '\\n') if result else '(empty)'
                        self._logger.warning(
                            f"章节 {ch_id} AI大纲解析失败 (status={status})，尝试正则提取\n"
                            f"  原始输出预览: {raw_preview}"
                        )
                        regex_parsed = self._extract_chapter_outline_regex(result, ch_id, vol_id)
                        if regex_parsed:
                            all_results.append(regex_parsed)
                        else:
                            self._logger.warning(f"章节 {ch_id} 正则提取也失败，生成基本大纲")
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

        all_results.sort(key=lambda x: int(x.get("chapter_id", 0)) if not isinstance(x.get("chapter_id", 0), int) else x.get("chapter_id", 0))
        return all_results

    @staticmethod
    def _to_char_name(item) -> str:
        if isinstance(item, dict):
            return item.get("name", item.get("character_name", str(item)))
        if isinstance(item, str):
            return item.strip()
        return str(item)

    def _normalize_chapter_outline(self, parsed: Dict, ch_id: int, vol_id: int) -> Dict:
        """标准化章节大纲字段：类型转换 + 字段名映射"""
        try:
            parsed["chapter_id"] = int(parsed.get("chapter_id", ch_id))
        except (ValueError, TypeError):
            parsed["chapter_id"] = ch_id
        try:
            parsed["volume_id"] = int(parsed.get("volume_id", vol_id))
        except (ValueError, TypeError):
            parsed["volume_id"] = vol_id

        parsed.setdefault("chapter_title", f"第{ch_id}章")
        parsed.setdefault("synopsis", "")

        if "involved_characters" not in parsed:
            chars = parsed.pop("characters", None)
            if isinstance(chars, str):
                parsed["involved_characters"] = [c.strip() for c in chars.split(",") if c.strip()]
            elif isinstance(chars, list):
                parsed["involved_characters"] = [self._to_char_name(c) for c in chars]
            else:
                parsed["involved_characters"] = []
        elif isinstance(parsed["involved_characters"], str):
            parsed["involved_characters"] = [c.strip() for c in parsed["involved_characters"].split(",") if c.strip()]
        elif isinstance(parsed["involved_characters"], list):
            parsed["involved_characters"] = [self._to_char_name(c) for c in parsed["involved_characters"]]

        parsed.setdefault("involved_factions", [])
        if isinstance(parsed.get("involved_factions"), list):
            parsed["involved_factions"] = [str(f) if isinstance(f, dict) else f for f in parsed["involved_factions"]]

        if "foreshadowing" not in parsed:
            plant = parsed.pop("foreshadowing_plant", "")
            resolve = parsed.pop("foreshadowing_resolve", "")
            if isinstance(plant, str):
                plant = [p.strip() for p in plant.split(",") if p.strip()]
            if isinstance(resolve, str):
                resolve = [r.strip() for r in resolve.split(",") if r.strip()]
            parsed["foreshadowing"] = {"plant": plant or [], "resolve": resolve or []}
        elif isinstance(parsed["foreshadowing"], dict):
            parsed["foreshadowing"].setdefault("plant", [])
            parsed["foreshadowing"].setdefault("resolve", [])
            plant = parsed["foreshadowing"]["plant"]
            resolve = parsed["foreshadowing"]["resolve"]
            if isinstance(plant, list):
                parsed["foreshadowing"]["plant"] = [str(p) if isinstance(p, dict) else p for p in plant]
            if isinstance(resolve, list):
                parsed["foreshadowing"]["resolve"] = [str(r) if isinstance(r, dict) else r for r in resolve]

        parsed.pop("foreshadowing_plant", None)
        parsed.pop("foreshadowing_resolve", None)
        parsed.pop("emotional_arc", None)
        parsed.pop("key_scenes", None)

        return parsed

    def _extract_chapter_outline_regex(self, text: str, ch_id: int, vol_id: int) -> Optional[Dict]:
        """正则回退提取章节大纲字段 - 支持字符串值、数组值、嵌套对象"""
        if not text:
            return None

        result = {
            "chapter_id": ch_id,
            "volume_id": vol_id,
            "chapter_title": f"第{ch_id}章",
            "synopsis": "",
            "involved_characters": [],
            "involved_factions": [],
            "foreshadowing": {"plant": [], "resolve": []},
        }

        def _extract_str(key: str) -> Optional[str]:
            for pat in [
                rf'"{key}"\s*:\s*"([^"]*)"',
                rf'"{key}"\s*:\s*\'([^\']*)\'',
                rf"'{key}'\s*:\s*'([^']*)'",
                rf"'{key}'\s*:\s*\"([^\"]*)\"",
                rf'{key}\s*:\s*"([^"]*)"',
                rf"{key}\s*:\s*'([^']*)'",
            ]:
                m = re.search(pat, text)
                if m:
                    return m.group(1)
            return None

        def _extract_str_list(key: str) -> List[str]:
            for pat in [
                rf'"{key}"\s*:\s*\[(.*?)\]',
                rf"'{key}'\s*:\s*\[(.*?)\]",
                rf'{key}\s*:\s*\[(.*?)\]',
            ]:
                m = re.search(pat, text, re.DOTALL)
                if m:
                    inner = m.group(1)
                    items = re.findall(r'["\']([^"\']*)["\']', inner)
                    if items:
                        return [it.strip() for it in items if it.strip()]
            str_val = _extract_str(key)
            if str_val:
                return [c.strip() for c in str_val.split(",") if c.strip()]
            return []

        def _extract_nested_str_list(parent_key: str, child_key: str) -> List[str]:
            for pat in [
                rf'"{parent_key}"\s*:\s*\{{[^}}]*"{child_key}"\s*:\s*\[(.*?)\]',
                rf"'{parent_key}'\s*:\s*\{{[^}}]*'{child_key}'\s*:\s*\[(.*?)\]",
                rf'{parent_key}\s*:\s*\{{[^}}]*{child_key}\s*:\s*\[(.*?)\]',
            ]:
                m = re.search(pat, text, re.DOTALL)
                if m:
                    inner = m.group(1)
                    items = re.findall(r'["\']([^"\']*)["\']', inner)
                    if items:
                        return [it.strip() for it in items if it.strip()]
            return []

        title = _extract_str("chapter_title")
        if title:
            result["chapter_title"] = title

        synopsis = _extract_str("synopsis")
        if synopsis:
            result["synopsis"] = synopsis

        chars = _extract_str_list("involved_characters")
        if not chars:
            chars = _extract_str_list("characters")
        if chars:
            result["involved_characters"] = chars

        factions = _extract_str_list("involved_factions")
        if factions:
            result["involved_factions"] = factions

        plant = _extract_nested_str_list("foreshadowing", "plant")
        if not plant:
            plant_str = _extract_str("foreshadowing_plant")
            if plant_str:
                plant = [p.strip() for p in plant_str.split(",") if p.strip()]
        if plant:
            result["foreshadowing"]["plant"] = plant

        resolve = _extract_nested_str_list("foreshadowing", "resolve")
        if not resolve:
            resolve_str = _extract_str("foreshadowing_resolve")
            if resolve_str:
                resolve = [r.strip() for r in resolve_str.split(",") if r.strip()]
        if resolve:
            result["foreshadowing"]["resolve"] = resolve

        has_content = bool(result["synopsis"]) or bool(result["involved_characters"]) or (
            result["chapter_title"] and result["chapter_title"] != f"第{ch_id}章"
        )
        if not has_content:
            return None

        return result

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
        extra_context: str = "",
    ) -> List[Dict]:
        """章节切片并行写作 - 轮次批量并发

        核心策略：按轮次推进，每一轮收集所有活跃章节的当前切片，
        合并为一个批量请求发给 /v2/chat/completions，充分利用 GPU 批量推理能力。

        每个章节的切片数量由 _plan_slice_counts 自动规划，
        slices_per_chapter 作为最大切片数上限。
        """
        total_chapters = len(chapter_outlines)

        slice_count_map = self._plan_slice_counts(chapter_outlines, slices_per_chapter)

        total_slices = sum(slice_count_map.values())

        batch_size = self._concurrency_config.get("batch_size", 8)

        self._logger.info(
            f"开始章节切片并行写作 - {total_chapters}章, 总切片={total_slices}, "
            f"批量大小={batch_size}"
        )

        self._init_chapter_matrix_variable(total_chapters, slice_count_map)

        if total_chapters == 0:
            self._logger.warning("章节大纲为空，跳过章节写作")
            return []

        MIN_SLICE_CHARS = 200

        style_guide = self._read_style_guide()

        slice_results: Dict[int, Dict[int, str]] = {}
        for chapter in chapter_outlines:
            ch_id = chapter["chapter_id"]
            slice_results[ch_id] = {}

        chapter_data = {}
        for chapter in chapter_outlines:
            ch_id = chapter["chapter_id"]
            ch_slice_count = slice_count_map.get(ch_id, slices_per_chapter)
            slice_types = self._build_slice_types(ch_slice_count)
            involved_chars = chapter.get("involved_characters", [])
            involved_factions = chapter.get("involved_factions", [])
            world_state_text = self._get_world_state_text(involved_chars, involved_factions, ch_id)
            relevant_characters = self._get_relevant_characters(characters, involved_chars)

            chapter_data[ch_id] = {
                "chapter": chapter,
                "slice_count": ch_slice_count,
                "slice_types": slice_types,
                "world_state_text": world_state_text,
                "relevant_characters": relevant_characters,
                "current_slice": 0,
                "completed": False,
            }

        active_chapters = set(chapter_data.keys())
        total_completed = 0

        while active_chapters:
            batch_prompts = []
            batch_meta = []
            batch_samplings = []

            for ch_id in list(active_chapters):
                cd = chapter_data[ch_id]
                if cd["completed"]:
                    active_chapters.discard(ch_id)
                    continue

                slice_idx = cd["current_slice"]
                if slice_idx >= cd["slice_count"]:
                    cd["completed"] = True
                    active_chapters.discard(ch_id)
                    continue

                slice_type = cd["slice_types"][slice_idx % len(cd["slice_types"])]

                previous_content = ""
                if slice_idx > 0 and (slice_idx - 1) in slice_results[ch_id]:
                    previous_content = slice_results[ch_id][slice_idx - 1]

                # 增加 earlier_slices_summary 长度 + 完整句段
                earlier_summary = ""
                if slice_idx > 1:
                    earlier_parts = []
                    for si in range(slice_idx - 1):
                        sc = slice_results[ch_id].get(si, "")
                        if sc:
                            # 截取更多内容以让模型明确知道已写什么
                            earlier_parts.append(f"[切片{si+1}] {sc[:400]}")
                    if earlier_parts:
                        earlier_summary = "\n".join(earlier_parts)

                # 提取 allowed_names（所有合法角色名）
                allowed_names = []
                for ch in (cd["relevant_characters"] or []):
                    if isinstance(ch, dict):
                        n = ch.get("name", "").strip()
                        if n:
                            allowed_names.append(n)

                prompt = PromptBuilder.build_slice_writing_prompt(
                    chapter_info=cd["chapter"],
                    slice_type=slice_type["name"],
                    slice_description=slice_type["description"],
                    slice_idx=slice_idx,
                    total_slices=cd["slice_count"],
                    characters=cd["relevant_characters"],
                    main_storyline=main_storyline,
                    world_state_text=cd["world_state_text"],
                    previous_slice_content=previous_content,
                    earlier_slices_summary=earlier_summary,
                    style_guide=style_guide,
                    original_storyline=extra_context or "",
                    allowed_names=allowed_names,
                )

                sampling = self._get_slice_sampling(slice_type["row_name"])

                batch_prompts.append(prompt)
                prev_tail = previous_content.strip()[-50:] if previous_content and previous_content.strip() else ""
                batch_meta.append({"ch_id": ch_id, "slice_idx": slice_idx, "slice_type": slice_type, "prev_tail": prev_tail})
                batch_samplings.append(sampling)

                self._update_slice_progress(ch_id, slice_idx, "in_progress")

                if len(batch_prompts) >= batch_size:
                    break

            if not batch_prompts:
                break

            sampling_groups = {}
            for i, meta in enumerate(batch_meta):
                s = batch_samplings[i]
                key = (s.temperature, s.top_p, s.alpha_presence, s.alpha_frequency, s.alpha_decay, s.max_tokens)
                if key not in sampling_groups:
                    sampling_groups[key] = {"sampling": s, "indices": []}
                sampling_groups[key]["indices"].append(i)

            for key, group in sampling_groups.items():
                group_prompts = [batch_prompts[i] for i in group["indices"]]
                group_meta = [batch_meta[i] for i in group["indices"]]
                sampling = group["sampling"]

                try:
                    results = self._client.chat_completions_v2(
                        contents=group_prompts,
                        sampling=sampling,
                        stream=False,
                    )
                except Exception as e:
                    self._logger.error(f"批量切片写作请求失败: {e}")
                    results = [""] * len(group_prompts)

                for j, meta in enumerate(group_meta):
                    ch_id = meta["ch_id"]
                    slice_idx = meta["slice_idx"]
                    result = results[j] if j < len(results) else ""

                    # 提取该章节的合法角色名
                    cd = chapter_data[ch_id]
                    slice_allowed_names = []
                    for ch in (cd["relevant_characters"] or []):
                        if isinstance(ch, dict):
                            n = ch.get("name", "").strip()
                            if n:
                                slice_allowed_names.append(n)

                    # 收集本切片之前的所有切片内容（用于跨切片去重）
                    prev_slices = []
                    for si in range(slice_idx):
                        sc = slice_results[ch_id].get(si, "")
                        if sc:
                            prev_slices.append(sc)

                    # 先做基本清理
                    slice_content = self._clean_slice_output(
                        result,
                        meta.get("prev_tail", ""),
                        allowed_names=slice_allowed_names,
                        characters=cd["relevant_characters"],
                    )

                    # 跨切片段落级去重（移除与之前切片重复的段落）
                    if slice_content and prev_slices:
                        try:
                            slice_content = _remove_cross_duplicate_paragraphs(
                                slice_content, prev_slices, threshold=0.55
                            )
                        except Exception:
                            pass

                    if slice_content and len(slice_content) < MIN_SLICE_CHARS:
                        self._logger.warning(
                            f"章节{ch_id}切片{slice_idx}内容不足{MIN_SLICE_CHARS}字"
                            f"({len(slice_content)}字)，尝试重写"
                        )
                        try:
                            retry_results = self._client.big_batch_completions(
                                contents=[group_prompts[j]],
                                sampling=SamplingParams(temperature=0.95, top_p=0.9, max_tokens=2048),
                                stream=False,
                            )
                            retry_content = self._clean_slice_output(
                                retry_results[0] if retry_results else "",
                                meta.get("prev_tail", ""),
                                allowed_names=slice_allowed_names,
                                characters=cd["relevant_characters"],
                            )
                            # 重写时也做跨切片段落级去重
                            if retry_content and prev_slices:
                                try:
                                    retry_content = _remove_cross_duplicate_paragraphs(
                                        retry_content, prev_slices, threshold=0.55
                                    )
                                except Exception:
                                    pass
                            if len(retry_content) > len(slice_content):
                                slice_content = retry_content
                                self._logger.info(f"章节{ch_id}切片{slice_idx}重写成功({len(slice_content)}字)")
                        except Exception:
                            pass

                    slice_results[ch_id][slice_idx] = slice_content
                    self._update_slice_progress(ch_id, slice_idx, "completed" if slice_content else "error", slice_content)

                    if slice_content:
                        total_completed += 1

                    chapter_data[ch_id]["current_slice"] = slice_idx + 1

            self._logger.info(f"切片进度: {total_completed}/{total_slices} 完成")

        final_chapters = self._merge_slices_variable(
            chapter_outlines, slice_results, slice_count_map
        )

        self._logger.info(f"章节并行写作完成 - {len(final_chapters)}章")
        return final_chapters, slice_count_map

    def _get_world_state_text(
        self,
        involved_characters: List,
        involved_factions: List,
        chapter_id: int,
    ) -> str:
        """获取与当前章节相关的世界状态文本"""
        try:
            norm_chars = [self._to_char_name(c) for c in involved_characters]
            norm_factions = [str(f) if isinstance(f, dict) else f for f in involved_factions]
            summary = self._world.extract_summary(
                norm_chars, norm_factions, chapter_id
            )
            return summary.format_for_prompt()
        except Exception as e:
            self._logger.warning(f"获取世界状态失败: {e}")
            return ""

    def _get_relevant_characters(
        self,
        all_characters: List[Dict],
        involved_ids: List,
    ) -> List[Dict]:
        """获取与当前章节相关的角色信息"""
        if not involved_ids:
            return all_characters[:8]

        normalized_ids = [self._to_char_name(c) for c in involved_ids]
        relevant = []
        id_set = set(normalized_ids)
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

    def _self_check_and_fix_slices(
        self,
        chapters: List[Dict],
        chapter_outlines: List[Dict],
        characters: List[Dict],
        main_storyline: Dict,
        slice_count_map: Dict[int, int],
        style_guide: str = "",
    ) -> List[Dict]:
        """自检所有章节切片，补全不完整的切片

        在所有切片写完后执行，检测内容过短的切片并用完整上下文重新生成。
        最多迭代3轮，每轮修复后重新合并章节。
        """
        MIN_SLICE_CHARS = 200
        MAX_FIX_ROUNDS = 3

        outline_map = {ch["chapter_id"]: ch for ch in chapter_outlines}

        for round_num in range(MAX_FIX_ROUNDS):
            short_slices = []

            for chapter in chapters:
                ch_id = chapter["chapter_id"]
                slices = chapter.get("slices", {})
                ch_slice_count = slice_count_map.get(ch_id, 5)

                for slice_idx in range(ch_slice_count):
                    content = slices.get(slice_idx, "")
                    if content and len(content) < MIN_SLICE_CHARS:
                        short_slices.append((ch_id, slice_idx, len(content)))

            if not short_slices:
                self._logger.info(f"切片自检通过 - 所有切片均满足{MIN_SLICE_CHARS}字要求")
                break

            self._logger.info(
                f"切片自检第{round_num + 1}轮 - 发现{len(short_slices)}个不完整切片，开始补全"
            )

            fixes_by_chapter: Dict[int, List[int]] = {}
            for ch_id, slice_idx, orig_len in short_slices:
                if ch_id not in fixes_by_chapter:
                    fixes_by_chapter[ch_id] = []
                fixes_by_chapter[ch_id].append(slice_idx)

            for ch_id, bad_slices in fixes_by_chapter.items():
                chapter = next((c for c in chapters if c["chapter_id"] == ch_id), None)
                if not chapter:
                    continue

                outline = outline_map.get(ch_id)
                slices = chapter.get("slices", {})
                ch_slice_count = slice_count_map.get(ch_id, 5)

                # 提取该章节的合法角色名
                fix_allowed_names = []
                for ch in (characters or []):
                    if isinstance(ch, dict):
                        n = ch.get("name", "").strip()
                        if n:
                            fix_allowed_names.append(n)

                for slice_idx in bad_slices:
                    prev_content = slices.get(slice_idx - 1, "") if slice_idx > 0 else ""
                    next_content = slices.get(slice_idx + 1, "") if slice_idx + 1 < ch_slice_count else ""

                    fix_prompt = self._build_slice_fix_prompt(
                        chapter_outline=outline,
                        slice_idx=slice_idx,
                        total_slices=ch_slice_count,
                        prev_content=prev_content,
                        next_content=next_content,
                        characters=characters,
                        main_storyline=main_storyline,
                        style_guide=style_guide,
                    )

                    try:
                        results = self._client.big_batch_completions(
                            contents=[fix_prompt],
                            sampling=SamplingParams(temperature=0.9, top_p=0.92, max_tokens=2048),
                            stream=False,
                        )
                        new_content = self._clean_slice_output(
                            results[0] if results else "",
                            prev_content.strip()[-50:] if prev_content and prev_content.strip() else "",
                            allowed_names=fix_allowed_names,
                            characters=characters,
                        )

                        # 补全时也做跨切片段落级去重
                        if new_content:
                            other_slices = []
                            for si in range(ch_slice_count):
                                if si != slice_idx:
                                    sc = slices.get(si, "")
                                    if sc:
                                        other_slices.append(sc)
                            if other_slices:
                                try:
                                    new_content = _remove_cross_duplicate_paragraphs(
                                        new_content, other_slices, threshold=0.55
                                    )
                                except Exception:
                                    pass

                        old_len = len(slices.get(slice_idx, ""))
                        if new_content and len(new_content) >= MIN_SLICE_CHARS:
                            slices[slice_idx] = new_content
                            self._logger.info(
                                f"章节{ch_id}切片{slice_idx}补全成功 ({old_len}→{len(new_content)}字)"
                            )
                        elif new_content and len(new_content) > old_len:
                            slices[slice_idx] = new_content
                            self._logger.info(
                                f"章节{ch_id}切片{slice_idx}部分改善 ({old_len}→{len(new_content)}字)"
                            )
                        else:
                            self._logger.warning(
                                f"章节{ch_id}切片{slice_idx}补全未改善 ({old_len}字)，保持原内容"
                            )
                    except Exception as e:
                        self._logger.error(f"章节{ch_id}切片{slice_idx}补全异常: {e}")

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

                chapter["content"] = merged_content
                chapter["slice_count"] = len([s for s in slices.values() if s])

        return chapters

    def _build_slice_fix_prompt(
        self,
        chapter_outline: Dict,
        slice_idx: int,
        total_slices: int,
        prev_content: str,
        next_content: str,
        characters: List[Dict],
        main_storyline: Dict,
        style_guide: str = "",
    ) -> str:
        """构建切片补全 Prompt - 提供前后文完整上下文"""
        synopsis = chapter_outline.get("synopsis", "") if chapter_outline else ""
        ch_title = chapter_outline.get("chapter_title", "未命名") if chapter_outline else "未命名"
        ch_id = chapter_outline.get("chapter_id", "?") if chapter_outline else "?"

        parts = [
            "User: 你是一位小说作家。以下章节的一个片段内容过短，请根据前后文补全这个片段。",
            f"\n## 章节信息",
            f"第{ch_id}章「{ch_title}」",
            f"章节概要: {synopsis}",
            f"本片段位置: 第{slice_idx + 1}/{total_slices}部分",
        ]

        if prev_content:
            parts.append("\n## 前文内容（本片段必须紧接此内容续写）")
            parts.append(prev_content.strip()[-500:])

        if next_content:
            parts.append("\n## 后文内容（本片段结尾必须自然过渡到此内容）")
            parts.append(next_content.strip()[:300])

        involved = chapter_outline.get("involved_characters", []) if chapter_outline else []
        if involved:
            parts.append(f"\n## 涉及角色: {', '.join(involved)}")

        if characters:
            relevant_names = set(involved) if involved else set()
            shown = []
            for ch in characters:
                if isinstance(ch, dict):
                    name = ch.get("name", "")
                    if name in relevant_names:
                        shown.append(ch)
            if not shown:
                shown = list(characters[:5])

            if shown:
                parts.append("\n## 角色参考")
                for ch in shown:
                    if isinstance(ch, dict):
                        name = ch.get("name", "未知")
                        role = ch.get("role_type", "")
                        identity = ch.get("identity", "")
                        parts.append(f"- {name}({role}): {identity}")

        if style_guide:
            parts.append(f"\n## 风格要求\n{style_guide[:500]}")

        parts.append("\n## 要求")
        parts.append("1. 紧接前文内容续写，确保情节连贯")
        if next_content:
            parts.append("2. 结尾自然过渡到后文内容")
        parts.append("3. 只输出小说正文，不要输出任何说明、标签或格式标记")
        parts.append(f"4. 内容不少于200字")

        parts.append("\nAssistant: ")

        return "\n".join(parts)

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

    def _clean_slice_output(self, raw: str, prev_tail: str = "", allowed_names: List[str] = None, characters: List[Dict] = None) -> str:
        """清理单切片输出，只保留纯净的小说正文

        处理：
        1. 去除模型可能回显的前文末尾（续写格式中 Assistant: 后的重复部分）
        2. 去除标题、标签、大纲等非正文内容
        3. 去除思维链和指令回显
        4. 去除科幻/现代等非仙侠元素
        5. 替换凭空生成的新角色名（林逸风等）为白名单角色
        6. 段落级内部去重
        """
        if not raw or not raw.strip():
            return ""

        content = raw.strip()

        if prev_tail and content.startswith(prev_tail):
            content = content[len(prev_tail):].strip()

        content = self._clean_merged_content(content)

        # 段落级题材违规过滤（移除含科幻/现代元素的段落）
        try:
            content = sanitize_genre_violation(content)
        except Exception:
            pass

        # 单切片内部段落级去重
        try:
            content = _remove_duplicate_paragraphs(content, threshold=0.55)
        except Exception:
            pass

        # 替换凭空生成的新角色名
        if allowed_names:
            try:
                content = _replace_forbidden_names(content, allowed_names, characters)
            except Exception:
                pass

        return content

    def _clean_merged_content(self, content: str) -> str:
        """清理合并后的内容 - 过滤思维链、指令标签等非正文内容

        使用逐行状态机过滤，支持跨行区块移除:
        1. XML标签式思维链 (<feelings>, <thinking> 等)
        2. 指令区块 (# 场景, # 指令, # 结构要求, # 创作要求 及其下所有内容)
        3. 已完成切片摘要回显
        4. 切片类型标签 (# 角色登场, # 场景引入 等)
        5. 属性列表 (* **人物**: 等)
        6. 旁白注释 (> *——...*)
        7. 模型思维链前缀 (好的，让我，需要等分析性开头)
        8. 章节标题重复 (# 第N章 ...)
        9. 切片分界标签 (# 切片N, # 小说章节)
        """
        lines = content.split('\n')
        cleaned = []
        skip_mode = None
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if skip_mode == 'instruction_block':
                if stripped.startswith('#') and not _is_instruction_heading(stripped):
                    skip_mode = None
                else:
                    i += 1
                    continue

            if skip_mode == 'summary_echo':
                if not stripped.startswith('>') and stripped != '':
                    skip_mode = None
                else:
                    i += 1
                    continue

            if skip_mode == 'thinking_prefix':
                if _is_novel_content_start(stripped):
                    skip_mode = None
                elif stripped.startswith('#') and not _is_instruction_heading(stripped):
                    skip_mode = None
                else:
                    i += 1
                    continue

            xml_match = re.match(r'^<(\w+)>', stripped)
            if xml_match:
                tag = xml_match.group(1)
                close_re = re.compile(rf'</{tag}>')
                j = i
                found = False
                while j < len(lines):
                    if close_re.search(lines[j].strip()):
                        found = True
                        break
                    j += 1
                i = j + 1 if found else i + 1
                continue

            if _is_instruction_block_heading(stripped):
                skip_mode = 'instruction_block'
                i += 1
                continue

            if _is_slice_summary_echo(stripped):
                skip_mode = 'summary_echo'
                i += 1
                continue

            if stripped.startswith('>'):
                if _is_summary_blockquote(stripped) or _is_side_note(stripped):
                    i += 1
                    continue
                if stripped in ('>', '> '):
                    i += 1
                    continue

            if _is_slice_type_heading(stripped):
                i += 1
                continue

            if _is_chapter_title_repeat(stripped):
                i += 1
                continue

            if _is_slice_divider(stripped):
                i += 1
                continue

            if _is_attribute_line(stripped):
                i += 1
                continue

            if _is_thinking_prefix(stripped):
                skip_mode = 'thinking_prefix'
                i += 1
                continue

            if _is_misc_junk(stripped):
                i += 1
                continue

            cleaned.append(line)
            i += 1

        result = '\n'.join(cleaned)
        result = re.sub(r'\n{3,}', '\n\n', result)
        return result.strip()

    def _get_slice_sampling(self, row_name: str) -> 'SamplingParams':
        """根据切片阶段获取差异化采样参数

        开场: 低temp稳定铺垫，高top_p保证多样性
        发展: 中等temp推进节奏
        高潮: 高temp张力爆发，低top_p聚焦关键
        结尾: 低temp收束沉稳

        所有切片均启用 alpha_presence/alpha_frequency + alpha_decay=0.996 防止重复输出
        """
        sampling_map = {
            "开场": SamplingParams(temperature=0.75, top_p=0.92, max_tokens=1024,
                                   alpha_presence=0.3, alpha_frequency=0.3, alpha_decay=0.996),
            "发展": SamplingParams(temperature=0.85, top_p=0.90, max_tokens=1024,
                                   alpha_presence=0.3, alpha_frequency=0.3, alpha_decay=0.996),
            "高潮": SamplingParams(temperature=0.95, top_p=0.85, max_tokens=1024,
                                   alpha_presence=0.4, alpha_frequency=0.4, alpha_decay=0.996),
            "结尾": SamplingParams(temperature=0.70, top_p=0.90, max_tokens=1024,
                                   alpha_presence=0.3, alpha_frequency=0.3, alpha_decay=0.996),
        }
        return sampling_map.get(row_name, SamplingParams(temperature=0.85, top_p=0.9, max_tokens=1024,
                                                          alpha_presence=0.3, alpha_frequency=0.3, alpha_decay=0.996))

    def _read_style_guide(self) -> str:
        """读取写作风格指南 + 激活的 SKILL.md 内容"""
        try:
            base = self._fm.read_style_guide()
        except Exception:
            base = ""
        try:
            skills = self._fm.read_active_skills()
        except Exception:
            skills = ""
        if skills:
            if base:
                return base + "\n\n---\n\n" + skills
            return skills
        return base

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
        """通知进度变更（在 _progress_lock 内调用），节流：最少间隔0.3秒"""
        if self._progress_callback:
            now = time.time()
            if now - self._last_notify_time < self._notify_min_interval:
                return
            self._last_notify_time = now
            try:
                snapshot = self._progress.copy()
                self._progress_callback(snapshot)
            except Exception:
                pass

    def get_progress(self) -> Dict:
        """获取当前进度"""
        with self._progress_lock:
            return self._progress.copy()

    def _cache_interim(self, name: str, data):
        """实时缓存中间结果到输出目录的 .cache 子目录"""
        try:
            cache_dir = os.path.join(self._fm.output_dir, ".cache")
            os.makedirs(cache_dir, exist_ok=True)

            if name == "full_outline":
                self._fm.write_json(os.path.join(cache_dir, "full_outline.json"), data)
                outline_md = self._format_full_outline_md(data, data.get("title", "") if data else "")
                self._fm.write_markdown(os.path.join(cache_dir, "全书大纲.md"), outline_md)

                volumes = data.get("volumes", []) if data else []
                if volumes:
                    vol_cache_dir = os.path.join(cache_dir, "分卷大纲")
                    os.makedirs(vol_cache_dir, exist_ok=True)
                    for vol in volumes:
                        vol_id = vol.get("volume_id", 1)
                        vol_title = vol.get("volume_title", f"第{vol_id}卷")
                        safe_vol = "".join(c for c in vol_title if c not in r'\/:*?"<>|').strip()
                        if not safe_vol:
                            safe_vol = f"第{vol_id}卷"
                        vol_md = self._format_volume_outline_md(vol)
                        self._fm.write_markdown(os.path.join(vol_cache_dir, f"{safe_vol}.md"), vol_md)

            elif name == "chapter_outlines":
                self._fm.write_json(os.path.join(cache_dir, "chapter_outlines.json"), data)
                ch_cache_dir = os.path.join(cache_dir, "章节大纲")
                os.makedirs(ch_cache_dir, exist_ok=True)
                for ch in data:
                    ch_id = ch.get("chapter_id", 0)
                    ch_md = self._format_chapter_outline_md(ch)
                    self._fm.write_markdown(os.path.join(ch_cache_dir, f"第{ch_id}章.md"), ch_md)

            elif name == "characters":
                self._fm.write_json(os.path.join(cache_dir, "characters.json"), data)

            elif name == "main_storyline":
                self._fm.write_json(os.path.join(cache_dir, "main_storyline.json"), data)

            self._logger.info(f"中间结果已缓存: {name}")
        except Exception as e:
            self._logger.warning(f"缓存中间结果失败 ({name}): {e}")

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
        """保存结果 - 多级目录结构

        目录结构:
        草稿_{小说名称}/
        ├── 全书大纲.md
        ├── characters.json
        ├── main_storyline.json
        ├── outline.json
        ├── {分卷名称}/
        │   ├── 分卷大纲.md
        │   └── {章节名称}/
        │       ├── 章节大纲/
        │       │   └── 大纲.md
        │       ├── 初步草稿/
        │       │   └── 草稿.md
        │       └── 正式文稿/
        │           └── (等待润色后生成)
        """
        self._logger.info("保存管线结果...")

        novel_title = ""
        if full_outline and isinstance(full_outline, dict):
            novel_title = full_outline.get("title", "")
        if not novel_title and main_storyline and isinstance(main_storyline, dict):
            novel_title = main_storyline.get("title", "")
        if not novel_title:
            novel_title = "未命名小说"

        safe_title = "".join(c for c in novel_title if c not in r'\/:*?"<>|').strip()
        if not safe_title:
            safe_title = "未命名小说"

        novel_dir = os.path.join(self._fm.output_dir, f"草稿_{safe_title}")
        os.makedirs(novel_dir, exist_ok=True)

        outline_md = self._format_full_outline_md(full_outline, novel_title)
        self._fm.write_markdown(os.path.join(novel_dir, "全书大纲.md"), outline_md)

        char_path = os.path.join(novel_dir, "characters.json")
        self._fm.write_json(char_path, characters)

        storyline_path = os.path.join(novel_dir, "main_storyline.json")
        self._fm.write_json(storyline_path, main_storyline)

        outline_path = os.path.join(novel_dir, "outline.json")
        self._fm.write_json(outline_path, full_outline)

        volumes = full_outline.get("volumes", []) if full_outline else []

        if not volumes and chapter_outlines:
            vol_ids = sorted(set(int(co.get("volume_id", 1)) for co in chapter_outlines))
            volumes = [{"volume_id": vid, "volume_title": f"第{vid}卷"} for vid in vol_ids]
            self._logger.info(f"全书大纲无卷信息，从章节大纲推导出 {len(volumes)} 卷")

        chapter_by_volume = {}
        for ch_outline in chapter_outlines:
            vol_id = ch_outline.get("volume_id", 1)
            chapter_by_volume.setdefault(vol_id, []).append(ch_outline)

        chapter_content_by_id = {}
        for ch in chapters:
            ch_id = ch.get("chapter_id", 0)
            chapter_content_by_id[ch_id] = ch

        for vol in volumes:
            vol_id = vol.get("volume_id", 1)
            vol_title = vol.get("volume_title", f"第{vol_id}卷")
            safe_vol = "".join(c for c in vol_title if c not in r'\/:*?"<>|').strip()
            if not safe_vol:
                safe_vol = f"第{vol_id}卷"

            vol_dir = os.path.join(novel_dir, safe_vol)
            os.makedirs(vol_dir, exist_ok=True)

            vol_outline_md = self._format_volume_outline_md(vol)
            self._fm.write_markdown(os.path.join(vol_dir, "分卷大纲.md"), vol_outline_md)

            vol_chapters = chapter_by_volume.get(vol_id, [])
            for ch_outline in vol_chapters:
                ch_id = ch_outline.get("chapter_id", 0)
                ch_title = ch_outline.get("chapter_title", f"第{ch_id}章")
                safe_ch = "".join(c for c in ch_title if c not in r'\/:*?"<>|').strip()
                if not safe_ch:
                    safe_ch = f"第{ch_id}章"

                ch_dir = os.path.join(vol_dir, safe_ch)
                outline_dir = os.path.join(ch_dir, "章节大纲")
                draft_dir = os.path.join(ch_dir, "初步草稿")
                final_dir = os.path.join(ch_dir, "正式文稿")
                os.makedirs(outline_dir, exist_ok=True)
                os.makedirs(draft_dir, exist_ok=True)
                os.makedirs(final_dir, exist_ok=True)

                ch_outline_md = self._format_chapter_outline_md(ch_outline)
                self._fm.write_markdown(os.path.join(outline_dir, "大纲.md"), ch_outline_md)

                ch_data = chapter_content_by_id.get(ch_id, {})
                content = ch_data.get("content", "")
                if content:
                    self._fm.write_markdown(os.path.join(draft_dir, "草稿.md"), content)

        saved_outline_ids = {co.get("chapter_id") for co in chapter_outlines}
        orphan_chapters = [ch for ch in chapters if ch.get("chapter_id", 0) not in saved_outline_ids]
        if orphan_chapters:
            orphan_dir = os.path.join(novel_dir, "未分配章节")
            os.makedirs(orphan_dir, exist_ok=True)
            for ch in orphan_chapters:
                ch_id = ch.get("chapter_id", 0)
                draft_path = os.path.join(orphan_dir, f"第{ch_id}章.md")
                self._fm.write_markdown(draft_path, ch.get("content", ""))

        self._logger.info(f"管线结果保存完成 - 目录: {novel_dir}")
        return novel_dir

    def _format_full_outline_md(self, full_outline: Dict, novel_title: str) -> str:
        """格式化全书大纲为 Markdown"""
        parts = [f"# {novel_title}\n"]

        if not full_outline or not isinstance(full_outline, dict):
            return "\n".join(parts)

        genre = full_outline.get("genre", "")
        if genre:
            parts.append(f"**类型**: {genre}\n")

        main_conflict = full_outline.get("main_conflict", "")
        if main_conflict:
            parts.append(f"**核心冲突**: {main_conflict}\n")

        ending = full_outline.get("ending_direction", "")
        if ending:
            parts.append(f"**结局走向**: {ending}\n")

        volumes = full_outline.get("volumes", [])
        if volumes:
            parts.append("## 分卷概览\n")
            for vol in volumes:
                vol_id = vol.get("volume_id", "?")
                vol_title = vol.get("volume_title", f"第{vol_id}卷")
                parts.append(f"### 第{vol_id}卷: {vol_title}")
                desc = vol.get("description", "")
                if desc:
                    parts.append(f"{desc}\n")
                events = vol.get("main_events", [])
                if events:
                    parts.append("**主要事件**:")
                    for evt in events:
                        if isinstance(evt, dict):
                            parts.append(f"- {evt.get('event_name', '')}: {evt.get('description', '')}")
                        else:
                            parts.append(f"- {evt}")
                    parts.append("")

        return "\n".join(parts)

    def _format_volume_outline_md(self, vol: Dict) -> str:
        """格式化分卷大纲为 Markdown"""
        vol_id = vol.get("volume_id", "?")
        vol_title = vol.get("volume_title", f"第{vol_id}卷")

        parts = [f"# 第{vol_id}卷: {vol_title}\n"]

        desc = vol.get("description", "")
        if desc:
            parts.append(f"{desc}\n")

        events = vol.get("main_events", [])
        if events:
            parts.append("## 主要事件\n")
            for evt in events:
                if isinstance(evt, dict):
                    parts.append(f"- **{evt.get('event_name', '')}**: {evt.get('description', '')}")
                else:
                    parts.append(f"- {evt}")
            parts.append("")

        return "\n".join(parts)

    def _format_chapter_outline_md(self, ch_outline: Dict) -> str:
        """格式化章节大纲为 Markdown"""
        ch_id = ch_outline.get("chapter_id", "?")
        ch_title = ch_outline.get("chapter_title", f"第{ch_id}章")
        vol_id = ch_outline.get("volume_id", "?")

        parts = [f"# 第{ch_id}章: {ch_title}\n"]
        parts.append(f"- **所属卷**: 第{vol_id}卷")

        synopsis = ch_outline.get("synopsis", "")
        if synopsis:
            parts.append(f"- **概要**: {synopsis}")

        involved = ch_outline.get("involved_characters", [])
        if involved:
            involved_str = ", ".join(self._to_char_name(c) for c in involved) if isinstance(involved, list) else str(involved)
            parts.append(f"- **涉及角色**: {involved_str}")

        factions = ch_outline.get("involved_factions", [])
        if factions:
            factions_str = ", ".join(str(f) for f in factions) if isinstance(factions, list) else str(factions)
            parts.append(f"- **涉及势力**: {factions_str}")

        foreshadow = ch_outline.get("foreshadowing", {})
        if isinstance(foreshadow, dict):
            plant = foreshadow.get("plant", [])
            resolve = foreshadow.get("resolve", [])
            if plant:
                plant_str = ", ".join(str(p) for p in plant) if isinstance(plant, list) else str(plant)
                parts.append(f"- **埋设伏笔**: {plant_str}")
            if resolve:
                resolve_str = ", ".join(str(r) for r in resolve) if isinstance(resolve, list) else str(resolve)
                parts.append(f"- **回收伏笔**: {resolve_str}")

        return "\n".join(parts)


_SLICE_TYPE_NAMES = frozenset([
    "角色登场", "场景引入", "氛围营造", "背景铺垫", "悬念设置",
    "情节推进", "冲突升级", "关系变化", "线索展开", "危机逼近",
    "冲突爆发", "关键转折", "生死抉择", "真相揭露", "命运交汇",
    "冲突解决", "情感升华", "伏笔收束", "余韵营造", "承前启后",
])

_INSTRUCTION_HEADINGS = frozenset([
    "场景", "指令", "结构要求", "创作要求", "推荐章节内容",
])

_ATTR_KEYWORDS = frozenset([
    "姓名", "身份", "性格", "特点", "人物", "人物属性", "地点", "时间", "场景",
    "事件", "核心冲突", "状态", "动作", "对象", "程度", "方式", "结果",
    "描述", "上文", "下文", "天气", "准备动作", "心情", "工具", "衣物", "特征",
])

_THINKING_PREFIXES = (
    "好的，", "好的,", "好的!", "让我", "我需要", "用户需要",
    "让我先", "让我来", "我来看看", "我理解", "根据用户",
    "首先，", "首先,", "第一步", "接下来", "让我分析",
    "我注意到", "我看到了", "让我理解", "让我思考",
    # === 新增：过滤模型思考过程 ===
    "嗯，", "嗯!", "嗯。", "嗯？", "嗯...", "嗯……",
    "啊，", "啊!", "啊。", "啊？",
    "等等，", "等等!", "等等。", "等等……",
    "再仔细", "再考虑", "再思考", "再读一遍", "再看看", "再看一下",
    "先确认", "先看看", "先思考", "先理清", "先分析", "先理解",
    "需要注意", "需要明确", "需要确认", "需要考虑",
    "那么严格来说", "那么我就", "那么这里", "那么这次", "那么按",
    "可以这样", "可以采用", "可以考虑", "可以这样构思",
    "这样构思", "这样想", "这样写", "这样设定",
    "这样合理", "这样安排", "这样处理", "这样写比较",
    "决定采用", "决定写", "决定从", "决定以", "决定用",
    "合理推断", "合理推测", "合理安排", "合理选择", "合理设计",
    "用户可能", "用户想要", "用户给出", "用户提示", "用户要求",
    "根据约束", "根据提示", "根据用户", "根据设定", "根据上文",
    "我决定", "我选择", "我认为", "我觉得",
    "所以我", "所以这里", "所以现在", "所以决定",
    "现在我", "现在让", "现在开", "现在写",
    "毕竟我", "毕竟这是", "毕竟这里",
    "再思考", "再考虑", "再读",
    "嗯，", "嗯，", "嗯...",
    "啊，问题在于",
    "等等……",
    "先确认",
    "需要注意",
    "可以这样构思",
    "决定采用",
    "合理推断",
    "用户可能误将",
    "根据约束条件",
    "我应该描写",
    "否则就违反了",
    "那么严格来说",
    "既然没有明确指定起点",
    "所以合理推断",
    "再仔细想想：",
    "回顾提示：",
    "再考虑：",
    "先聚焦",
    "嗯……决定采用",
)

def _is_instruction_heading(stripped: str) -> bool:
    if not stripped.startswith('#'):
        return False
    text = stripped.lstrip('#').strip()
    return text in _INSTRUCTION_HEADINGS or text == "已完成切片摘要"


def _is_instruction_block_heading(stripped: str) -> bool:
    if not stripped.startswith('#'):
        return False
    text = stripped.lstrip('#').strip()
    if text in _INSTRUCTION_HEADINGS:
        return True
    if text.startswith("推荐章节内容"):
        return True
    return False

def _is_slice_summary_echo(stripped: str) -> bool:
    if stripped == "已完成切片摘要" or stripped.startswith("已完成切片摘要"):
        return True
    if "已完成切" in stripped and len(stripped) < 15:
        return True
    return False

def _is_summary_blockquote(stripped: str) -> bool:
    if not stripped.startswith('>'):
        return False
    inner = stripped[1:].strip()
    if not inner:
        return True
    if inner.startswith('已完成切片摘要'):
        return True
    if inner.startswith('第') and '章' in inner[:6]:
        return True
    if inner.startswith('#') and '章' in inner[:10]:
        return True
    if inner.startswith('[') and '切片' in inner[:10]:
        return True
    return False

def _is_side_note(stripped: str) -> bool:
    if not stripped.startswith('>'):
        return False
    inner = stripped[1:].strip()
    if inner.startswith('*——') or inner.startswith('*—'):
        return True
    if inner.startswith('*') and inner.endswith('*') and len(inner) > 2:
        return True
    return False

def _is_slice_type_heading(stripped: str) -> bool:
    if not stripped.startswith('#'):
        return False
    text = stripped.lstrip('#').strip()
    if text in _SLICE_TYPE_NAMES:
        return True
    for name in _SLICE_TYPE_NAMES:
        if text.startswith(name):
            return True
    return False

def _is_chapter_title_repeat(stripped: str) -> bool:
    if stripped.startswith('#'):
        text = stripped.lstrip('#').strip()
        return bool(re.match(r'^第\d+章', text))
    if stripped.startswith('>'):
        inner = stripped[1:].strip()
        if inner.startswith('#'):
            inner = inner.lstrip('#').strip()
        if re.match(r'^第\d+章', inner) and len(inner) < 30:
            return True
    if re.match(r'^第\d+章', stripped) and len(stripped) < 30:
        return True
    return False

def _is_slice_divider(stripped: str) -> bool:
    if stripped.startswith('#'):
        text = stripped.lstrip('#').strip()
        if text.startswith('切片') and len(text) <= 5:
            return True
        if text.startswith('小说章节'):
            return True
        if '已完成切片部分分界' in text:
            return True
    return False

def _is_attribute_line(stripped: str) -> bool:
    clean = re.sub(r'\*+', '', stripped).strip()
    m = re.match(r'^(.+?)\s*[：:]', clean)
    if not m:
        return False
    key = m.group(1).strip()
    if key in _ATTR_KEYWORDS:
        return True
    if key in ("上文衔接点", "当前位置", "开头", "核心动作", "环境描写",
               "高潮", "完结部分", "结尾句"):
        return True
    if key.startswith('[') and key.endswith(']'):
        return True
    return False

def _is_thinking_prefix(stripped: str) -> bool:
    return stripped.startswith(_THINKING_PREFIXES)

def _is_novel_content_start(stripped: str) -> bool:
    if not stripped:
        return False
    if stripped.startswith('"') or stripped.startswith('"') or stripped.startswith('「'):
        return True
    if stripped.startswith('他') or stripped.startswith('她') or stripped.startswith('它'):
        return True
    if stripped.startswith('云尘') or stripped.startswith('云涛'):
        return True
    if re.match(r'^[\u4e00-\u9fff]', stripped) and len(stripped) > 10:
        return True
    return False

def _is_misc_junk(stripped: str) -> bool:
    if stripped.startswith('**典故') and ('：' in stripped or ':' in stripped):
        return True
    if stripped.startswith('这里是「') and stripped.endswith('」部分'):
        return True
    if stripped == '**切片结束**':
        return True
    if stripped.startswith('* [') and ']' in stripped:
        return True
    if stripped.startswith('> 历代') or stripped.startswith('> 对境界'):
        return True
    if re.match(r'^>\s*\*—+.*?\*$', stripped):
        return True
    # === 新增：过滤"元文本泄露"和占位符 ===
    # 元说明标签：第N/M部分、前文接续、本切片写作任务完成等
    if re.match(r'^（(第\s*\d+/\d+\s*部分|前文接续|本切片写作任务完成|.*省略.*字|.*接续.*|.*本切片.*完成|.*切片\d+.*完成|.*完毕|.*已完.*)）', stripped):
        return True
    if re.match(r'^[(（]((第\s*\d+/\d+\s*部分|前文接续|本切片写作任务完成|.*省略.*字|.*接续.*|.*本切片.*完成|.*切片\d+.*完成|.*完毕|.*已完.*))）', stripped):
        return True
    if stripped.startswith('---') and len(stripped) <= 6:
        return True
    if re.match(r'^\(此处省略\d+字\)$', stripped):
        return True
    # === 新增：过滤AI自检/规则引用等元话语 ===
    if re.match(r'^[(（].*(?:硬约束|遵循.*?约束|本片段|本节|已检查|均已|禁止|规则|要求).*[)）]', stripped):
        return True
    if re.match(r'^[*【\s]*(?:硬约束|遵循|本片段|本节|已检查|均已检查|未出场|硬性约束|不违反)', stripped):
        return True
    if re.match(r'^[（(].*?(?:不(?:违反|允许)|不(?:出场|出现)|首段不省略|禁止元文本).*?[)）]', stripped):
        return True
    # 过滤"续写内容待接"、"未完待续"等占位
    if "续写内容待接" in stripped or "续写待接" in stripped:
        return True
    if re.match(r'^[（(](?:未完待续|下文待续|待续|续写|下文)[）)]', stripped):
        return True
    if stripped.startswith("(未完") or stripped.startswith("（未完"):
        return True
    # === 新增：过滤仙侠题材违规（科幻/现代元素） ===
    sci_fi_terms = [
        "测量仪器", "显示屏", "电路", "传感器", "急救包", "止血贴",
        "防护服", "防毒面具", "防弹衣", "手电筒", "对讲机", "监控器",
        "监控摄像头", "监控屏幕", "齿轮", "轴承", "传动结构",
        "能量传递效率", "能量读数", "过载保护", "安全模式", "倒计时装置",
        "数据流", "二进制", "共振频率", "转速", "应力节点",
        "工程师", "自动售货机", "医师（西医）", "急救室",
        "传送阵结构", "电路图", "机械系统", "防护服", "芯片", "电容",
        "电阻", "电压", "电流", "电动", "电池", "保险丝",
        "电脑", "笔记本", "手机", "扫描", "蓝牙", "WiFi", "GPS",
        "时间戳", "日志", "服务器", "客户端", "操作系统", "程序员",
    ]
    for term in sci_fi_terms:
        if term in stripped:
            return True
    # === 新增：过滤模型思考/分析过程 ===
    if re.match(r'^(嗯|啊|哦|呃|哦|呀|哈|啧)[\s,!?.…、。，！?？~～—\-]+', stripped):
        return True
    if re.match(r'^(先|再|那么|所以|可以|应该|决定|合理|用户|根据|既然|毕竟|注意|等)[\s，,。!?:？…]+', stripped) and len(stripped) < 200:
        # 短句+分析性开头词，疑似思考过程
        thinking_indicators = [
            "思考", "考虑", "想想", "看看", "确认", "理解", "分析",
            "构思", "推断", "决定", "采用", "选择", "认为", "觉得",
            "用户", "提示", "约束", "可能", "想要", "应该", "必须",
            "等等", "这样", "那样", "如何", "怎么", "为何", "什么",
        ]
        if any(ind in stripped for ind in thinking_indicators):
            return True
    # 包含元话语词汇且无对话/叙事标记
    meta_words = ["构思", "考虑一下", "可能这样", "可能误将", "更合理",
                  "先回顾", "回顾提示", "再确认一下", "那么按", "按照这个"]
    if any(mw in stripped for mw in meta_words) and len(stripped) < 200:
        return True
    return False


# 段落级题材违规检测 - 用于过滤大段违规内容
SCIFI_BLOCK_TERMS = [
    "测量仪器", "显示屏", "防毒面具", "防护服", "急救包", "止血贴",
    "电路图", "二进制", "共振频率", "应力节点", "能量传递效率",
    "过载保护装置", "安全模式", "能量读数爆表", "传送阵结构",
    "机械系统", "工程师", "齿轮间隙", "百分比", "百分之",
    "金属靴底", "电路图案", "数据流", "监控屏幕", "防护服",
    "电动", "电池", "蓝牙", "WiFi", "GPS", "时间戳", "服务器",
    "操作系统", "自动售货机", "调试", "转速", "rpm", "频率",
    "电压", "电流", "电阻", "电容", "芯片", "传感器", "扫描",
    "电脑屏幕", "手机", "笔记本", "对讲机", "蓝牙", "保险丝",
    "现代物理", "现代科学", "工程学课程", "辅助工程", "工艺设计",
    "过载保护", "倒计时装置", "应力分析", "数据结构", "代码",
    "程序员", "监控", "监控器", "应急逃生通道",
    "反复重复着自己当初", "阻止丈夫从阳台坠落", "下意识地用手摸了摸胸前的挂坠",
]


def sanitize_genre_violation(content: str) -> str:
    """移除段落级别的题材违规内容（科幻/现代元素）

    对于包含明显科幻术语的整个段落，移除它们
    """
    if not content:
        return content
    lines = content.split('\n')
    result = []
    skip_para = False
    paragraph_buffer = []
    threshold = 1  # 段落中包含1个及以上科幻术语就移除

    def flush_buffer():
        nonlocal skip_para, paragraph_buffer
        if not paragraph_buffer:
            return
        joined = '\n'.join(paragraph_buffer)
        # 检查是否包含科幻术语
        violation_count = sum(1 for term in SCIFI_BLOCK_TERMS if term in joined)
        if violation_count < threshold:
            result.append(joined)
        # else: 整段丢弃
        paragraph_buffer = []

    for line in lines:
        if not line.strip():
            flush_buffer()
            result.append(line)
            continue
        paragraph_buffer.append(line)

    flush_buffer()
    return '\n'.join(result)


# ============================================================
# 段落级相似度检测与去重
# ============================================================

def _split_into_paragraphs(content: str) -> List[str]:
    """将内容拆分为段落（按空行或长换行分组）"""
    if not content:
        return []
    # 先按空行分段
    raw_paras = re.split(r'\n\s*\n', content.strip())
    paras = []
    for p in raw_paras:
        p = p.strip()
        if p:
            paras.append(p)
    return paras


def _paragraph_similarity(p1: str, p2: str) -> float:
    """段落级相似度 - 基于字符集合的 Jaccard 系数

    返回 0~1，1 表示完全相同
    """
    if not p1 or not p2:
        return 0.0

    # 移除空白和标点，归一化
    def normalize(text: str) -> str:
        return re.sub(r'[\s\u3000，。！？、：；"\'\u201c\u201d\u300c\u300d\u300e\u300f《》（）()…—\-]+', '', text)

    s1 = normalize(p1)
    s2 = normalize(p2)

    if not s1 or not s2:
        return 0.0

    # 短串：完全相同
    if s1 == s2:
        return 1.0

    # 字符集合 Jaccard
    set1 = set(s1)
    set2 = set(s2)
    intersection = set1 & set2
    union = set1 | set2
    jaccard = len(intersection) / len(union) if union else 0.0

    # 长度差异
    len_ratio = min(len(s1), len(s2)) / max(len(s1), len(s2))

    # 综合得分：要求字符集合相似 + 长度相近
    return jaccard * 0.5 + len_ratio * 0.5


def _remove_duplicate_paragraphs(content: str, threshold: float = 0.55) -> str:
    """段落级去重 - 在单个内容中移除重复或高度相似的段落

    Args:
        content: 文本
        threshold: 相似度阈值，超过则视为重复并去重

    Returns:
        去重后的内容
    """
    if not content:
        return content

    paragraphs = _split_into_paragraphs(content)
    if len(paragraphs) <= 1:
        return content

    kept = []
    for p in paragraphs:
        is_dup = False
        for kp in kept:
            sim = _paragraph_similarity(p, kp)
            if sim >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(p)

    return "\n\n".join(kept)


def _remove_cross_duplicate_paragraphs(
    current_content: str,
    previous_contents: List[str],
    threshold: float = 0.55,
) -> str:
    """跨切片去重 - 移除当前切片中与之前切片重复的段落

    Args:
        current_content: 当前切片内容
        previous_contents: 之前所有切片内容列表
        threshold: 相似度阈值

    Returns:
        去重后的当前切片内容（至少保留首段以保持连续性）
    """
    if not current_content or not previous_contents:
        return current_content

    # 收集之前所有段落
    prev_paragraphs = []
    for prev in previous_contents:
        prev_paragraphs.extend(_split_into_paragraphs(prev))

    if not prev_paragraphs:
        return current_content

    current_paragraphs = _split_into_paragraphs(current_content)
    if not current_paragraphs:
        return current_content

    # 始终保留首段（确保连续性）
    kept = [current_paragraphs[0]] if current_paragraphs else []

    for p in current_paragraphs[1:]:
        is_dup = False
        for pp in prev_paragraphs:
            sim = _paragraph_similarity(p, pp)
            if sim >= threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(p)

    return "\n\n".join(kept)


# ============================================================
# 新角色名检测与替换
# ============================================================

# 常见的"模型凭空调用出来的新名字"启发式列表（黑名单示例）
COMMON_FORBIDDEN_NAMES = [
    "林逸风", "苏音音", "宋一鸣", "秦师傅", "老陈", "王婆",
    "张道长", "李长老", "王掌门", "赵师兄", "陈师弟", "杨公子",
    "林师妹", "苏姑娘", "周先生", "吴夫人", "郑先生", "孙少爷",
    "何师弟", "高姑娘", "罗公子", "梁长老", "宋姑娘", "韩弟子",
    "唐师妹", "冯道长", "邓少侠", "曹帮主", "彭公子", "曾长老",
    "萧姑娘", "田弟子", "董小姐", "袁师兄", "潘姑娘", "于少侠",
    "蒋先生", "蔡姑娘", "余公子", "杜道长", "叶小姐", "程先生",
]


def detect_forbidden_names(content: str, allowed_names: List[str]) -> List[str]:
    """检测内容中是否出现了不在白名单中、且可能是角色名的 2~3 字中文名字

    采用保守策略：
    1. 优先匹配 COMMON_FORBIDDEN_NAMES 列表中的具体名字
    2. 不做激进的 2-3 字汉字启发式匹配（容易误判常见词组）
    3. 仅在词组前/后有明显人物对话标记时（如"道："、"说"）才做更严格判断

    Returns:
        检测到的违禁角色名列表
    """
    if not content or not allowed_names:
        return []

    allowed_set = set(allowed_names)
    found = []

    # 第一轮：精确匹配已知黑名单
    for name in COMMON_FORBIDDEN_NAMES:
        if name in content and name not in allowed_set:
            found.append(name)

    # 第二轮：检测对话标记 + 2字名字（仅在引号/道/说的上下文中检测）
    # 例如：「林道风道」「苏姑娘问」等
    # 模式：引号/句号/逗号 + 2-3字中文 + 道/说/问/答/喝
    dialogue_patterns = [
        r'[，。！？：；""\'\u201c\u201d\u300c\u300d]\s*([\u4e00-\u9fff]{2,3})\s*[道说问答喝喊叫]',
        r'([\u4e00-\u9fff]{2,3})\s*[道说问答喝喊叫][：:""\'\u201c\u201d\u300c\u300d]?',
        r'["\u201c\u300c]([\u4e00-\u9fff]{2,3})["\u201d\u300d]',
    ]
    for pat in dialogue_patterns:
        for m in re.finditer(pat, content):
            name = m.group(1) if m.lastindex else m.group(0)
            if name in allowed_set or name in found:
                continue
            if name in COMMON_FORBIDDEN_NAMES:
                if name not in found:
                    found.append(name)
            # 第三轮：如果是引号包裹的 2-3 字名字，且不在白名单
            elif pat == dialogue_patterns[2]:  # 引号模式
                if name not in allowed_set and len(name) <= 3:
                    # 必须不在常见词中
                    if name not in {"你们", "我们", "他们", "她们", "自己", "什么",
                                    "怎么", "为何", "这里", "那里", "如此", "这般",
                                    "那个", "这个", "正是", "可是", "但是", "而且",
                                    "因为", "所以", "不过", "然而", "如果", "虽然",
                                    "啊", "哦", "嗯", "呀", "吧", "呢", "吗",
                                    "宋霄", "钱开凤", "墨羽", "冷无涯", "林逸风",
                                    "苏音音"}:
                        # 仅在白名单很大时才使用此启发式
                        if len(allowed_set) > 5:  # 多角色场景才严格
                            if name not in found:
                                found.append(name)

    return found


def _replace_forbidden_names(
    content: str,
    allowed_names: List[str],
    characters: List[Dict] = None,
) -> str:
    """将内容中检测到的违禁角色名替换为白名单中的角色

    替换策略：
    1. 如果有 characters 信息，优先按"角色定位"匹配（青云宗弟子 -> 墨羽）
    2. 否则替换为白名单中第一个"配角"角色
    """
    if not content or not allowed_names:
        return content

    forbidden = detect_forbidden_names(content, allowed_names)
    if not forbidden:
        return content

    # 选择替换目标：白名单中"看起来像配角"的角色
    candidates = allowed_names.copy()
    # 排序：让"无涯"或"墨"作为首选（反派/长老）
    preferred = []
    for n in candidates:
        if "无涯" in n or "墨" in n or "长老" in n:
            preferred.append(n)
    for n in candidates:
        if n not in preferred:
            preferred.append(n)

    result = content
    for i, name in enumerate(forbidden):
        replacement = preferred[i % len(preferred)] if preferred else allowed_names[0]
        result = result.replace(name, replacement)

    return result



