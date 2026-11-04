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
- **📡 实时进度**: 科室开始接诊即点亮，完成时更新状态并写入接诊时间线 ✅
- **🚨 急症预检**: 调用模型前先做红旗症状筛查，命中立即提示拨打 120；结合年龄、免疫状态与抗凝用药补充高危规则（如 3 个月以下婴儿发热），并覆盖无尿/少尿、糖尿病急症与孕产期出血 ✅
- **💊 用药安全提示**: 根据主诉、正在使用的药物与过敏史，规则化筛查重复用药、常见相互作用和过敏交叉风险（华法林、他汀、左甲状腺素、喹诺酮/四环素、SSRI、甲氨蝶呤、别嘌醇/硫唑嘌呤、阿片/苯二氮䓬等），在模型回复前先行提示 ✅
- **🧮 临床工具**: BMI（中国成人分类）、CKD-EPI 2021 eGFR、CHA₂DS₂-VASc、HAS-BLED、CURB-65、GCS、Wells DVT/PE、Child-Pugh、PHQ-9 抑郁筛查、GAD-7 焦虑筛查、qSOFA、FIB-4、MELD 与药物相互作用检查，自动带入年龄与性别，结果附适用范围说明；PHQ-9 第 9 题阳性时提示立即寻求专业帮助，评分可保存到健康记录 ✅
- **📉 健康记录**: 记录并编辑血压、体重、血糖、体温与 PHQ-9/GAD-7 评分，可关联患者档案；提供最近指标概览、参考范围提示、历史列表与趋势图，血压按指南分级（正常 / 正常高值 / 1–3 级），支持按时间范围查看、为每位患者设置指标目标上限、超标标记与趋势图目标线，支持 CSV 导入/导出，随整库备份导出与恢复 ✅
- **⏰ 随访提醒**: 为复查、复诊创建带日期的提醒，到期自动标记「已到期」并按时间排序；支持编辑、完成、推迟 7 天与删除，关键操作写入审计日志；入口徽标显示到期数量，可选开启浏览器到期通知 ✅
- **🩺 就诊摘要**: 一键导出患者档案、近期健康指标（含自定义目标与超标标记）、待完成随访与近期会诊的一页式 HTML 摘要，可打印后带去门诊 ✅
- **🧾 结构化补充**: 可选填写年龄、性别、病程、既往史、用药与过敏史，支持保存、重命名与删除常用档案（保存在服务端 SQLite，可跨浏览器使用；旧版本浏览器本地档案会自动迁移），贯穿分诊、单科与多学科会诊 ✅
- **🩺 接诊模式**: 可勾选「仅单科接诊」，跳过多学科协作以降低延迟与用量 ✅
- **📚 知识库管理**: Web 端批量上传 TXT/Markdown/PDF、拖拽上传、在线预览与编辑、按文档类型检索测试、删除文档、一键重建索引（幂等）✅
- **🧾 意见透明化**: 展示各科室原始意见与知识库参考来源 ✅
- **📊 用量统计**: 展示每轮会诊的 Token 消耗与模型调用次数，历史列表汇总总用量，并随 Markdown 导出保留 ✅
- **📈 用量趋势**: 历史抽屉展示近 7 天 Token 柱状图，支持悬停明细、CSV 导出与数据表 ✅
- **💴 费用估算**: 可选配置每百万 Token 单价，在会诊面板、历史汇总与趋势中显示预估值 ✅
- **🎯 月度预算**: 可选设置每月 Token 预算，历史抽屉展示本月进度、剩余额度与超限提醒 ✅
- **⭐ 收藏会诊**: 标记重要会诊并只看收藏，列表、用量汇总与趋势同步过滤 ✅
- **👍 会诊反馈**: 每条意见可标记「有用 / 待改进」，待改进可填写原因；历史抽屉汇总反馈数与好评率，并列出最近待改进反馈，支持导出反馈 CSV，反馈随备份导出与恢复 ✅
- **📈 质量概览**: 历史抽屉汇总近 30 天会诊量、急症警示、用药提示、反馈好评率、平均用时与 Token，并列出常见风险标签 ✅
- **🧾 操作审计**: 记录删除会诊、归档/恢复、导入导出、删除患者档案与健康记录等关键操作，可在系统自检中按动作、关键字与日期范围筛选查看，并按当前筛选导出 CSV ✅
- **🩺 系统自检**: Web 端查看 Python/依赖/配置/知识库、会话数据库与访问保护/限流状态，可一键测试模型连通性、查看操作审计，并按天数清理旧会诊 ✅
- **🧠 记忆系统**: 短期记忆（会话级对话历史）+ 长期记忆（Mem0跨会话记忆）+ **多轮对话上下文利用** ✅
- **🗂️ 会话历史**: SQLite 持久化会诊记录，服务重启后可分页浏览、搜索（命中高亮与正文片段）、时间范围筛选、标签分类与筛选、患者档案筛选、归档/恢复、批量归档/删除/设置标签/导出 Markdown、重命名、复制直达链接、导出、按筛选导出列表 CSV、整库备份与恢复、删除；支持按 `MEDIX_AUTO_ARCHIVE_DAYS` 自动归档旧会话，支持 `?session=` 链接直接打开 ✅
- **💬 会诊操作**: 复制意见、复制 SOAP、导出 Markdown、导出独立 HTML 报告（单文件可打印为 PDF）、打印 / PDF、一键设随访提醒、失败重试、再次会诊、停止生成、草稿与常用问题自动保存 ✅
- **🆚 会诊对比**: 在会诊记录中勾选任意两次会诊即可并排对比标题、标签、用量、急症/用药/来源统计与每轮意见 ✅
- **🎙️ 语音输入**: 支持 Web Speech API 的浏览器可在输入框旁语音听写，实时转成文字并自动保存草稿；不支持时按钮自动隐藏 ✅
- **⌨️ 键盘与无障碍**: 跳到输入框、Ctrl+K 打开会诊记录、Enter 发送 / Shift+Enter 换行、Esc 关闭抽屉、焦点圈定与语义标签；知识库、健康记录、随访提醒、临床工具与自检的状态提示通过 `role="status"` 播报 ✅
- **🌗 深色模式**: 跟随系统偏好，可手动切换并记住选择 ✅
- **🔒 访问保护**: 可选 `MEDIX_ACCESS_TOKEN`，暴露到局域网时保护模型调用与会诊记录；静态资源带 CSP 等安全响应头 ✅
- **🚦 请求限流**: 会诊接口默认每分钟 30 次，超限返回 429 与 Retry-After，可通过 `MEDIX_RATE_LIMIT` 调整 ✅
- **🔁 失败重试**: 429 / 超时 / 5xx 指数退避重试，流式已输出内容后不再重复生成 ✅
- **💾 Milvus 知识库**: 统一知识管理，语义检索，支持模糊查询（"血压高" → "高血压"）✅
- **📲 可安装 PWA**: 提供 manifest、图标与 Service Worker，可添加到桌面；只缓存静态外壳，API 与会诊数据不进入离线缓存 ✅
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

