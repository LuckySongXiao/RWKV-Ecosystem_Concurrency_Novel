"""项目健康检查脚本"""

import os
import sys

print('='*60)
print('项目健康检查报告')
print('='*60)

all_ok = True

# 1. 核心模块导入
print('\n[1] 核心模块导入检查')
modules = [
    ('src.core.config', '配置管理'),
    ('src.core.error_handler', '异常处理'),
    ('src.core.rwkv_client', 'RWKV客户端'),
    ('src.core.rwkv_service', 'RWKV服务管理'),
    ('src.core.file_manager', '文件管理'),
    ('src.core.world_state_engine', '世界状态引擎'),
    ('src.core.character_table_generator', '角色信息表生成器'),
    ('src.core.character_batch_generator', '角色批量生成器'),
    ('src.core.storyline_generator', '故事主线生成器'),
    ('src.core.auto_pipeline', '全自动管线'),
    ('src.core.optimized_pipeline', '优化版管线'),
    ('src.orchestrator', '管线编排器'),
    ('src.web.app', 'Web应用'),
]

for module, name in modules:
    try:
        __import__(module)
        print(f'  ✓ {name}')
    except Exception as e:
        print(f'  ✗ {name}: {e}')
        all_ok = False

# 2. 配置文件检查
print('\n[2] 配置文件检查')
configs = [
    ('pipeline.config.json', '管线配置'),
]
for config, name in configs:
    if os.path.exists(config):
        print(f'  ✓ {name} ({config})')
    else:
        print(f'  ✗ {name} 缺失')
        all_ok = False

# 3. 目录结构检查
print('\n[3] 目录结构检查')
dirs = ['src', 'src/core', 'src/web', 'src/web/templates', 'tests', 'output']
for d in dirs:
    if os.path.isdir(d):
        print(f'  ✓ {d}/')
    else:
        print(f'  ✗ {d}/ 缺失')
        all_ok = False

# 4. Web模板文件检查
print('\n[4] Web模板文件检查')
templates = ['index.html', 'create.html', 'matrix.html']
for t in templates:
    path = os.path.join('src', 'web', 'templates', t)
    if os.path.exists(path):
        print(f'  ✓ {t}')
    else:
        print(f'  ✗ {t} 缺失')
        all_ok = False

# 5. 核心文件检查
print('\n[5] 核心Python文件检查')
core_files = [
    'src/core/character_table_generator.py',
    'src/core/optimized_pipeline.py',
    'src/core/auto_pipeline.py',
    'src/core/character_batch_generator.py',
    'src/core/storyline_generator.py',
    'src/core/error_handler.py',
    'src/core/rwkv_service.py',
    'src/core/config.py',
    'src/core/world_state_engine.py',
    'src/core/file_manager.py',
    'src/core/rwkv_client.py',
    'src/orchestrator.py',
    'src/web/app.py',
    'main.py',
]

for f in core_files:
    if os.path.exists(f):
        print(f'  ✓ {f}')
    else:
        print(f'  ✗ {f} 缺失')
        all_ok = False

# 6. 测试文件检查
print('\n[6] 测试文件检查')
test_files = [
    'tests/test_error_handler.py',
    'tests/test_pipeline.py',
    'tests/test_batch.py',
    'tests/test_conflict.py',
    'tests/test_checkpoint.py',
    'tests/test_auto_pipeline.py',
    'tests/test_optimized_pipeline.py',
]

for f in test_files:
    if os.path.exists(f):
        print(f'  ✓ {f}')
    else:
        print(f'  ✗ {f} 缺失')
        all_ok = False

print('\n' + '='*60)
if all_ok:
    print('✅ 项目健康检查通过 - 所有模块正常')
    sys.exit(0)
else:
    print('⚠️  项目健康检查发现问题，请查看上方详情')
    sys.exit(1)
print('='*60)
