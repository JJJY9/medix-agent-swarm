---
name: assess-risk
description: Assess symptom risk level (low/medium/high/emergency/unknown). Use when user describes symptoms and needs risk evaluation to determine urgency of medical attention.
---

# Assess Risk (风险评估)

评估症状的风险等级，判断是否需要紧急就医。

## When to Use

- 用户描述症状，需要评估严重程度
- 判断是否需要紧急就医
- 风险分级（低/中/高/紧急）

## 底层实现

- 技术: 风险规则引擎 + 急症红旗预检 + Milvus 向量数据库
- 数据源: 高风险症状规则库 + 医学知识库（RAG）
- 增强: 从知识库检索风险相关的医学建议

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `symptoms` | string | ✅ | 症状描述，多个症状可用逗号分隔 |

## 调用方式

```bash
/assess-risk 胸痛,呼吸困难
```

## 返回格式

```json
{
  "answer": "格式化的风险评估结果",
  "risk_level": "low | medium | high | emergency | unknown",
  "recommendation": "就医建议",
  "kb_advice": "知识库建议或 null",
  "sources": []
}
```

- `emergency`：命中急症红旗（如胸痛伴呼吸困难、意识改变），建议立即拨打 120 或前往急诊。
- `unknown`：未提供有效症状描述，需补充信息后再评估，不能当作低危。
- 症状为空时不会发起知识库检索，也不会默认返回 `low`。
