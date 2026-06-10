"""RWKV 超级并发多智能体小说共创框架 - 主入口

用法:
    python main.py --config pipeline.config.json          # 启动管线
    python main.py --config pipeline.config.json --resume  # 断点恢复
    python main.py --config pipeline.config.json --web     # 启动 Web UI
    python main.py --config pipeline.config.json --web --port 5000  # 指定端口
"""

from gevent import monkey
monkey.patch_all()

import argparse
import os
import sys


def main():
    parser = argparse.ArgumentParser(description="RWKV 超级并发多智能体小说共创框架")
    parser.add_argument("--config", default="pipeline.config.json", help="配置文件路径")
    parser.add_argument("--resume", action="store_true", help="从断点恢复管线")
    parser.add_argument("--web", action="store_true", help="启动 Web UI")
    parser.add_argument("--host", default="0.0.0.0", help="Web UI 监听地址")
    parser.add_argument("--port", type=int, default=5000, help="Web UI 端口")
    parser.add_argument("--pipeline-only", action="store_true", help="仅运行管线（不启动Web UI）")
    parser.add_argument("--no-auto-rwkv", action="store_true", help="不自动启动 RWKV 推理服务")
    args = parser.parse_args()

    # 确保工作目录正确
    project_root = os.path.dirname(os.path.abspath(__file__))
    os.chdir(project_root)

    # 检查配置文件
    config_path = os.path.join(project_root, args.config)
    if not os.path.exists(config_path):
        print(f"[ERROR] 配置文件不存在: {config_path}")
        print("请先创建 pipeline.config.json，可参考项目中的模板。")
        sys.exit(1)

    if args.web or not args.pipeline_only:
        # 启动 Web UI（管线在后台线程运行）
        print("=" * 60)
        print(" RWKV 超级并发多智能体小说共创框架")
        print(" Web UI 模式")
        print("=" * 60)
        print(f" 配置: {config_path}")
        print(f" 地址: http://{args.host}:{args.port}")
        print(f" 自动启动RWKV: {'否' if args.no_auto_rwkv else '是'}")
        print("=" * 60)

        from src.web.app import run_server
        run_server(config_path, args.host, args.port, auto_start_rwkv=not args.no_auto_rwkv)

    else:
        # 仅运行管线（CLI 模式）
        print("=" * 60)
        print(" RWKV 超级并发多智能体小说共创框架")
        print(" CLI 模式")
        print("=" * 60)

        from src.orchestrator import Orchestrator
        orchestrator = Orchestrator(config_path)
        orchestrator.run()


if __name__ == "__main__":
    main()