> 用 `--host 0.0.0.0` 暴露到局域网时，建议同时设置 `MEDIX_ACCESS_TOKEN`。设置后除 `/api/status` 外的所有 API 都需要令牌：浏览器首次打开会提示输入，命令行请求可带 `X-Medix-Token` 请求头（也支持 `Authorization: Bearer` 或 `?token=`）。

### 界面说明

| 区域 | 内容 |
|------|------|
| 心电监护带 | 波形随系统状态改变节律：空闲时平缓、会诊中加快、配置或请求异常时转为琥珀/红色 |
| 会诊科室 | 三个科室（全科门诊 / 急诊医学科 / 循证医学中心）的状态，随流式事件逐个点亮 |
| 监护读数 | 接诊方式（单科接诊 / 多学科会诊）、参与科室、完成项目、会诊用时、知识库状态、会诊编号 |
| 接诊记录 | 每一步的分诊与调度日志 |
| 问诊记录 | 用户输入标为「主诉」，答复渲染成带抬头的「多学科会诊意见书」，并自动提取风险等级、诊疗建议和免责声明 |
| 急症警示 | 主诉命中胸痛、呼吸困难、卒中样表现等红旗时，在模型回复前先显示红色警示条；补充信息为 3 个月以下婴儿发热、免疫低下伴发热或抗凝期间头部外伤时同样会触发 |
| 用药安全提示 | 过敏史、重复用药或常见相互作用命中时，在模型回复前显示橙色/黄色提示，并注明不能替代医生或药师判断 |
| 知识库管理 | 会诊面板下方「管理知识库」：查看文档与向量块、上传 TXT/MD/PDF、删除、重建索引 |
| 会诊依据 | 多学科会诊可展开各科室原始意见；使用知识库时展示来源、文件名、相关度与原文片段 |
| 系统自检 | 会诊面板下方「系统自检」：Python、依赖版本、LLM 配置、知识库状态与操作审计，可复制诊断信息、清理旧会诊 |
| 临床工具 | 会诊面板下方「临床工具」：BMI、eGFR（CKD-EPI 2021）、CHA₂DS₂-VASc、HAS-BLED、CURB-65、GCS、Wells DVT/PE、Child-Pugh、PHQ-9、GAD-7、qSOFA、FIB-4、MELD 与药物相互作用检查，自动带入补充信息中的年龄与性别 |
| 健康记录 | 会诊面板下方「健康记录」：录入血压、体重、血糖、体温与 PHQ-9/GAD-7 评分，可关联患者档案，按 7/30/90 天范围查看最近指标概览、历史记录与趋势图，血压自动分级，可设置自定义目标上限，导出/导入 CSV |
| 随访提醒 | 会诊面板下方「随访提醒」：为复查、复诊创建带日期的提醒，到期标记「已到期」，支持完成与删除；入口显示到期数量徽标，可选开启浏览器通知 |
| 就诊摘要 | 会诊面板下方「就诊摘要」：导出档案信息、近期指标、待办随访与近期会诊的一页式 HTML，可打印为 PDF |
| 历史会诊 | 右上角「会诊记录」抽屉：按时间浏览、搜索、打开或删除历史会诊；可查看最近待改进反馈与质量概览；刷新页面自动恢复上次会诊 |
| 意见操作 | 每条意见下方可复制、导出 Markdown、再次会诊；生成过程中可随时停止 |
| 常用问题 | 首页「常见主诉」旁「＋ 存为常用」：把输入框内容保存为常用问题（浏览器本地，最多 8 条，可删除） |
| 会诊对比 | 会诊记录「批量」勾选两次会诊后点「对比」：并排查看元信息、风险统计与每轮意见 |
| 语音输入 | 输入框旁麦克风按钮：语音听写实时转文字，再次点击停止；浏览器不支持时自动隐藏 |
| 外观 | 跟随系统深色偏好，也可用顶栏按钮切换；选择保存在浏览器本地 |

