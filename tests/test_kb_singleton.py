"""知识库单例初始化的线程安全回归测试。"""
import threading
import time

import pytest

pytest.importorskip("sentence_transformers")

from knowledge.milvus_kb import MedicalKnowledgeBase


def test_concurrent_first_init_loads_once(monkeypatch):
    original_instance = MedicalKnowledgeBase._instance
    original_initialize = MedicalKnowledgeBase._initialize
    calls = []
    MedicalKnowledgeBase._instance = None

    def fake_initialize(self, *args, **kwargs):
        calls.append(1)
        time.sleep(0.05)
        self._initialized = True

    monkeypatch.setattr(
        MedicalKnowledgeBase, "_initialize", fake_initialize
    )
    instances = []

    def worker():
        instances.append(id(MedicalKnowledgeBase()))

    threads = [threading.Thread(target=worker) for _ in range(5)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        MedicalKnowledgeBase._instance = original_instance
        MedicalKnowledgeBase._initialize = original_initialize

    assert len(set(instances)) == 1
    assert len(calls) == 1
