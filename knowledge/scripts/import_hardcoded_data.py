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
from knowledge.milvus_kb import MedicalKnowledgeBase
from knowledge.service import KnowledgeService


def main():
    parser = argparse.ArgumentParser(description="导入医学知识文档")
    parser.add_argument(
        "--append",
        action="store_true",
        help="追加导入而不是重建索引（默认重建，避免重复）",
    )
    args = parser.parse_args()

    logger.info("=" * 70)
    logger.info("开始导入医学知识文档")
    logger.info("=" * 70)

    service = KnowledgeService()
    try:
        result = service.rebuild(replace_existing=not args.append)
    except ValueError as e:
        logger.error(str(e))
        return

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


if __name__ == "__main__":
    main()
