# MediX多智能体医疗助手

基于 Skills-Agent 两层架构的多智能体协作医疗助手系统，融合 Agent Loop、Agent Swarm、记忆管理和 Milvus 知识库。

## 📌 项目来源与改动说明

**本仓库的核心代码（Agent 架构、Skills、Swarm 协作、记忆系统、知识库）由他人编写，非本人原创。**
本人在此基础上完成了 Windows 平台适配、缺陷修复与 Web 前端开发。

原项目未附带 LICENSE 文件，按默认著作权法属于「保留所有权利」。因此本仓库目前设为**私有**，
未经原作者许可不应公开分发或用于商业用途。

### 本版本相对原项目的主要改动

| 改动 | 说明 |
|------|------|
| 修复模块文件名 | `swarm/events.py` 和 `validation/auto_fixer.py` 被改名为带时间戳的备份名，导致 `import swarm` 直接失败 |
| 移除硬编码路径 | `core/llm_client.py` 里写死了 macOS 路径，改为按文件位置动态解析，并支持 `.env` 覆盖 |
| 新增 Windows 向量库降级 | Milvus Lite 官方不支持 Windows，`knowledge/local_vector_store.py` 用 numpy 实现了接口一致的本地向量库，自动接管 |
| 新增 Web 界面 | `api_server.py`（FastAPI）+ `web/`（原生 HTML/CSS/JS），含 SSE 流式接口与会诊面板 |
| 修复 GBK 编码崩溃 | 中文 Windows 控制台默认 GBK，打印 emoji 抛 `UnicodeEncodeError` |
| 新增工程配套 | `.vscode/` 运行配置、`.env.example`、运行截图脚本、端到端验证脚本 |

## 📋 项目概述

本项目采用创新的 **Skills-Agent 两层架构**，通过7个自包含的原子 Skills 和3个专业 Agent 协同工作，提供智能、专业的医疗服务。

### 🎯 核心特性

- **🔧 Skills 直达架构**: 7个原子 Skills 自包含，直接转换为 OpenAI function calling 格式 ✅
- **🤖 Agent Loop**: LLM 驱动的 Skill 调用循环，Agent 自主规划、调用 Skills 并完成任务 ✅
- **🐝 Agent Swarm**: 真正的群体智能（去中心化协作，自主任务认领，并行执行）✅
- **⚡ 流式会诊**: 汇总阶段逐段推送 Token，意见边生成边显示 ✅
- **🚨 急症预检**: 调用模型前先做红旗症状筛查，命中立即提示拨打 120 ✅
- **📚 知识库管理**: Web 端上传 TXT/Markdown/PDF、删除文档、一键重建索引（幂等）✅
- **🧾 意见透明化**: 展示各科室原始意见与知识库参考来源 ✅
- **🧠 记忆系统**: 短期记忆（会话级对话历史）+ 长期记忆（Mem0跨会话记忆）+ **多轮对话上下文利用** ✅
- **🗂️ 会话历史**: SQLite 持久化会诊记录，服务重启后可浏览、搜索、重命名、导出、恢复和删除 ✅
- **💬 会诊操作**: 复制意见、导出 Markdown、再次会诊、停止生成 ✅
- **🌗 深色模式**: 跟随系统偏好，可手动切换并记住选择 ✅
- **💾 Milvus 知识库**: 统一知识管理，语义检索，支持模糊查询（"血压高" → "高血压"）✅
- **⚡ Claude Code Skills**: 8个预定义技能（7个原子 + 1个复杂），一键调用医疗助手 ✅
- **🏗️ Harness Engineering**: 约束驱动 + 熵管理，系统自动验证和优化，保证安全、简洁、高质量 ✅

## 🎯 Skills 直达架构

### 架构设计

```
Skills (函数) → 直接转换 → OpenAI Format → LLM 调用
         ↓
    Milvus/业务逻辑
```

### 关键特性

1. **Skills 直达 LLM**
   - Skill 函数直接转换为 OpenAI function calling 格式
   - SkillRegistry 统一管理：注册、执行、格式转换

2. **简化的注册流程**
   ```python
   skill → OpenAI Format
   ```

3. **Agent 灵活选择**
   - 每个 Agent 注册全部7个 Skills
   - Agent Loop 根据任务自主选择合适的 Skills
   - 一个 Agent 可以跨领域调用 Skills

4. **用户友好入口**
   - 7个原子 Skills：快速查询，立即响应
   - 1个复杂 Skill：触发 Swarm 协作
   - 用户无需理解 Agent 架构

5. **多轮对话支持**
   - 短期记忆：会话级对话历史（10条消息）
   - 长期记忆：Mem0 跨会话记忆
   - **上下文利用率 100%**：追问能正确理解历史对话

### 测试验证

**所有测试通过（26个测试用例，100% 通过率）**：