风险提示的配色对齐急诊分诊的四级色标（红 / 橙 / 黄 / 绿），但用的是「风险等级」表述，不作临床分诊结论。

前端代码在 `web/` 目录（原生 HTML/CSS/JS，无需构建步骤）。

浏览器支持时可把页面「安装」到桌面或主屏幕；浏览器触发可安装事件后，会诊面板会显示「安装应用」按钮。Service Worker 只缓存 HTML/CSS/JS/图标等静态外壳；`/api/` 请求不缓存，离线时仍需要本地后端服务才能读取会诊与健康记录，页面会显示离线提示并在恢复连接后自动重新检查服务状态。

### 会话历史与意见导出

- 会诊记录保存在 `data/sessions.db`（SQLite，首次运行自动创建，已加入 `.gitignore`），服务重启后不会丢失
- 会诊时选择了常用档案，本次会诊会自动关联到该档案（只补全一次）；历史抽屉的「档案」筛选可只看某位患者的会诊，用量/质量/反馈/tag 统计也会同步按档案筛选，就诊摘要按档案汇总
- 重命名档案会同步迁移历史会诊、随访提醒、指标目标与健康记录；删除档案后历史会诊仍保留，可在「档案」筛选中以「（已删除）」形式查看
- 右上角「会诊记录」支持搜索、打开、重命名、导出和删除历史会诊，点击条目可恢复完整问诊记录
- 在会诊记录抽屉点「批量」，勾选会诊后可「导出选中」为合并的 Markdown 文件
- 历史抽屉的「列表 CSV」按当前搜索与筛选条件导出会话列表（含档案、标签、Token 与调用次数），便于表格分析
- 每条会诊意见下方提供「复制意见」「导出 Markdown」「导出 HTML 报告」「再次会诊」
- 每条会诊意见下方可标记「有用 / 待改进」，再次点击同一按钮可取消；反馈保存在该条消息的元数据中，随整库备份导出与恢复
- 多学科会诊的汇总阶段，以及单 Agent 使用工具后的强制收尾，都支持流式输出；直接作答仍为一次性返回
- 生成过程中点「停止」会中断本次请求；服务端在客户端断开后取消后台任务

