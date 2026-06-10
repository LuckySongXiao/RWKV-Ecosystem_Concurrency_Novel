"""Web UI - Flask 应用

提供管线管理、世界状态查看、Roleplay 交互、冲突裁决等 Web 界面。
支持 WebSocket 实时推送管线进度到矩阵视图。
"""

import json
import os
import queue
import threading
import time
from typing import Optional

from flask import Flask, render_template, jsonify, request, redirect, url_for
from flask_socketio import SocketIO, emit

from src.orchestrator import Orchestrator

def _is_path_within(target_path: str, base_dir: str) -> bool:
    """校验 target_path 是否在 base_dir 之内（跨平台、跨正反斜杠、大小写不敏感）"""
    if not target_path or not base_dir:
        return False
    try:
        norm_target = os.path.normcase(os.path.abspath(target_path))
        norm_base = os.path.normcase(os.path.abspath(base_dir))
        common = os.path.commonpath([norm_target, norm_base])
        return common == norm_base
    except (ValueError, OSError):
        return False


socketio = SocketIO(cors_allowed_origins="*", async_mode="gevent")


def create_app(config_path: str = "pipeline.config.json") -> Flask:
    """创建 Flask 应用"""
    app = Flask(__name__,
                template_folder=os.path.join(os.path.dirname(__file__), "templates"),
                static_folder=os.path.join(os.path.dirname(__file__), "static"))
    app.config["SECRET_KEY"] = "rwkv-novel-socketio"
    socketio.init_app(app)

    orchestrator: Optional[Orchestrator] = None
    pipeline_thread: Optional[threading.Thread] = None

    _ws_queue = queue.Queue()
    _ws_queue_running = True

    def _ws_queue_worker():
        while _ws_queue_running:
            try:
                items = []
                try:
                    item = _ws_queue.get(timeout=0.1)
                    items.append(item)
                    while not _ws_queue.empty():
                        items.append(_ws_queue.get_nowait())
                except queue.Empty:
                    continue

                merged = {}
                for event_name, data in items:
                    if event_name not in merged:
                        merged[event_name] = data
                    elif event_name == "progress_update":
                        merged[event_name] = data
                    elif event_name == "state_update":
                        merged[event_name].update(data)

                for event_name, data in merged.items():
                    try:
                        socketio.emit(event_name, data)
                    except Exception:
                        pass
            except Exception:
                pass

    _ws_worker_thread = threading.Thread(target=_ws_queue_worker, daemon=True)
    _ws_worker_thread.start()

    def get_orchestrator() -> Orchestrator:
        nonlocal orchestrator
        if orchestrator is None:
            orchestrator = Orchestrator(config_path)
        return orchestrator

    # ---- WebSocket 事件 ----
    @socketio.on("connect")
    def handle_connect():
        emit("connected", {"message": "矩阵视图已连接"})

    @socketio.on("disconnect")
    def handle_disconnect():
        pass

    @socketio.on("request_progress")
    def handle_request_progress():
        optimized_orch = app.config.get("optimized_pipeline_orchestrator")
        if optimized_orch:
            emit("progress_update", optimized_orch.get_progress())
        else:
            emit("progress_update", {
                "status": "idle",
                "current_stage": "",
                "total_tasks": 0,
                "completed_tasks": 0,
                "chapter_matrix": [],
            })

    def _emit_progress(data: dict):
        try:
            _ws_queue.put(("progress_update", data))
            state_update = {
                "pipeline_status": data.get("status", "idle"),
                "pipeline_stage": data.get("current_stage", ""),
            }
            if data.get("total_tasks", 0) > 0:
                state_update["pipeline_progress"] = round(
                    (data.get("completed_tasks", 0) / data["total_tasks"]) * 100, 1
                )
            global_state.update(state_update)
            _ws_queue.put(("state_update", state_update))
        except Exception:
            pass

    global_state = {
        "theme": "",
        "genres": [],
        "character_count": 6,
        "protagonist_names": [],
        "antagonist_names": [],
        "volume_count": 3,
        "chapters_per_volume": 5,
        "slices_per_chapter": 10,
        "concurrency_config": {
            "character_concurrency": 6,
            "outline_concurrency": 5,
            "chapter_concurrency": 8,
            "batch_size": 8,
        },
        "extra_context": "",
        "pipeline_status": "idle",
        "pipeline_stage": "",
        "pipeline_progress": 0,
        "spec_fields": {},
        "style_guide": "",
    }

    def _broadcast_state(changes: dict):
        try:
            global_state.update(changes)
            _ws_queue.put(("state_update", changes))
        except Exception:
            pass

    @socketio.on("request_state")
    def handle_request_state():
        emit("state_update", global_state)

    @app.route("/api/global/state")
    def api_global_state():
        return jsonify(global_state)

    @app.route("/api/global/state", methods=["POST"])
    def api_update_global_state():
        data = request.json
        if not data:
            return jsonify({"status": "error", "message": "empty data"})
        _broadcast_state(data)
        return jsonify({"status": "ok"})

    # ---- 页面路由 ----
    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/pipeline")
    def pipeline_page():
        return render_template("pipeline.html")

    @app.route("/world")
    def world_page():
        return render_template("world.html")

    @app.route("/roleplay")
    def roleplay_page():
        return render_template("roleplay.html")

    @app.route("/review")
    def review_page():
        return render_template("review.html")

    @app.route("/create")
    def create_page():
        return render_template("create.html")

    @app.route("/matrix")
    def matrix_page():
        return render_template("matrix.html")

    @app.route("/bookshelf")
    def bookshelf_page():
        return render_template("bookshelf.html")

    # ---- API 路由 ----
    @app.route("/api/models")
    def api_list_models():
        """列出可用模型"""
        from src.core.rwkv_service import scan_available_models, get_service_manager
        import os
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        models = scan_available_models(project_root)
        mgr = get_service_manager(project_root)
        current_model = mgr.get_current_model()
        service_running = mgr.is_service_running()
        return jsonify({
            "models": models,
            "current_model": current_model,
            "service_running": service_running,
        })

    @app.route("/api/models/start", methods=["POST"])
    def api_start_model():
        """启动指定模型的服务"""
        from src.core.rwkv_service import ensure_rwkv_service, get_service_manager
        import os
        data = request.json
        model_path = data.get("model_path", "")

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        mgr = get_service_manager(project_root)

        if mgr.is_service_running():
            return jsonify({"status": "already_running", "message": "RWKV 服务已在运行，请先停止当前服务再切换模型"})

        if model_path:
            if not os.path.exists(model_path):
                return jsonify({"status": "error", "message": f"模型文件不存在: {model_path}"})
            mgr.set_model(model_path)

        success = ensure_rwkv_service(project_root, model_path=model_path if model_path else None)
        if success:
            return jsonify({"status": "started", "message": f"模型服务已启动", "model": mgr.get_current_model()})
        else:
            return jsonify({"status": "error", "message": "模型服务启动失败"})

    @app.route("/api/models/stop", methods=["POST"])
    def api_stop_model():
        """停止模型服务"""
        from src.core.rwkv_service import shutdown_rwkv_service
        shutdown_rwkv_service()
        return jsonify({"status": "stopped"})

    @app.route("/api/status")
    def api_status():
        orch = get_orchestrator()
        status = orch.get_status()
        status["unresolved_conflicts"] = orch._tools.get_unresolved_conflicts()
        return jsonify(status)

    @app.route("/api/pipeline/start", methods=["POST"])
    def api_pipeline_start():
        nonlocal pipeline_thread
        orch = get_orchestrator()

        if pipeline_thread and pipeline_thread.is_alive():
            return jsonify({"status": "already_running"})

        pipeline_thread = threading.Thread(target=orch.run, daemon=True)
        pipeline_thread.start()
        return jsonify({"status": "started"})

    @app.route("/api/pipeline/resume", methods=["POST"])
    def api_pipeline_resume():
        nonlocal pipeline_thread
        orch = get_orchestrator()

        if pipeline_thread and pipeline_thread.is_alive():
            return jsonify({"status": "already_running"})

        pipeline_thread = threading.Thread(target=orch.run, daemon=True)
        pipeline_thread.start()
        return jsonify({"status": "resumed"})

    @app.route("/api/pipeline/auto", methods=["POST"])
    def api_pipeline_auto():
        """全自动管线 - 从主题到正文的完整流程，自动启动服务"""
        from src.core.auto_pipeline import AutoPipelineOrchestrator
        
        data = request.json
        theme = data.get("theme", "仙侠")
        protagonist_names = data.get("protagonist_names", [])
        antagonist_names = data.get("antagonist_names", [])
        volume_count = data.get("volume_count", 3)
        chapters_per_volume = data.get("chapters_per_volume", 5)
        max_concurrency = data.get("max_concurrency", 200)
        extra_context = data.get("extra_context", "")

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        try:
            from src.core.rwkv_service import ensure_rwkv_service, get_service_manager
            mgr = get_service_manager(project_root)
            if not mgr.is_service_running():
                if not ensure_rwkv_service(project_root):
                    return jsonify({
                        "status": "error",
                        "message": "RWKV 模型服务启动失败，请检查模型配置",
                    })
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"无法启动 RWKV 服务: {e}",
            })

        def run_auto_pipeline():
            try:
                orchestrator = AutoPipelineOrchestrator(config_path, max_concurrency)
                result = orchestrator.run_full_pipeline(
                    theme=theme,
                    protagonist_names=protagonist_names,
                    antagonist_names=antagonist_names,
                    volume_count=volume_count,
                    chapters_per_volume=chapters_per_volume,
                    extra_context=extra_context,
                )
                # 保存结果到全局变量
                app.config['last_auto_pipeline_result'] = result
            except Exception as e:
                app.config['last_auto_pipeline_result'] = {
                    "status": "failed",
                    "error": str(e),
                }

        # 在后台线程中运行
        thread = threading.Thread(target=run_auto_pipeline, daemon=True)
        thread.start()

        return jsonify({
            "status": "started",
            "message": "全自动管线已启动，请在状态中查看进度",
        })

    @app.route("/api/pipeline/status")
    def api_pipeline_status():
        """获取管线状态"""
        orch = get_orchestrator()
        status = orch.get_status()
        
        # 添加全自动管线结果
        auto_result = app.config.get('last_auto_pipeline_result')
        if auto_result:
            status['auto_pipeline_result'] = auto_result
        
        return jsonify(status)

    @app.route("/api/pipeline/progress")
    def api_pipeline_progress():
        """获取管线进度（用于矩阵视图）"""
        optimized_orch = app.config.get('optimized_pipeline_orchestrator')
        if optimized_orch:
            return jsonify(optimized_orch.get_progress())
        return jsonify({
            "status": "idle",
            "current_stage": "",
            "total_tasks": 0,
            "completed_tasks": 0,
            "chapter_matrix": [],
        })

    @app.route("/api/pipeline/checkpoint")
    def api_pipeline_checkpoint():
        """检查是否有可恢复的检查点"""
        from src.core.config import load_config
        from src.core.file_manager import FileManager
        try:
            cfg = load_config(config_path)
            fm = FileManager(cfg.paths)
            cp_path = os.path.join(fm.output_dir, ".cache", "pipeline_checkpoint.json")
            if not os.path.exists(cp_path):
                return jsonify({"has_checkpoint": False})
            with open(cp_path, 'r', encoding='utf-8') as f:
                checkpoint = json.load(f)
            stage = checkpoint.get("stage", "")
            data = checkpoint.get("data", {})
            params = data.get("pipeline_params", {})
            return jsonify({
                "has_checkpoint": True,
                "stage": stage,
                "timestamp": checkpoint.get("timestamp", 0),
                "params": params,
                "error": data.get("error", ""),
                "completed_stages": data.get("completed_stages", []),
            })
        except Exception as e:
            return jsonify({"has_checkpoint": False, "error": str(e)})

    @app.route("/api/pipeline/checkpoint", methods=["DELETE"])
    def api_pipeline_checkpoint_delete():
        """删除检查点"""
        from src.core.config import load_config
        from src.core.file_manager import FileManager
        try:
            cfg = load_config(config_path)
            fm = FileManager(cfg.paths)
            cp_path = os.path.join(fm.output_dir, ".cache", "pipeline_checkpoint.json")
            if os.path.exists(cp_path):
                os.remove(cp_path)
            return jsonify({"status": "deleted"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    @app.route("/api/pipeline/optimized", methods=["POST"])
    def api_pipeline_optimized():
        """优化版全自动管线 - 支持章节切片和矩阵视图，自动启动服务"""
        from src.core.optimized_pipeline import OptimizedPipelineOrchestrator
        
        data = request.json
        theme = data.get("theme", "仙侠")
        character_count = data.get("character_count", 6)
        protagonist_names = data.get("protagonist_names", [])
        antagonist_names = data.get("antagonist_names", [])
        volume_count = data.get("volume_count", 3)
        chapters_per_volume = data.get("chapters_per_volume", 5)
        slices_per_chapter = data.get("slices_per_chapter", 10)
        concurrency_config = data.get("concurrency_config", {})
        extra_context = data.get("extra_context", "")
        resume = data.get("resume", False)

        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        try:
            from src.core.rwkv_service import ensure_rwkv_service, get_service_manager
            mgr = get_service_manager(project_root)
            if not mgr.is_service_running():
                if not ensure_rwkv_service(project_root):
                    return jsonify({
                        "status": "error",
                        "message": "RWKV 模型服务启动失败，请检查模型配置",
                    })
        except Exception as e:
            return jsonify({
                "status": "error",
                "message": f"无法启动 RWKV 服务: {e}",
            })

        _broadcast_state({
            "theme": theme,
            "character_count": character_count,
            "protagonist_names": protagonist_names,
            "antagonist_names": antagonist_names,
            "volume_count": volume_count,
            "chapters_per_volume": chapters_per_volume,
            "slices_per_chapter": slices_per_chapter,
            "concurrency_config": concurrency_config,
            "extra_context": extra_context,
            "pipeline_status": "running",
            "pipeline_stage": "starting",
        })

        def run_optimized_pipeline():
            try:
                orchestrator = OptimizedPipelineOrchestrator(
                    config_path, concurrency_config,
                    progress_callback=_emit_progress,
                )
                app.config['optimized_pipeline_orchestrator'] = orchestrator
                
                result = orchestrator.run_pipeline(
                    theme=theme,
                    character_count=character_count,
                    protagonist_names=protagonist_names,
                    antagonist_names=antagonist_names,
                    volume_count=volume_count,
                    chapters_per_volume=chapters_per_volume,
                    slices_per_chapter=slices_per_chapter,
                    extra_context=extra_context,
                    resume=resume,
                )
                app.config['last_optimized_pipeline_result'] = result
                _emit_progress(orchestrator.get_progress())
                _broadcast_state({
                    "pipeline_status": result.get("status", "completed"),
                    "pipeline_stage": "",
                })
            except Exception as e:
                app.config['last_optimized_pipeline_result'] = {
                    "status": "failed",
                    "error": str(e),
                }
                _emit_progress({"status": "failed", "error": str(e),
                                "current_stage": "", "total_tasks": 0,
                                "completed_tasks": 0, "chapter_matrix": []})
                _broadcast_state({
                    "pipeline_status": "failed",
                    "pipeline_stage": "",
                })

        thread = threading.Thread(target=run_optimized_pipeline, daemon=True)
        thread.start()

        return jsonify({
            "status": "started",
            "message": "优化版管线已启动，请在矩阵视图中查看进度",
        })

    # ---- 世界状态 API ----
    @app.route("/api/world/characters")
    def api_characters():
        orch = get_orchestrator()
        chars = [c.to_dict() for c in orch.world_engine.characters.values()]
        return jsonify(chars)

    @app.route("/api/world/factions")
    def api_factions():
        orch = get_orchestrator()
        factions = [f.to_dict() for f in orch.world_engine.factions.values()]
        return jsonify(factions)

    @app.route("/api/world/economy")
    def api_economy():
        orch = get_orchestrator()
        return jsonify(orch.world_engine.economy.to_dict())

    @app.route("/api/world/entity/<entity_id>")
    def api_entity(entity_id):
        orch = get_orchestrator()
        entity = orch.world_engine.query_entity(entity_id)
        relations = orch.world_engine.query_relations(entity_id)
        return jsonify({"entity": entity, "relations": relations})

    @app.route("/api/world/foreshadowings")
    def api_foreshadowings():
        orch = get_orchestrator()
        status_filter = request.args.get("status", None)
        return jsonify(orch.world_engine.query_foreshadowings(status_filter))

    @app.route("/api/world/timeline")
    def api_timeline():
        orch = get_orchestrator()
        from_ch = int(request.args.get("from", 0))
        to_ch = int(request.args.get("to", 99999))
        return jsonify(orch.world_engine.query_timeline(from_ch, to_ch))

    @app.route("/api/world/chapters/count")
    def api_chapters_count():
        orch = get_orchestrator()
        try:
            chapters = orch._fm.read_jsonl(orch._fm.chapters_path())
            return jsonify({"count": len(chapters)})
        except Exception:
            return jsonify({"count": 0})

    # ---- 统计与报告 API ----
    @app.route("/api/stats/summary")
    def api_stats_summary():
        """获取项目统计摘要"""
        import os
        from datetime import datetime
        
        orch = get_orchestrator()
        
        # 统计生成内容
        draft_count = 0
        draft_dir = orch._fm.get_draft_dir()
        if os.path.exists(draft_dir):
            draft_count = len([f for f in os.listdir(draft_dir) if f.endswith('.md')])
        
        # 统计章节
        try:
            chapters = orch._fm.read_jsonl(orch._fm.chapters_path())
            chapter_count = len(chapters)
        except Exception:
            chapter_count = 0
        
        # 统计卷
        try:
            volumes = orch._fm.read_jsonl(orch._fm.volumes_path())
            volume_count = len(volumes)
        except Exception:
            volume_count = 0
        
        # 统计日志
        log_count = 0
        log_dir = os.path.join(orch._fm.get_output_dir(), "logs")
        if os.path.exists(log_dir):
            log_count = len([f for f in os.listdir(log_dir) if f.endswith('.log')])
        
        # 检查点信息
        checkpoint_info = None
        if os.path.exists(orch._checkpoint_path):
            try:
                with open(orch._checkpoint_path, 'r', encoding='utf-8') as f:
                    checkpoint_info = json.load(f)
            except Exception:
                pass
        
        return jsonify({
            "draft_chapters": draft_count,
            "total_chapters": chapter_count,
            "total_volumes": volume_count,
            "characters": len(orch.world_engine.characters),
            "factions": len(orch.world_engine.factions),
            "log_files": log_count,
            "checkpoint": checkpoint_info,
            "generated_at": datetime.now().isoformat(),
        })

    # ---- Roleplay API ----
    @app.route("/api/roleplay/novels")
    def api_roleplay_novels():
        """列出所有包含角色数据的小说目录"""
        orch = get_orchestrator()
        output_dir = orch._fm.get_output_dir()
        novel_dirs = []
        if os.path.isdir(output_dir):
            for name in os.listdir(output_dir):
                if name.startswith("草稿_"):
                    novel_path = os.path.join(output_dir, name)
                    char_file = os.path.join(novel_path, "characters.json")
                    if os.path.isdir(novel_path) and os.path.isfile(char_file):
                        try:
                            with open(char_file, 'r', encoding='utf-8') as f:
                                chars = json.load(f)
                            novel_dirs.append({
                                "name": name,
                                "path": novel_path,
                                "character_count": len(chars) if isinstance(chars, list) else 0,
                            })
                        except Exception:
                            novel_dirs.append({"name": name, "path": novel_path, "character_count": 0})
        return jsonify({"novels": novel_dirs})

    @app.route("/api/roleplay/characters")
    def api_roleplay_characters():
        """从管线生成的 characters.json 读取角色列表

        查询参数:
          - novel_dir: 小说目录路径（可选，默认自动查找第一个）
        """
        novel_dir = request.args.get("novel_dir", "")
        if not novel_dir:
            orch = get_orchestrator()
            output_dir = orch._fm.get_output_dir()
            if os.path.isdir(output_dir):
                for name in os.listdir(output_dir):
                    if name.startswith("草稿_"):
                        novel_dir = os.path.join(output_dir, name)
                        break
        if not novel_dir or not os.path.isdir(novel_dir):
            return jsonify({"characters": [], "novel_name": ""})

        char_file = os.path.join(novel_dir, "characters.json")
        if not os.path.isfile(char_file):
            return jsonify({"characters": [], "novel_name": os.path.basename(novel_dir)})

        try:
            with open(char_file, 'r', encoding='utf-8') as f:
                characters = json.load(f)
            if isinstance(characters, list):
                for i, ch in enumerate(characters):
                    if "character_id" not in ch:
                        ch["character_id"] = ch.get("name", f"char_{i}")
            return jsonify({
                "characters": characters,
                "novel_name": os.path.basename(novel_dir).replace("草稿_", ""),
            })
        except Exception as e:
            return jsonify({"characters": [], "novel_name": "", "error": str(e)})

    @app.route("/api/roleplay/character/<character_name>")
    def api_roleplay_character_detail(character_name):
        """获取单个角色的详细信息

        查询参数:
          - novel_dir: 小说目录路径
        """
        novel_dir = request.args.get("novel_dir", "")
        if not novel_dir:
            orch = get_orchestrator()
            output_dir = orch._fm.get_output_dir()
            if os.path.isdir(output_dir):
                for name in os.listdir(output_dir):
                    if name.startswith("草稿_"):
                        novel_dir = os.path.join(output_dir, name)
                        break

        char_file = os.path.join(novel_dir, "characters.json") if novel_dir else ""
        if not char_file or not os.path.isfile(char_file):
            return jsonify({"error": "角色数据文件不存在"}), 404

        try:
            with open(char_file, 'r', encoding='utf-8') as f:
                characters = json.load(f)
            for ch in characters:
                if ch.get("name") == character_name or ch.get("character_id") == character_name:
                    return jsonify(ch)
            return jsonify({"error": f"角色 {character_name} 未找到"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/roleplay/dialogue", methods=["POST"])
    def api_roleplay_dialogue():
        """角色扮演对话 - 支持管线生成的角色数据

        请求体:
        {
            "character_id": "角色名或ID",
            "scene_context": "场景上下文",
            "user_input": "用户输入",
            "dialogue_history": "对话历史",
            "novel_dir": "小说目录路径（可选）"
        }
        """
        data = request.json or {}
        character_id = data.get("character_id", "")
        scene_context = data.get("scene_context", "")
        user_input = data.get("user_input", "")
        dialogue_history = data.get("dialogue_history", "")
        novel_dir = data.get("novel_dir", "")

        character_info = None
        if novel_dir:
            char_file = os.path.join(novel_dir, "characters.json")
            if os.path.isfile(char_file):
                try:
                    with open(char_file, 'r', encoding='utf-8') as f:
                        characters = json.load(f)
                    for ch in characters:
                        if ch.get("name") == character_id or ch.get("character_id") == character_id:
                            character_info = ch
                            break
                except Exception:
                    pass

        if character_info:
            from src.core.config import SamplingParams
            from src.core.prompt_builder import PromptBuilder

            char_state_str = json.dumps(character_info, ensure_ascii=False, indent=2)
            prompt = PromptBuilder.build_roleplay_prompt(
                character_id=character_info.get("name", character_id),
                character_state=char_state_str,
                scene_context=scene_context,
                user_input=user_input,
                dialogue_history=dialogue_history,
            )

            orch = get_orchestrator()
            sampling = SamplingParams(temperature=0.85, top_p=0.9, max_tokens=1024)
            try:
                results = orch._client.big_batch_completions(
                    contents=[prompt],
                    sampling=sampling,
                    stream=False,
                )
                response = results[0] if results else ""
            except Exception as e:
                response = f"[对话生成失败: {e}]"
        else:
            try:
                orch = get_orchestrator()
                response = orch.roleplay.dialogue(
                    character_id=character_id,
                    scene_context=scene_context,
                    user_input=user_input,
                    dialogue_history=dialogue_history,
                )
            except Exception as e:
                response = f"[对话生成失败: {e}]"

        return jsonify({"response": response})

    @app.route("/api/roleplay/monologue", methods=["POST"])
    def api_roleplay_monologue():
        """角色内心独白 - 支持管线生成的角色数据

        请求体:
        {
            "character_id": "角色名或ID",
            "situation": "当前处境",
            "novel_dir": "小说目录路径（可选）"
        }
        """
        data = request.json or {}
        character_id = data.get("character_id", "")
        situation = data.get("situation", "")
        novel_dir = data.get("novel_dir", "")

        character_info = None
        if novel_dir:
            char_file = os.path.join(novel_dir, "characters.json")
            if os.path.isfile(char_file):
                try:
                    with open(char_file, 'r', encoding='utf-8') as f:
                        characters = json.load(f)
                    for ch in characters:
                        if ch.get("name") == character_id or ch.get("character_id") == character_id:
                            character_info = ch
                            break
                except Exception:
                    pass

        if character_info:
            from src.core.config import SamplingParams
            from src.core.prompt_builder import PromptBuilder

            char_state_str = json.dumps(character_info, ensure_ascii=False, indent=2)
            prompt = PromptBuilder.build_roleplay_prompt(
                character_id=character_info.get("name", character_id),
                character_state=char_state_str,
                scene_context=situation,
                user_input="请描述你此刻的内心想法和感受。",
                dialogue_history="",
            )

            orch = get_orchestrator()
            sampling = SamplingParams(temperature=0.9, top_p=0.9, max_tokens=1024)
            try:
                results = orch._client.big_batch_completions(
                    contents=[prompt],
                    sampling=sampling,
                    stream=False,
                )
                monologue = results[0] if results else ""
            except Exception as e:
                monologue = f"[独白生成失败: {e}]"
        else:
            try:
                orch = get_orchestrator()
                monologue = orch.roleplay.inner_monologue(
                    character_id=character_id,
                    situation=situation,
                )
            except Exception as e:
                monologue = f"[独白生成失败: {e}]"

        return jsonify({"monologue": monologue})

    @app.route("/api/roleplay/multi", methods=["POST"])
    def api_roleplay_multi():
        orch = get_orchestrator()
        data = request.json
        result = orch.roleplay.multi_dialogue(
            character_ids=data["character_ids"],
            scene_context=data.get("scene_context", ""),
            topic=data["topic"],
            rounds=data.get("rounds", 3),
        )
        return jsonify(result)

    @app.route("/api/roleplay/reset/<character_id>", methods=["POST"])
    def api_roleplay_reset(character_id):
        orch = get_orchestrator()
        orch.roleplay.reset_session(character_id)
        return jsonify({"status": "reset"})

    # ---- 审核与裁决 API ----
    @app.route("/api/review/pending")
    def api_pending_approvals():
        orch = get_orchestrator()
        return jsonify(orch._tools.get_pending_approvals())

    @app.route("/api/review/approve/<int:approval_id>", methods=["POST"])
    def api_approve(approval_id):
        orch = get_orchestrator()
        data = request.json or {}
        result = orch._tools.approve_pending(
            approval_id,
            approved=data.get("approved", True),
            modification=data.get("modification"),
        )
        return jsonify({"success": result.success, "result": result.result})

    @app.route("/api/review/reviewable")
    def api_reviewable():
        orch = get_orchestrator()
        return jsonify(orch._tools.get_reviewable_results())

    @app.route("/api/review/revoke/<int:result_id>", methods=["POST"])
    def api_revoke(result_id):
        orch = get_orchestrator()
        orch._tools.revoke_reviewable(result_id)
        return jsonify({"status": "revoked"})

    @app.route("/api/review/conflict/resolve", methods=["POST"])
    def api_resolve_conflict():
        """解决冲突"""
        orch = get_orchestrator()
        data = request.json
        conflict_data = data.get("conflict", {})
        resolution = data.get("resolution", "manual")

        resolved = False
        for conflict in orch.world_engine._conflicts:
            if (conflict.conflict_type == conflict_data.get("conflict_type") and
                conflict.chapter_id == conflict_data.get("chapter_id") and
                conflict.description == conflict_data.get("description")):
                conflict.resolution = resolution
                resolved = True
                break

        if resolved:
            orch.world_engine.persist()
            return jsonify({"status": "resolved", "resolution": resolution})

        return jsonify({"status": "not_found"})

    @app.route("/api/review/results")
    def api_review_results():
        """获取审核结果列表"""
        orch = get_orchestrator()
        review_dir = os.path.join(orch._fm.get_output_dir(), "tracking")
        results = []

        latest_path = os.path.join(review_dir, "review_latest.json")
        if os.path.exists(latest_path):
            try:
                with open(latest_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                chapter_details = data.get("chapter_details", {})
                for ch_id, detail in chapter_details.items():
                    results.append({
                        "chapter_id": int(ch_id) if str(ch_id).isdigit() else ch_id,
                        "passed": data.get("passed", False),
                        "quality_score": detail.get("quality_score", 0),
                        "content_length": detail.get("content_length", 0),
                        "issues": detail.get("issues", []),
                        "rejections": data.get("rejections", []),
                    })
            except Exception:
                pass

        return jsonify(results)

    # ---- 设定文档 API（小说创建起始输入）----
    @app.route("/api/context/spec")
    def api_get_spec():
        """读取世界观设定"""
        orch = get_orchestrator()
        try:
            content = orch._fm.read_specification()
            return jsonify({"content": content, "exists": True})
        except FileNotFoundError:
            return jsonify({"content": "", "exists": False})

    @app.route("/api/context/spec/fields")
    def api_get_spec_fields():
        """读取结构化设定字段（逐条，含风格和大纲）"""
        from src.core.spec_fields import parse_spec_to_fields, fields_to_dict
        orch = get_orchestrator()
        try:
            content = orch._fm.read_specification()
        except FileNotFoundError:
            content = ""
        try:
            style_content = orch._fm.read_style_guide()
        except FileNotFoundError:
            style_content = ""
        # 读取大纲
        outline_text = ""
        outline_path = orch._fm.outline_path()
        if orch._fm.exists(outline_path):
            try:
                import json
                outline_data = orch._fm.read_json(outline_path)
                outline_text = json.dumps(outline_data, ensure_ascii=False, indent=2)
            except Exception:
                pass
        fields = parse_spec_to_fields(content, style_content, outline_text)
        return jsonify(fields_to_dict(fields))

    @app.route("/api/context/spec/fields", methods=["POST"])
    def api_save_spec_fields():
        """保存结构化设定字段（逐条）"""
        from src.core.spec_fields import SpecField, fields_to_spec, FIELD_MAP
        orch = get_orchestrator()
        data = request.json  # {"fields": [{"key": "...", "value": "..."}, ...]}

        field_updates = data.get("fields", [])
        fields = []
        for fu in field_updates:
            key = fu.get("key", "")
            value = fu.get("value", "")
            if key in FIELD_MAP:
                f = SpecField(
                    key=FIELD_MAP[key].key,
                    label=FIELD_MAP[key].label,
                    value=value,
                    placeholder=FIELD_MAP[key].placeholder,
                    auto_fillable=FIELD_MAP[key].auto_fillable,
                    multiline=FIELD_MAP[key].multiline,
                    order=FIELD_MAP[key].order,
                )
                fields.append(f)

        if fields:
            spec_text, style_text, outline_text = fields_to_spec(fields)
            orch._fm.write_markdown(
                os.path.join(orch._fm.context_dir, "specification.md"),
                spec_text
            )
            if style_text:
                orch._fm.write_markdown(
                    os.path.join(orch._fm.context_dir, "style-guide.md"),
                    style_text
                )
            _broadcast_state({"spec_fields": {f.key: f.value for f in fields}})
            return jsonify({"status": "saved", "field_count": len(fields)})
        return jsonify({"status": "no_fields"})

    @app.route("/api/context/spec/field/<field_key>", methods=["POST"])
    def api_save_single_field(field_key):
        """保存单条设定字段"""
        from src.core.spec_fields import parse_spec_to_fields, update_field, fields_to_spec, FIELD_MAP
        orch = get_orchestrator()
        data = request.json
        value = data.get("value", "")

        if field_key not in FIELD_MAP:
            return jsonify({"status": "error", "message": f"Unknown field: {field_key}"}), 400

        # 读取当前设定
        try:
            content = orch._fm.read_specification()
        except FileNotFoundError:
            content = ""
        try:
            style_content = orch._fm.read_style_guide()
        except FileNotFoundError:
            style_content = ""

        fields = parse_spec_to_fields(content, style_content)
        fields = update_field(fields, field_key, value)
        spec_text, style_text, outline_text = fields_to_spec(fields)

        orch._fm.write_markdown(
            os.path.join(orch._fm.context_dir, "specification.md"),
            spec_text
        )
        if style_text:
            orch._fm.write_markdown(
                os.path.join(orch._fm.context_dir, "style-guide.md"),
                style_text
            )
        return jsonify({"status": "saved", "key": field_key})

    # ---- 角色批量导入 API ----
    @app.route("/api/characters/import", methods=["POST"])
    def api_import_characters():
        """批量导入角色（名称+性别），返回解析后的角色列表"""
        from src.core.character_import import parse_character_list, character_to_dict, GENDER_ROLE_TEMPLATES
        data = request.json
        raw_text = data.get("text", "")
        genre = data.get("genre", "仙侠")

        characters = parse_character_list(raw_text)
        result = [character_to_dict(c) for c in characters]

        return jsonify({
            "characters": result,
            "count": len(result),
            "genre_templates": list(GENDER_ROLE_TEMPLATES.get(genre, {}).keys()),
        })

    @app.route("/api/characters/prefill", methods=["POST"])
    def api_prefill_characters():
        """根据题材模板预填角色身份（无需AI）"""
        from src.core.character_import import CharacterFiller, character_to_dict, characters_to_markdown
        from src.core.spec_fields import parse_spec_to_fields, update_field, fields_to_spec
        data = request.json
        characters = data.get("characters", [])
        genre = data.get("genre", "仙侠")

        filler = CharacterFiller(None, None)
        filled = filler.prefill_from_template(characters, genre)

        return jsonify({
            "characters": [character_to_dict(c) for c in filled],
            "markdown": characters_to_markdown(filled),
        })

    @app.route("/api/characters/fill_single", methods=["POST"])
    def api_fill_single_character():
        """AI补全单个角色

        接收参数：
          - name, gender, genre: 基本信息
          - spec_context: 文本版上下文（兼容旧接口）
          - spec_map: 结构化字段字典 {key: value}（推荐），后端会按 SPEC_FIELD_ORDER 排序
                      并自动排除 characters 字段，让 AI 看到所有关联设定
        """
        from src.core.character_import import CharacterFiller, character_to_dict, characters_to_markdown
        orch = get_orchestrator()
        data = request.json
        name = data.get("name", "")
        gender = data.get("gender", "")
        genre = data.get("genre", "仙侠")
        spec_context = data.get("spec_context", "")
        spec_map = data.get("spec_map")

        # 优先使用 spec_map 构造结构化上下文（自动排除 characters 字段）
        if isinstance(spec_map, dict) and spec_map:
            spec_context = _build_structured_spec_context(spec_map, exclude_key="characters")

        filler = CharacterFiller(orch._client, orch._config)
        result = filler.fill_single(name, gender, genre, spec_context)

        if result:
            return jsonify({
                "status": "filled",
                "character": character_to_dict(result),
                "markdown": characters_to_markdown([result]),
            })
        return jsonify({"status": "failed"})

    @app.route("/api/characters/fill_batch", methods=["POST"])
    def api_fill_batch_characters():
        """AI并发补全多个角色

        接收参数：
          - characters, genre: 基本信息
          - spec_context: 文本版上下文（兼容旧接口）
          - spec_map: 结构化字段字典 {key: value}（推荐），后端会按 SPEC_FIELD_ORDER 排序
                      并自动排除 characters 字段，让每个角色都看到所有关联设定
        """
        from src.core.character_import import CharacterFiller, character_to_dict, characters_to_markdown
        orch = get_orchestrator()
        data = request.json
        characters = data.get("characters", [])
        genre = data.get("genre", "仙侠")
        spec_context = data.get("spec_context", "")
        spec_map = data.get("spec_map")

        # 优先使用 spec_map 构造结构化上下文（自动排除 characters 字段）
        if isinstance(spec_map, dict) and spec_map:
            spec_context = _build_structured_spec_context(spec_map, exclude_key="characters")

        filler = CharacterFiller(orch._client, orch._config)
        filled = filler.fill_batch_concurrent(characters, genre, spec_context)

        return jsonify({
            "status": "filled",
            "characters": [character_to_dict(c) for c in filled],
            "markdown": characters_to_markdown(filled),
        })

    @app.route("/api/characters/generate", methods=["POST"])
    def api_generate_characters():
        """AI 自主生成角色（无需用户预先提供角色名）

        完全基于【已有设定】（世界观/体系/势力/冲突/主线/故事背景等），
        自主生成 count 个角色，覆盖主角/重要配角/反派/导师等不同定位。

        如果 spec_map 的 storyline 等字段中已存在人名（如"宋霄"），
        这些已存在的人名会被自动抽取出来作为 seed_characters，
        AI 必须保留这些人名作为主要角色并填充完整字段，
        不得改名或遗漏。

        接收参数：
          - genre: 题材（默认"仙侠"）
          - count: 生成数量（默认 4）
          - spec_context: 文本版上下文（兼容旧接口）
          - spec_map: 结构化字段字典 {key: value}（推荐），后端会按 SPEC_FIELD_ORDER 排序
                      并自动排除 characters 字段
        """
        from src.core.character_import import (
            CharacterFiller, character_to_dict, characters_to_markdown,
            extract_character_names,
        )
        orch = get_orchestrator()
        data = request.json or {}
        genre = data.get("genre", "仙侠")
        count = int(data.get("count", 4))
        spec_context = data.get("spec_context", "")
        spec_map = data.get("spec_map")

        # 优先使用 spec_map 构造结构化上下文（自动排除 characters 字段）
        if isinstance(spec_map, dict) and spec_map:
            spec_context = _build_structured_spec_context(spec_map, exclude_key="characters")

        # 从 spec_map 的 storyline/background 等字段中自动抽取已存在的人名
        seed_characters: List[Dict] = []
        if isinstance(spec_map, dict):
            # 合并可能含人名的字段文本
            text_to_scan = " ".join(
                str(spec_map.get(k, ""))
                for k in ("storyline", "background", "world_law", "core_conflict", "characters")
                if spec_map.get(k)
            )
            seed_characters = extract_character_names(text_to_scan, max_count=3)

        filler = CharacterFiller(orch._client, orch._config)
        chars = filler.generate_from_spec(
            genre, spec_context, count=count, seed_characters=seed_characters
        )

        return jsonify({
            "status": "generated" if chars else "failed",
            "characters": [character_to_dict(c) for c in chars],
            "markdown": characters_to_markdown(chars),
            "count": len(chars),
            "seed_names": [c.get("name") for c in seed_characters],
        })

    @app.route("/api/characters/save", methods=["POST"])
    def api_save_characters():
        """将角色列表保存到 specification.md 的主要人物节"""
        from src.core.character_import import characters_to_markdown
        from src.core.spec_fields import parse_spec_to_fields, update_field, fields_to_spec
        orch = get_orchestrator()
        data = request.json
        characters = data.get("characters", [])

        md_text = characters_to_markdown(characters)

        # 读取当前设定，更新 characters 字段
        try:
            content = orch._fm.read_specification()
        except FileNotFoundError:
            content = ""
        try:
            style_content = orch._fm.read_style_guide()
        except FileNotFoundError:
            style_content = ""

        fields = parse_spec_to_fields(content, style_content)
        fields = update_field(fields, "characters", md_text)
        spec_text, style_text, _ = fields_to_spec(fields)

        orch._fm.write_markdown(
            os.path.join(orch._fm.context_dir, "specification.md"),
            spec_text
        )
        return jsonify({"status": "saved", "character_count": len(characters)})

    @app.route("/api/context/spec", methods=["POST"])
    def api_save_spec():
        """保存世界观设定"""
        orch = get_orchestrator()
        data = request.json
        content = data.get("content", "")
        orch._fm.write_markdown(
            os.path.join(orch._fm.context_dir, "specification.md"),
            content
        )
        return jsonify({"status": "saved", "length": len(content)})

    # ---- SKILL.md 管理 API（写作风格技能文件）----
    @app.route("/api/skills")
    def api_list_skills():
        """列出所有已加载的 SKILL 文件"""
        from src.core.skill_manager import SkillManager
        orch = get_orchestrator()
        sm = SkillManager(orch._fm.context_dir)
        return jsonify({
            "skills": sm.list_skills(),
            "active": sm.get_active_skill_names(),
            "dir": sm.skills_dir,
        })

    @app.route("/api/skills/<name>", methods=["GET"])
    def api_get_skill(name):
        """获取单个 SKILL 文件内容"""
        from src.core.skill_manager import SkillManager
        orch = get_orchestrator()
        sm = SkillManager(orch._fm.context_dir)
        content = sm.read_skill(name)
        if content is None:
            return jsonify({"error": f"SKILL {name} 不存在"}), 404
        return jsonify({"name": name, "content": content})

    @app.route("/api/skills", methods=["POST"])
    def api_save_skill():
        """保存或新建 SKILL 文件"""
        from src.core.skill_manager import SkillManager
        orch = get_orchestrator()
        data = request.json
        name = (data.get("name") or "").strip()
        content = data.get("content", "")
        if not name:
            return jsonify({"error": "name 不能为空"}), 400
        if not name.lower().endswith(".md"):
            name = name + ".md"
        sm = SkillManager(orch._fm.context_dir)
        ok = sm.save_skill(name, content)
        if not ok:
            return jsonify({"error": "保存失败"}), 500
        return jsonify({"status": "saved", "name": name})

    @app.route("/api/skills/<name>", methods=["DELETE"])
    def api_delete_skill(name):
        """删除 SKILL 文件"""
        from src.core.skill_manager import SkillManager
        orch = get_orchestrator()
        if not name.lower().endswith(".md"):
            name = name + ".md"
        sm = SkillManager(orch._fm.context_dir)
        ok = sm.delete_skill(name)
        if not ok:
            return jsonify({"error": "删除失败"}), 404
        return jsonify({"status": "deleted", "name": name})

    @app.route("/api/skills/active", methods=["POST"])
    def api_set_active_skills():
        """设置激活的 SKILL 列表（仅激活的会被管线引用）"""
        from src.core.skill_manager import SkillManager
        orch = get_orchestrator()
        data = request.json
        names = data.get("names", [])
        sm = SkillManager(orch._fm.context_dir)
        sm.set_active(names)
        return jsonify({"status": "ok", "active": sm.get_active_skill_names()})

    @app.route("/api/skills/import", methods=["POST"])
    def api_import_skill_pack():
        """从外部目录导入一个技能包

        Body: {"source_dir": "e:/xxx/skill-pack",
               "overwrite": false,
               "activate":   true}
        """
        from src.core.skill_manager import SkillManager
        data = request.json or {}
        source_dir = (data.get("source_dir") or "").strip()
        if not source_dir:
            return jsonify({"error": "请提供 source_dir 路径"}), 400
        # 路径规范化
        source_dir = os.path.normpath(source_dir)
        if not os.path.isdir(source_dir):
            return jsonify({"error": f"目录不存在: {source_dir}"}), 400

        # 安全检查：仅允许项目根目录下
        project_root = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        if not _is_path_within(source_dir, project_root):
            return jsonify({
                "error": f"源目录必须在项目根目录之下: {project_root}"
            }), 400

        overwrite = bool(data.get("overwrite", False))
        activate = bool(data.get("activate", True))
        orch = get_orchestrator()
        sm = SkillManager(orch._fm.context_dir)
        result = sm.import_pack(source_dir, overwrite=overwrite, activate=activate)
        return jsonify({"status": "ok", **result})

    @app.route("/api/context/style")
    def api_get_style():
        """读取写作风格约束"""
        orch = get_orchestrator()
        try:
            content = orch._fm.read_style_guide()
            return jsonify({"content": content, "exists": bool(content)})
        except FileNotFoundError:
            return jsonify({"content": "", "exists": False})

    @app.route("/api/context/style", methods=["POST"])
    def api_save_style():
        """保存写作风格约束"""
        orch = get_orchestrator()
        data = request.json
        content = data.get("content", "")
        orch._fm.write_markdown(
            os.path.join(orch._fm.context_dir, "style-guide.md"),
            content
        )
        _broadcast_state({"style_guide": content})
        return jsonify({"status": "saved", "length": len(content)})

    @app.route("/api/context/expand", methods=["POST"])
    def api_expand_spec():
        """题材扩展 - 根据题材自动补全世界观设定"""
        from src.core.genre_expander import GenreExpander
        data = request.json
        spec = data.get("content", "")
        genre_override = data.get("genre", "")
        expander = GenreExpander(token_budget=3000)
        expanded, genre, report = expander.expand(spec, genre_override)
        return jsonify({
            "expanded_content": expanded,
            "detected_genre": genre,
            "confidence": report["confidence"],
            "expanded_sections": report["expanded_sections"],
            "preserved_sections": report["preserved_sections"],
            "token_estimate": report["token_estimate"],
        })

    @app.route("/api/context/auto_fill/<field_key>", methods=["POST"])
    def api_auto_fill_field(field_key):
        """逐条AI自动补全 - 对单个设定字段调用AI生成内容"""
        from src.core.spec_fields import FIELD_MAP, KEY_TO_SECTION
        from src.core.genre_expander import GenreExpander, GENRE_TEMPLATES
        from src.core.concurrent_spec_filler import (
            _build_character_prompt, _build_storyline_prompt, _build_style_prompt,
            _parse_characters, _parse_storyline, _parse_style,
            _format_characters_md, _format_storyline_md, _format_style_md,
        )

        if field_key not in FIELD_MAP:
            return jsonify({"status": "error", "message": f"Unknown field: {field_key}"}), 400

        orch = get_orchestrator()
        data = request.json
        genre = data.get("genre", "仙侠")
        # 兼容两种入参：spec_context（已格式化的明文）或 spec_map（结构化字段）
        spec_context = data.get("spec_context", "")
        spec_map = data.get("spec_map")  # {key: value, ...}

        # 如果提供了 spec_map，重新构建为结构化上下文，并自动排除当前字段
        if isinstance(spec_map, dict) and spec_map:
            spec_context = _build_structured_spec_context(spec_map, exclude_key=field_key)

        field_label = KEY_TO_SECTION.get(field_key, field_key)
        template = GENRE_TEMPLATES.get(genre, GENRE_TEMPLATES["仙侠"])

        # 模板补全映射
        template_map = {
            "world_law": template.get("world_law", ""),
            "cultivation_system": template.get("cultivation_system", ""),
            "faction_pattern": template.get("faction_pattern", ""),
            "economy_system": template.get("economy_system", ""),
            "conflict_types": template.get("conflict_types", ""),
            "power_ceiling": template.get("power_ceiling", ""),
        }

        # 对于有模板的字段，直接返回模板内容（无需AI调用）
        if field_key in template_map and template_map[field_key]:
            return jsonify({
                "status": "filled",
                "key": field_key,
                "value": template_map[field_key],
                "source": "template",
            })

        # 上下文长度策略：长字段（背景/主线）允许 5000 字符；其他允许 4000
        max_ctx = 5000 if field_key in ("characters", "storyline", "background") else 4000
        ctx = (spec_context or "").strip()[:max_ctx]
        if not ctx:
            ctx = f"（暂无其他设定，请基于题材「{genre}」合理生成本字段内容。）"

        # 对于需要AI的字段（characters, storyline, style），构造prompt并调用
        if field_key == "characters":
            prompt = _build_character_prompt(ctx, genre)
        elif field_key == "storyline":
            prompt = _build_storyline_prompt(ctx, genre)
        else:
            # 通用补全prompt（背景、体系、势力、经济、冲突、力量上限、世界观、风格等）
            # 强调字段互依：先描述其他设定，再描述本字段的目标
            prompt = (
                f"User: 基于下方【已有设定】，生成「{field_label}」字段的详细文本。\n"
                f"题材类型: {genre}\n"
                f"【已有设定 - 必须与这些内容保持一致/承接】:\n{ctx}\n"
                f"要求：\n"
                f"1. 与上述世界观、体系、势力、冲突、人物自洽，不要凭空引入新的大陆/新境界/新势力而与设定冲突。\n"
                f"2. 适当**引用**上述设定中的关键名词（势力名、境界名、人物、地点）以体现连贯性。\n"
                f"3. 输出 200-500 字的中文叙述性文本，不要 JSON 格式。\n"
                "\nAssistant: "
            )

        # 调用模型
        from src.core.config import SamplingParams
        sampling = SamplingParams(temperature=1.0, top_p=0.1, max_tokens=2048)
        try:
            results = orch._client.big_batch_completions(
                contents=[prompt],
                sampling=sampling,
                stream=False,
            )
            result = results[0] if results else ""

            # 解析结果
            if field_key == "characters":
                chars, _ = _parse_characters(result)
                if chars:
                    value = _format_characters_md(chars)
                else:
                    value = result
            elif field_key == "storyline":
                story, _ = _parse_storyline(result)
                if story:
                    value = _format_storyline_md(story)
                else:
                    value = result
            else:
                value = result

            return jsonify({
                "status": "filled",
                "key": field_key,
                "value": value,
                "source": "ai",
            })
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)})

    @app.route("/api/context/concurrent_fill", methods=["POST"])
    def api_concurrent_fill():
        """并发补全 - 利用 /big_batch/completions 同时生成人物/主线/风格

        接收两种入参方式：
        1. content (str) + genre (str)：使用全文作为上下文（兼容旧接口）
        2. spec_map (dict) {key: value, ...}：使用结构化字段构建上下文
           此时会自动为三个子任务（characters / storyline / style）分别构建
           "排除自身" 的结构化上下文，让 AI 看到所有相关联的已有设定。
        """
        from src.core.genre_expander import GenreExpander
        from src.core.concurrent_spec_filler import ConcurrentSpecFiller
        data = request.json or {}
        spec = data.get("content", "")
        genre_override = data.get("genre", "")
        spec_map = data.get("spec_map")  # 可选：{key: value, ...}

        # 先做题材扩展
        expander = GenreExpander(token_budget=3000)
        expanded_spec, genre, expand_report = expander.expand(spec, genre_override)

        # 并发补全
        orch = get_orchestrator()
        filler = ConcurrentSpecFiller(
            orch._client, orch._config,
            orch._logger if hasattr(orch, '_logger') else None
        )

        # 如果提供了 spec_map，针对三个子任务分别构造"排除自身字段"的结构化上下文
        if isinstance(spec_map, dict) and spec_map:
            chars_ctx = _build_structured_spec_context(spec_map, exclude_key="characters")
            story_ctx = _build_structured_spec_context(spec_map, exclude_key="storyline")
            style_ctx = _build_structured_spec_context(spec_map, exclude_key="style")
            # 替换 filler 中的 prompt 构造，使用我们的结构化上下文
            from src.core import concurrent_spec_filler as _csf
            prompts = [
                _csf._build_character_prompt(chars_ctx[:5000], genre),
                _csf._build_storyline_prompt(story_ctx[:5000], genre),
                _csf._build_style_prompt(genre, style_ctx[:5000]),
            ]
            from src.core.config import SamplingParams
            import time as _t
            sampling = SamplingParams(temperature=1.0, top_p=0.1, max_tokens=2048)
            start = _t.time()
            results = orch._client.big_batch_completions(
                contents=prompts, sampling=sampling, stream=False,
            )
            elapsed = (_t.time() - start) * 1000
            if isinstance(results, str):
                results = [results]
            elif not isinstance(results, list):
                results = [str(results)]

            output = {
                "characters": None, "characters_md": "",
                "storyline": None, "storyline_md": "",
                "style": None, "style_md": "",
                "elapsed_ms": elapsed,
                "filled_items": [],
            }
            task_names = ["characters", "storyline", "style"]
            for i, name in enumerate(task_names):
                txt = results[i] if i < len(results) else ""
                if name == "characters":
                    chars, _ = _csf._parse_characters(txt)
                    if chars:
                        output["characters"] = chars
                        output["characters_md"] = _csf._format_characters_md(chars)
                        output["filled_items"].append("characters")
                elif name == "storyline":
                    story, _ = _csf._parse_storyline(txt)
                    if story:
                        output["storyline"] = story
                        output["storyline_md"] = _csf._format_storyline_md(story)
                        output["filled_items"].append("storyline")
                elif name == "style":
                    sty, _ = _csf._parse_style(txt)
                    if sty:
                        output["style"] = sty
                        output["style_md"] = _csf._format_style_md(sty)
                        output["filled_items"].append("style")

            fill_result = output
        else:
            fill_result = filler.fill(expanded_spec, genre)

        # 合并到设定文档
        merged_spec = filler.merge_to_spec(expanded_spec, fill_result)

        return jsonify({
            "merged_spec": merged_spec,
            "detected_genre": genre,
            "expanded_sections": expand_report["expanded_sections"],
            "filled_items": fill_result["filled_items"],
            "characters": fill_result.get("characters"),
            "characters_md": fill_result.get("characters_md", ""),
            "storyline": fill_result.get("storyline"),
            "storyline_md": fill_result.get("storyline_md", ""),
            "style": fill_result.get("style"),
            "style_md": fill_result.get("style_md", ""),
            "elapsed_ms": fill_result.get("elapsed_ms", 0),
        })

    # ---- 设定结构化上下文构造工具 ----
    # 字段显示顺序（与"创作设定"标签页一致）
    _SPEC_FIELD_ORDER = [
        ("genre", "【题材】"),
        ("world_law", "【世界观】"),
        ("background", "【故事背景】"),
        ("cultivation_system", "【修行/能力体系】"),
        ("faction_pattern", "【势力格局】"),
        ("economy_system", "【经济体系】"),
        ("conflict_types", "【核心冲突类型】"),
        ("power_ceiling", "【力量上限】"),
        ("characters", "【主要人物】"),
        ("storyline", "【故事主线】"),
        ("style", "【写作风格】"),
        ("outline", "【全书大纲】"),
    ]
    # 占位符过滤：避免把模板占位文本当作有效设定传给 AI
    _PLACEHOLDER_PATTERNS = (
        r"（请在此处填写[^）]*）",
        r"（[^）]*填写[^）]*）",
        r"\[请填写[^\]]*\]",
        r"^待填写$", r"^TODO$", r"^TBD$",
    )

    def _build_structured_spec_context(spec_map: dict, exclude_key: str = None) -> str:
        """将 spec_map 字段字典序列化为【已有设定】上下文

        - 自动按 SPEC_FIELD_ORDER 排序
        - 自动排除 exclude_key（避免把正在补全的字段自己的内容喂给 AI）
        - 过滤占位符/空内容
        - 每段以 【xxx】 开头分隔，方便 AI 引用
        """
        import re as _re
        patterns = [_re.compile(p) for p in _PLACEHOLDER_PATTERNS]
        lines = []
        for key, label in _SPEC_FIELD_ORDER:
            if exclude_key and key == exclude_key:
                continue
            val = (spec_map.get(key) or "").strip()
            if not val:
                continue
            # 过滤纯占位文本
            if any(p.search(val) for p in patterns):
                continue
            # 截断单字段过长内容
            if len(val) > 1500:
                val = val[:1500] + "..."
            lines.append(f"{label}\n{val}")
        return "\n\n".join(lines)

    @app.route("/api/context/genres")
    def api_list_genres():
        """列出所有支持的题材类型"""
        from src.core.genre_expander import GENRE_TEMPLATES
        return jsonify(list(GENRE_TEMPLATES.keys()))

    # ---- 输出内容 API ----
    @app.route("/api/output/outline")
    def api_get_outline():
        """读取已生成的大纲"""
        orch = get_orchestrator()
        path = orch._fm.outline_path()
        if orch._fm.exists(path):
            return jsonify(orch._fm.read_json(path))
        return jsonify({"exists": False})

    def _read_json_with_cache(output_dir: str, name: str):
        """优先读 output/<name>，回退到 output/.cache/<name>"""
        primary = os.path.join(output_dir, name)
        if os.path.isfile(primary):
            with open(primary, "r", encoding="utf-8") as f:
                return json.load(f)
        cache = os.path.join(output_dir, ".cache", name)
        if os.path.isfile(cache):
            with open(cache, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    @app.route("/api/output/main_storyline")
    def api_get_main_storyline():
        """读取优化版管线生成的故事主线（main_storyline.json）"""
        orch = get_orchestrator()
        data = _read_json_with_cache(orch._fm.output_dir, "main_storyline.json")
        if data is None:
            return jsonify({"exists": False})
        return jsonify(data)

    @app.route("/api/output/full_outline")
    def api_get_full_outline():
        """读取优化版管线生成的全书大纲（full_outline.json）"""
        orch = get_orchestrator()
        data = _read_json_with_cache(orch._fm.output_dir, "full_outline.json")
        if data is None:
            return jsonify({"exists": False})
        return jsonify(data)

    @app.route("/api/output/storyline")
    def api_get_storyline():
        """读取传统管线的 storyline.json"""
        orch = get_orchestrator()
        data = _read_json_with_cache(orch._fm.output_dir, "storyline.json")
        if data is None:
            return jsonify({"exists": False})
        return jsonify(data)

    @app.route("/api/output/characters")
    def api_get_characters_json():
        """读取角色信息表（characters.json）"""
        orch = get_orchestrator()
        data = _read_json_with_cache(orch._fm.output_dir, "characters.json")
        if data is None:
            return jsonify({"exists": False})
        return jsonify(data)

    @app.route("/api/output/drafts")
    def api_list_drafts():
        """列出所有初稿"""
        orch = get_orchestrator()
        draft_dir = os.path.join(orch._fm.output_dir, "draft")
        drafts = []
        if os.path.exists(draft_dir):
            for f in sorted(os.listdir(draft_dir)):
                if f.endswith(".md"):
                    drafts.append({"filename": f, "chapter_id": int(f.replace(".md", ""))})
        return jsonify(drafts)

    @app.route("/api/output/draft/<int:chapter_id>")
    def api_get_draft(chapter_id):
        """读取指定章节初稿"""
        orch = get_orchestrator()
        path = orch._fm.draft_path(chapter_id)
        if orch._fm.exists(path):
            return jsonify({"content": orch._fm.read_markdown(path), "exists": True})
        return jsonify({"exists": False})

    @app.route("/api/novel/dirs")
    def api_list_novel_dirs():
        """列出所有草稿小说目录"""
        orch = get_orchestrator()
        output_dir = orch._fm.get_output_dir()
        novel_dirs = []
        if os.path.isdir(output_dir):
            for name in os.listdir(output_dir):
                if name.startswith("草稿_"):
                    novel_path = os.path.join(output_dir, name)
                    if os.path.isdir(novel_path):
                        novel_dirs.append({"name": name, "path": novel_path})
        return jsonify({"novels": novel_dirs})

    @app.route("/api/novel/tree")
    def api_novel_tree():
        """获取小说目录树结构"""
        novel_dir = request.args.get("dir", "")
        if not novel_dir or not os.path.isdir(novel_dir):
            return jsonify({"error": "目录不存在"}), 404

        def _build_tree(path, depth=0):
            items = []
            try:
                for entry in sorted(os.listdir(path)):
                    full = os.path.join(path, entry)
                    if os.path.isdir(full):
                        children = _build_tree(full, depth + 1) if depth < 4 else []
                        items.append({
                            "name": entry,
                            "type": "dir",
                            "path": full,
                            "children": children,
                        })
                    else:
                        items.append({
                            "name": entry,
                            "type": "file",
                            "path": full,
                        })
            except PermissionError:
                pass
            return items

        tree = _build_tree(novel_dir)
        return jsonify({"tree": tree})

    @app.route("/api/novel/file")
    def api_read_novel_file():
        """读取小说目录中的文件内容"""
        file_path = request.args.get("path", "")
        if not file_path or not os.path.isfile(file_path):
            return jsonify({"error": "文件不存在"}), 404

        output_dir = orch._fm.get_output_dir() if (orch := get_orchestrator()) else ""
        if not _is_path_within(file_path, output_dir):
            return jsonify({"error": "路径不合法"}), 403

        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return jsonify({"content": content, "path": file_path})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/novel/file/save", methods=["POST"])
    def api_save_novel_file():
        """保存文件修改"""
        data = request.get_json() or {}
        file_path = data.get("path", "")
        content = data.get("content", "")

        if not file_path:
            return jsonify({"error": "缺少文件路径"}), 400

        orch = get_orchestrator()
        output_dir = orch._fm.get_output_dir()
        if not _is_path_within(file_path, output_dir):
            return jsonify({"error": "路径不合法"}), 403

        if not os.path.isfile(file_path):
            return jsonify({"error": "文件不存在"}), 404

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return jsonify({"success": True, "path": file_path})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/novel/file/delete", methods=["POST"])
    def api_delete_novel_file():
        """删除文件或目录"""
        data = request.get_json() or {}
        target_path = data.get("path", "")

        if not target_path:
            return jsonify({"error": "缺少路径"}), 400

        orch = get_orchestrator()
        output_dir = orch._fm.get_output_dir()
        if not _is_path_within(target_path, output_dir):
            return jsonify({"error": "路径不合法"}), 403

        try:
            if os.path.isfile(target_path):
                os.remove(target_path)
                return jsonify({"success": True, "deleted": "file"})
            elif os.path.isdir(target_path):
                import shutil
                shutil.rmtree(target_path)
                return jsonify({"success": True, "deleted": "dir"})
            else:
                return jsonify({"error": "目标不存在"}), 404
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/novel/file/create", methods=["POST"])
    def api_create_novel_file():
        data = request.get_json() or {}
        file_path = data.get("path", "")
        content = data.get("content", "")

        if not file_path:
            return jsonify({"error": "缺少文件路径"}), 400

        orch = get_orchestrator()
        output_dir = orch._fm.get_output_dir()
        if not _is_path_within(file_path, output_dir):
            return jsonify({"error": "路径不合法"}), 403

        if os.path.exists(file_path):
            return jsonify({"error": "文件已存在"}), 409

        try:
            parent = os.path.dirname(file_path)
            os.makedirs(parent, exist_ok=True)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            return jsonify({"success": True, "path": file_path})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/novel/dir/create", methods=["POST"])
    def api_create_novel_dir():
        data = request.get_json() or {}
        dir_path = data.get("path", "")

        if not dir_path:
            return jsonify({"error": "缺少目录路径"}), 400

        orch = get_orchestrator()
        output_dir = orch._fm.get_output_dir()
        if not _is_path_within(dir_path, output_dir):
            return jsonify({"error": "路径不合法"}), 403

        if os.path.exists(dir_path):
            return jsonify({"error": "目录已存在"}), 409

        try:
            os.makedirs(dir_path, exist_ok=True)
            return jsonify({"success": True, "path": dir_path})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/novel/rename", methods=["POST"])
    def api_rename_novel_item():
        data = request.get_json() or {}
        old_path = data.get("old_path", "")
        new_name = data.get("new_name", "")

        if not old_path or not new_name:
            return jsonify({"error": "缺少路径或新名称"}), 400

        if any(c in new_name for c in r'\/:*?"<>|'):
            return jsonify({"error": "名称包含非法字符"}), 400

        orch = get_orchestrator()
        output_dir = orch._fm.get_output_dir()
        if not _is_path_within(old_path, output_dir):
            return jsonify({"error": "路径不合法"}), 403

        parent = os.path.dirname(old_path)
        new_path = os.path.join(parent, new_name)

        if os.path.exists(new_path):
            return jsonify({"error": "目标名称已存在"}), 409

        try:
            os.rename(old_path, new_path)
            return jsonify({"success": True, "old_path": old_path, "new_path": new_path})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/polish", methods=["POST"])
    def api_polish():
        """润色章节文本 - 逐句给出润色条件让AI润色

        请求体:
        {
            "draft_path": "草稿_{小说名}/{分卷}/{章节}/初步草稿/草稿.md",
            "polish_instructions": "润色要求",
            "chapter_title": "章节标题"
        }
        """
        data = request.get_json() or {}
        draft_path = data.get("draft_path", "")
        polish_instructions = data.get("polish_instructions", "")
        chapter_title = data.get("chapter_title", "")

        if not draft_path or not polish_instructions:
            return jsonify({"error": "缺少草稿路径或润色要求"}), 400

        if not os.path.isfile(draft_path):
            return jsonify({"error": "草稿文件不存在"}), 404

        orch = get_orchestrator()
        output_dir = orch._fm.get_output_dir()
        if not _is_path_within(draft_path, output_dir):
            return jsonify({"error": "路径不合法"}), 403

        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                original_text = f.read()
        except Exception as e:
            return jsonify({"error": f"读取草稿失败: {e}"}), 500

        if not original_text.strip():
            return jsonify({"error": "草稿内容为空"}), 400

        from src.core.prompt_builder import PromptBuilder
        from src.core.rwkv_client import RWKVClient, SamplingParams
        from src.core.logger import Logger

        logger = Logger.get(os.path.join(output_dir, "logs"))
        client = RWKVClient(orch._config.api, logger)

        style_guide = orch._fm.read_style_guide() if orch._fm else ""

        prompt = PromptBuilder.build_polish_prompt(
            original_text=original_text,
            polish_instructions=polish_instructions,
            chapter_title=chapter_title,
            style_guide=style_guide,
        )

        sampling = SamplingParams(temperature=0.7, top_p=0.85, max_tokens=4096)

        try:
            results = client.big_batch_completions(
                contents=[prompt],
                sampling=sampling,
                stream=False,
            )
            polished = results[0] if results else ""
        except Exception as e:
            return jsonify({"error": f"AI润色失败: {e}"}), 500

        if not polished.strip():
            return jsonify({"error": "润色结果为空"}), 500

        final_dir = os.path.join(os.path.dirname(os.path.dirname(draft_path)), "正式文稿")
        os.makedirs(final_dir, exist_ok=True)
        final_path = os.path.join(final_dir, "文稿.md")

        try:
            with open(final_path, 'w', encoding='utf-8') as f:
                f.write(polished)
        except Exception as e:
            return jsonify({"error": f"保存润色结果失败: {e}"}), 500

        return jsonify({
            "success": True,
            "final_path": final_path,
            "original_length": len(original_text),
            "polished_length": len(polished),
        })

    @app.route("/api/polish/preview", methods=["POST"])
    def api_polish_preview():
        """润色预览 - 只返回润色结果不保存

        请求体:
        {
            "text": "待润色文本",
            "polish_instructions": "润色要求",
            "chapter_title": "章节标题"
        }
        """
        data = request.get_json() or {}
        text = data.get("text", "")
        polish_instructions = data.get("polish_instructions", "")
        chapter_title = data.get("chapter_title", "")

        if not text or not polish_instructions:
            return jsonify({"error": "缺少文本或润色要求"}), 400

        from src.core.prompt_builder import PromptBuilder
        from src.core.rwkv_client import RWKVClient, SamplingParams
        from src.core.logger import Logger

        orch = get_orchestrator()
        output_dir = orch._fm.get_output_dir()
        logger = Logger.get(os.path.join(output_dir, "logs"))
        client = RWKVClient(orch._config.api, logger)

        style_guide = orch._fm.read_style_guide() if orch._fm else ""

        prompt = PromptBuilder.build_polish_prompt(
            original_text=text,
            polish_instructions=polish_instructions,
            chapter_title=chapter_title,
            style_guide=style_guide,
        )

        sampling = SamplingParams(temperature=0.7, top_p=0.85, max_tokens=4096)

        try:
            results = client.big_batch_completions(
                contents=[prompt],
                sampling=sampling,
                stream=False,
            )
            polished = results[0] if results else ""
        except Exception as e:
            return jsonify({"error": f"AI润色失败: {e}"}), 500

        return jsonify({
            "polished": polished,
            "original_length": len(text),
            "polished_length": len(polished),
        })

    return app


def run_server(config_path: str = "pipeline.config.json", host: str = "0.0.0.0", port: int = 5000, auto_start_rwkv: bool = True):
    """启动 Web UI 服务器
    
    Args:
        config_path: 配置文件路径
        host: 监听地址
        port: 监听端口
        auto_start_rwkv: 是否自动启动 RWKV 推理服务
    """
    # 自动启动 RWKV 推理服务
    if auto_start_rwkv:
        try:
            from src.core.rwkv_service import ensure_rwkv_service
            import os
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            if not ensure_rwkv_service(project_root):
                print("[WARNING] RWKV 服务未启动，Web UI 仍可访问但管线功能不可用")
        except Exception as e:
            print(f"[WARNING] 无法启动 RWKV 服务: {e}")
            print("[INFO] Web UI 仍可访问，但管线功能需要手动启动 RWKV 服务")
    
    app = create_app(config_path)
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