**核心功能**：
- ✅ Agent Loop 和 Skill 调用
- ✅ Agent Swarm 群体智能
- ✅ 记忆系统（短期+长期）
- ✅ 多轮对话上下文利用 🌟
- ✅ Skills 自主选择
- ✅ Milvus 知识库集成

**Harness Engineering（Phase 8）**：
- ✅ 约束系统（Skill 调用验证、输出验证、自动修复）
- ✅ 熵管理（去重、压缩、熵估算）
- ✅ Agent Loop 集成（约束 + 熵管理协同工作）

运行测试套件：
```bash
# 运行所有测试（包含 Harness Engineering）
python examples/test_all.py
```

## 🖥️ Web 界面

除命令行外，项目提供了浏览器界面，整体按**门诊多学科会诊（MDT）**的场景设计：顶部一条心电监护带，左侧是问诊记录，右侧是会诊科室面板，实时显示哪些科室已经出具意见。

```bash
python api_server.py
```

浏览器打开 <http://127.0.0.1:8000>

常用参数：

```bash
python api_server.py --port 8080          # 换端口
python api_server.py --host 0.0.0.0       # 允许局域网访问
python api_server.py --reload             # 开发模式，改代码自动重载
```

### 界面说明

| 区域 | 内容 |
|------|------|
| 心电监护带 | 波形随系统状态改变节律：空闲时平缓、会诊中加快、配置或请求异常时转为琥珀/红色 |
| 会诊科室 | 三个科室（全科门诊 / 急诊医学科 / 循证医学中心）的状态，随流式事件逐个点亮 |
| 监护读数 | 接诊方式（单科接诊 / 多学科会诊）、参与科室、完成项目、会诊用时、知识库状态、会诊编号 |
| 接诊记录 | 每一步的分诊与调度日志 |
| 问诊记录 | 用户输入标为「主诉」，答复渲染成带抬头的「多学科会诊意见书」，并自动提取风险等级、诊疗建议和免责声明 |
| 急症警示 | 主诉命中胸痛、呼吸困难、卒中样表现等红旗时，在模型回复前先显示红色警示条 |
| 知识库管理 | 会诊面板下方「管理知识库」：查看文档与向量块、上传 TXT/MD/PDF、删除、重建索引 |
| 会诊依据 | 多学科会诊可展开各科室原始意见；使用知识库时展示来源、文件名、相关度与原文片段 |
| 历史会诊 | 右上角「会诊记录」抽屉：按时间浏览、搜索、打开或删除历史会诊；刷新页面自动恢复上次会诊 |
| 意见操作 | 每条意见下方可复制、导出 Markdown、再次会诊；生成过程中可随时停止 |
| 外观 | 跟随系统深色偏好，也可用顶栏按钮切换；选择保存在浏览器本地 |

风险提示的配色对齐急诊分诊的四级色标（红 / 橙 / 黄 / 绿），但用的是「风险等级」表述，不作临床分诊结论。

前端代码在 `web/` 目录（原生 HTML/CSS/JS，无需构建步骤）。

### 会话历史与意见导出

- 会诊记录保存在 `data/sessions.db`（SQLite，首次运行自动创建，已加入 `.gitignore`），服务重启后不会丢失
- 右上角「会诊记录」支持搜索、打开、重命名、导出和删除历史会诊，点击条目可恢复完整问诊记录
- 每条会诊意见下方提供「复制意见」「导出 Markdown」「再次会诊」
- 多学科会诊的汇总阶段，以及单 Agent 使用工具后的强制收尾，都支持流式输出；直接作答仍为一次性返回
- 生成过程中点「停止」会中断本次请求；服务端在客户端断开后取消后台任务

### 知识库管理

- 会诊面板下方「管理知识库」可查看内置/上传文档、向量块数量和存储目录
- 支持上传 TXT / Markdown / PDF（≤ 5 MB），上传或删除后自动重建索引
- 重建会先清空旧 collection 再导入，重复执行不会产生重复向量块
- 用户上传的文档保存在 `knowledge/data/uploads/`（已加入 `.gitignore`，不会提交到仓库）
- 命令行等价操作：`python knowledge/scripts/import_hardcoded_data.py`（默认重建；`--append` 追加）

### 各科室意见与参考来源

- 多学科会诊意见下方可展开「各科室意见」，查看每个 Agent 的原始分析
- 使用知识库的 Skill 会返回来源元数据，界面展示来源、文档名、相关度与原文片段
- 单条意见导出 Markdown 时也会包含各科室意见和参考来源
- 说明：科室意见与来源随本次响应返回；历史会话恢复时只保留最终意见（SQLite 暂不保存这两类附加数据）

### 截图

`docs/screenshots/` 下是用真实运行结果抓的截图（首页、会诊进行中、完整会诊意见、诊疗建议、移动端两图）。

需要重新生成时，先把服务跑起来，另开一个终端执行：

```bash
node tools/capture_screenshots.js
```

