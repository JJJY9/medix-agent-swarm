"""让 tests/ 下的用例可以直接 import 项目根目录的模块。"""
import os
import sys
from pathlib import Path

# 测试环境跳过 embedding 模型预热，避免无谓的模型加载与后台线程
os.environ.setdefault("MEDIX_SKIP_WARMUP", "1")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_auth_token_proofs():
    """认证成功后的短期凭证是进程内缓存：用例之间必须隔离，避免上一条
    用例的成功认证让本用例的「正确令牌仍被锁定」断言意外通过。"""
    module = sys.modules.get("api_server")
    if module is not None:
        module._known_token_proofs.clear()
    yield
    module = sys.modules.get("api_server")
    if module is not None:
        module._known_token_proofs.clear()
