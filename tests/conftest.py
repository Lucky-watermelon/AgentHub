"""测试环境：数据目录指向临时目录，避免污染本地 data/。"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="agenthub_test_"))
os.environ["AH_DATA_DIR"] = str(_ROOT / "data")
os.environ["AH_REPORTS_DIR"] = str(_ROOT / "reports")
os.environ["AH_LLM_MOCK"] = "true"
os.environ["AH_SEED_DEMO"] = "true"