脚本会自动提交一条主诉、等答复生成完再截图。依赖 Playwright：

```bash
npm install -D playwright
```

若本机已装 Edge 或 Chrome，不装 Playwright 自带的 Chromium 也能跑（脚本会依次尝试自带 Chromium → 系统 Edge → 系统 Chrome）。

有两个坑已经处理好了，改脚本时别踩回去：

- 截图固定加 `--disable-gpu`。这台机器上开硬件加速会把洋红噪点烤进 PNG
- `.thread` 是内部滚动容器，整页截图前脚本会临时解除其滚动，否则只能截到可视区那一段

### API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/status` | 健康检查、模型配置、知识库状态 |
| POST | `/api/chat` | 同步问答，返回完整结果 |
| POST | `/api/chat/stream` | SSE 流式问答：分诊、急症预检、科室完成、汇总 Token 与最终结果 |
| POST | `/api/sessions` | 新建会话 |
| GET | `/api/sessions` | 历史会诊列表（支持 `q` 搜索） |
| GET | `/api/sessions/{id}/messages` | 读取会话消息（优先读 SQLite） |
| PATCH | `/api/sessions/{id}` | 重命名会话 |
| DELETE | `/api/sessions/{id}` | 删除会话 |
| GET | `/api/knowledge` | 知识库文档与向量块统计 |
| POST | `/api/knowledge/rebuild` | 重建知识库索引（幂等） |
| POST | `/api/knowledge/upload` | 上传文档（multipart，TXT/MD/PDF） |
| DELETE | `/api/knowledge/documents/{filename}` | 删除上传文档并重建 |

前端代码在 `web/` 目录（原生 HTML/CSS/JS，无需构建步骤）。

## 🚀 从零开始运行

### 1. 环境准备

需要 Python 3.10 以上（推荐 3.12）。

```bash
cd medix-agent-swarm
python -m venv .venv
```

Windows：
```bash
.venv\Scripts\activate
```

macOS / Linux：
```bash
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

> 如果需要 `torch`，桌面端建议装 CPU 版本以免下载数 GB 的 CUDA 运行时：
> `pip install torch --index-url https://download.pytorch.org/whl/cpu`

### 3. 配置 API

**方式一（推荐）**：复制 `.env.example` 为 `.env`，填写 API Key：

```bash
cp .env.example .env
```

```ini
MEDIX_API_KEY=你的-api-key
MEDIX_MODEL=doubao-seed-1-6-flash-250828
MEDIX_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
MEDIX_TIMEOUT=60
```

`MEDIX_TIMEOUT` 是单次模型请求超时（秒），可不填，默认 60。服务会把 401（Key 无效）、402（余额不足）、429（限流）、超时和网络错误转换成可读提示，其中 401/402 不会反复重试。

**方式二**：编辑上一层目录的 `config.py`：

```python
LLM_CONFIG = {
    "api_key": "your-llm-api-key",
    "model_name": "your-model-name",
    "base_url": "https://api.openai.com/v1",
    "temperature": 0.7,
    "max_tokens": 8192,
}

MEM0_CONFIG = {"api_key": "m0-your-api-key-here"}   # 可选，不填则自动降级
```

环境变量优先级高于 `config.py`。任何 OpenAI 兼容接口都可以，改 `base_url` 和 `model_name` 即可。

### 4. 初始化知识库

```bash
python knowledge/scripts/import_hardcoded_data.py
```

首次运行会下载中文 Embedding 模型 `BAAI/bge-small-zh-v1.5`（约 100MB）。如果访问 HuggingFace 缓慢，可先设置镜像：

```bash
set HF_ENDPOINT=https://hf-mirror.com        # Windows
export HF_ENDPOINT=https://hf-mirror.com     # macOS / Linux
```

导入默认重建索引，重复执行不会产生重复向量块；也可以启动 Web 端后在「管理知识库」里上传 TXT/Markdown/PDF。

### 5. 运行测试

离线单元测试（不需要 API Key，也不会访问网络，CI 跑的就是这一套）：

```bash
pip install pytest
python -m pytest tests
```

完整集成测试（会真实调用 LLM，产生 API 费用）：

```bash
python examples/test_all.py
```

### 6. 环境自检（可选）

装好依赖、填好 `.env` 后，可以运行自检脚本确认环境是否完整：

```bash
python scripts/diagnose.py
```

需要同时验证模型接口和知识库时：

```bash
python scripts/diagnose.py --llm --kb
```

脚本会检查 Python 版本、依赖、LLM 配置、知识库文档和向量库文件；输出可直接粘贴到 Issue 的「环境」一栏（不会打印 API Key）。

### 7. 开始使用

Web 界面：

```bash
python api_server.py
```

命令行：

```bash
python main.py
```

### 在 VSCode 中运行

项目已附带 `.vscode/` 配置：

