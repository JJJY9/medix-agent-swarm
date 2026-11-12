"""
网络搜索模块

提供医学领域的网络搜索能力，基于 DuckDuckGo Search API (DDGS)
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from urllib.parse import urlsplit
from loguru import logger
import asyncio
import httpx

try:
    from bs4 import BeautifulSoup
except ImportError:  # 离线测试环境可能没有安装 beautifulsoup4
    BeautifulSoup = None
    logger.warning("beautifulsoup4 不可用，网页正文抓取已禁用")

# 尝试导入 ddgs (新包名) 或 duckduckgo_search (旧包名)
try:
    try:
        from ddgs import DDGS
        # ddgs 9.x 已禁用 bing 后端，继续指定会每次先失败一轮
        _DDGS_BACKENDS = ("duckduckgo", "auto")
    except ImportError:
        from duckduckgo_search import DDGS
        # 旧版 duckduckgo_search 只接受 api/html/lite
        _DDGS_BACKENDS = ("api", "html", "lite")
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False
    _DDGS_BACKENDS = ()
    logger.error("DDGS not available. Install with: pip install ddgs")


@dataclass
class SearchResult:
    """搜索结果数据结构"""
    title: str
    url: str
    snippet: str  # 摘要
    source: str = "web"  # 来源标识


class WebSearchTool:
    """
    网络搜索工具

    功能：
    - 使用 DuckDuckGo 进行网络搜索
    - 专注于医学领域网站
    - 结果去重和质量过滤
    """

    def __init__(self, timeout: int = 30):
        """
        初始化搜索工具

        Args:
            timeout: HTTP 请求超时时间（秒）
        """
        self.timeout = timeout

        # 医学领域权威网站白名单
        self.medical_domains = [
            "pubmed.ncbi.nlm.nih.gov",
            "mayoclinic.org",
            "webmd.com",
            "who.int",
            "cdc.gov",
            "nih.gov",
            "uptodate.com",
            "medscape.com",
            "healthline.com",
            "medicalnewstoday.com"
        ]

    async def search(
        self,
        query: str,
        max_results: int = 10,
        region: str = "cn-zh",  # 中国区域，更适合中文搜索
        safesearch: str = "on",  # 严格安全搜索
        timelimit: Optional[str] = None,  # 时间限制：'d'(天), 'w'(周), 'm'(月), 'y'(年)
        retry_count: int = 2  # 重试次数
    ) -> List[SearchResult]:
        """
        执行搜索（参考 shanglv 项目的实现）

        Args:
            query: 搜索查询
            max_results: 最大结果数
            region: 地区设置（cn-zh = 中国区域）
            safesearch: 安全搜索级别（on = 严格）
            timelimit: 时间限制
            retry_count: 重试次数

        Returns:
            搜索结果列表
        """
        if not DDGS_AVAILABLE:
            logger.error("DDGS not available, cannot perform web search")
            return []

        for attempt in range(retry_count + 1):
            try:
                logger.info(
                    f"Web searching (attempt {attempt + 1}): "
                    f"query_length={len(str(query or ''))} "
                    f"(max_results={max_results})"
                )

                # 在查询中添加医学相关关键词，提高结果相关性
                enhanced_query = f"{query} 医学" if "医学" not in query else query

                # 使用 DDGS 搜索（参考 shanglv：多后端尝试，不使用上下文管理器）
                results = []
                search_results = []

                # 尝试多个后端：bing → duckduckgo → auto（旧包为 api/html/lite）
                backend_errors = []
                backends = _DDGS_BACKENDS or ("auto",)
                for backend in backends:
                    try:
                        ddgs = DDGS()
                        # DDGS.text 是同步阻塞调用，必须放到线程里，
                        # 否则 gather 的并发搜索会退化为串行并卡住事件循环
                        raw = await asyncio.to_thread(
                            ddgs.text,
                            enhanced_query,
                            max_results=max_results * 2,  # 获取更多结果用于过滤
                            safesearch=safesearch,
                            region=region,
                            backend=backend  # 关键：指定后端
                        )
                        search_results = list(raw)
                        if search_results:
                            logger.debug(f"DDGS backend {backend} succeeded with {len(search_results)} results")
                            break
                    except Exception as e:
                        backend_errors.append(f"{backend}: {e}")
                        logger.debug(f"DDGS backend {backend} failed: {e}")
                        # 切换后端前稍作等待，避免连续请求加剧限流
                        await asyncio.sleep(0.5)
                        continue

                # 所有后端都失败时向上抛出，让重试与退避真正生效，
                # 而不是把请求失败伪装成“没有搜索结果”
                if not search_results and backend_errors:
                    raise RuntimeError(
                        "所有搜索后端均失败：" + "；".join(backend_errors)
                    )
                if not search_results:
                    # 后端正常返回但确实没有结果：立即返回，不要重试
                    logger.info(
                        "No results found "
                        f"(query_length={len(str(query or ''))})"
                    )
                    return []

                # 处理搜索结果
                for result in search_results:
                    search_result = SearchResult(
                        title=result.get("title", ""),
                        url=result.get("href", ""),
                        snippet=result.get("body", ""),
                        source="web"
                    )
                    results.append(search_result)

                    if len(results) >= max_results:
                        break

                if results:
                    logger.info(
                        f"Found {len(results)} results "
                        f"(query_length={len(str(query or ''))})"
                    )
                    return results
                else:
                    logger.warning(
                        "No results found "
                        f"(query_length={len(str(query or ''))})"
                    )

            except Exception as e:
                logger.warning(f"Web search error (attempt {attempt + 1}): {e}")

                if attempt < retry_count:
                    # 等待后重试
                    await asyncio.sleep(2 ** attempt)  # 指数退避
                else:
                    logger.error(f"Web search failed after {retry_count + 1} attempts")
                    return []

        return []

    def filter_by_domain(
        self,
        results: List[SearchResult],
        allowed_domains: Optional[List[str]] = None
    ) -> List[SearchResult]:
        """
        按域名过滤结果

        Args:
            results: 搜索结果
            allowed_domains: 允许的域名列表（默认使用医学域名白名单）

        Returns:
            过滤后的结果
        """
        if allowed_domains is None:
            allowed_domains = self.medical_domains

        normalized_domains = [
            str(domain or "").strip().lower().strip(".")
            for domain in allowed_domains
            if str(domain or "").strip()
        ]

        def host_allowed(url: str) -> bool:
            try:
                host = (
                    urlsplit(str(url or "")).hostname or ""
                ).lower().rstrip(".")
            except ValueError:
                return False
            if not host:
                return False
            return any(
                host == domain or host.endswith("." + domain)
                for domain in normalized_domains
            )

        filtered = [
            result for result in results if host_allowed(result.url)
        ]

        logger.info(f"Filtered {len(filtered)}/{len(results)} results by domain")
        return filtered

    async def fetch_content(
        self,
        url: str,
        max_length: int = 2000
    ) -> Optional[str]:
        """
        抓取网页内容

        Args:
            url: 网页 URL
            max_length: 最大内容长度

        Returns:
            网页文本内容（提取正文）
        """
        try:
            if BeautifulSoup is None:
                logger.warning("beautifulsoup4 未安装，无法抓取网页正文")
                return None
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(url)
                response.raise_for_status()

                # 使用 BeautifulSoup 提取正文
                soup = BeautifulSoup(response.text, 'html.parser')

                # 移除 script 和 style 标签
                for script in soup(["script", "style"]):
                    script.decompose()

                # 提取文本
                text = soup.get_text()

                # 清理空白字符
                lines = (line.strip() for line in text.splitlines())
                chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
                text = ' '.join(chunk for chunk in chunks if chunk)

                # 限制长度
                if len(text) > max_length:
                    text = text[:max_length] + "..."

                return text

        except Exception as e:
            logger.error(f"Failed to fetch content from {url}: {e}")
            return None

    async def search_with_content(
        self,
        query: str,
        max_results: int = 5,
        fetch_full_content: bool = False
    ) -> List[Dict[str, Any]]:
        """
        搜索并获取内容

        Args:
            query: 搜索查询
            max_results: 最大结果数
            fetch_full_content: 是否抓取完整内容

        Returns:
            包含内容的搜索结果
        """
        # 执行搜索
        results = await self.search(query, max_results=max_results)

        # 如果需要，抓取完整内容
        enriched_results = []
        for result in results:
            enriched = {
                "title": result.title,
                "url": result.url,
                "snippet": result.snippet,
                "source": result.source,
                "full_content": None
            }

            if fetch_full_content:
                content = await self.fetch_content(result.url)
                enriched["full_content"] = content

            enriched_results.append(enriched)

        return enriched_results


# 便捷函数
async def search_medical_web(
    query: str,
    max_results: int = 10
) -> List[SearchResult]:
    """
    快速搜索医学网络信息

    Args:
        query: 搜索查询
        max_results: 最大结果数

    Returns:
        搜索结果列表
    """
    tool = WebSearchTool()
    return await tool.search(query, max_results=max_results)
