"""Web UI - Flask 应用

提供管线管理、世界状态查看、Roleplay 交互、冲突裁决等 Web 界面。
"""

import json
import os
import threading
from typing import Optional

from flask import Flask, render_template, jsonify, request, redirect, url_for

from src.orchestrator import Orchestrator


def create_app(config_path: str = "pipeline.config.json") -> Flask:
    """创建 Flask 应用"""
    app = Flask(__name__,
                template_folder=os.path.join(os.path.dirname(__file__), "templates"),
                static_folder=os.path.join(os.path.dirname(__file__), "static"))

    orchestrator: Optional[Orchestrator] = None
    pipeline_thread: Optional[threading.Thread] = None

    def get_orchestrator() -> Orchestrator:
        nonlocal orchestrator
        if orchestrator is None:
            orchestrator = Orchestrator(config_path)
        return orchestrator

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

    # ---- API 路由 ----
    @app.route("/api/status")
    def api_status():
        orch = get_orchestrator()
        return jsonify(orch.get_status())

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

    # ---- Roleplay API ----
    @app.route("/api/roleplay/dialogue", methods=["POST"])
    def api_roleplay_dialogue():
        orch = get_orchestrator()
        data = request.json
        response = orch.roleplay.dialogue(
            character_id=data["character_id"],
            scene_context=data.get("scene_context", ""),
            user_input=data["user_input"],
            dialogue_history=data.get("dialogue_history", ""),
        )
        return jsonify({"response": response})

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

    @app.route("/api/roleplay/monologue", methods=["POST"])
    def api_roleplay_monologue():
        orch = get_orchestrator()
        data = request.json
        response = orch.roleplay.inner_monologue(
            character_id=data["character_id"],
            situation=data["situation"],
        )
        return jsonify({"monologue": response})

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
        """AI补全单个角色"""
        from src.core.character_import import CharacterFiller, character_to_dict, characters_to_markdown
        orch = get_orchestrator()
        data = request.json
        name = data.get("name", "")
        gender = data.get("gender", "")
        genre = data.get("genre", "仙侠")
        spec_context = data.get("spec_context", "")

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
        """AI并发补全多个角色"""
        from src.core.character_import import CharacterFiller, character_to_dict, characters_to_markdown
        orch = get_orchestrator()
        data = request.json
        characters = data.get("characters", [])
        genre = data.get("genre", "仙侠")
        spec_context = data.get("spec_context", "")

        filler = CharacterFiller(orch._client, orch._config)
        filled = filler.fill_batch_concurrent(characters, genre, spec_context)

        return jsonify({
            "status": "filled",
            "characters": [character_to_dict(c) for c in filled],
            "markdown": characters_to_markdown(filled),
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
        spec_context = data.get("spec_context", "")  # 当前设定上下文

        # 根据字段类型构造不同的 prompt
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

        # 对于需要AI的字段（characters, storyline），构造prompt并调用
        if field_key == "characters":
            prompt = _build_character_prompt(spec_context[:2000], genre)
        elif field_key == "storyline":
            prompt = _build_storyline_prompt(spec_context[:2000], genre)
        else:
            # 通用补全prompt
            prompt = (
                f"Instruction: 基于以下题材和世界观，生成{field_label}的详细设定。\n"
                f"题材: {genre}\n"
                f"已有设定:\n{spec_context[:1500]}\n"
                f"直接输出内容文本，不需要JSON格式。\n"
                f"Response: "
            )

        # 调用模型
        from src.core.config import SamplingParams
        sampling = SamplingParams(temperature=1.0, top_p=0.1, max_tokens=2048)
        try:
            result = orch._client.openai_chat_completions(
                messages=[{"role": "user", "content": prompt}],
                sampling=sampling,
                stream=False,
            )

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
        """并发补全 - 利用 /big_batch/completions 同时生成人物/主线/风格"""
        from src.core.genre_expander import GenreExpander
        from src.core.concurrent_spec_filler import ConcurrentSpecFiller
        data = request.json
        spec = data.get("content", "")
        genre_override = data.get("genre", "")

        # 先做题材扩展
        expander = GenreExpander(token_budget=3000)
        expanded_spec, genre, expand_report = expander.expand(spec, genre_override)

        # 并发补全
        orch = get_orchestrator()
        filler = ConcurrentSpecFiller(
            orch._client, orch._config,
            orch._logger if hasattr(orch, '_logger') else None
        )
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

    return app


def run_server(config_path: str = "pipeline.config.json", host: str = "0.0.0.0", port: int = 5000):
    """启动 Web UI 服务器"""
    app = create_app(config_path)
    app.run(host=host, port=port, debug=False)