1. 打开项目文件夹，VSCode 会提示选择解释器 —— 选 `.venv\Scripts\python.exe`
2. 按 `F5`，在下列配置中选择：
   - **启动 Web 服务（浏览器界面）** — 推荐
   - **命令行对话（main.py）**
   - **初始化知识库**
   - **运行测试套件**
3. 也可以按 `Ctrl+Shift+B` 直接跑默认任务（启动 Web 服务）

### 平台说明（Windows）

Milvus Lite 官方只提供 Linux / macOS 的 wheel，Windows 上没有。项目在 `knowledge/local_vector_store.py` 里实现了一个接口一致的纯 numpy 向量库作为降级方案，会自动接管，无需手动切换：

- Linux / macOS → 仍然使用 Milvus Lite（`knowledge/data/milvus_lite.db`）
- Windows → 使用本地实现，数据存在 `knowledge/data/milvus_lite.db.local.json`

本项目的知识库只有几十个文档块，暴力计算余弦相似度的开销可以忽略，两种后端行为一致。

## 📦 项目结构

```
medix-agent-swarm/
├── .claude/skills/                    # Claude Code Skills (10个)
│   ├── search-knowledge/              # 搜索医学知识库
│   │   └── script/
│   │       ├── __init__.py
│   │       └── search.py
│   ├── assess-risk/                   # 风险评估
│   │   └── script/
│   │       ├── __init__.py
│   │       └── risk.py
│   ├── analyze-symptoms/              # 症状分析
│   │   └── script/
│   │       ├── __init__.py
│   │       └── symptoms.py
│   ├── recommend-lifestyle/           # 生活方式建议
│   │   └── script/
│   │       ├── __init__.py
│   │       └── lifestyle.py
│   ├── disease-code/                  # ICD-10疾病编码
│   │   └── script/
│   │       ├── __init__.py
│   │       └── code.py
│   ├── clinical-guideline/            # 临床指南检索
│   │   └── script/
│   │       ├── __init__.py
│   │       └── guideline.py
│   ├── deep-research/                 # 深度研究
│   │   └── script/
│   │       ├── __init__.py
│   │       └── research.py
│   ├── search-history/                # 搜索会话历史（短期记忆）
│   │   └── script/search.py
│   └── search-similar-cases/          # 搜索相似案例（长期记忆）
│       └── script/search.py
│
├── agents/                            # Agent 实现
│   ├── __init__.py
│   ├── base_agent.py                  # Agent 基类
│   ├── consultation_agent.py          # 健康咨询 Agent
│   ├── diagnostic_agent.py            # 症状诊断 Agent
│   ├── research_agent.py              # 医学研究 Agent
│   └── skill_registry_mixin.py        # Skill 注册混入
│
├── core/                              # 核心引擎
│   ├── __init__.py
│   ├── agent_loop.py                  # Agent Loop（集成约束验证）
│   ├── llm_client.py                  # LLM 客户端
│   ├── skill_loader.py                # 动态加载 Skills
│   ├── skill_registry.py              # Skill 注册表（直接转 OpenAI format，无 Tool 层）
│   └── state_manager.py               # 状态管理
│
├── swarm/                             # Swarm 协调器
│   ├── __init__.py
│   ├── events.py                      # 事件驱动通信
│   ├── lead_agent.py                  # 任务分解和汇总
│   ├── shared_context.py              # 共享环境（信息素）
│   └── swarm_coordinator.py           # 智能路由
│
├── memory/                            # 记忆管理（集成熵管理）
│   ├── __init__.py
│   ├── agent_identity.py              # Agent 身份管理
│   ├── long_term.py                   # 长期记忆（Mem0）
│   ├── short_term.py                  # 短期记忆（自动去重和压缩）
│   ├── session_summary.py             # 会话总结
│   ├── entropy_manager.py             # 熵管理器（Harness）
│   ├── agents/                        # Agent 身份文件存储
│   └── swarm/session_summaries/       # Swarm 会话总结存储
│
├── constraints/                       # 约束系统（Harness）
│   ├── __init__.py
│   ├── agent_constraints.yaml         # Agent 能力边界定义
│   ├── swarm_constraints.yaml         # Swarm 协作规则
│   └── validator.py                   # 运行时约束验证器
│
├── validation/                        # 输出验证和修复（Harness）
│   ├── __init__.py
│   └── auto_fixer.py                  # 自动修复器
│
├── knowledge/                         # 知识库
│   ├── __init__.py
│   ├── milvus_kb.py                   # 知识库封装（Milvus Lite）
│   ├── local_vector_store.py          # 本地向量库（Windows 降级方案）
│   ├── data/
│   │   ├── documents/                 # 医学知识文档（txt）
│   │   └── milvus_lite.db             # 向量数据库（自动生成）
│   └── scripts/
│       ├── __init__.py
│       └── import_hardcoded_data.py   # 数据导入脚本
│
├── web/                               # 前端页面（原生 HTML/CSS/JS）
│   ├── index.html
│   ├── styles.css
│   └── app.js
│
├── tools/
│   └── capture_screenshots.js         # 抓运行截图，输出到 docs/screenshots/
│
├── docs/screenshots/                  # 运行截图（真实运行结果）
│
├── .vscode/                           # VSCode 运行配置
│   ├── launch.json
│   ├── settings.json
│   └── tasks.json
│
├── main.py                            # 命令行入口（交互式对话）
├── api_server.py                      # Web 服务入口（FastAPI）
├── setup.py                           # 安装脚本
├── requirements.txt                   # 依赖列表
├── .env.example                       # 配置模板（复制为 .env）
└── README.md                          # 本文档
│
├── research/                          # DeepResearch 模块
│   ├── __init__.py
│   ├── deep_research_workflow.py      # 深度研究工作流
│   ├── evidence_synthesizer.py        # 证据综合器
│   ├── knowledge_base.py              # 知识库（可选 Qdrant）
│   └── web_search.py                  # 网络搜索
│
├── examples/
│   └── test_all.py                    # 完整测试套件（26个测试）
│
├── main.py                            # 主入口（交互式对话）
├── setup.py                           # 安装脚本
├── requirements.txt                   # 依赖列表
└── README.md                          # 本文档
```

