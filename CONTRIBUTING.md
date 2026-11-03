# 贡献指南

感谢参与 MediX。这是一个学习研究用途的多智能体医疗助手，欢迎提交 Bug 报告和功能建议。

## 开始之前

- Python 3.10+（推荐 3.12）
- 按 README 的「从零开始运行」准备环境
- 复制 `.env.example` 为 `.env` 并填写 API Key。**不要提交 `.env`**

## 分支与提交规范

分支命名：

- `feat/<topic>`：新功能
- `fix/<topic>`：缺陷修复
- `docs/<topic>`：文档
- `test/<topic>`：测试
- `refactor/<topic>`：重构

提交信息使用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat(web): 增加会话历史抽屉
fix(knowledge): 修正 COSINE 相似度分数反转
test(session): 补充会话持久化用例
docs: 更新运行说明
```

一个 PR 只做一件事；PR 描述按仓库的 `pull_request_template.md` 填写，标题使用语义前缀。

## 测试要求

每次提交前至少运行离线单元测试：

```bash
python -m pytest tests
```

涉及 LLM 调用、Agent Loop 或 Swarm 行为的改动，再运行完整集成测试（会产生 API 费用）：

```bash
python examples/test_all.py
```

界面改动请启动 Web 服务并在浏览器实际验证，PR 中附截图：

```bash
python api_server.py
```

## 代码约定

- 日志统一使用 `loguru.logger`，库代码中不要新增 `print()`
- 新增运行时依赖写入 `requirements.txt`，并在 PR 中说明原因
- Skills 保持自包含：`.claude/skills/*/script/` 不要反向依赖 `agents/` 或 `swarm/`
- 不要提交密钥、`.env`、`data/` 下的会话数据库和 `knowledge/data/` 的本地向量库
- 修改接口或行为时同步更新 README

## 环境自检

提 Issue 或 PR 前可以运行：

```bash
python scripts/diagnose.py
```

需要验证模型与知识库连通性时：

```bash
python scripts/diagnose.py --llm --kb
```

把输出（不包含 API Key）粘贴到 Issue 的「环境」一栏。
