"""运行执行器：有界线程池 + 排队上限 + 优雅停机。

替代此前「每个 Run 一个裸线程」的做法：
- 固定 worker 数（``AH_RUN_WORKERS``），避免线程数随并发线性增长
- 排队上限（``AH_RUN_QUEUE_SIZE``），超出直接拒绝并返回 AH-RUNTIME-429
- 停机时等待在途运行结束（``shutdown``），避免任务被硬切断
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from .config import settings

logger = logging.getLogger("agenthub.executor")

_workers = max(1, settings.run_workers)
_queue_limit = max(1, settings.run_queue_size)

_executor: ThreadPoolExecutor
_slots: threading.Semaphore
_lock = threading.Lock()
_stats = {"submitted": 0, "rejected": 0, "finished": 0, "failed": 0, "running": 0, "pending": 0}
_closed = False


def _build() -> None:
    global _executor, _slots
    _executor = ThreadPoolExecutor(max_workers=_workers, thread_name_prefix="ah-run")
    _slots = threading.Semaphore(_queue_limit)


def ensure_started() -> None:
    """应用启动时确保线程池可用（测试中多个 app 实例会反复启停）。"""
    global _closed
    with _lock:
        if _closed:
            _build()
            _closed = False


_build()


def submit_run(run_id: str, resume: bool = False) -> bool:
    """提交一次运行；返回 False 表示队列已满或执行器已关闭。"""
    global _closed
    if _closed:
        return False
    if not _slots.acquire(blocking=False):
        with _lock:
            _stats["rejected"] += 1
        return False
    with _lock:
        _stats["submitted"] += 1
        _stats["pending"] += 1
    try:
        future = _executor.submit(_task, run_id, resume)
    except RuntimeError:
        _slots.release()
        with _lock:
            _stats["pending"] -= 1
        return False
    future.add_done_callback(_on_done)
    return True


def _task(run_id: str, resume: bool) -> None:
    from .runner import execute_run

    with _lock:
        _stats["pending"] -= 1
        _stats["running"] += 1
    try:
        execute_run(run_id, resume=resume)
    except Exception:
        logger.exception("运行执行异常 run_id=%s", run_id)


def _on_done(future) -> None:
    _slots.release()
    with _lock:
        _stats["finished"] += 1
        _stats["running"] = max(0, _stats["running"] - 1)
        if future.exception():
            _stats["failed"] += 1
    if future.exception():
        logger.error("运行任务失败：%s", future.exception())


def pool_stats() -> dict:
    with _lock:
        snapshot = dict(_stats)
    snapshot.update({"workers": _workers, "queue_limit": _queue_limit})
    return snapshot


def shutdown(wait: bool = True) -> None:
    global _closed
    _closed = True
    _executor.shutdown(wait=wait, cancel_futures=False)