**架构说明**：
- ✅ **直达架构**：Skills → OpenAI Format
- ✅ **Skills 自包含**：每个 Skill 在 `script/` 目录下实现，直接调用知识库
- ✅ **动态加载**：`skill_loader.py` 扫描 `.claude/skills/` 目录动态加载
- ✅ **SkillRegistry**：统一管理 Skill 注册、执行、格式转换
- ✅ **统一配置**：使用上一层目录的 `config.py`，也可用 `.env` 覆盖
- ✅ **记忆分离**：Agent 身份文件和会话总结分别存储在 `memory/agents/` 和 `memory/swarm/`

## 🤖 Skills 和 Agent 清单

### 7个原子 Skills（两层架构）

**所有 Agent 共享以下 Skills**：

| Skill | 功能 | 数据源 | 特点 |
|-------|------|--------|------|
| `search_knowledge` | 搜索医学知识库 | Milvus | 语义检索 |
| `recommend_lifestyle` | 生活方式和用药建议 | Milvus | 个性化建议 |
| `assess_risk` | 风险等级评估 | 规则引擎 | 高危症状识别 |
| `analyze_symptoms` | 症状模式分析 | 规则引擎 | 多系统分析 |
| `disease_code` | ICD-10疾病编码 | Milvus | 标准编码 |
| `clinical_guideline` | 临床指南检索 | Milvus | 权威指南 |
| `deep_research` | 深度研究 | 网络搜索 | 最新进展 |

### 3个专业 Agent（自主选择 Skills）

#### 1. ConsultationAgent（健康咨询）
- **能力**: 通用健康咨询和生活方式指导
- **注册 Skills**: 全部7个（自主选择合适的 Skills）
- **常用 Skills**: `search_knowledge`, `recommend_lifestyle`

#### 2. DiagnosticAgent（症状诊断）
- **能力**: 症状分析、风险评估和鉴别诊断
- **注册 Skills**: 全部7个（自主选择合适的 Skills）
- **常用 Skills**: `assess_risk`, `analyze_symptoms`, `disease_code`

#### 3. ResearchAgent（医学研究）
- **能力**: 循证医学证据和权威指南检索
- **注册 Skills**: 全部7个（自主选择合适的 Skills）
- **常用 Skills**: `clinical_guideline`, `deep_research`

### 2个协调 Agent

- **LeadAgent**: 任务分解和结果汇总（非编排器）
- **SwarmCoordinator**: 智能路由（简单问题→单Agent，复杂问题→Swarm）

### Skills 架构特点

- ✅ **直达架构**: Skills → OpenAI Format
- ✅ **Skills 自包含**: 直接调用 Milvus 或内置逻辑
- ✅ **Agent 灵活性**: 每个 Agent 注册全部7个 Skills，根据任务自主选择
- ✅ **SkillRegistry**: 统一管理注册、执行、格式转换
- ✅ **统一知识库**: 医学知识统一存储在 Milvus 向量数据库，支持语义检索
- ✅ **易于扩展**: 添加新 Skill 或新知识无需修改 Agent 代码


## ⚙️ 配置说明

项目使用上一层目录的统一配置文件 `../config.py`，也可以用项目根目录的 `.env` 覆盖（环境变量优先）。

### 配置内容

```python
# LLM API config (LLM)
LLM_CONFIG = {
    "api_key": "your-your-model-api-key",
    "model_name": "your-model",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "temperature": 0.7,
    "max_tokens": 8192,
}

# Mem0 API config (Long-term memory)
MEM0_CONFIG = {
    "api_key": "m0-your-api-key-here",  # 获取地址：https://app.mem0.ai
}

# Redis config (Short-term memory persistence, optional)
REDIS_CONFIG = {
    "host": "localhost",
    "port": 6379,
    "db": 0,
}
```

