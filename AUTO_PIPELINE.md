# RWKV 全自动创作管线使用说明

## 概述

全自动创作管线实现了从主题到完整小说正文的端到端自动化流程：

```
主题 + 主角名称 → 批量角色设定 → 剧情线路 → 全书大纲 → 卷宗拆分 → 章节规划 → 模块级并行写作 → 完整小说
```

## 使用方式

### 方式一：Web UI（推荐）

1. 启动项目：
   ```bash
   python main.py --web
   ```
   或双击 `start.bat`

2. 打开浏览器访问：http://localhost:5000/create

3. 点击"全自动管线"标签

4. 填写配置：
   - **主角名称**：用户设定的主角名（必填，逗号分隔多个）
   - **反派名称**：可选，AI会自动补全
   - **卷数**：小说总卷数
   - **每卷章节数**：每卷包含的章节数
   - **并发上限**：最大并发任务数（1-960）
   - **额外设定**：可选的背景故事或特殊要求

5. 点击"🚀 启动全自动管线"

6. 等待执行完成，查看生成的小说内容

### 方式二：Python API

```python
from src.core.auto_pipeline import AutoPipelineOrchestrator

# 创建管线编排器
orchestrator = AutoPipelineOrchestrator(
    config_path="pipeline.config.json",
    max_concurrency=96  # 用户自定义并发上限
)

# 运行全自动管线
result = orchestrator.run_full_pipeline(
    theme="仙侠",
    protagonist_names=["林孤云", "苏芸"],
    antagonist_names=["墨渊"],
    volume_count=4,
    chapters_per_volume=10,
    extra_context="这是一个关于修仙者逆天而行的故事。"
)

# 查看结果
print(f"状态: {result['status']}")
print(f"总章节数: {result['total_chapters']}")
print(f"总耗时: {result['total_elapsed_ms'] / 1000:.1f}秒")
```

## 管线流程详解

### 阶段1：批量角色设定
- 根据主题和用户设定的主角名称，AI自动生成：
  - 主角团队（使用用户指定的名称）
  - 反派团队
  - 配角团队
  - 势力列表

### 阶段2：剧情线路生成
- 基于角色体系生成：
  - 主线剧情（按卷划分阶段）
  - 支线剧情（个人线、感情线、势力线等）
  - 角色弧光（成长轨迹）
  - 伏笔规划（埋设与回收）

### 阶段3：全书大纲生成
- 整合角色和剧情，生成完整的全书大纲

### 阶段4：卷宗拆分
- 根据用户设定的卷数和每卷章节数，自动拆分大纲

### 阶段5：章节剧情模块规划
- 为每个章节分配剧情模块：
  - 主线剧情模块（每个章节必有）
  - 支线剧情模块（交错分配）

### 阶段6：并发写正文（模块级并行）
- 所有章节的所有模块同时进行并发写作
- 按用户设定的并发上限分批执行
- 自动合并各模块内容为完整章节

## 输出文件

管线执行完成后，输出文件位于 `output/` 目录：

```
output/
├── characters.json      # 角色设定
├── storyline.json       # 剧情线路
├── outline.json         # 全书大纲
├── volumes.jsonl        # 卷宗大纲
├── chapters.jsonl       # 章节规划
└── draft/               # 章节正文
    ├── 0001.md
    ├── 0002.md
    └── ...
```

## 性能优化

### 并发上限设置
- **低配置**（1-16）：适合本地测试
- **中配置**（17-96）：适合常规使用
- **高配置**（97-960）：需要强大的计算资源

### 注意事项
1. 大模型推理需要时间，请耐心等待
2. 并发数越高，资源消耗越大
3. 建议首次使用时使用较低的并发数测试

## 故障排除

### JSON解析失败
- 系统已内置JSON自动修复功能
- 如果仍然失败，请检查RWKV服务是否正常

### 管线执行失败
- 查看日志文件：`output/logs/`
- 确认RWKV推理服务已启动
- 检查配置文件 `pipeline.config.json`

## 技术架构

- **角色生成器**：`src/core/character_batch_generator.py`
- **剧情生成器**：`src/core/storyline_generator.py`
- **管线编排器**：`src/core/auto_pipeline.py`
- **Web API**：`src/web/app.py` (`/api/pipeline/auto`)
