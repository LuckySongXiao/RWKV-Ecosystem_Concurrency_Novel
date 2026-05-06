"""测试JSON修复功能"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.optimized_pipeline import OptimizedPipelineOrchestrator

class MockLogger:
    def error(self, msg): print(f'ERROR: {msg}')
    def warning(self, msg): print(f'WARN: {msg}')
    def info(self, msg): print(f'INFO: {msg}')

# 创建测试实例
orch = OptimizedPipelineOrchestrator('pipeline.config.json')
orch._logger = MockLogger()

# 测试1: 正常JSON
print('=== 测试1: 正常JSON ===')
result = orch._parse_json_result('{"name": "test", "value": 123}')
print(f'结果: {result}')

# 测试2: 带代码块的JSON
print('\n=== 测试2: 带代码块的JSON ===')
result = orch._parse_json_result('```json\n{"name": "test"}\n```')
print(f'结果: {result}')

# 测试3: 尾部逗号
print('\n=== 测试3: 尾部逗号 ===')
result = orch._parse_json_result('{"name": "test",}')
print(f'结果: {result}')

# 测试4: 未闭合字符串
print('\n=== 测试4: 未闭合字符串 ===')
result = orch._parse_json_result('{"name": "test}')
print(f'结果: {result}')

# 测试5: 空输入
print('\n=== 测试5: 空输入 ===')
result = orch._parse_json_result('')
print(f'结果: {result}')

# 测试6: 章节大纲为空的情况
print('\n=== 测试6: 章节大纲为空 ===')
result = orch._plan_chapter_outlines({}, 2, 3)
print(f'结果: {result}')

# 测试7: 全书大纲无volumes
print('\n=== 测试7: 全书大纲无volumes ===')
result = orch._plan_chapter_outlines({"title": "test"}, 2, 3)
print(f'结果: {result}')

# 测试8: 卷无events
print('\n=== 测试8: 卷无events ===')
result = orch._plan_chapter_outlines({
    "volumes": [{"volume_id": 1, "volume_title": "卷一"}]
}, 2, 3)
print(f'结果: {len(result)}章')

print('\n=== 所有测试完成 ===')
