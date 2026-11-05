---
name: clinical-guideline
description: Search clinical practice guidelines and expert consensus documents. Use when user needs authoritative treatment standards or evidence-based medical recommendations.
---

# Clinical Guideline (临床指南)

检索临床诊疗指南和专家共识，获取权威的治疗标准。

## When to Use

- 需要权威指南或专家共识支持
- 需要标准治疗路径或诊断标准
- 需要引用指南名称、发布机构与年份

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | ✅ | 疾病名称或治疗主题 |
| `max_results` | integer | ❌ | 返回的指南条数，默认 1（限制在 1–10） |

## 调用方式

```bash
/clinical-guideline 高血压 2
```

## 返回格式

```json
{
  "answer": "1–N 条指南正文（每条含指南名称、发布机构、发布年份与内容）",
  "guideline_title": "中国高血压防治指南（2023年修订版）",
  "organization": "中华医学会心血管病学分会",
  "year": "2023年",
  "source": "向量数据库",
  "sources": [{"title": "...", "filename": "...", "score": 0.88}]
}
```

未找到时返回 `source="未找到"` 与说明文字，`sources=[]`。
