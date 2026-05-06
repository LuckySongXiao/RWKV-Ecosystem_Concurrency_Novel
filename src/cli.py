"""CLI 人类交互接口

提供以下交互功能:
- 冲突裁决: 展示冲突报告，接收人类裁决
- 必确认级审批: 展示分析+选项，接收确认/驳回/修改
- 驳回重写审查: 展示驳回历史，支持人工修改
"""

import json
import sys
from typing import Any, Dict, List, Optional
from datetime import datetime


class CLIInterface:
    """CLI 人类交互接口"""

    def __init__(self):
        self._input_fn = input

    def display_conflict_report(self, conflict: Dict) -> None:
        """展示冲突报告"""
        print("\n" + "=" * 60)
        print("⚠️  世界状态冲突报告")
        print("=" * 60)
        print(f"章节: {conflict.get('chapter_id', '未知')}")
        print(f"冲突类型: {conflict.get('conflict_type', '未知')}")
        print(f"描述: {conflict.get('description', '无')}")
        print()

        if 'entities' in conflict:
            print("涉及实体:")
            for entity in conflict['entities']:
                print(f"  - {entity}")

        if 'options' in conflict:
            print("\n建议解决方案:")
            for i, option in enumerate(conflict['options'], 1):
                print(f"  {i}. {option.get('label', '方案' + str(i))}")
                print(f"     {option.get('description', '')}")

        print("=" * 60)

    def request_conflict_resolution(self, conflict: Dict) -> Dict:
        """请求冲突裁决

        Returns:
            裁决结果 {"action": "resolve|skip|custom", "choice": int, "custom_value": str}
        """
        self.display_conflict_report(conflict)

        while True:
            print("\n请选择处理方式:")
            print("  1. 选择建议方案")
            print("  2. 跳过此冲突（标记为未解决）")
            print("  3. 自定义解决方案")
            print("  4. 查看详细信息")

            choice = self._input_fn("\n请输入选项 (1-4): ").strip()

            if choice == "1":
                if 'options' in conflict and conflict['options']:
                    option_idx = self._input_fn("请选择方案编号: ").strip()
                    try:
                        idx = int(option_idx) - 1
                        if 0 <= idx < len(conflict['options']):
                            return {
                                "action": "resolve",
                                "choice": idx,
                                "timestamp": datetime.now().isoformat(),
                            }
                        else:
                            print("无效的选项编号")
                    except ValueError:
                        print("请输入有效数字")
                else:
                    print("无可用方案")
            elif choice == "2":
                return {
                    "action": "skip",
                    "reason": "用户选择跳过",
                    "timestamp": datetime.now().isoformat(),
                }
            elif choice == "3":
                custom = self._input_fn("请输入自定义解决方案: ").strip()
                if custom:
                    return {
                        "action": "custom",
                        "custom_value": custom,
                        "timestamp": datetime.now().isoformat(),
                    }
                else:
                    print("解决方案不能为空")
            elif choice == "4":
                print(json.dumps(conflict, ensure_ascii=False, indent=2))
            else:
                print("无效输入")

    def display_approval_request(self, request: Dict) -> None:
        """展示必确认级审批请求"""
        print("\n" + "=" * 60)
        print("📋 审批请求 (必确认级)")
        print("=" * 60)
        print(f"工具: {request.get('tool_name', '未知')}")
        print(f"操作: {request.get('operation', '未知')}")
        print()

        if 'analysis' in request:
            print("AI分析:")
            print(f"  {request['analysis']}")

        if 'proposed_changes' in request:
            print("\n提议的变更:")
            for change in request['proposed_changes']:
                print(f"  - {change}")

        if 'options' in request:
            print("\n可选操作:")
            for i, option in enumerate(request['options'], 1):
                print(f"  {i}. {option.get('label', '选项' + str(i))}")
                print(f"     {option.get('description', '')}")

        print("=" * 60)

    def request_approval(self, request: Dict) -> Dict:
        """请求必确认级审批

        Returns:
            审批结果 {"action": "approve|reject|modify", "modification": str}
        """
        self.display_approval_request(request)

        while True:
            print("\n请选择:")
            print("  1. 批准执行")
            print("  2. 驳回")
            print("  3. 修改后执行")
            print("  4. 查看详情")

            choice = self._input_fn("\n请输入选项 (1-4): ").strip()

            if choice == "1":
                return {
                    "action": "approve",
                    "timestamp": datetime.now().isoformat(),
                }
            elif choice == "2":
                reason = self._input_fn("请输入驳回原因 (可选): ").strip()
                return {
                    "action": "reject",
                    "reason": reason or "用户驳回",
                    "timestamp": datetime.now().isoformat(),
                }
            elif choice == "3":
                modification = self._input_fn("请输入修改内容: ").strip()
                if modification:
                    return {
                        "action": "modify",
                        "modification": modification,
                        "timestamp": datetime.now().isoformat(),
                    }
                else:
                    print("修改内容不能为空")
            elif choice == "4":
                print(json.dumps(request, ensure_ascii=False, indent=2))
            else:
                print("无效输入")

    def display_rejection_history(self, chapter_id: int, history: Dict) -> None:
        """展示驳回历史"""
        print("\n" + "=" * 60)
        print(f"📝 章节 {chapter_id} 驳回历史")
        print("=" * 60)
        print(f"驳回次数: {history.get('rejection_count', 0)}/{history.get('max_rejections', 3)}")

        if 'rejection_reasons' in history:
            print("\n驳回原因:")
            for i, reason in enumerate(history['rejection_reasons'], 1):
                print(f"  {i}. {reason}")

        if history.get('is_dead_loop', False):
            print("\n⚠️  检测到死循环！建议人工介入修改。")

        print("=" * 60)

    def request_manual_review(self, chapter_id: int, content: str, history: Dict) -> str:
        """请求人工修改

        Returns:
            修改后的内容
        """
        self.display_rejection_history(chapter_id, history)

        print("\n原始内容:")
        print("-" * 40)
        print(content[:1000])
        if len(content) > 1000:
            print("... (内容已截断)")
        print("-" * 40)

        print("\n请手动修改内容 (输入 END 结束):")
        lines = []
        while True:
            line = self._input_fn()
            if line.strip() == "END":
                break
            lines.append(line)

        return "\n".join(lines)

    def display_progress(self, stage: str, progress: float, details: Optional[str] = None) -> None:
        """显示管线进度"""
        bar_length = 40
        filled = int(bar_length * progress / 100)
        bar = "█" * filled + "░" * (bar_length - filled)
        print(f"\r[{bar}] {progress:.1f}% - {stage}", end="", flush=True)
        if details:
            print(f" | {details}", end="", flush=True)

    def confirm_action(self, message: str) -> bool:
        """确认操作"""
        choice = self._input_fn(f"\n{message} (y/n): ").strip().lower()
        return choice in ("y", "yes")

    def select_option(self, prompt: str, options: List[str]) -> int:
        """选择选项

        Returns:
            选择的索引 (0-based)
        """
        print(f"\n{prompt}")
        for i, option in enumerate(options, 1):
            print(f"  {i}. {option}")

        while True:
            choice = self._input_fn("请选择 (输入数字): ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(options):
                    return idx
                else:
                    print("无效的选项")
            except ValueError:
                print("请输入有效数字")

    def show_menu(self, title: str, items: List[str]) -> int:
        """显示菜单

        Returns:
            选择的索引 (0-based)
        """
        print(f"\n{'=' * 40}")
        print(f"  {title}")
        print(f"{'=' * 40}")
        for i, item in enumerate(items, 1):
            print(f"  {i}. {item}")
        print(f"{'=' * 40}")

        while True:
            choice = self._input_fn("请选择: ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(items):
                    return idx
                else:
                    print("无效选择")
            except ValueError:
                print("请输入有效数字")


def create_cli() -> CLIInterface:
    """创建CLI接口实例"""
    return CLIInterface()