### 随访提醒

- 会诊面板下方「随访提醒」：填写提醒内容与日期即可创建，日期默认一周后
- 到期（含当天）的提醒会显示「已到期」标记，并在列表顶部汇总数量
- 提醒支持「编辑」「完成」「推迟」（+7 天）与「删除」，可勾选「显示已完成」查看完成项并「恢复」为待完成；新增/修改/完成/推迟/恢复/删除都会写入操作审计
- 提醒与当前会诊编号关联保存；创建时会记录当前选中的患者档案，列表显示所属档案
- 抽屉内可按档案筛选提醒；删除档案后其历史提醒仍以「（已删除）」显示并可筛选
- 入口按钮显示已到期数量；在抽屉中可开启浏览器通知，到期当天打开页面时提示一次（依赖浏览器通知权限）

### 健康指标目标

- 「健康记录」抽屉中点击「设置目标」可为当前档案与指标设置上限（血压填收缩压/舒张压）
- 记录超过目标时数值旁显示「高于目标」徽标，趋势图绘制红色虚线目标线；「清除」后恢复为无目标状态
- 目标按档案保存到服务端，随整库备份导出与恢复

### 会诊对比

- 勾选恰好两次会诊后点「对比」
- 左右两栏分别展示标题、更新时间、消息数、Token、标签，以及急症预检/用药安全/参考来源/科室意见统计
- 每轮会诊按顺序并排展示主诉与会诊意见，便于复诊前后对照

### 就诊摘要

- 会诊面板下方「就诊摘要」：按当前患者档案汇总基本信息、近期 30 条健康指标（列出目标并标注超标）、该档案的待完成随访与最近 5 次会诊
- 摘要为单文件 HTML，可离线打开并一键打印或另存为 PDF，方便就诊时与医生沟通
- 摘要只读取本机记录，不会上传到任何第三方

### 知识库管理

- 会诊面板下方「管理知识库」可查看内置/上传文档、向量块数量和存储目录
- 检索测试支持按文档类型筛选（生活方式 / 疾病分类 / 临床指南 / 通用或上传），点击检索结果可直接预览文档
- 支持上传 TXT / Markdown / PDF（单个 ≤ 5 MB），可一次选择多个文件或拖拽多个文件，全部保存后只重建一次索引
- 重建会先清空旧 collection 再导入，重复执行不会产生重复向量块
- 用户上传的文档保存在 `knowledge/data/uploads/`（已加入 `.gitignore`，不会提交到仓库）
- 命令行等价操作：`python knowledge/scripts/import_hardcoded_data.py`（默认重建；`--append` 追加）

### 各科室意见与参考来源

