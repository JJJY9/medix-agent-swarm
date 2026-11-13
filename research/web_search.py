"""
网络搜索模块

提供医学领域的网络搜索能力，基于 DuckDuckGo Search API (DDGS)
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from loguru import logger
import asyncio
import ipaddress
import logging
import socket
import httpx

from core.security import redact_url_queries

# ddgs 内部以 INFO 级别打印后端的异常 repr（含完整请求 URL 与查询串），
# 统一压到 WARNING 以上，避免患者查询内容随日志泄露
logging.getLogger("ddgs").setLevel(logging.WARNING)

#: 单页响应体上限：搜索结果可能指向超大页面，不能整页读入内存
MAX_FETCH_BYTES = 2 * 1024 * 1024
#: 重定向最大跳数
MAX_FETCH_REDIRECTS = 4

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


class UnsafeFetchTarget(ValueError):
    """URL 指向本机/内网或使用了不允许的协议。"""


def _validate_fetch_target(url: str) -> str:
    """校验待抓取 URL：只允许 http/https 且必须解析到公网地址。

    搜索结果来自第三方，若直接 GET 可被用于探测/读取内网服务
    （127.0.0.1、192.168.x.x、169.254.169.254 等），故在请求前
    解析主机并拒绝私有/回环/链路本地地址。
    """
    text = str(url or "").strip()
    if not text:
        raise UnsafeFetchTarget("URL 为空")
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise UnsafeFetchTarget("URL 无法解析") from exc
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UnsafeFetchTarget(f"不支持的协议：{scheme or '空'}")
    host = (parts.hostname or "").strip().lower()
    if not host:
        raise UnsafeFetchTarget("缺少主机名")
    if host == "localhost" or host.endswith(".localhost"):
        raise UnsafeFetchTarget("不允许抓取本机地址")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as exc:
        raise UnsafeFetchTarget("主机名无法解析") from exc
    for info in infos:
        raw = str(info[4][0] or "").split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            continue
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_unspecified or ip.is_reserved
        ):
            raise UnsafeFetchTarget("不允许抓取内网地址")
    return text


def _extract_html_text(
    body: bytes, encoding: Optional[str], max_length: int
) -> str:
    """在后台线程解析网页正文；无 charset 声明时交给 BeautifulSoup 嗅探。"""
    if BeautifulSoup is None:
        return ""
    text: Optional[str] = None
    if encoding:
        try:
            text = body.decode(encoding, errors="replace")
        except LookupError:
            text = None
    soup = BeautifulSoup(
        text if text is not None else body, "html.parser"
    )
    for script in soup(["script", "style"]):
        script.decompose()
    raw = soup.get_text()
    lines = (line.strip() for line in raw.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    joined = " ".join(chunk for chunk in chunks if chunk)
    if len(joined) > max_length:
        joined = joined[:max_length] + "..."
    return joined


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
                #: 任一后端明确返回「无结果」时，整体视为空结果而不是故障
                empty_results_seen = False
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
                        message = str(e)
                        if "no results found" in message.lower():
                            # ddgs 9.x 对“确实没有结果”抛异常而不是返回
                            # 空列表：这是正常结果，不是后端故障，也不该重试
                            empty_results_seen = True
                            logger.info(
                                "No results found "
                                f"(query_length={len(str(query or ''))})"
                            )
                            continue
                        # 异常文本常带完整请求 URL（含 URL 编码的查询串），
                        # 与 #682 的「只记 query_length」口径保持一致
                        backend_errors.append(
                            f"{backend}: {redact_url_queries(message)}"
                        )
                        logger.debug(
                            f"DDGS backend {backend} failed: "
                            f"{redact_url_queries(message)}"
                        )
                        # 切换后端前稍作等待，避免连续请求加剧限流
                        await asyncio.sleep(0.5)
                        continue

                # 所有后端都失败时向上抛出，让重试与退避真正生效，
                # 而不是把请求失败伪装成“没有搜索结果”
                if not search_results and backend_errors and not empty_results_seen:
                    raise RuntimeError(
                        "所有搜索后端均失败：" + "；".join(backend_errors)
                    )
                if not search_results:
                    # 任一后端明确无结果（ddgs 9.x 以异常表达）或后端
                    # 正常返回空：立即返回，不要重试、不要放大请求
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
                logger.warning(
                    f"Web search error (attempt {attempt + 1}): "
                    f"{redact_url_queries(str(e))}"
                )

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
            current = await asyncio.to_thread(_validate_fetch_target, url)
            async with httpx.AsyncClient(
                timeout=self.timeout, follow_redirects=False
            ) as client:
                for _hop in range(MAX_FETCH_REDIRECTS):
                    async with client.stream("GET", current) as response:
                        if response.is_redirect:
                            location = response.headers.get("location")
                            if not location:
                                return None
                            # 每一跳都要重新校验，防止 302 指向内网地址
                            current = await asyncio.to_thread(
                                _validate_fetch_target,
                                urljoin(current, location),
                            )
                            continue
                        response.raise_for_status()
                        chunks: List[bytes] = []
                        total = 0
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > MAX_FETCH_BYTES:
                                logger.warning(
                                    "网页响应超过抓取上限，已截断"
                                )
                                break
                            chunks.append(chunk)
                        body = b"".join(chunks)
                        encoding = response.charset_encoding
                    # 解析大页面会占用 CPU：放到线程里，不阻塞事件循环
                    return await asyncio.to_thread(
                        _extract_html_text, body, encoding, max_length
                    )
                return None

        except UnsafeFetchTarget as e:
            logger.warning(f"拒绝抓取不安全的地址：{e}")
            return None
        except Exception as e:
            logger.error(
                "Failed to fetch content: "
                f"{redact_url_queries(str(e))}"
            )
            return None

    async def search_with_content(
        self,
        query: str,
        max_results: int = 5,
        fetch_full_content: bool = False,
        allowed_domains: Optional[List[str]] = None,
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
        if allowed_domains:
            # 全文抓取前按白名单收敛，避免抓取任意第三方 URL
            results = self.filter_by_domain(results, allowed_domains)

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