### 记忆系统配置

本系统支持两层记忆机制：**短期记忆（会话级）** 和 **长期记忆（跨会话）**。

#### 短期记忆（ShortTermMemory）

**作用**：存储当前会话的对话历史，支持多轮对话上下文理解。

**配置**：
```python
# 方式1: 内存存储（默认，无需配置）
from memory.short_term import ShortTermMemory
memory = ShortTermMemory(session_id="user_123", storage_type="memory")

# 方式2: Redis持久化（可选）
memory = ShortTermMemory(session_id="user_123", storage_type="redis")
```

**使用示例**：
```python
# 添加消息
memory.add_message(role="user", content="我有高血压")
memory.add_message(role="assistant", content="高血压需要...")

# 获取对话历史（最近10条）
history = memory.get_messages(limit=10)

# 会话结束时清空
memory.clear()
```

**存储方式**：
- **内存**（默认）：无需配置，保留时间60分钟
- **Redis**（可选）：需配置 REDIS_CONFIG，支持持久化
- 存储容量：最近10条消息

#### 长期记忆（Mem0）

**作用**：跨会话记忆，通过向量相似度检索历史案例和经验。

**配置**：
```python
# 在上一层 config.py 或项目根目录 .env 中配置
MEM0_CONFIG = {
    "api_key": "m0-your-api-key-here",  # 获取地址：https://app.mem0.ai
}
```

**使用示例**：
```python
from memory.long_term import LongTermMemory
memory = LongTermMemory(user_id="user_123")

# 存储会话总结
memory.add("患者有高血压，给出了生活方式建议")

# 检索相关记忆
results = memory.search("高血压患者如何管理？")
# → 返回历史相似案例
```

**存储方式**：
- **Mem0 云服务**：自动处理向量化和相似度搜索
- 存储范围：跨会话持久化
- 存储内容：会话总结
- 无需本地部署向量数据库

#### 记忆系统如何融入对话

**流程**：

```
1. 会话开始
   ↓
2. 从 Mem0 检索相关长期记忆（历史案例）
   ↓
3. 初始化短期记忆（对话历史）
   ↓
4. Agent 执行
   - 读取短期记忆：获取当前会话上下文
   - 写入短期记忆：记录本轮对话
   - 参考长期记忆：利用历史经验
   ↓
5. 会话结束
   ↓
6. 短期记忆转换为结构化数据 → 存入 Mem0 长期记忆
   ↓
7. 清空短期记忆
```

**多轮对话示例**：

```python
# 第1轮
用户: "我有高血压"
系统: [短期记忆添加用户消息]
系统: [Agent 处理] "高血压需要注意..."
系统: [短期记忆添加助手消息]

# 第2轮
用户: "那我应该吃什么药？"  # 追问
系统: [读取短期记忆] → 获取上一轮"高血压"上下文
系统: [Agent 处理] "根据您的高血压情况，建议..."  # 正确理解追问
```

**注意事项**：
- 未设置 `MEM0_CONFIG["api_key"]` 时，系统会优雅降级，仅使用短期记忆继续工作
- 短期记忆默认使用内存存储，无需配置 Redis
- 长期记忆依赖 Mem0 云服务，需注册账号获取 API Key

## 🏗️ Harness Engineering 融合

**核心理念**："人类设计约束，AI 代理执行" —— 让 AI 在明确约束下自主工作、自我修正。

### 实现的 Harness 原则

| 原则 | MediX 实现 | 位置 |
|------|-----------|------|
| **约束驱动** | YAML 定义 Agent 能力边界，运行时验证 | `constraints/` |
| **自动修复** | 输出违规自动添加免责声明、高危警告 | `validation/` |
| **熵管理** | 记忆自动去重和压缩，防止系统膨胀 | `memory/entropy_manager.py` |

### 核心功能

**1. 约束验证**（`constraints/agent_constraints.yaml`）
- 定义每个 Agent 允许的 Skills 和禁止的行为
- 运行时自动验证 Skill 调用和输出内容
- 违规时记录警告日志

**2. 自动修复**（`validation/auto_fixer.py`）
- 缺少免责声明 → 自动添加
- 高危症状（胸痛、呼吸困难等）→ 自动添加就医提醒

**3. 熵管理**（`memory/entropy_manager.py`）
- 自动去重重复消息（基于 MD5 哈希）
- 自动压缩历史对话（保留最近消息，压缩早期消息为摘要）
- 熵估算和优化建议

### 验证

运行完整测试套件（包含 Harness 测试）：
```bash
python examples/test_all.py
```

---

## 📚 统一知识库

