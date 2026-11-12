"""
导入医学知识文档到知识库

数据来源：
- knowledge/data/documents/*.txt|md|pdf   内置文档
- knowledge/data/uploads/*.txt|md|pdf     用户上传文档

默认重建索引（幂等，重复执行不会重复入库）；加 --append 则追加导入。
"""
import argparse
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from loguru import logger
from core.console import ensure_utf8_stdio
from knowledge.milvus_kb import MedicalKnowledgeBase
from knowledge.service import KnowledgeService

ensure_utf8_stdio()


def main() -> int:
    parser = argparse.ArgumentParser(description="导入医学知识文档")
    parser.add_argument(
        "--append",
        action="store_true",
        help="已弃用：追加会重复写入全部文档，现在统一按幂等重建执行",
    )
    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("开始导入医学知识文档")
    logger.info("=" * 70)

    service = KnowledgeService()
    if args.append:
        logger.warning(
            "--append 会重复写入全部文档，已改为幂等重建（不再追加）"
        )
    try:
        result = service.rebuild(replace_existing=True)
    except ValueError as e:
        logger.error(str(e))
        return 1

    logger.info(
        f"完成：{result['documents']} 个文档（内置 {result['builtin_documents']} / "
        f"上传 {result['uploaded_documents']}），共 {result['chunks']} 个向量块"
    )

    kb = MedicalKnowledgeBase()
    logger.info("\n🔍 测试语义检索...")
    test_queries = [
        ("血压高怎么办", "lifestyle"),
        ("糖尿病编码", "disease_classification"),
        ("高血压治疗指南", "clinical_guideline"),
    ]

    for query, filter_type in test_queries:
        results = kb.search(query, top_k=1, filter_type=filter_type)
        if results:
            logger.info(
                f"  ✅ '{query}' → {results[0]['metadata'].get('disease', 'N/A')} "
                f"(相似度: {results[0]['score']:.3f})"
            )
        else:
            logger.warning(f"  ⚠️  '{query}' → 未找到结果")
    return 0


if __name__ == "__main__":
    sys.exit(main())
