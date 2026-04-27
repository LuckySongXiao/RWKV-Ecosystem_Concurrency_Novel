# 超级并发多智能体小说共创框架 - 项目开发文档

## 1. 项目概述
本项目是一套基于RWKV（v7-G1c）的**超高吞吐量、多智能体协作**长篇小说创作系统。  
框架深度融合了RWKV的`/big_batch/completions`超级并发API、State Tuning技术、Agent工具调用能力，将长篇小说创作从“逐章串行”升级为“**宏观串行规划 → 章节级千路并行创作 → 世界状态严格串行结算 → 闭环自迭代**”的工程化管线。

## 2. 核心设计理念
- **分层解耦**：将创作划分为宏观规划、内容生成、状态管理、质量审核四大独立层次。
- **极致并行**：章节大纲与正文的生成利用RWKV批量API，单次请求即可驱动数百至上千章同步推进。
- **状态唯一真值**：角色、势力、经济体系等状态通过独立的“世界状态引擎”进行**严格按章顺序的串行结算**，杜绝并行写入冲突，确保长篇小说世界观的一致性。
- **多智能体自主权**：引入总编Agent、作家Agent、世界管理Agent、审核Agent，各司其职并能自主调用工具（搜索、状态查询、冲突解决等），在设定的权限级别内自动化工作。
- **State文件定制**：为不同Agent挂载专用State文件，使7B级模型表现出专业级能力。

## 3. 系统架构

### 3.1 核心组件
- **中央调度器**：解析人类作者提供的设定，生成初始任务，并按流程触发各Agent、拆分并发请求、管理数据流转。
- **总编Agent**（自主权：高）：负责全书/卷大纲的结构化生成，宏观叙事决策。
- **作家Agent群**（自主权：中）：基于章节大纲和实时世界状态卡，并行创作章节正文，并自动附带状态变更JSON。
- **世界管理Agent**（自主权：中）：收集各章状态变更请求，按章排序、冲突校验、合并更新世界状态档案。
- **审核Agent**（自主权：高）：在章节大纲、正文、状态更新后启动质量审查，发现问题可驳回并触发重写。

### 3.2 工具集
Agent可自主调用以下工具：
- `search_web(query)` – 网络搜索
- `query_world_state(entity)` – 查询当前角色/势力/经济状态
- `propose_state_change(changes)` – 提出状态变更请求
- `resolve_conflict(conflict)` – 尝试解决实体冲突
- `check_narrative_consistency(scene)` – 叙事一致性检查
- `format_checker(text)` – 格式与语法修正
- `save_content(content, filepath)` – 存储生成内容

## 4. 超级并发工艺流程图

