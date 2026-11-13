"""
Search Similar Cases Skill
搜索相似历史案例（长期记忆/Mem0）
"""
from typing import Dict, Any
import asyncio
import math
from loguru import logger


async def search_similar_cases(query: str, max_results: int = 3) -> Dict[str, Any]:
    """
    搜索相似的历史案例

    Args:
        query: 查询内容
        max_results: 最多返回结果数（默认3）

    Returns:
        {
            "answer": "格式化的相似案例",
            "total_found": 找到的案例数,
            "query": "原始查询"
        }
    """
    logger.info(
        f"Searching similar cases: query_length={len(query or '')}, "
        f"max_results={max_results}"
    )

    try:
        max_results = max(1, min(int(max_results or 3), 10))
    except (TypeError, ValueError):
        max_results = 3

    try:
        from core.session_context import get_memory_scope

        scope = get_memory_scope()
        if not scope:
            return {
                "answer": "当前会话没有可用的长期记忆作用域，无法检索历史案例。",
                "total_found": 0,
                "query": query
            }

        # 删除清理尚未完成的作用域不能读取：同名档案重建/清理重试
        # 窗口内，云端仍可能留着已删除患者的旧摘要（与协调器预检索
        # 的门禁保持一致）
        try:
            from memory import get_session_store

            pending = await asyncio.to_thread(
                get_session_store().memory_scope_cleanup_pending,
                scope,
            )
        except Exception:
            pending = False
        if pending:
            return {
                "answer": (
                    "该档案/会话的长期记忆清理尚未完成，"
                    "暂不检索历史案例，请稍后再试。"
                ),
                "total_found": 0,
                "query": query,
            }

        # 导入长期记忆系统
        from memory.long_term import LongTermMemory

        # 获取长期记忆实例；客户端初始化可能做网络校验，必须放线程池
        memory = await asyncio.to_thread(LongTermMemory)

        if not memory.enabled:
            return {
                "answer": "长期记忆功能未启用。无法搜索历史案例。",
                "total_found": 0,
                "query": query
            }

        # 只搜索当前患者/会话作用域，避免跨患者泄漏；
        # Mem0 是同步 HTTP 调用，放到线程池避免阻塞事件循环
        results = await asyncio.to_thread(
            memory.search_similar_sessions,
            query=query,
            limit=max_results,
            user_id=scope,
        )

        if not results:
            return {
                "answer": f"未找到与'{query}'相关的历史案例。",
                "total_found": 0,
                "query": query
            }

        # 格式化输出
        formatted_cases = format_cases(results)

        return {
            "answer": formatted_cases,
            "total_found": len(results),
            "query": query
        }

    except Exception as e:
        logger.error(f"Failed to search similar cases: {e}")
        return {
            "answer": "抱歉，搜索历史案例时出错，请稍后重试。",
            "total_found": 0,
            "query": query
        }


def format_cases(results: list) -> str:
    """
    格式化相似案例

    Args:
        results: 检索结果列表

    Returns:
        格式化的字符串
    """
    if not results:
        return "未找到相似案例。"

    output = ["【相似历史案例】\n"]

    for i, case in enumerate(results, 1):
        content = case.get("content", "")
        try:
            score = float(case.get("score", 0.0))
        except (TypeError, ValueError, OverflowError):
            score = 0.0
        if not math.isfinite(score):
            score = 0.0
        metadata = case.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        output.append(f"【案例 {i}】（相似度: {score:.2%}）")
        output.append(content[:300] + "..." if len(content) > 300 else content)

        # 显示时间戳（如果有）
        timestamp = metadata.get("timestamp", "")
        if timestamp:
            output.append(f"时间: {timestamp}")

        output.append("")  # 空行分隔

    return "\n".join(output)


# 同步版本
def search_similar_cases_sync(query: str, max_results: int = 3) -> Dict[str, Any]:
    """同步版本的搜索相似案例"""
    import asyncio
    return asyncio.run(search_similar_cases(query, max_results))


if __name__ == "__main__":
    # 测试
    import asyncio

    test_query = "高血压患者的生活方式建议"
    result = asyncio.run(search_similar_cases(test_query))

    print("=" * 70)
    print(f"查询: {test_query}")
    print("=" * 70)
    print(result["answer"])
    print("=" * 70)
    print(f"找到案例数: {result['total_found']}")
