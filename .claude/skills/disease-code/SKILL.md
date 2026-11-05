---
name: disease-code
description: Search for ICD-10 disease codes and classifications. Use when user asks about disease coding, classification, or needs ICD-10 codes for medical records.
---

# Disease Code (疾病编码)

查询 ICD-10 疾病编码和分类信息。

## When to Use

- 用户询问疾病的 ICD-10 编码
- 需要疾病分类或病历编码
- 需要标准疾病名称与编码对照

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `disease_name` | string | ✅ | 标准疾病名称，如「高血压」「冠心病」 |

## 调用方式

```bash
/disease-code 冠心病
```

## 返回格式

```json
{
  "answer": "格式化的疾病编码信息（命中文档内容）",
  "icd10_code": "I25",
  "category": "循环系统疾病",
  "source": "向量数据库",
  "sources": [{"title": "冠心病", "filename": "...", "score": 0.82}]
}
```

未找到时返回 `icd10_code=""`、`category=""`、`source="未找到"`，`answer` 会说明未找到并建议使用更标准的疾病名称。编码只从与 `disease_name` 匹配的疾病小节提取，不会返回文档里其他疾病的编码。
