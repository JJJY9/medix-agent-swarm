# MediX多智能体医疗助手

[![CI](https://github.com/JJJY9/medix-agent-swarm/actions/workflows/ci.yml/badge.svg)](https://github.com/JJJY9/medix-agent-swarm/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB.svg)

基于 Skills-Agent 两层架构的多智能体协作医疗助手系统，融合 Agent Loop、Agent Swarm、记忆管理和 Milvus 知识库。

## 📌 项目说明

本项目及其代码（Agent 架构、Skills、Swarm 协作、记忆系统、本地知识库、Web 会诊界面与健康数据功能）由仓库作者原创实现并持续改进。此前 README 中关于代码来源的说明有误，现已更正。

仓库当前为**公开**仓库，采用 MIT License；是否保持公开由作者决定。

### 主要实现

| 方向 | 说明 |
|------|------|
| 多智能体协作 | Skills-Agent 两层架构、Agent Loop、Agent Swarm 并行协作与 SSE 流式会诊 |
| 记忆与知识库 | 短期/长期记忆、Mem0 接入、Milvus 知识库与本地向量库降级 |
| Web 会诊界面 | FastAPI + 原生 HTML/CSS/JS，含会诊面板、健康记录、随访提醒、临床工具与系统自检 |
| 健康数据管理 | 指标/目标、症状日记、用药清单与打卡（含库存与有效期提醒）、疫苗接种记录、就诊预约、就诊记录、化验结果、过敏史、慢病清单、复查计划、健康小结、就诊摘要，以及 CSV 与整库备份导入导出 |
| Windows 适配 | 控制台编码、路径解析与向量库降级等平台适配 |
| 工程配套 | `.env.example`、完整测试套件、诊断与端到端验证脚本 |

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
- **🧮 临床工具**: BMI（中国成人分类）、CKD-EPI 2021 eGFR、CHA₂DS₂-VASc、HAS-BLED、CURB-65、GCS、Wells DVT/PE、Child-Pugh、PHQ-9 抑郁筛查、GAD-7 焦虑筛查、qSOFA、FIB-4、APRI、MELD、eAG 平均血糖、STOP-BANG 睡眠呼吸暂停筛查、Padua 静脉血栓风险、ABCD2 TIA 卒中风险、Morse 跌倒风险与药物相互作用检查，自动带入年龄与性别，eGFR/FIB-4/MELD 可从最近化验结果一键带入并自动换算单位，APRI 可带入 AST/血小板并按化验单带入 AST 正常上限，eAG 可带入糖化血红蛋白（支持 mmol/mol 换算），Child-Pugh 可按胆红素/白蛋白/INR 自动换算并映射评分，CURB-65 可带入尿素氮与最近血压，结果附适用范围说明；PHQ-9 第 9 题阳性时提示立即寻求专业帮助，评分可保存到健康记录 ✅
- **📉 健康记录**: 记录并编辑血压、体重、血糖、体温与 PHQ-9/GAD-7 评分，可关联患者档案；提供最近指标概览、参考范围提示、历史列表与趋势图，血压按指南分级（正常 / 正常高值 / 1–3 级），支持按时间范围查看、为每位患者设置指标目标、按方向标注超标/低于目标与趋势图目标线，支持 CSV 导入/导出；血压可标记晨起/睡前，血糖可标记空腹/餐后/睡前/随机，便于区分家庭血压与餐后血糖；录入后自动识别危急值（血压 ≥180/120 或 <90/60、血糖 ≤3.9 或 ≥16.7 mmol/L、体温 ≥39 或 ≤35 °C、心率 <50 或 >120、血氧 <90%）并红色提示；含症状日记、用药清单与打卡（按频次估算近 7/30/90 天依从率，可登记剩余库存、单位、低库存阈值、有效期与开始/停药日期，缺货/临期时提示并可一键补货或创建提醒）、健康月历、家庭指标对比、近 7/30/90 天健康小结（含目标达标、会诊次数与连续记录天数），可导出单人或家庭健康档案 Markdown，支持疫苗接种记录、下一剂提醒、就诊预约、就诊记录、化验结果与异常/危急值标记、结构化过敏史、慢病清单、复查计划，随整库备份导出与恢复 ✅
- **👨‍👩‍👧 家庭健康总览**: 首页按家庭成员汇总最近指标（含目标未达标与危急值）、用药打卡、用药库存/效期提醒、待办/逾期提醒、近 7 天症状、疫苗接种、下次就诊、化验异常、复查计划、最近就诊、慢病清单与会诊次数（附最近一次会诊风险等级），标注今日是否已有记录与最需关注的一条提示（危急值优先，其次逾期提醒、复查逾期、重度症状、化验异常与库存提醒），成员按关注级别排序、同级内按最近活动排序，点击成员即可切换当前档案，可复制摘要或导出家庭总览 CSV ✅
- **⏰ 随访提醒**: 为复查、复诊创建带日期的提醒，到期自动标记「已到期」并按时间排序；支持编辑、完成、推迟 1/3/7/14 天、一键清理已完成与删除，用药提醒自动标记，关键操作写入审计日志；入口徽标显示到期数量，可选开启浏览器到期通知 ✅
- **🩺 就诊摘要**: 一键导出患者档案、近 7 天小结、近期健康指标（含自定义目标与超标/低于目标标记）、用药清单与打卡、近期症状、待完成随访、就医资料与近期会诊的一页式 HTML 摘要，可打印后带去门诊 ✅
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
- **🩺 系统自检**: Web 端查看 Python/依赖/配置/知识库、会话数据库与各数据表记录数、访问保护/限流状态，可一键测试模型连通性、查看操作审计，并按天数清理旧会诊 ✅
- **🧠 记忆系统**: 短期记忆（会话级对话历史）+ 长期记忆（Mem0跨会话记忆）+ **多轮对话上下文利用** ✅
- **🗂️ 会话历史**: SQLite 持久化会诊记录，服务重启后可分页浏览、搜索（命中高亮与正文片段）、时间范围筛选、标签分类与筛选、患者档案筛选、归档/恢复、批量归档/删除/设置标签/导出 Markdown、重命名、复制直达链接、导出、按筛选导出列表 CSV、整库备份、本地立即备份与本地备份恢复、删除，列表显示会诊风险等级；支持按 `MEDIX_AUTO_ARCHIVE_DAYS` 自动归档旧会话，支持 `?session=` 链接直接打开 ✅
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

**测试状态**：原始项目的 26 个端到端用例保持通过；本仓库新增的离线测试（`pytest tests`）当前 **800+ 个用例**全部通过（无需 API Key，CI 覆盖 Python 3.10/3.12 与 Linux/Windows）。

原始项目验证项：

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
python api_server.py --host 0.0.0.0       # 允许局域网访问（需设置 MEDIX_ACCESS_TOKEN）
python api_server.py --reload             # 开发模式，改代码自动重载
```

> 用 `--host 0.0.0.0` 暴露到局域网时，**必须**同时设置 `MEDIX_ACCESS_TOKEN`：未设置令牌时服务会拒绝启动，避免同网段任何设备无凭证读取全部问诊与影像、或调用删除接口。确实在完全可信的内网使用时，可以加 `--allow-insecure-lan`（或设置 `MEDIX_ALLOW_INSECURE_LAN=1`）显式放行，启动时会打印醒目警告。该限制对所有启动方式生效：即使用 `uvicorn api_server:app --host 0.0.0.0` 或 gunicorn 直接启动，未设置令牌时的非本机 API 请求也会被 403 拒绝（`MEDIX_ALLOW_INSECURE_LAN=1` 同样放行）。设置令牌后除 `/api/status` 外的所有 API 都需要令牌：浏览器首次打开会提示输入，命令行请求可带 `X-Medix-Token` 请求头（也支持 `Authorization: Bearer`）。为了不把令牌写进访问日志，不再支持 `?token=` 查询参数。
>
> 服务会校验 Host 头以防 DNS rebinding：IP 地址、`localhost` 与 `testserver` 默认允许；如需通过局域网域名访问，请设置 `MEDIX_ALLOWED_HOSTS=medix.local`（逗号分隔多个域名）。
>
> 注意：绑定 `127.0.0.1` 不能防护端口转发。使用 `ssh -R`、socat、frp 等透明 TCP 隧道把服务暴露出去时，请求不会携带任何代理头，服务端无法与真正的本机请求区分；任何隧道外露前都必须设置 `MEDIX_ACCESS_TOKEN`。经本机反向代理访问时，携带 `X-Forwarded-For` / `X-Real-IP` / `Forwarded` 的请求默认按远程处理；确需按代理头里的真实客户端地址判定本机，请设置 `MEDIX_TRUST_PROXY_HEADERS=1`，并确保代理用 `$remote_addr` 覆盖 `X-Forwarded-For`（如 nginx `proxy_set_header X-Forwarded-For $remote_addr;`），同时保留 Host 端口（如 `proxy_set_header Host $http_host;`；若用 `$host` 去掉非默认端口，浏览器同源写请求会被拒绝）。只设置 `X-Forwarded-Proto` 等头而不提供客户端地址的代理，会被视为远程请求（未设置令牌时返回 403）。信任模型按**单跳**本机反向代理设计：多级本机代理（如 tailscale serve/Caddy → nginx → 应用）时，请显式设置 `MEDIX_TRUST_PROXY_HEADERS=1`，并由**最后一跳**用 `proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;` 追加真实地址，否则远程客户端会被当作本机；这类部署必须设置 `MEDIX_ACCESS_TOKEN`。应用启动时会关闭 uvicorn 的 `proxy_headers`；若用 `uvicorn api_server:app` 或 gunicorn 直接托管，请加 `--no-proxy-headers`（gunicorn 设置 `FORWARDED_ALLOW_IPS=''`），让代理头信任只由 `MEDIX_TRUST_PROXY_HEADERS` 决定。

### 界面说明

| 区域 | 内容 |
|------|------|
| 心电监护带 | 波形随系统状态改变节律：空闲时平缓、会诊中加快、配置或请求异常时转为琥珀/红色 |
| 会诊科室 | 三个科室（全科门诊 / 急诊医学科 / 循证医学中心）的状态，随流式事件逐个点亮 |
| 监护读数 | 接诊方式（单科接诊 / 多学科会诊）、参与科室、完成项目、会诊用时、知识库状态、会诊编号 |
| 接诊记录 | 每一步的分诊与调度日志 |
| 首页速览 | 未开始会诊时，输入框上方显示卡片：今日待办（待打卡药物与待处理提醒）、待办提醒（逾期数量、下一条日期）、最近健康指标与最新症状、用药清单、健康小结、健康提示（当前档案最需关注的一条，危急/需关注高亮）、家庭健康总览与数据备份（上次备份时间，超 30 天提示，点击导出完整备份）；点击卡片直接打开对应抽屉 |
| 问诊记录 | 用户输入标为「主诉」，答复渲染成带抬头的「多学科会诊意见书」；会诊背景会自动附带当前档案的最近健康指标、症状、用药清单（含近 7 天打卡次数）与待办随访，并自动提取风险等级、诊疗建议和免责声明 |
| 急症警示 | 主诉命中胸痛、呼吸困难、卒中样表现等红旗时，在模型回复前先显示红色警示条；补充信息为 3 个月以下婴儿发热、免疫低下伴发热或抗凝期间头部外伤时同样会触发 |
| 用药安全提示 | 过敏史、重复用药或常见相互作用命中时，在模型回复前显示橙色/黄色提示，并注明不能替代医生或药师判断 |
| 知识库管理 | 会诊面板下方「管理知识库」：查看文档与向量块、上传 TXT/MD/PDF、删除、重建索引 |
| 会诊依据 | 多学科会诊可展开各科室原始意见；使用知识库时展示来源、文件名、相关度与原文片段 |
| 系统自检 | 会诊面板下方「系统自检」：Python、依赖版本、LLM 配置、知识库状态、各数据表记录数、附件目录份数与占用、操作审计，可复制诊断信息、清理旧会诊 |
| 临床工具 | 会诊面板下方「临床工具」：BMI、eGFR（CKD-EPI 2021）、CHA₂DS₂-VASc、HAS-BLED、CURB-65、GCS、Wells DVT/PE、Child-Pugh、PHQ-9、GAD-7、AUDIT-C、mMRC、CAT、ESS、qSOFA、FIB-4、APRI、MELD、eAG、STOP-BANG、Padua、ABCD2、Morse 与药物相互作用检查，下拉按类别分组并记住上次选择，自动带入补充信息中的年龄与性别；eGFR/FIB-4/MELD 支持从最近化验结果一键带入肌酐、AST/ALT、血小板、胆红素与 INR（自动换算 mg/dL），APRI 可带入 AST 与血小板并带入 AST 正常上限，eAG 可带入糖化血红蛋白（支持 mmol/mol 换算），Child-Pugh 可按最近胆红素/白蛋白/INR 自动换算并选择对应分值，CURB-65 可带入尿素氮与最近一次血压 |
| 健康记录 | 会诊面板下方「健康记录」：录入血压、体重、血糖、体温、心率、血氧、腰围、睡眠时长、步数、饮水量，以及 PHQ-9/GAD-7/CAT/ESS/AUDIT-C 评分与 mMRC 分级，可关联患者档案（档案下拉包含已删除档案的历史记录），按 7/30/90 天范围查看最近指标、近 7 天多指标变化与趋势图（血压行显示正常/正常高值/1–3 级/偏低分布）；统计摘要显示条数、平均、最高、最低、较上期变化与目标达标条数（步数/饮水量改为按自然日累计的今日进度：多条记录相加后与每日目标比较，显示「今日累计/还差多少」，不再显示按条达标数）；血压/心率/血氧自动分级，血压可标记晨起/睡前、血糖可标记空腹/餐后/睡前/随机，异常记录可一键带入复查提醒；「快速记录」支持一句话录入（如「血压 150/95 早上」「血糖 7.8 空腹」「体重 62.5」「睡眠 7.5 小时」「步数 8000」「6500 步」「饮水 1500 ml」），先预览解析结果再确认保存，保存后可撤销；「家庭血压监测」可按开始日期与 7/14/30 天统计晨起/睡前时段完成度、整体与晨晚均值、晨晚收缩压差，列出未测时段并可复制摘要（仅统计记录，不判断达标）；可设置并集中管理自定义目标（血压/血糖/体重等为上限，血氧/睡眠/步数/饮水量为下限），列表支持分页加载更早记录、按备注或档案搜索，导出/导入 CSV；可展开「症状日记」记录每天症状与 0–10 程度（常见症状可一键填入），支持搜索、程度趋势、CSV 导出/导入并随备份迁移；可展开「疫苗接种」记录每剂疫苗的接种日期、下一剂日期与备注，支持编辑、删除、CSV 导出与一键为下一剂创建提前 7 天的提醒（随备份迁移）；可展开「就诊预约」记录待就诊、已完成与已取消的预约，显示倒计时并可一键完成、取消、恢复或创建提前 1 天的就诊提醒；可展开「化验结果」登记任意化验项目、分组、数值、单位与参考范围，自动标记偏高/偏低，血钾、血钠、血糖、血红蛋白、血小板、白细胞、INR、肌酐、血钙与离子钙达到成人危急值时标注「危急值」并在保存后红色提示（离子钙使用独立阈值；以检验报告标注与本机构标准为准），同一项目多条记录显示趋势迷你图，支持搜索、CSV 导入导出与模板；可展开「过敏史」按药物/食物/环境记录过敏原、反应、严重程度与发现时间，在用过敏原会自动并入会诊上下文与相互作用检查；可展开「复查计划」按间隔设置糖化、血脂、眼底、肾功能等复查，完成后自动顺延下次日期，并可为临期项目创建提前 7 天的提醒；可展开「就诊记录」记录实际就诊的诊断、医嘱、处方与下次复诊时间，补齐「预约 → 就诊 → 结果」闭环，已完成预约可一键带入补记就诊记录；可展开「慢病清单」记录高血压、糖尿病、CKD 等问题与状态，未痊愈的问题会自动并入会诊既往史，高血压/糖尿病/慢性肾脏病/血脂异常可一键套用模板生成复查计划与指标目标；可维护用药清单（剂量、频次、在用状态、开始/停药日期与停药原因），新增或修改药物时自动复查在用药物相互作用、过敏史与最近化验（血钾、eGFR/肌酐、ALT、INR、地高辛浓度、电解质）并提示，结合慢病清单（哮喘/慢阻肺、溃疡/消化道出血、CKD、心衰、肝硬化、青光眼、前列腺增生、糖尿病）提示需要与医生或药师核对的风险，支持按药名/剂量/备注搜索与在用/停用筛选，可展开查看每条药物的变更历史（创建、剂量/频次调整、启停与日期变化），相互作用检查支持一键带入，用药条目可一键代入用药提醒（按频次自动设置重复周期），每条在用药物可打卡记录服用并显示今日次数、近 7/30/90 天次数（可切换统计范围）、按频次估算的应服次数与依从率（无法识别的频次只显示打卡次数）与最近时间（未打卡时提示，支持撤销、最近打卡逐条删除与 CSV 导出，随备份导出导入），打卡会自动扣减已登记的库存；也可登记「未服」并选择原因（忘记/副作用/担心副作用/费用/不方便/其他），未服不计入已服次数与依从率、不扣减库存，列表显示今日与区间未服次数及最近原因，健康提示与小结汇总漏服次数和 Top 原因；首页速览汇总今日总次数，展示近 7 天健康小结入口，并提供「今日用药」清单（可直接打卡或撤销最近一次，标注未打卡，全部完成时提示，无需打开抽屉）；打卡时自动完成当天的用药提醒（重复提醒自动排下一次），可一键为在用药物按频次批量创建用药提醒；「健康小结」支持今天/近 7/30/90 天的指标（含较上期变化与达标、血压晨晚/血糖空腹餐后分情境均值）、症状与用药打卡（含较上期变化与依从率）、化验异常、危急值、疫苗到期、复查计划、就诊与预约、慢病清单、会诊次数（含较上期变化与最近一次风险等级）、待办提醒与连续记录天数汇总，可一键复制为文本或导出 Markdown 文件；「健康月历」按月查看每天的指标、症状、用药打卡、会诊、提醒、疫苗、预约、化验、复查、就诊与就医资料数量，未达标指标、重度症状（≥7/10）与高危/紧急会诊标记为「异常」，点击日期可跳转到当天时间线，支持导出月历 CSV；「复诊准备单」可从就诊记录一键生成，按近 30/90/180 天或上次就诊以来汇总上次就诊、异常指标与化验、症状、目前用药与未服原因、过敏与慢病、待办与复查，并生成可复制、导出 Markdown 的问医生问题清单与单页打印版，附带本机就医资料清单（附件标明关联就诊/化验，不构成医疗建议）；「家庭指标对比」按成员对比所选指标在近 7/30/90 天的平均值、最低–最高、达标条数、最近一次记录与记录次数，可导出 CSV；「健康时间线」按时间合并今天/近 7/30/90 天的指标、症状、用药打卡、疫苗接种、就诊预约、化验结果、就医资料（附件事件可直接预览）与会诊，会诊条目附带风险等级，支持按类型筛选、关键字搜索、只看异常与只看高危会诊与 CSV 导出（含异常列），超出目标或低于下限的指标按方向标注「超标」/「低于目标」，达到危急值的指标标注「危急值」并在保存时红色提示，会诊条目可点击直达记录，记录较多时可加载更多（最多 500 条）；抽屉顶部「健康搜索」可跨指标、症状、用药、化验、疫苗、预约、复查、就诊、过敏、慢病、提醒与就医资料（含附件关联就诊/化验，结果可直接预览）搜索，可勾选「全部档案」跨成员搜索，可导出搜索结果 CSV，结果点击直达对应分区并带入关键字（Ctrl+Shift+K 或 /?action=search&q=）；「健康提示」按危急/需关注/提示分级汇总近 30 天的指标与化验危急值、在用药物安全、指标未达标、体重趋势、晨晚血压差、用药依从、化验异常与复查/疫苗/就诊/提醒逾期，可一键跳到对应分区；「数据质量」折叠面板检查化验缺单位、重复用药/疫苗/复查计划、逾期提醒、过期缺货药物、附件文件缺失、大小不一致与同名同大小重复，并可跳到对应分区；「就医资料」支持选择、拖拽或粘贴上传 JPG/PNG/WebP/PDF（≤10 MB，图片解码后不得超过 4000 万像素）并按类型归档（一次最多多选 5 个文件），就诊资料/化验单可选择关联同一档案下的就诊或化验记录（标题显示已关联份数，点击可只看该记录资料），也可从就诊/化验记录一键带入上传；附件仅保存在本机 data/attachments，不进入 JSON 备份、不发送给模型，可在应用内预览、下载、编辑备注与删除，列表支持按类型/关联状态/上传日期筛选、按文件名、备注或关联记录（就诊诊断/科室/医生、化验项目/分组/机构）搜索、多选删除、批量关联或取消关联、导出选中附件包、按上传时间/大小/文件名排序与分页加载，可一键清除筛选，列表摘要显示当前筛选的份数与总大小，并按类型显示份数与占用；可检查登记与本地文件是否一致（缺失、大小不一致、孤儿文件）并清理孤儿文件、导出全部附件包与附件清单 CSV；CSV 导出会跟随当前类型、搜索、日期与只看异常筛选；可导出健康档案 Markdown（单人或家庭，含健康提示、档案信息、连续记录、指标目标、最近指标、最近化验、症状、疫苗接种、就诊预约、用药依从率、待办提醒、就医资料与近期会诊风险）；首页「家庭健康总览」按成员汇总最近指标、用药打卡、提醒、症状、疫苗、库存、下次就诊、化验异常与化验危急值、用药与化验/慢病交叉提示（含最近一次会诊风险），标注今日是否已有记录，点击成员切换档案，可复制摘要或导出家庭总览 CSV |
| 随访提醒 | 会诊面板下方「随访提醒」：为复查、复诊创建带日期与所属档案的提醒，列表按「已逾期 / 今天到期 / 未来 7 天 / 更晚 / 已完成」分组，列表显示剩余/逾期天数，抽屉顶部可选择近 7/30/90 天查看到期、已完成、待办与逾期数量及完成率，支持关键字搜索、只看逾期与月历视图（按月查看数量、按日筛选，可一键回到今天）；可设置提前 0–30 天提醒，进入提前窗口即计入入口徽标与浏览器通知；支持按天/周/月批量安排、重复提醒（完成后自动排下一次）、完成、推迟 1/3/7/14 天、编辑、删除，用药提醒带「用药」标记，可一键推迟全部逾期提醒 1 天、清理已完成提醒，导出 CSV / 日历（ICS）与 CSV 批量导入 |
| 就诊摘要 | 会诊面板下方「就诊摘要」：导出档案信息、近 7 天小结、指标概览（记录数、最新、平均、最低–最高、达标条数）、近期指标、用药清单（含今日与近 7 天打卡次数）、症状记录、待办随访与近期会诊的一页式 HTML，可打印为 PDF |
| 急救信息卡 | 健康记录抽屉「急救卡」：按当前档案生成一页式可打印 HTML，含血型、年龄性别、活动过敏、在用药物（抗凝/抗血小板醒目标注）、慢病清单、紧急联系人与更新时间，固定提示紧急情况拨打 120；血型与紧急联系人在补充信息中维护，随档案与备份保存 |
| 历史会诊 | 右上角「会诊记录」抽屉：按时间浏览、搜索、打开或删除历史会诊，支持按风险等级筛选（中危及以上/高危及以上/仅紧急）；可查看最近待改进反馈与质量概览；刷新页面自动恢复上次会诊 |
| 意见操作 | 每条意见下方可复制、导出 Markdown、再次会诊；生成过程中可随时停止 |
| 常用问题 | 首页「常见主诉」旁「＋ 存为常用」：把输入框内容保存为常用问题（浏览器本地，最多 8 条，可删除） |
| 会诊对比 | 会诊记录「批量」勾选两次会诊后点「对比」：并排查看元信息、风险统计与每轮意见 |
| 语音输入 | 输入框旁麦克风按钮：语音听写实时转文字，再次点击停止；浏览器不支持时自动隐藏 |
| 外观 | 跟随系统深色偏好，也可用顶栏按钮切换；选择保存在浏览器本地 |

风险提示的配色对齐急诊分诊的四级色标（红 / 橙 / 黄 / 绿），但用的是「风险等级」表述，不作临床分诊结论。

前端代码在 `web/` 目录（原生 HTML/CSS/JS，无需构建步骤）。

浏览器支持时可把页面「安装」到桌面或主屏幕；浏览器触发可安装事件后，会诊面板会显示「安装应用」按钮。安装后长按应用图标可使用「新建会诊」「随访提醒」「健康记录」快捷方式（PWA shortcuts，也可用 `?action=new|reminders|metrics` 深链直接打开；提醒月历用 `?action=reminders&calendar=1`，提醒搜索用 `?action=reminders&q=关键词`）。Service Worker 只缓存 HTML/CSS/JS/图标等静态外壳；`/api/` 请求不缓存，离线时仍需要本地后端服务才能读取会诊与健康记录，页面会显示离线提示并在恢复连接后自动重新检查服务状态。

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
- 支持按当前筛选导出提醒 CSV（含到期日、档案、状态与备注）
- 支持按当前筛选导出 iCalendar（.ics），可导入手机或桌面日历
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

- 会诊面板下方「就诊摘要」：按当前患者档案汇总基本信息、近 7 天小结（指标、症状、用药打卡与提醒）、近期 30 条健康指标（列出目标并按方向标注超标/低于目标）、用药清单（今日与近 7 天打卡次数）、近期症状、该档案的待完成随访与最近 5 次会诊
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
| GET | `/api/sessions` | 历史列表：`q` 搜索、`limit`/`offset` 分页、`favorite=1` 收藏筛选、`days=N` 时间范围、`archived=true` 查看已归档、`tag=标签` 标签筛选、`profile=档案` 患者档案筛选、`risk=medium\|high\|emergency` 按风险等级及以上筛选、用量与反馈汇总；每条会话附带风险等级 |
| GET | `/api/tags` | 未归档会话的标签与数量 |
| GET | `/api/session-profiles` | 会话中出现过的档案名及数量（含已删除档案留下的历史名字） |
| GET | `/api/profile-names` | 所有数据表中出现过的档案名（含已删除档案留下的历史名字） |
| GET | `/api/sessions/export` | 导出全部会话、消息与元数据为 JSON |
| GET | `/api/sessions/export.csv` | 按 `q`/`favorite`/`days`/`archived`/`tag`/`profile`/`risk` 筛选导出会话列表 CSV（带 BOM，含风险等级） |
| POST | `/api/maintenance/purge` | 删除指定天数以前的会诊及其消息，并同步清理长期记忆与会诊总结（不可恢复，写入审计；本地备份不受影响） |
| POST | `/api/sessions/import` | 导入备份 JSON（会话、档案、指标、目标、提醒、症状、用药、疫苗接种、就诊预约、就诊记录、化验结果、过敏史、慢病清单、复查计划、打卡与操作审计；重复记录跳过） |
| GET | `/api/templates/{kind}` | 下载 CSV 导入模板（`metrics`/`symptoms`/`medications`/`reminders`/`lab-results`/`allergies`，带 BOM 与示例行） |
| GET | `/api/backups` | 列出本地自动备份（最多 50 份，含大小与修改时间） |
| POST | `/api/backups` | 立即创建一份本地备份（受 `MEDIX_BACKUP_KEEP` 保留策略约束） |
| GET | `/api/backups/{name}/inspect` | 恢复前预检：校验 SHA-256，汇总会话/消息/档案/指标/化验/用药等数量并返回警告（含「备份不包含附件」提示与附件总量） |
| POST | `/api/backups/{name}/restore` | 恢复指定本地备份（校验文件名与 SHA-256，重复记录跳过，写入审计） |
| DELETE | `/api/backups/{name}` | 删除指定本地备份（需 `confirm=true`，校验文件名，写入审计） |
| GET | `/api/sessions/{id}/messages` | 读取会话消息（优先读 SQLite；`limit=0` 返回全部） |
| GET | `/api/profiles` | 常用患者档案列表（服务端持久化） |
| GET | `/api/family/overview` | 家庭健康总览：按档案汇总最近指标、用药打卡、待办提醒、近期症状、疫苗接种、下次就诊、就诊记录、化验异常、复查计划、慢病清单与会诊（含最近一次会诊风险与 `attention` 关注提示，`days`，默认 7；成员按关注级别排序） |
| GET | `/api/family/compare/export.csv` | 导出家庭指标对比 CSV（`metric`/`days`，带 BOM，每位成员一行） |
| GET | `/api/family/compare` | 家庭指标对比：按档案统计指定指标在近 N 天的平均值、最近记录与次数（`metric`/`days`） |
| GET | `/api/family/overview/export.csv` | 导出家庭健康总览 CSV（带 BOM，每位成员一行） |
| GET | `/api/health/dossier.md` | 导出单个患者的健康档案 Markdown（健康提示、档案信息、过敏史、慢病清单、复查计划、近期就诊、连续记录、指标目标、最近指标、最近化验、症状、用药与依从率、疫苗接种、就诊预约、提醒、就医资料、近期会诊风险，`profile`/`days`） |
| GET | `/api/health/emergency-card` | 急救信息卡：按档案生成一页式可打印 HTML，含血型、年龄性别、活动过敏、在用药物（抗凝/抗血小板标注）、慢病与紧急联系人（`profile`） |
| POST | `/api/attachments` | 上传就医资料附件（JPG/PNG/WebP/PDF，单文件 ≤10 MB，`profile`/`kind`/`ref_id`/`note`，仅保存在本机 `data/attachments`） |
| GET | `/api/attachments` | 附件列表（可按 `profile`/`kind`/`ref_id`/`linked`/`start`/`end` 与关键字 `q`（含关联记录）筛选，按 `sort`（最新/最早/大小/名称）排序并分页，不返回文件内容；关联记录附 `ref_label` 摘要，并标记文件是否缺失 `file_missing`；同时返回 `total_count` 与 `total_size`） |
| GET | `/api/attachments/counts` | 按就诊/化验记录统计已关联附件数量，并返回类型分布与总大小 |
| POST | `/api/attachments/link` | 批量关联附件到同一档案的就诊/化验记录（`ids` 1–200 个，`ref_id` 为记录 ID，0 表示取消关联；仅支持同类型附件） |
| GET | `/api/attachments/{id}/file` | 读取附件原文件（`inline`，`Cache-Control: no-store`） |
| DELETE | `/api/attachments/{id}` | 删除附件登记并清理本机文件 |
| PATCH | `/api/attachments/{id}` | 更新附件备注（`note`，最多 300 字，空白清空） |
| GET | `/api/attachments/export.csv` | 导出附件清单 CSV（带 BOM、关联摘要与文件状态，支持 `profile`/`kind`/`ref_id`/`start`/`end`/`q`/`linked`/`sort`） |
| GET | `/api/attachments/export.zip` | 导出附件包（可选 `ids`，最多 200 个，仅导出选中附件）：ZIP 内含 `manifest.json`（含每个文件的 SHA-256）与可读取文件，缺失/不可读文件只记入清单；总大小上限 200 MB，限流 6 次/分钟 |
| GET | `/api/attachments/integrity` | 检查附件登记与本地文件：缺失文件、大小不一致、孤儿文件与最近写入数量 |
| POST | `/api/attachments/cleanup` | 清理没有数据库登记的孤儿文件（需 `confirm=true` 与预检的 `expected_orphans`，10 分钟内新文件保留） |
| GET | `/api/health/dossier-family.md` | 导出全部患者档案的 Markdown（每位成员一份，包含「未指定档案」的记录，`days`） |
| POST | `/api/profiles` | 新增或覆盖患者档案（`name`、`context`） |
| PATCH | `/api/profiles/{name}` | 重命名患者档案并同步健康指标 |
| DELETE | `/api/profiles/{name}` | 删除患者档案 |
| POST | `/api/tools/bmi` | 计算 BMI（身高 cm、体重 kg） |
| POST | `/api/tools/egfr` | CKD-EPI 2021 eGFR（年龄、性别、血肌酐、单位） |
| POST | `/api/tools/chads-vasc` | CHA₂DS₂-VASc 评分（年龄、性别与危险因素） |
| POST | `/api/tools/curb65` | CURB-65 评分（年龄、意识、尿素氮、呼吸频率与血压） |
| POST | `/api/tools/phq9` | PHQ-9 抑郁筛查评分（9 个 0–3 答案，第 9 题阳性时 urgent） |
| POST | `/api/tools/gad7` | GAD-7 焦虑筛查评分（7 个 0–3 答案） |
| POST | `/api/tools/audit-c` | AUDIT-C 酒精使用筛查（3 个 0–4 答案，可选 `sex` 使用男女阈值） |
| POST | `/api/tools/mmrc` | mMRC 呼吸困难量表（0–4 级） |
| POST | `/api/tools/cat` | CAT 慢阻肺评估测试（8 个 0–5 答案） |
| POST | `/api/tools/ess` | Epworth 嗜睡量表（8 个 0–3 答案） |
| POST | `/api/tools/has-bled` | HAS-BLED 房颤抗凝出血风险评分（年龄与危险因素） |
| POST | `/api/tools/gcs` | GCS 格拉斯哥昏迷评分（睁眼/语言/运动） |
| POST | `/api/tools/wells-dvt` | Wells 深静脉血栓概率评分 |
| POST | `/api/tools/child-pugh` | Child-Pugh 肝硬化严重程度评分 |
| POST | `/api/tools/wells-pe` | Wells 肺栓塞概率评分 |
| POST | `/api/tools/interactions` | 药物相互作用检查（药物列表 + 可选过敏史，可从用药清单与补充信息中的过敏史一键带入） |
| POST | `/api/tools/qsofa` | qSOFA 脓毒症风险筛查（呼吸频率、收缩压、意识状态） |
| POST | `/api/tools/fib-4` | FIB-4 肝纤维化指数（年龄、AST、ALT、血小板） |
| POST | `/api/tools/apri` | APRI 肝纤维化指数（AST、血小板与可选 AST 正常上限） |
| POST | `/api/tools/meld` | 经典 MELD 终末期肝病评分（胆红素、INR、肌酐、透析） |
| POST | `/api/tools/eag` | 由 HbA1c 估算平均血糖 eAG（mg/dL 与 mmol/L） |
| POST | `/api/tools/stop-bang` | STOP-BANG 阻塞性睡眠呼吸暂停筛查（8 项） |
| POST | `/api/tools/padua` | Padua 内科住院患者静脉血栓风险评分 |
| POST | `/api/tools/abcd2` | ABCD2 TIA 后短期卒中风险评分 |
| POST | `/api/tools/morse-fall` | Morse 跌倒风险评分 |
| GET | `/api/metrics` | 健康指标记录（可按 `profile` / `metric` / `days` 筛选，`q` 按备注/档案关键字搜索；`limit`/`offset` 分页，默认最近 100 条；每条附带 `critical_alerts` 危急值提示与 `context` 测量情境） |
| GET | `/api/metrics/latest` | 每种指标类型最新的一条记录（可按 `profile`/`days` 筛选，忽略未来时间，用于首页速览、最近指标与会诊上下文） |
| GET | `/api/metrics/summary` | 指定指标在范围内的条数、平均/最高/最低、最新值、上一周期均值、目标与达标条数（`profile`/`metric`/`days`）；步数与饮水量另返回今日累计（`daily_total`/`daily_count`/`daily_within_target`） |
| GET | `/api/reports/weekly` | 近 N 天健康小结：指标汇总与分情境均值（血压晨起/睡前、血糖空腹/餐后）、症状统计、用药打卡与依从率、未服次数与 Top 原因、化验异常、危急值、疫苗到期、复查计划、就诊预约/记录、慢病清单、会诊次数与风险、待办提醒（`profile`/`days`，1–90） |
| GET | `/api/timeline` | 健康时间线：指标、症状、用药打卡、疫苗接种、就诊预约、化验结果、复查计划、就诊记录与会诊按时间倒序合并，指标附带目标偏差标记、化验附带偏高/偏低标记、会诊附带风险等级（`profile`/`days`/`limit`/`date`/`type`/`only_alert`/`only_risk`/`q`） |
| GET | `/api/timeline/export.csv` | 导出健康时间线 CSV（带 BOM，含异常列，可按 `profile`/`days`/`date`/`type`/`only_alert`/`only_risk`/`q` 筛选） |
| GET | `/api/health/insights` | 规则化健康提示：近 N 天危急值、在用药物安全（相互作用/过敏）、指标未达标、体重趋势、晨晚血压差、用药依从、化验异常、重度症状、高危会诊、复查/疫苗/就诊/提醒逾期与近期安排，按危急→需关注→提示排序（`profile`/`days`，默认 30） |
| GET | `/api/health/data-quality` | 健康数据质量检查：化验缺单位、重复用药/疫苗/复查计划、逾期提醒、过期或缺货药物、附件文件缺失、大小不一致与同名同大小重复（`profile` 可选） |
| GET | `/api/health/bp-monitoring` | 家庭血压监测：统计晨起/睡前时段完成度、整体与晨晚均值、晨晚收缩压差并列出未测时段（`profile`/`days`/`start`，仅统计记录、不判断达标） |
| GET | `/api/health/visit-prep` | 复诊准备单：按档案与天数汇总上次就诊、异常指标与化验、症状、用药与未服原因、过敏与慢病、待办复查，并生成问题清单与 Markdown 文本（`profile`/`days`，1–180） |
| GET | `/api/health/visit-prep/print` | 复诊准备单打印版：单页 HTML，可用浏览器打印或另存为 PDF（`profile`/`days`） |
| GET | `/api/health/search` | 全局健康搜索：按关键字跨指标、症状、用药、化验、疫苗、预约、复查、就诊、过敏、慢病、提醒与就医资料（含附件关联就诊/化验，结果可直接预览）搜索（`q`/`profile`/`limit`，省略 `profile` 搜索全部档案，每类最多返回 20 条） |
| GET | `/api/health/search/export.csv` | 导出全局健康搜索结果 CSV（带 BOM；`q`/`profile`，最多 200 条） |
| GET | `/api/health/calendar` | 健康月历：按天汇总指标、症状、用药打卡、会诊、提醒完成情况、疫苗、预约、化验、复查、就诊记录与异常数（未达标指标/重度症状/高危会诊/异常化验，`profile`/`month`，格式 YYYY-MM） |
| GET | `/api/health/calendar/export.csv` | 导出健康月历 CSV（带 BOM，按天列出指标、症状、用药、会诊、提醒、疫苗、预约、化验、复查、就诊与异常，`profile`/`month`） |
| GET | `/api/health/streak` | 健康记录连续天数：当前连续、最长连续、今日是否记录与近 30 天记录天数（`profile`） |
| POST | `/api/metrics` | 新增血压、体重、血糖、体温、心率、血氧、腰围、睡眠时长、步数、饮水量或 PHQ-9/GAD-7 评分（血压/血糖可带 `context` 测量情境；响应含 `critical_alerts` 危急值提示） |
| POST | `/api/metrics/quick-parse` | 解析一句话健康记录（如「血压 150/95 早上」「血糖 7.8 空腹」），只返回预览不写入；无法识别时返回 `code` 与提示 |
| GET | `/api/symptoms` | 症状日记（可按 `profile`/`days` 筛选，`limit`/`offset` 分页） |
| POST | `/api/symptoms` | 新增症状（症状、0–10 程度、记录时间与备注） |
| PATCH | `/api/symptoms/{id}` | 更新症状记录 |
| DELETE | `/api/symptoms/{id}` | 删除症状记录 |
| GET | `/api/symptoms/export.csv` | 导出症状日记 CSV（带 BOM，可按 `profile`/`days` 筛选） |
| POST | `/api/symptoms/import` | 从导出的 CSV 格式导入症状，重复记录自动跳过 |
| GET | `/api/vaccinations` | 疫苗接种记录（可按 `profile` 筛选，按接种日期倒序，`limit` 默认 1000） |
| POST | `/api/vaccinations` | 新增疫苗接种记录（疫苗名称、剂次 1–20、接种日期、下一剂日期与备注） |
| PATCH | `/api/vaccinations/{id}` | 部分更新疫苗接种记录 |
| DELETE | `/api/vaccinations/{id}` | 删除疫苗接种记录 |
| GET | `/api/vaccinations/export.csv` | 导出疫苗接种 CSV（带 BOM，可按 `profile` 筛选） |
| POST | `/api/vaccinations/{id}/reminder` | 为下一剂创建提前 7 天的接种提醒；已有未完成提醒时直接返回 |
| GET | `/api/appointments` | 就诊预约列表（可按 `profile`/`status` 筛选，`upcoming=true` 只看未过期的待就诊） |
| POST | `/api/appointments` | 新增就诊预约（主题、科室、医生、地点、时间、状态与备注） |
| PATCH | `/api/appointments/{id}` | 部分更新就诊预约（含完成、取消与恢复待就诊） |
| DELETE | `/api/appointments/{id}` | 删除就诊预约 |
| GET | `/api/appointments/export.csv` | 导出就诊预约 CSV（带 BOM，可按 `profile` 筛选） |
| POST | `/api/appointments/{id}/reminder` | 为待就诊预约创建提前 1 天的提醒；已有未完成提醒时直接返回 |
| GET | `/api/lab-results` | 化验结果列表（可按 `profile`/`item`/`panel`/`days` 筛选，`limit`/`offset` 分页，附带参考范围、偏高/偏低与危急值标记） |
| GET | `/api/lab-results/latest` | 每个化验项目最近一次结果（可按 `profile` 筛选） |
| POST | `/api/lab-results` | 新增化验结果（分组、项目、数值、单位、参考上下限、检测时间与机构） |
| PATCH | `/api/lab-results/{id}` | 部分更新化验结果 |
| DELETE | `/api/lab-results/{id}` | 删除化验结果 |
| GET | `/api/lab-results/export.csv` | 导出化验结果 CSV（带 BOM，可按 `profile`/`item`/`panel`/`days` 筛选） |
| POST | `/api/lab-results/import` | 从导出的 CSV 格式导入化验结果，重复记录自动跳过 |
| GET | `/api/allergies` | 过敏史列表（可按 `profile`/`active_only`/`kind`/`q` 筛选） |
| POST | `/api/allergies` | 新增过敏史（过敏原、反应、严重程度、类型、发现时间与备注） |
| PATCH | `/api/allergies/{id}` | 部分更新过敏史 |
| DELETE | `/api/allergies/{id}` | 删除过敏史 |
| GET | `/api/allergies/export.csv` | 导出过敏史 CSV（带 BOM，可按 `profile` 筛选） |
| POST | `/api/allergies/import` | 从导出的 CSV 格式导入过敏史，重复记录自动跳过 |
| GET | `/api/care-items` | 复查计划列表（可按 `profile`/`active_only`/`overdue` 筛选，附带倒计时与逾期标记） |
| POST | `/api/care-items` | 新增复查计划（项目、间隔天数、上次完成与下次复查日期） |
| PATCH | `/api/care-items/{id}` | 部分更新复查计划 |
| POST | `/api/care-items/{id}/done` | 记录复查完成、按间隔自动顺延下次日期，并自动创建下次复查提醒 |
| DELETE | `/api/care-items/{id}` | 删除复查计划 |
| GET | `/api/care-items/export.csv` | 导出复查计划 CSV（带 BOM，可按 `profile` 筛选） |
| POST | `/api/care-items/{id}/reminder` | 为复查计划创建提前 7 天的提醒；已有未完成提醒时直接返回 |
| GET | `/api/visits` | 就诊记录列表（可按 `profile`/`days`/`q` 筛选，附带下次复诊倒计时） |
| POST | `/api/visits` | 新增就诊记录（日期、科室、医生、机构、诊断、医嘱、处方、下次复诊与备注） |
| PATCH | `/api/visits/{id}` | 部分更新就诊记录 |
| POST | `/api/visits/{id}/schedule-followup` | 按下次复诊日期一键创建预约与提前 1 天提醒（重复调用复用已有安排） |
| DELETE | `/api/visits/{id}` | 删除就诊记录 |
| GET | `/api/visits/export.csv` | 导出就诊记录 CSV（带 BOM，可按 `profile` 筛选） |
| GET | `/api/conditions` | 慢病/健康问题清单（可按 `profile`/`active_only`/`status` 筛选） |
| POST | `/api/conditions` | 新增慢病记录（名称、ICD-10、确诊日期、状态、严重程度与备注） |
| PATCH | `/api/conditions/{id}` | 部分更新慢病记录 |
| POST | `/api/conditions/{id}/apply-template` | 按慢病类型（高血压/糖尿病/慢性肾脏病/血脂异常）一键生成复查计划与指标目标，已有项目自动跳过 |
| DELETE | `/api/conditions/{id}` | 删除慢病记录 |
| GET | `/api/conditions/export.csv` | 导出慢病清单 CSV（带 BOM，可按 `profile` 筛选） |
| GET | `/api/medications` | 用药清单（可按 `profile`/`active_only` 筛选，`days` 指定依从统计范围 1–365，默认 7；`limit`/`offset` 分页；附带库存、库存单位、低库存阈值、有效期与缺货/临期提示） |
| POST | `/api/medications` | 新增药物（名称、剂量、频次、备注、在用状态，可选库存、库存单位、低库存阈值、有效期与开始/停药日期；响应含 `safety` 相互作用/过敏提示） |
| PATCH | `/api/medications/{id}` | 更新药物记录（含库存与有效期；手动设置库存会重置历史打卡的扣减基线；响应含 `safety` 安全提示） |
| DELETE | `/api/medications/{id}` | 删除药物记录 |
| POST | `/api/medications/{id}/reminder` | 为低库存（`kind=stock`）或临期（`kind=expiry`）药物创建提醒；已有未完成提醒时直接返回 |
| GET | `/api/medications/export.csv` | 导出用药清单 CSV（带 BOM，可按 `profile`/`active_only`/`status=active\|inactive`/`q` 筛选，`q` 匹配药名、剂量、频次、备注与档案；含今日/近 7 天次数、最近服用时间、近 7 天应服次数、依从率、库存、库存单位、低库存阈值、有效期、开始/停药日期与库存/效期提示） |
| POST | `/api/medications/import` | 从导出的 CSV 格式导入用药，重复记录自动跳过 |
| GET | `/api/medications/{id}/logs` | 某个药物的用药打卡记录（按服用时间倒序，可按 `days` 筛选） |
| GET | `/api/medications/{id}/history` | 某个药物的变更历史（创建、剂量/频次调整、启停与日期变化，按时间倒序） |
| POST | `/api/medications/{id}/logs` | 记录一次用药（可带 `taken_at`/`note`，默认当前时间） |
| POST | `/api/medications/{id}/skip` | 记录一次未服及原因（`forget`/`side_effect`/`worry_side_effect`/`cost`/`inconvenient`/`other`；不计入已服次数、不扣库存） |
| GET | `/api/medications/skip-reasons` | 未服原因选项（键与中文名） |
| DELETE | `/api/medication-logs/{id}` | 撤销一条用药打卡或未服记录 |
| GET | `/api/medications/logs/export.csv` | 导出用药打卡 CSV（带 BOM，含状态与未服原因，可按 `profile`/`days` 筛选） |
| GET | `/api/medication-logs` | 用药打卡记录（跨药物，按服用时间倒序，可按 `profile`/`days` 筛选） |
| PATCH | `/api/metrics/{id}` | 更新健康指标记录 |
| DELETE | `/api/metrics/{id}` | 删除健康指标记录 |
| GET | `/api/metrics/export.csv` | 导出健康记录 CSV（带 BOM，可按 `profile`/`metric`/`days`/`q` 筛选） |
| POST | `/api/metrics/import` | 从导出的 CSV 格式导入健康记录，重复记录自动跳过 |
| GET | `/api/metric-targets` | 自定义健康指标目标（可按 `profile` 筛选） |
| PUT | `/api/metric-targets` | 设置或覆盖目标上限（血氧/睡眠/步数/饮水量为下限；血压需同时提供舒张压） |
| DELETE | `/api/metric-targets/{metric}` | 删除某档案某指标的目标（`profile` 查询参数） |
| GET | `/api/reminders` | 随访提醒列表（默认未完成，`include_done=true` 含已完成，`profile` 按档案筛选） |
| GET | `/api/reminders/stats` | 近 N 天到期提醒的完成情况（到期、已完成、待办、逾期与完成率，`profile`/`days`） |
| POST | `/api/reminders/import` | 从导出的 CSV 格式导入随访提醒，重复提醒自动跳过 |
| GET | `/api/reminders/export.csv` | 按筛选导出随访提醒 CSV（带 BOM；支持 `profile`、`include_done`、`q` 关键字、`overdue=true` 只看逾期、`date=YYYY-MM-DD` 指定日期） |
| GET | `/api/reminders/export.ics` | 按筛选导出随访提醒 iCalendar（.ics，重复提醒输出 RRULE） |
| POST | `/api/reminders` | 新增随访提醒（内容 + 日期，可选 `profile`、`session_id` 关联会诊、`medication_id` 关联用药、`advance_days` 提前天数、`repeat_days`/`repeat_until` 重复设置；日期支持 `YYYY-MM-DD` 或 ISO 时间） |
| POST | `/api/reminders/{id}/complete` | 完成提醒；重复提醒会返回自动生成的 `next` |
| POST | `/api/reminders/{id}/reopen` | 把已完成的随访提醒恢复为待完成 |
| PATCH | `/api/reminders/{id}` | 修改随访提醒的内容、日期、备注、档案、提前天数或重复设置 |
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
| GET | `/api/knowledge/search` | 知识库检索测试（`q` 检索词必填，`top_k` 默认 5，返回来源、相关度与片段，可用 `filter_type` 按文档类型过滤） |
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

设置 `MEDIX_ACCESS_TOKEN` 后，除 `/api/status` 外的 `/api/*` 都要求携带令牌（`X-Medix-Token` 请求头或 `Authorization: Bearer`），失败返回 401；`GET /api/auth/verify` 可用于校验令牌。令牌不接受 `?token=` 查询参数，避免被 uvicorn 访问日志记录。

`/api/chat` 的 `context` 支持 `age`、`sex`、`duration`、`history`、`medications`、`allergies` 等字段，会注入任务分解和所有科室的提示词，并随会诊记录保存。`enable_swarm=false` 时本次请求跳过多学科协作、直接单科接诊。

`GET /api/sessions/export` 的 JSON（`version: 1`）可直接用于 `POST /api/sessions/import`；导出会同时包含常用患者档案、健康指标、自定义目标、随访提醒、症状日记、用药清单、用药打卡、操作审计（`audit_log`）与归档状态，并附带内容 SHA-256 校验和，导入时自动校验，文件损坏或被修改会返回 400。备份文件可能包含健康与操作记录，请按敏感数据保管；就医资料附件仅保存在本机 `data/attachments`，不包含在 JSON 备份中。设置 `MEDIX_AUTO_BACKUP_HOURS` 后可定期把同样的完整备份写入 `data/backups/`，`MEDIX_BACKUP_KEEP` 控制保留份数（默认 7），`MEDIX_BACKUP_DIR` 可自定义目录；自动备份、立即备份与本地恢复的详细步骤见 [docs/backup.md](docs/backup.md)。导入时只接受 user/assistant 消息，已存在的会话编号、档案名称与相同记录会跳过，不覆盖现有记录。单个备份最多 100000 个会话、每个会话最多 10000 条消息，其余数据类上限同为 100000 条。会诊接口默认限流 30 次/分钟，`/api/diagnose/llm` 自检限制 5 次/分钟，附件上传限制 30 次/分钟，超限返回 429 与 `Retry-After`。

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

`MEDIX_TIMEOUT` 是单次模型请求超时（秒），可不填，默认 60。服务会把 401（Key 无效）、402（余额不足）、429（限流）、超时和网络错误转换成可读提示，其中 401/402 不会反复重试。`MEDIX_RATE_LIMIT` 控制 `/api/chat` 与 `/api/chat/stream` 每分钟允许的请求数，默认 30，设为 0 关闭限流；`/api/diagnose/llm` 自检单独限制 5 次/分钟。`MEDIX_PRICE_INPUT` / `MEDIX_PRICE_OUTPUT` 是每百万输入/输出 Token 的单价，配合 `MEDIX_CURRENCY`（默认 `¥`）在界面显示预估费用，未配置时不显示。`MEDIX_MONTHLY_TOKEN_BUDGET` 设置每月 Token 预算（0 或留空不启用），历史抽屉会显示本月进度和超限提醒。`MEDIX_AUTO_ARCHIVE_DAYS` 设置自动归档旧会话的天数（0 或留空关闭），归档只隐藏记录、不删除数据，可在「已归档」中恢复。`MEDIX_SESSION_DB` 可指定会话与健康数据的 SQLite 文件路径（默认 `data/sessions.db`）。

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
- 通过 `--host 0.0.0.0` 暴露到局域网时，必须同时设置 `MEDIX_ACCESS_TOKEN`（未设置会拒绝启动），避免任何人直接消耗你的模型额度；仅在完全可信的内网可显式使用 `--allow-insecure-lan`（或 `MEDIX_ALLOW_INSECURE_LAN=1`）放行；中间件对所有启动方式执行同样的限制。


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

1. 打开项目文件夹，VSCode 通常会自动发现 `.venv`；若没有，Windows 手动选择 `.venv\Scripts\python.exe`，macOS/Linux 选择 `.venv/bin/python`
2. 按 `F5`，在下列配置中选择：
   - **启动 Web 服务（浏览器界面）** — 推荐
   - **命令行对话（main.py）**
   - **初始化知识库**
   - **运行测试套件**
3. 也可以按 `Ctrl+Shift+B`（macOS 为 `Cmd+Shift+B`）直接跑默认任务（启动 Web 服务）

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
│   ├── clinical_tools.py              # BMI/eGFR/CHA₂DS₂-VASc/HAS-BLED/CURB-65/GCS/Wells/Child-Pugh/PHQ-9/GAD-7/qSOFA/FIB-4/MELD/STOP-BANG/Padua/ABCD2/Morse
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
├── tests/                             # 离线单元测试（无需 API Key，当前 800+ 个用例）
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

### 服务端环境变量

以下变量都可以写在 `.env` 中，未设置时使用括号内默认值：

| 变量 | 说明 |
| --- | --- |
| `MEDIX_API_KEY` / `MEDIX_BASE_URL` / `MEDIX_MODEL` | 覆盖 LLM 的密钥、地址与模型名（默认取 `config.py`） |
| `MEDIX_ACCESS_TOKEN` | 访问令牌；设置后所有 API 需要 `Authorization: Bearer <token>`（默认关闭） |
| `MEDIX_ALLOWED_HOSTS` | 允许的 Host 域名（逗号分隔）；IP 地址与 localhost 默认允许（默认空） |
| `MEDIX_TRUST_PROXY_HEADERS` | 设为 `1` 时信任本机反向代理带来的 `X-Forwarded-For` / `X-Real-IP` / `Forwarded` 客户端地址；默认关闭（见访问保护说明） |
| `MEDIX_LOG_LEVEL` | 服务日志级别（默认 `INFO`）；调试可设为 `DEBUG` |
| `MEDIX_RATE_LIMIT` | 每个客户端每分钟会诊请求上限（默认 30，`0` 表示关闭） |
| `MEDIX_TIMEOUT` | 模型请求超时秒数（默认 60） |
| `MEDIX_MONTHLY_TOKEN_BUDGET` | 每月 Token 预算（默认 0，不启用） |
| `MEDIX_PRICE_INPUT` / `MEDIX_PRICE_OUTPUT` / `MEDIX_CURRENCY` | 每百万 Token 价格与货币符号，用于费用展示 |
| `MEDIX_SESSION_DB` | SQLite 会话数据库路径（默认 `data/sessions.db`） |
| `MEDIX_AUTO_ARCHIVE_DAYS` | 超过 N 天未更新的会话自动归档（默认 0，关闭） |
| `MEDIX_AUTO_BACKUP_HOURS` / `MEDIX_BACKUP_KEEP` / `MEDIX_BACKUP_DIR` | 自动备份间隔、保留份数与目录（默认关闭 / 7 / `data/backups`） |

### 记忆系统配置

本系统支持两层记忆机制：**短期记忆（会话级）** 和 **长期记忆（跨会话）**。

#### 短期记忆（ShortTermMemory）

**作用**：存储当前会话的对话历史，支持多轮对话上下文理解。

**配置**：
```python
# 方式1: 内存存储（默认，无需配置）
from memory.short_term import ShortTermMemory
memory = ShortTermMemory()

# 方式2: Redis持久化（可选）
memory = ShortTermMemory(
    storage_type="redis",
    redis_config={"host": "localhost", "port": 6379},
)
```

**使用示例**：
```python
session_id = "user_123"

# 添加消息
memory.add_message(session_id=session_id, role="user", content="我有高血压")
memory.add_message(session_id=session_id, role="assistant", content="高血压需要...")

# 获取对话历史（最近10条）
history = memory.get_recent_messages(session_id=session_id, limit=10)

# 会话结束时清空
memory.clear_session(session_id)
```

**存储方式**：
- **内存**（默认）：无需配置，保留时间60分钟
- **Redis**（可选）：需配置 REDIS_CONFIG，支持持久化
- 存储容量：单会话最多保留 1000 条；Agent 每轮读取最近 10 条

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

# 也可以在构造时显式传入 {"api_key": "..."}
memory = LongTermMemory()

# 存储会话总结（session_id/question/answer 均必填）
memory.add_session_summary(
    session_id="s1",
    question="高血压患者如何管理？",
    answer="建议限盐、规律监测血压并按医嘱用药。",
    user_id="medix_local",
)

# 检索相关记忆
results = memory.search_similar_sessions(
    "高血压患者如何管理？", limit=5, user_id="medix_local"
)
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

## 🔐 数据与隐私

MediX 默认在本机运行，但**会诊本身需要调用你在 `.env` 中配置的第三方大模型接口**：每次提问都会把原始问题、患者档案上下文（年龄、性别、病史、过敏史、用药清单、近期指标、待办提醒等）以及最近对话/相似历史案例发送给该 LLM 提供商。启用 Mem0 后，还会把会话摘要（问题前 2000 字与回答前 500 字）和用于检索的原始问题发送到 Mem0 云服务。附件文件与本地备份不会上传到这些服务。主要数据的存储位置与删除行为：

| 数据 | 位置 | 删除会话 / 档案时 |
|------|------|------------------|
| 会话与消息 | `data/sessions.db`（SQLite） | 删除会话时清除 |
| 短期记忆 | 进程内存，或配置后的 Redis | 清除并写入墓碑，避免重启后恢复 |
| 会诊总结 | `memory/swarm/session_summaries/<日期>/<会话>.md` | 删除会话时删除文件并清理空日期目录 |
| 长期记忆 | Mem0（配置 `MEM0_API_KEY` 后） | 按会话精确删除；删除或重命名档案时清空旧命名空间 |
| 本地备份 | `data/backups/*.json` | **不会**自动删除；请在历史抽屉的“管理备份”中手动删除 |
| 浏览器导出 | 用户下载目录 | 由用户自行管理 |

- 发送给 LLM 的内容：原始问题、档案上下文（含病史/过敏/用药/指标）、短期记忆中的最近对话、Mem0 检索到的相似案例摘要。不希望外发的信息请不要填写在提问或档案中；确有隐私要求时请改用本地模型或自建网关。
- 发送给 Mem0 的内容：会话摘要文本（问题与回答片段）、检索时的原始问题、档案/会话命名空间的 SHA-256 哈希、时间戳与模式等元数据；不发送档案姓名原文。
- 长期记忆命名空间只保存档案名 / 会话 ID 的 SHA-256 哈希，不向 Mem0 发送患者姓名。
- 本地备份与浏览器导出的 JSON 都是明文，包含完整健康数据；请勿提交 Git 或上传到不可信位置。
- 按天清理（维护清理）会同步清理长期记忆与会诊总结，但历史备份仍可恢复被清理的数据；为隐私清理时请同时删除备份。
- 附件文件只保存在本机 `data/attachments`（自定义会话库使用独立目录）；JSON 备份不包含附件，请另行备份附件目录。

## ⚠️ 免责声明

本系统仅供学习和研究使用，不能替代专业医生的诊断和治疗。所有医疗建议仅供参考，如有健康问题请及时就医。

## 📄 许可证

本项目采用 [MIT License](LICENSE)。

## 🙏 致谢

- 感谢 [FastAPI](https://fastapi.tiangolo.com/)、[Milvus](https://milvus.io/)、[Mem0](https://mem0.ai/) 等开源项目
- 感谢 [火山引擎](https://www.volcengine.com/) 提供的 LLM API

---
