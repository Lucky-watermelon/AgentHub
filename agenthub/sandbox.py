"""简易 Python 沙箱：AST 白名单 + 独立进程 + 超时 + 输出上限。

**边界说明**：这是演示 / 内部可信代码级别的隔离，不是安全边界。
- 独立子进程（``-s -B``）、临时工作目录、清理环境变量
- AST 白名单：只允许安全标准库；禁止文件、系统、网络、进程与动态执行
- 超时强杀；输出截断；POSIX 下额外限制地址空间与 CPU 时间
- Windows 没有可移植的地址空间限制，只做进程 + 超时 + 输出控制
因此不要用它执行不可信第三方代码；生产环境应换容器 / gVisor / Firecracker。
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ALLOWED_IMPORTS = {
    "math", "json", "re", "datetime", "statistics", "collections", "itertools",
    "functools", "operator", "decimal", "fractions", "random", "string", "typing",
    "dataclasses", "enum", "heapq", "bisect", "textwrap", "unicodedata", "uuid",
    "hashlib", "base64", "struct", "copy",
}
FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "__import__", "input", "breakpoint",
    "globals", "locals", "vars", "getattr", "setattr", "delattr", "memoryview",
    "__builtins__",
}
MAX_OUTPUT = 6000
MAX_CODE = 20000

PREAMBLE = '''import json as _json, sys as _sys

def _load():
    try:
        return _json.loads(_sys.stdin.read() or "{}")
    except Exception:
        return {}

_ctx = _load()
payload = _ctx.get("payload") or {}
features = _ctx.get("features") or {}
customer_no = _ctx.get("customer_no") or ""
params = _ctx.get("params") or {}

def emit(data):
    _sys.stdout.write(_json.dumps(data, ensure_ascii=False, default=str))
    _sys.stdout.flush()
    raise SystemExit(0)
'''


def check_code(code: str) -> list[str]:
    """静态检查脚本：语法、导入白名单、危险内建与私有属性。"""
    try:
        tree = ast.parse(code or "")
    except SyntaxError as exc:
        return [f"语法错误：{exc.msg}（第 {exc.lineno} 行）"]
    errors: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_IMPORTS:
                    errors.append(f"禁止导入：{alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if root not in ALLOWED_IMPORTS:
                errors.append(f"禁止导入：{node.module or '?'}")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            errors.append(f"禁止访问私有属性：{node.attr}")
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES or node.id.startswith("__"):
                errors.append(f"禁止使用：{node.id}")
    return list(dict.fromkeys(errors))


def normalize_scripts(scripts: list | None) -> list[dict]:
    """清洗脚本列表：只保留 name / language / code，限制长度。"""
    out: list[dict] = []
    for item in scripts or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:120]
        code = str(item.get("code") or "")
        if not name or not code.strip():
            continue
        out.append({"name": name, "language": str(item.get("language") or "python").lower()[:20],
                    "code": code[:MAX_CODE]})
    return out


def _posix_limits(timeout_s: int):
    """POSIX 下的资源限制：地址空间 + CPU 时间。"""
    import resource

    def limit():
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (timeout_s, timeout_s))

    return limit


def run_python(code: str, context: dict | None = None, timeout_s: int = 5) -> dict:
    """在受限子进程中执行 Python 脚本。

    脚本通过 stdin 接收 ``{"payload", "features", "customer_no", "params"}``，
    通过 ``emit(dict)`` 输出 JSON 结果（也可以直接 print JSON）。
    """
    errors = check_code(code)
    if errors:
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": "",
                "dur_ms": 0, "timed_out": False, "errors": errors, "result": None}

    timeout = max(1, min(int(timeout_s or 5), 30))
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="ah_sandbox_") as tmp:
            script_path = Path(tmp) / "main.py"
            script_path.write_text(PREAMBLE + "\n" + code, encoding="utf-8")
            env = {
                "PATH": os.environ.get("PATH", ""),
                "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
                "TEMP": tmp, "TMP": tmp,
                "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
            }
            kwargs = {"capture_output": True, "text": True, "encoding": "utf-8",
                      "errors": "replace", "timeout": timeout, "cwd": tmp, "env": env}
            if os.name == "posix":
                kwargs["preexec_fn"] = _posix_limits(timeout)
            proc = subprocess.run([sys.executable, "-s", "-B", str(script_path)],
                                  input=json.dumps(context or {}, ensure_ascii=False,
                                                   default=str),
                                  **kwargs)
            stdout = (proc.stdout or "")[:MAX_OUTPUT]
            stderr = (proc.stderr or "")[:MAX_OUTPUT]
            dur = int((time.perf_counter() - started) * 1000)
            result = None
            if stdout.strip():
                try:
                    result = json.loads(stdout)
                except json.JSONDecodeError:
                    result = None
            ok = proc.returncode == 0
            if not ok and not stderr:
                stderr = f"脚本以退出码 {proc.returncode} 结束"
            return {"ok": ok, "exit_code": proc.returncode, "stdout": stdout,
                    "stderr": stderr, "dur_ms": dur, "timed_out": False,
                    "errors": [], "result": result}
    except subprocess.TimeoutExpired as exc:
        dur = int((time.perf_counter() - started) * 1000)
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        return {"ok": False, "exit_code": None, "stdout": (stdout or "")[:MAX_OUTPUT],
                "stderr": f"执行超时（>{timeout}s）", "dur_ms": dur, "timed_out": True,
                "errors": [], "result": None}
    except Exception as exc:  # 解释器缺失、临时目录不可写等
        dur = int((time.perf_counter() - started) * 1000)
        return {"ok": False, "exit_code": None, "stdout": "", "stderr": str(exc)[:300],
                "dur_ms": dur, "timed_out": False, "errors": [], "result": None}


def pick_script(skill_scripts: list[dict], entrypoint: str = "",
                name: str | None = None) -> dict | None:
    """按名称 / 入口 / 第一个 Python 脚本选择要执行的脚本。"""
    script_name = (name or entrypoint or "").strip()
    if script_name:
        for item in skill_scripts:
            if item.get("name") == script_name:
                return item
    for item in skill_scripts:
        if (item.get("language") or "").lower() == "python":
            return item
    return skill_scripts[0] if skill_scripts else None