- **向量数据库**: Milvus Lite（本地文件，无需服务器）
- **Embedding 模型**: BAAI/bge-small-zh-v1.5（中文，512维）
- **数据存储**: `knowledge/data/documents/` (txt 文档)
- **初始化**: `python knowledge/scripts/import_hardcoded_data.py`

## 📅 开发路线图

### ✅ 阶段 1: 基础框架（已完成）

- [x] 项目目录结构
- [x] 配置管理（复用config.py）
- [x] LLM客户端（LLM集成，支持 function calling）
- [x] **Skill 注册系统**（SkillRegistry）
- [x] **Agent Loop**（LLM 驱动的 Skill 调用循环）
- [x] **3个医疗 Skills**（知识库搜索、风险评估、症状分析）
- [x] BaseAgent（支持 Skill 注册和执行）
- [x] ConsultationAgent（支持 Skill 调用）
- [x] 主入口 main.py（交互式+单次查询）
- [x] 基础示例和测试

### ✅ 阶段 2: Agent Swarm 群体智能（已完成）

- [x] **SharedContext**（共享环境 - 类似蚁群信息素）
- [x] **Events**（事件驱动通信）
- [x] **LeadAgent**（任务分解和结果汇总）
- [x] **DiagnosticAgent**（症状诊断 Worker）
- [x] **ResearchAgent**（文献检索 Worker）
- [x] **AgentIdentity**（Agent 身份）
- [x] **SessionSummary**（会话总结和经验提取）
- [x] **SwarmCoordinator**（智能路由）
- [x] 自主任务认领机制
- [x] 并行执行支持
- [x] 完全向后兼容

### ✅ 阶段 3: 记忆系统增强（已完成）

- [x] Agent Identity Files（IDENTITY.md）
- [x] Session Summaries（会话总结）
- [x] **短期记忆**（会话级对话历史，支持内存/Redis）
- [x] **长期记忆**（Mem0云服务集成，向量相似度搜索）
- [x] 跨会话记忆检索
- [x] 相似案例推荐
- [x] 优雅降级（Mem0不可用时系统继续工作）

### ✅ 阶段 4: Skills 扩展（已完成）

- [x] **ConsultationAgent Skills 扩展**
  - [x] `recommend_lifestyle`: 饮食、运动、睡眠、用药的具体建议
  - [x] 建议数据库：高血压、糖尿病、感冒、冠心病、哮喘等常见疾病

- [x] **DiagnosticAgent Skills 扩展**
  - [x] `disease_code`: ICD-10编码查询和疾病分类
  - [x] ICD-10数据库：20+常见疾病编码和分类信息

- [x] **ResearchAgent Skills 扩展**
  - [x] `clinical_guideline`: 临床指南和专家共识检索
  - [x] 指南数据库：10+中华医学会权威指南

- [x] **测试和文档**
  - [x] Skills 单元测试（`examples/test_all.py`）
  - [x] 阶段 4测试套件（3个测试用例）

### ✅ 阶段 5: DeepResearch（已完成）

- [x] **网络搜索模块**（WebSearchTool）
  - [x] DuckDuckGo 搜索API集成
  - [x] 医学领域网站白名单
  - [x] 重试机制和错误处理
  - [x] 网页内容抓取和清洗

- [x] **本地知识库**（KnowledgeBase）
  - [x] Qdrant 向量数据库集成
  - [x] Sentence Transformers embedding（BAAI/bge-small-zh-v1.5，统一使用中文模型）
  - [x] 双模式支持（Qdrant / 内存存储）
  - [x] 向量相似度检索

- [x] **证据综合器**（EvidenceSynthesizer）
  - [x] 多来源信息整合
  - [x] LLM 驱动的证据综合
  - [x] 结构化报告生成（关键发现、证据等级、置信度）
  - [x] 信息冲突识别

- [x] **深度研究工作流**（DeepResearchWorkflow）
  - [x] 多步骤研究流程（查询规划 → 并行搜索 → 证据综合 → 质量验证）
  - [x] LLM 驱动的查询拆解
  - [x] 并行执行（asyncio.gather）
  - [x] 迭代式研究支持

- [x] **ResearchAgent 集成**
  - [x] 注册 `deep_research` 工具
  - [x] 更新 system prompt
  - [x] 自动判断是否需要 DeepResearch

- [x] **测试和文档完善**
  - [x] 核心组件单元测试（使用模拟数据）
  - [x] 端到端集成测试（18个测试用例全部通过）
  - [x] 集成到统一测试套件（examples/test_all.py）

### ✅ 阶段 6: 统一知识库架构（已完成）

- [x] **Milvus 向量数据库集成**
  - [x] Milvus Lite 本地部署
  - [x] BAAI/bge-small-zh-v1.5 中文 Embedding 模型
  - [x] 512维向量，COSINE 相似度

- [x] **数据迁移**
  - [x] 生活方式建议（5种疾病）
  - [x] ICD-10疾病编码（20+疾病）
  - [x] 临床指南（10+指南）
  - [x] 数据导入脚本（knowledge/scripts/import_hardcoded_data.py）