- 多学科会诊意见下方可展开「各科室意见」，查看每个 Agent 的原始分析、耗时、工具调用次数与推理轮数
- 使用知识库的 Skill 会返回来源元数据，界面展示来源、文档名、相关度与原文片段
- 单条意见导出 Markdown / HTML 报告时也会包含各科室意见、参考来源、补充信息、急症预检与用药安全提示；HTML 报告为单文件、无外部依赖，可离线打开并一键打印或另存为 PDF
- 科室意见、来源、建议与急症预检结果会随会话保存，刷新页面或重启服务后仍可查看；整段会诊导出同样包含这些信息
- 会诊失败时提供「打开系统自检」入口，便于排查配置、余额或服务问题

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
| GET | `/api/status` | 健康检查、模型配置、鉴权/费用配置、知识库状态 |
| POST | `/api/chat` | 同步问答，返回完整结果（支持 `context` 与 `enable_swarm`） |
| POST | `/api/chat/stream` | SSE 流式问答：分诊、急症预检、用药安全提示、科室开始/完成、汇总 Token 与最终结果 |
| GET | `/api/auth/verify` | 校验访问令牌（受保护） |
| POST | `/api/sessions` | 新建会话 |
| GET | `/api/sessions` | 历史列表：`q` 搜索、`limit`/`offset` 分页、`favorite=1` 收藏筛选、`days=N` 时间范围、`archived=true` 查看已归档、`tag=标签` 标签筛选、`profile=档案` 患者档案筛选、用量与反馈汇总 |
| GET | `/api/tags` | 未归档会话的标签与数量 |
| GET | `/api/session-profiles` | 会话中出现过的档案名及数量（含已删除档案留下的历史名字） |
| GET | `/api/sessions/export` | 导出全部会话、消息与元数据为 JSON |
| GET | `/api/sessions/export.csv` | 按 `q`/`favorite`/`days`/`archived`/`tag`/`profile` 筛选导出会话列表 CSV（带 BOM） |
| POST | `/api/maintenance/purge` | 删除指定天数以前的会诊及其消息（不可恢复，写入审计） |
| POST | `/api/sessions/import` | 导入备份 JSON（已存在的会话编号跳过） |
| GET | `/api/sessions/{id}/messages` | 读取会话消息（优先读 SQLite；`limit=0` 返回全部） |
| GET | `/api/profiles` | 常用患者档案列表（服务端持久化） |
| POST | `/api/profiles` | 新增或覆盖患者档案（`name`、`context`） |
| PATCH | `/api/profiles/{name}` | 重命名患者档案并同步健康指标 |
| DELETE | `/api/profiles/{name}` | 删除患者档案 |
| POST | `/api/tools/bmi` | 计算 BMI（身高 cm、体重 kg） |
| POST | `/api/tools/egfr` | CKD-EPI 2021 eGFR（年龄、性别、血肌酐、单位） |
| POST | `/api/tools/chads-vasc` | CHA₂DS₂-VASc 评分（年龄、性别与危险因素） |
| POST | `/api/tools/curb65` | CURB-65 评分（年龄、意识、尿素氮、呼吸频率与血压） |
| POST | `/api/tools/phq9` | PHQ-9 抑郁筛查评分（9 个 0–3 答案，第 9 题阳性时 urgent） |
| POST | `/api/tools/gad7` | GAD-7 焦虑筛查评分（7 个 0–3 答案） |
| POST | `/api/tools/has-bled` | HAS-BLED 房颤抗凝出血风险评分（年龄与危险因素） |
| POST | `/api/tools/gcs` | GCS 格拉斯哥昏迷评分（睁眼/语言/运动） |
| POST | `/api/tools/wells-dvt` | Wells 深静脉血栓概率评分 |
| POST | `/api/tools/child-pugh` | Child-Pugh 肝硬化严重程度评分 |
| POST | `/api/tools/wells-pe` | Wells 肺栓塞概率评分 |
| POST | `/api/tools/interactions` | 药物相互作用检查（药物列表 + 可选过敏史） |
| POST | `/api/tools/qsofa` | qSOFA 脓毒症风险筛查（呼吸频率、收缩压、意识状态） |
| POST | `/api/tools/fib-4` | FIB-4 肝纤维化指数（年龄、AST、ALT、血小板） |
| POST | `/api/tools/meld` | 经典 MELD 终末期肝病评分（胆红素、INR、肌酐、透析） |
| GET | `/api/metrics` | 健康指标记录（可按 `profile` / `metric` / `days` 筛选，默认最近 100 条） |
| POST | `/api/metrics` | 新增血压、体重、血糖或体温记录 |
| PATCH | `/api/metrics/{id}` | 更新健康指标记录 |
| DELETE | `/api/metrics/{id}` | 删除健康指标记录 |
| GET | `/api/metrics/export.csv` | 导出当前患者档案/指标类型的健康记录 CSV（带 BOM） |
| POST | `/api/metrics/import` | 从导出的 CSV 格式导入健康记录，重复记录自动跳过 |
| GET | `/api/metric-targets` | 自定义健康指标目标（可按 `profile` 筛选） |
| PUT | `/api/metric-targets` | 设置或覆盖目标上限（血压需同时提供舒张压） |
| DELETE | `/api/metric-targets/{metric}` | 删除某档案某指标的目标（`profile` 查询参数） |
| GET | `/api/reminders` | 随访提醒列表（默认未完成，`include_done=true` 含已完成，`profile` 按档案筛选） |
| POST | `/api/reminders` | 新增随访提醒（内容 + 日期，可选 `profile` 关联档案；日期支持 `YYYY-MM-DD` 或 ISO 时间） |
| POST | `/api/reminders/{id}/complete` | 把随访提醒标记为完成 |
| POST | `/api/reminders/{id}/reopen` | 把已完成的随访提醒恢复为待完成 |
| PATCH | `/api/reminders/{id}` | 修改随访提醒的内容、日期或备注 |
| POST | `/api/reminders/{id}/snooze` | 把随访提醒推迟 N 天（`days` 查询参数，默认 7） |
| DELETE | `/api/reminders/{id}` | 删除随访提醒 |
| PATCH | `/api/sessions/{id}` | 重命名会话 |
| PATCH | `/api/sessions/{id}/favorite` | 收藏或取消收藏 |
| PATCH | `/api/sessions/{id}/tags` | 覆盖会话标签（最多 5 个，每个最长 20 字） |
| PATCH | `/api/sessions/{id}/archive` | 归档或移出归档（归档不删除会话） |
| PATCH | `/api/bulk/sessions/archive` | 批量归档/移出归档（最多 200 个） |
| POST | `/api/bulk/sessions/delete` | 批量删除会诊（最多 200 个） |
| PATCH | `/api/bulk/sessions/tags` | 批量覆盖会话标签（最多 200 个） |
| POST | `/api/sessions/{id}/feedback` | 记录会诊意见反馈（`message_id`、`rating`：1 有用 / -1 待改进 / 0 清除、可选 `comment`） |
| GET | `/api/feedback/recent` | 最近的会诊反馈（默认 `rating=-1` 待改进，`profile` 按档案筛选），用于质量复核 |
| GET | `/api/feedback/export.csv` | 导出反馈 CSV（带 BOM，可按 `rating` 筛选） |
| GET | `/api/insights/quality` | 近 N 天质量概览（急症、用药提示、反馈、平均用时与 Token，`profile` 按档案筛选） |
| DELETE | `/api/sessions/{id}` | 删除会话 |
| GET | `/api/usage/daily` | 近 N 天 Token 用量与调用次数（默认 7 天，可用 `favorite=1`/`profile` 筛选） |
| GET | `/api/usage/month` | 本月用量、预算进度与是否超限 |
| GET | `/api/usage/export.csv` | 导出近 N 天按日用量 CSV（含 BOM 与合计行，支持 `favorite`/`profile` 筛选） |
| GET | `/api/knowledge` | 知识库文档与向量块统计 |
| GET | `/api/knowledge/search` | 知识库检索测试（返回来源、相关度与片段，可用 `filter_type` 按文档类型过滤） |
| GET | `/api/knowledge/documents/{filename}/preview` | 预览内置/上传文档前 4000 字（`full=1` 返回全文） |
| PUT | `/api/knowledge/documents/{filename}` | 在线更新上传的 TXT/Markdown 并重建索引 |
| POST | `/api/knowledge/rebuild` | 重建知识库索引（幂等） |
| POST | `/api/knowledge/upload` | 上传文档（multipart，TXT/MD/PDF） |
| POST | `/api/knowledge/upload-batch` | 一次上传最多 10 个文档，全部保存后只重建一次索引 |
| DELETE | `/api/knowledge/documents/{filename}` | 删除上传文档并重建 |
| GET | `/api/diagnose` | 轻量环境自检（Python/依赖/配置/知识库/运行状态） |
| GET | `/api/audit` | 操作审计，支持 `action`、`q`、`start`、`end`、`limit` 筛选，返回可用动作列表 |
| GET | `/api/audit/export.csv` | 按同样筛选条件导出操作审计 CSV（带 BOM） |
| POST | `/api/diagnose/llm` | 用一次最小请求测试模型连通性 |

