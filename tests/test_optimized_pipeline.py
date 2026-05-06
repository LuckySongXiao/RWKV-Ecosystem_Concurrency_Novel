"""优化版管线测试脚本"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.optimized_pipeline import OptimizedPipelineOrchestrator


def test_optimized_pipeline():
    """测试优化版管线"""
    print("=" * 80)
    print("优化版管线测试")
    print("=" * 80)

    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                               "pipeline.config.json")

    if not os.path.exists(config_path):
        print(f"[ERROR] 配置文件不存在: {config_path}")
        return

    print("[INFO] 创建优化版管线编排器...")
    orchestrator = OptimizedPipelineOrchestrator(config_path, concurrency_config={
        "character_concurrency": 5,
        "outline_concurrency": 3,
        "chapter_concurrency": 2,
        "batch_size": 4,
    })

    print("[INFO] 运行优化版管线...")
    result = orchestrator.run_pipeline(
        theme="仙侠",
        character_count=5,
        protagonist_names=["林孤云"],
        antagonist_names=["墨渊"],
        volume_count=2,
        chapters_per_volume=3,
        slices_per_chapter=4,
        extra_context="这是一个关于修仙者逆天而行的故事。",
    )

    print("\n" + "=" * 80)
    print("管线执行结果")
    print("=" * 80)
    print(f"状态: {result.get('status', 'unknown')}")
    print(f"主题: {result.get('theme', 'unknown')}")
    print(f"角色数量: {result.get('character_count', 0)}")
    print(f"卷数: {result.get('volumes', 0)}")
    print(f"每卷章节数: {result.get('chapters_per_volume', 0)}")
    print(f"每章切片数: {result.get('slices_per_chapter', 0)}")
    print(f"总章节数: {result.get('total_chapters', 0)}")
    print(f"总耗时: {result.get('total_elapsed_ms', 0) / 1000:.1f}秒")

    print("\n各阶段结果:")
    for stage_name, stage_result in result.get("stages", {}).items():
        status = stage_result.get("status", "unknown")
        elapsed = stage_result.get("elapsed_ms", 0) / 1000
        print(f"  - {stage_name}: {status} ({elapsed:.1f}秒)")

    if result.get("status") == "completed":
        print("\n✅ 优化版管线执行成功！")
    else:
        print(f"\n❌ 管线执行失败: {result.get('error', '未知错误')}")


if __name__ == "__main__":
    test_optimized_pipeline()
