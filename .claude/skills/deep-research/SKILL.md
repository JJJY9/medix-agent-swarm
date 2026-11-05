---
name: deep-research
description: Conduct deep research combining web search, knowledge base, and evidence synthesis. Use for complex medical questions requiring latest research or comprehensive literature review.
---

# Deep Research (深度研究)

综合网络搜索、知识库和证据综合的深度研究能力。

## When to Use

- 复杂的医学问题，需要多源信息综合
- 需要最新研究进展和文献综述
- 需要高置信度的证据支持

## 底层实现

- 工作流: `DeepResearchWorkflow`
- 数据源: 网络搜索 + Milvus 向量数据库 + 证据综合（按实际可用来源标注）
- 技术: 并行搜索和检索 + LLM 证据综合

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | ✅ | 研究问题 |
| `max_results_scale` | integer | ❌ | 检索规模倍数，默认 2（限制在 1–5）；网络结果上限 `×5`、知识库上限 `×3` |

## 调用方式

```bash
/deep-research 糖尿病的最新治疗方法
```

## 返回格式

```json
{
  "answer": "格式化的研究报告（关键发现、综合总结、证据等级、置信度、冲突与建议）",
  "findings": ["发现1", "发现2"],
  "confidence": "high | medium | low",
  "source_count": 4,
  "evidence_level": "A | B | C",
  "status": "completed | error",
  "data_sources": "网络搜索 + 医学知识库（Milvus RAG）+ 证据综合"
}
```

研究失败时返回 `status="error"`、`findings=[]`、`confidence="low"`，并在 `answer` 中说明失败原因；`data_sources` 只列出实际用到的来源（无网络结果时不会声称使用了网络搜索）。