`/api/chat` 与 `/api/chat/stream` 的最终结果包含 `usage`（`prompt_tokens` / `completion_tokens` / `total_tokens`）与 `llm_calls`，分别汇总任务分解、各科室和最终汇总阶段的用量。`GET /api/sessions` 支持 `limit` / `offset` 分页；`summary` 汇总全部匹配会话（`sessions` 总数、`loaded` 已加载数、轮数、总 Token、调用次数、反馈数与好评率），每条会话也带有各自的 `usage` 与 `llm_calls`。流式接口会在模型调用前额外推送 `triage` 与 `medication_safety` 事件，最终结果的 `medication_safety.alerts` 会随会诊记录保存。`/api/chat` 与 `/api/chat/stream` 支持 `patient_profile`（常用档案名称），成功后会把会话关联到该档案，供历史筛选与就诊摘要使用。

设置 `MEDIX_ACCESS_TOKEN` 后，除 `/api/status` 外的 `/api/*` 都要求携带令牌（`X-Medix-Token` 请求头、`Authorization: Bearer` 或 `?token=`），失败返回 401；`GET /api/auth/verify` 可用于校验令牌。

`/api/chat` 的 `context` 支持 `age`、`sex`、`duration`、`history`、`medications`、`allergies` 等字段，会注入任务分解和所有科室的提示词，并随会诊记录保存。`enable_swarm=false` 时本次请求跳过多学科协作、直接单科接诊。

