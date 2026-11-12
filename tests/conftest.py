"""让 tests/ 下的用例可以直接 import 项目根目录的模块。"""
import os
import sys
from pathlib import Path

# 测试环境跳过 embedding 模型预热，避免无谓的模型加载与后台线程
os.environ.setdefault("MEDIX_SKIP_WARMUP", "1")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
