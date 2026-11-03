"""
医学知识库模块

提供基于 Milvus 的向量数据库封装。

注意：不要在包初始化时直接 import milvus_kb——它会加载 sentence-transformers
和 torch（数百 MB 的依赖）。改成惰性导入后，`import knowledge.scoring` 之类
的轻量使用不会再把整个向量模型拖进来，离线单元测试也能在最小依赖下运行。
"""

__all__ = ['MedicalKnowledgeBase']


def __getattr__(name):
    if name == 'MedicalKnowledgeBase':
        from .milvus_kb import MedicalKnowledgeBase
        return MedicalKnowledgeBase
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