```mermaid
graph TD
    A[人类作者] -->|编写/上传 context/specification.md<br/>世界观/人物/修行体系等| B(中央调度器: 识别spec,<br/>生成全书大纲任务)
    B --> C

    subgraph 宏观规划层 [宏观规划层 - 全串行]
        C(模型: 总编Agent<br/>API: /chat/completions<br/>State: editor_planning.st<br/>采样参数: "机械任务类") -->|QA格式Prompt<br/>[User: 生成全书大纲...]| D[output/outline.json<br/>+ 初始世界状态]
        D --> E(模型: 总编Agent<br/>API: 同上<br/>采样参数: "创意类")
        E -->|指令格式Prompt<br/>[Instruction: 基于全书大纲,<br/>生成所有卷的详细大纲...]| F[output/volumes.jsonl]
    end

    F --> G{调度器: 解析卷大纲,<br/>触发超级并发任务}

    subgraph 超级并发创作层 [超级并发创作层 - 全并行]
        direction TB
        subgraph 章节大纲并行生成 [章节大纲并行生成]
            H[api: /big_batch/completions<br/>state: editor_planning.st<br/>max_batch: 960<br/>采样: "机械任务类"]
            H -->|批量请求: 各章节大纲生成prompt<br/>[Instruction: 基于卷1大纲,<br/>生成第X章大纲...]| I[output/chapters.jsonl]
        end

        subgraph 章节内容并行创作 [章节内容并行创作]
            I --> J[调度器: 将章节大纲与<br/>世界状态摘要合并,<br/>构造批量续写请求]
            J --> K[api: /big_batch/completions<br/>state: writer_novel.st<br/>max_batch: 960<br/>采样: "小说创意类"]
            K -->|批量请求: 续写prompt<br/>[章节大纲+角色状态卡<br/>+势力状态卡+伏笔提醒...]| L[output/draft/*.md<br/>每章正文+尾附状态变更JSON]
        end
    end

    L --> M(世界管理Agent: 收集所有状态变更请求)

    subgraph 状态串行结算与演化层 [状态串行结算与演化层 - 全串行]
        M --> N[校验器: 按章节顺序排序]
        N -->|模型: 审核Agent<br/>API: /chat/completions<br/>state: reviewer_factcheck.st| O{冲突检测}
        O -- 通过 --> P[合并更新]
        O -- 冲突 --> Q[标记待审/生成报告<br/>人类作者或总编Agent裁决]
        Q --> P
        P --> R[更新世界状态档案<br/>output/tracking/*.jsonl]
        R --> S[更新知识图谱<br/>entity_store.json<br/>关系/伏笔/时间线]
    end

    S --> T{审核Agent: 最终审查}
    T -->|模型: 审核Agent<br/>API: /chat/completions<br/>state: reviewer_narrative.st| U{叙事一致性/伏笔匹配?}
    U -- 通过 --> V{全书完成?}
    V -- 是 --> W[全书初稿完成]
    V -- 否 --> G
    U -- 驳回 --> X[反馈给对应作家Agent重写]
    X --> K

    W --> Y[最终审查与成书]

    classDef human fill:#f9d5e5,stroke:#333,stroke-width:2px;
    classDef file fill:#eeeeee,stroke:#333,stroke-width:1px;
    classDef serial fill:#ffe5d9,stroke:#d00000,stroke-width:2px;
    classDef parallel fill:#d4f0ff,stroke:#0077b6,stroke-width:2px;
    classDef agent fill:#e6d3fc,stroke:#7b2ff7,stroke-width:2px;
    classDef bg fill:#ddd,stroke:#333,stroke-width:1px;

    class A human;
    class D,F,I,L,R,S,W,Y file;
    class C,E,N,O,P,Q serial;
    class H,K parallel;
    class B,G,J,M,T,U,V,X agent;


## 5. 核心工艺参数配置表


流程阶段	核心任务	推荐API端点	State文件	Temperature	Top_P	并发建议	提示词格式	说明
宏观规划层	全书大纲生成	/chat/completions	editor_planning.st	1.0	0.1	单次请求	User/Assistant	结构化输出，低随机性确保大纲逻辑严密
各卷大纲生成	/chat/completions	editor_planning.st	1.2	0.15	单次请求	Instruction/Response	需兼顾创意与结构
超级并发创作层	章节大纲并行生成	/big_batch/completions	editor_planning.st	1.0	0.1	最大960路	Instruction/Response	机械任务，快速批量产出
章节正文并行创作	/big_batch/completions	writer_novel.st	1.4	0.3	最大960路	User/Assistant（续写）	高并发下保持文采与逻辑
状态串行结算层	状态提取+冲突校验	/chat/completions	reviewer_factcheck.st	1.0	0.2	串行	Instruction/Response	严格按格式输出JSON
叙事一致性审查	/chat/completions	reviewer_narrative.st	1.0	0.2	串行	User/Assistant	降低随机性保证评判准确性

采样参数说明：

Temperature：控制随机性，1.0为平衡，>1.2偏向创意，<1.0趋于保守。

Top_P：核采样阈值，0.1~0.3用于高度聚焦的结构化/事实性任务，0.3~0.5用于创意续写。

以上参数基于RWKV v7-G1c官方推荐，实际使用时可根据模型版本微调。


6. 关键机制详解
6.1 超级并发实现
本框架的最大性能突破在于章节内容并行创作阶段。

统一采用RWKV的 /big_batch/completions API，该端点由rwkv_lightning库驱动，专为超高吞吐量设计。

实测可在单张消费级显卡（如RTX 4090）上实现960路并发，总计10000+ token/s的生成速度。

单次请求可包含整卷甚至整书的章节续写任务，真正实现“一秒出多章”。

若需更丰富的采样策略，可选用v1/chat/completions端点（支持独立设置频率/存在惩罚等）。

6.2 State文件体系
深度整合RWKV独有的State Tuning技术，为每个核心Agent定制专用状态文件：

总编Agent → editor_planning.st：强化结构化大纲策划、起承转合设计能力。

作家Agent → writer_novel.st：注入大师级文笔、对话节奏、场景渲染风格。

审核Agent（事实校验） → reviewer_factcheck.st：训练对矛盾信息的高度敏感。

审核Agent（叙事审查） → reviewer_narrative.st：专注伏笔、节奏、人物弧光评判。

State文件可通过RWKV Runner单独挂载，或使用merge_state.py工具直接融合到基底模型，使中小参数模型获得专业作家/编辑水平。

6.3 世界状态演化
构建了一套随故事推演而进化的世界模型，彻底解决长篇小说前后矛盾问题。

状态载体：output/tracking/characters.jsonl（角色状态）、factions.jsonl（势力状态）、economy.json（经济快照）。

更新流程：各章并行产出“状态变更请求JSON” → 世界管理Agent严格按章节顺序排序 → 冲突检测（唯一物品、时间、位置） → 无冲突则原子化合并，有冲突则暂停并报告作者。

知识图谱：额外维护entity_store.json，存储所有实体间的动态关系网络，支持复杂查询（如“当前所有与主角好感度>80的角色”）。

注入机制：新章节生成前，自动提取与该章相关的角色/势力实时状态摘要注入Prompt，确保AI始终基于最新世界事实创作。

6.4 提示词格式规范
RWKV对提示词结构高度敏感，本框架严格区分任务类型使用官方推荐格式：

结构化提取任务（大纲生成、状态抽取等）：

text
Instruction: 基于以下全书大纲，为每一卷生成详细大纲，输出JSON...
Input: {全书大纲内容}
Response:
创作型续写任务：

text
User: （章节大纲 + 角色状态卡 + 势力状态卡 + 前情提要）请续写本章内容...
Assistant: 
快思考模式：在需要高效处理机械任务时，使用<think> </think>标签包裹思考过程或直接关闭思考，提升回答直接性。

6.5 Agent自主权分级
赋予Agent的权限严格区分为三级，确保作者对创作核心的绝对掌控：

🔵 全自动级：语法拼写修正、格式标准化、伏笔列表自动更新、年龄/日期等简单属性推进。

🟡 建议执行级：优化冗余描写、为平淡桥段提供增强补丁、微调对话语气，执行后标记供作者回顾并可一键撤回。

🔴 必确认级：关键角色死亡/退场、主角领悟核心能力、引入重大世界观新规则、解决唯一性冲突等。Agent必须暂停流程，提交详细分析与选项，等待人类作者裁决。

7. 工程目录结构
text
my-novel-project/
├── context/                    # 人类作者编写的初始设定（AI只读）
│   ├── specification.md        # 核心世界观、人物小传、修行体系等
│   └── style-guide.md          # 写作风格约束
├── output/                     # AI生成的所有内容
│   ├── outline.json            # 全书大纲
│   ├── volumes.jsonl           # 每行一个卷的详细大纲
│   ├── chapters.jsonl          # 每行一个章节的详细大纲
│   ├── draft/                  # 各章节Markdown初稿（含状态变更尾部）
│   ├── tracking/               # 世界状态档案库
│   │   ├── characters.jsonl    # 角色状态实时档案
│   │   ├── factions.jsonl      # 势力状态实时档案
│   │   ├── economy.json        # 经济体系快照
│   │   ├── entity_store.json   # 知识图谱（关系、伏笔、时间线）
│   │   └── changelog.md        # 状态变更日志
│   └── final/                  # 最终审定后成书
├── states/                     # 各Agent定制State文件
│   ├── editor_planning.st
│   ├── writer_novel.st
│   ├── reviewer_factcheck.st
│   └── reviewer_narrative.st
├── pipeline.config.json        # 流程配置文件（API密钥、并发数等）
└── README.md                   # 项目说明
8. 快速启动建议
准备State文件：根据目标小说类型（仙侠、科幻、都市等），使用RWKV Runner微调或寻找开源State文件，放入states/目录。

编写设定文档：在context/specification.md中详细描述世界观、主要人物、力量体系、故事主线。

部署API服务：启动RWKV Runner或Ai00服务端，确保/big_batch/completions端点可用。

运行调度器：执行orchestrator.py，自动按流程图开始全流程创作。

实时监控：作者可随时检查output/tracking/下的状态档案，对标记为“待审”的决策进行人工干预。

本项目框架将AI从简单的续写工具提升为拥有记忆、能自主规划并遵守权限的创作共生体，真正实现“人类定魂，AI行文，万物有迹，万事有序”的写作新范式。