- [x] **Skills 重构**
  - [x] `search_knowledge` → Milvus 语义检索
  - [x] `recommend_lifestyle` → Milvus 检索生活方式建议
  - [x] `disease_code` → Milvus 检索 ICD-10 编码
  - [x] `clinical_guideline` → Milvus 检索临床指南

- [x] **语义检索能力**
  - [x] 模糊查询支持（"血压高" → "高血压"）
  - [x] 按类型过滤（lifestyle, disease_classification, clinical_guideline）
  - [x] 相似度评分

### ✅ 阶段 7: Harness Engineering 融合（已完成）

**核心理念**："人类设计约束，AI代理执行" —— 让 AI 在明确的约束下自主工作、自我修正。

#### Phase 1: 约束系统（Explicit Constraint System）

**目标**：将隐式的规则显式化为可验证的约束

- [x] **约束定义文件**
  - `constraints/agent_constraints.yaml`: 定义 Agent 能力边界、允许的工具、输出约束
  - `constraints/swarm_constraints.yaml`: 定义 Swarm 协作规则、任务分解规则、Agent 选择规则

- [x] **约束验证器**（`constraints/validator.py`）
  - `validate_tool_call()`: 验证工具调用是否允许
  - `validate_output()`: 验证输出是否符合约束（免责声明、高危警告等）
  - `validate_task_decomposition()`: 验证任务分解是否合理
  - `get_required_agents()`: 根据问题推荐必须包含的 Agent

- [x] **自动修复器**（`validation/auto_fixer.py`）
  - `fix_missing_disclaimer()`: 自动添加免责声明
  - `fix_high_risk_warning()`: 自动添加高危症状警告
  - `fix_output()`: 综合修复输出违规

- [x] **非侵入式集成**
  - 在 `core/agent_loop.py` 注入约束检查（工具调用前、输出生成后）
  - 不修改现有 Agent 代码，保持向后兼容
  - 优雅降级：约束模块不存在时系统继续工作

## 🤝 技术架构

### Agent Loop (Think-Act-Observe)

```
┌─────────┐     ┌────────┐     ┌──────────┐
│  Think  │ ──> │  Act   │ ──> │  Observe │
└─────────┘     └────────┘     └──────────┘
     ↑                               │
     └───────────────────────────────┘
```

### Skills 直达架构

```
用户问题
   ↓
【原子查询】→ 直接调用 Skills → Milvus/业务逻辑
   │                ↓
   │         OpenAI Format
   │
   └─【复杂问题】
          ↓
   SwarmCoordinator（智能路由）
          ↓
     LeadAgent（分解任务）
          ↓
    发布到 SharedContext（共享环境）
          ↓
    ┌─────┴─────┬────────┐
    ↓           ↓        ↓
ConsultAgent DiagAgent ResearchAgent
（SkillRegistry）（直达 LLM）（并行执行）
    │           │        │
    └───────────┴────────┘
          ↓
    LeadAgent（汇总结果）
          ↓
   SessionSummary（学习）
```

**核心原理**：
- ✅ Skills → OpenAI Format
- ✅ SkillRegistry 统一管理（注册、执行、转换）
- ✅ Agent 注册所有 Skills，根据任务自主选择
- ✅ Agent 通过"信息素"（SharedContext）间接通信
- ✅ 去中心化协作，整体能力涌现

### Agent Swarm 群体智能

**关键特性**：去中心化、自组织、涌现智能

**工作流程**：
1. 简单问题 → 单 Agent（快速响应）
2. 复杂问题 → LeadAgent 分解任务
3. Worker Agents 自主认领（基于能力匹配）
4. 并行执行（每个 Agent 自主选择 Skills）
5. LeadAgent 汇总结果
6. SessionSummary 学习总结

### 记忆系统架构

```
┌────────────────────────────────────┐
│  短期记忆（会话级，内存/Redis）     │
│  - 对话历史（messages）            │
│  - 当前会话上下文                  │
│  - 保留时间：60分钟                │
│  存储：内存（默认）或 Redis        │
└────────────────────────────────────┘
           ↕ (会话结束时)
┌────────────────────────────────────┐
│  长期记忆（跨会话，Mem0云服务）    │
│  - 会话总结                        │
│  存储：Mem0 API + 向量数据库       │
└────────────────────────────────────┘
```

## ⚠️ 免责声明

本系统仅供学习和研究使用，不能替代专业医生的诊断和治疗。所有医疗建议仅供参考，如有健康问题请及时就医。

## 📄 许可证

MIT License

## 🙏 致谢

- 基于 [MediX-R1](https://github.com/...) 医学多模态模型
- 使用 [LLM API](https://www.volcengine.com/) 作为LLM后端
- 记忆管理基于 [Mem0](https://mem0.ai/)

---