`GET /api/sessions/export` 的 JSON（`version: 1`）可直接用于 `POST /api/sessions/import`；导出会同时包含常用患者档案、健康指标、自定义目标、随访提醒与归档状态。导入时只接受 user/assistant 消息，已存在的会话编号、档案名称与相同健康记录会跳过，不覆盖现有记录。单个备份最多 5000 个会话、每个会话最多 10000 条消息。会诊接口默认限流 30 次/分钟，超限返回 429 与 `Retry-After`。

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

`MEDIX_TIMEOUT` 是单次模型请求超时（秒），可不填，默认 60。服务会把 401（Key 无效）、402（余额不足）、429（限流）、超时和网络错误转换成可读提示，其中 401/402 不会反复重试。`MEDIX_RATE_LIMIT` 控制 `/api/chat` 与 `/api/chat/stream` 每分钟允许的请求数，默认 30，设为 0 关闭限流。`MEDIX_PRICE_INPUT` / `MEDIX_PRICE_OUTPUT` 是每百万输入/输出 Token 的单价，配合 `MEDIX_CURRENCY`（默认 `¥`）在界面显示预估费用，未配置时不显示。`MEDIX_MONTHLY_TOKEN_BUDGET` 设置每月 Token 预算（0 或留空不启用），历史抽屉会显示本月进度和超限提醒。`MEDIX_AUTO_ARCHIVE_DAYS` 设置自动归档旧会话的天数（0 或留空关闭），归档只隐藏记录、不删除数据，可在「已归档」中恢复。

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

#### 保护 API Key

- `.env` 已被 `.gitignore` 忽略，仓库里只保留空占位符的 `.env.example`；`.env.*` 变体、`*.pem`、`*.key`、`id_rsa*` 等密钥文件同样不会入库。
- 日志、异常提示和接口错误响应统一经过 `core/security.py` 脱敏：完整 Key 只会显示为 `sk-a***k1L2` 这样的首尾形式，即使模型 SDK 在报错文本里带出 Key 也不会原样输出。
- `tests/test_secret_hygiene.py` 会在 CI 中扫描所有已跟踪文件，发现 `sk-`、`ghp_`、`AKIA` 或私钥头等疑似密钥时直接失败；同时校验 `.env` 被忽略且未被跟踪。
- 如果 Key 已经进入 Git 历史，仅删除文件不够：请立即在服务商控制台轮换 Key，再用 `git filter-repo` 清理历史并强制推送（私有仓库同样建议处理）。
- 通过 `--host 0.0.0.0` 暴露到局域网时，建议同时设置 `MEDIX_ACCESS_TOKEN`，避免任何人直接消耗你的模型额度。


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

启动 Web 服务后，也可以在会诊面板下方点击「系统自检」，查看同样的信息并一键复制诊断结果。

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
│   ├── clinical_tools.py              # BMI/eGFR/CHA₂DS₂-VASc/HAS-BLED/CURB-65/PHQ-9/GAD-7
│   ├── context.py                     # 结构化问诊上下文
│   ├── diagnostics.py                 # 环境自检
│   ├── llm_client.py                  # LLM 客户端
│   ├── medication_safety.py           # 用药安全规则引擎
│   ├── retention.py                   # 自动归档策略
│   ├── security.py                    # 密钥脱敏与日志保护
│   ├── serialization.py               # JSON 安全序列化
│   ├── skill_loader.py                # 动态加载 Skills
│   ├── skill_registry.py              # Skill 注册表（直接转 OpenAI format，无 Tool 层）
│   ├── state_manager.py               # 状态管理
│   └── triage.py                      # 急症红旗预检
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
│   ├── session_store.py               # SQLite 会话/档案/健康记录/随访提醒/审计
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
│   ├── app.js
│   ├── manifest.webmanifest           # PWA manifest
│   ├── sw.js                          # Service Worker（只缓存静态外壳）
│   └── icons/                         # PWA 图标（192/512）
│
├── tests/                             # 离线单元测试（无需 API Key）
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
