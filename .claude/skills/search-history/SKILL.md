---
name: search-history
description: Search current session conversation history (short-term memory). Use when user asks "我之前问过什么", "历史对话", "上次说了什么", or needs context from earlier in the same conversation.
---

# Search Conversation History (搜索会话历史)

搜索当前会话的历史对话记录（短期记忆）。

## When to Use

- 用户问"我之前问过什么""我们刚才讨论了什么""上次的话题"
- 需要回顾当前会话的早期内容
- 需要上下文连贯性（如"继续上次的话题"）

## 参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `session_id` | string | ✅ | 当前会话 ID，取自系统信息「当前会话ID」 |
| `limit` | integer | ❌ | 返回的对话轮数，默认 10（限制在 1–50）；实际读取 `limit × 2` 条消息 |

## 调用方式

```bash
/search-history 20260420-001919-xxx 5
```

## 返回格式

```json
{
  "answer": "格式化的历史对话记录（按轮次分组）",
  "total_messages": 10,
  "session_id": "20260420-001919-xxx"
}
```

`total_messages` 是本次返回的消息条数（user + assistant），不是对话轮数。

## Note

This skill searches **current session only**. For cross-session history, use `/search-similar-cases`.
