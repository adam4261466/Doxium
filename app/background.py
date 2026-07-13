"""
Simple thread-based background task runner.
Replaces Celery for environments where the Celery worker is not running.
Tasks are executed in background threads; results are stored in an in-memory dict.
"""

import threading
import uuid
import time
import logging
import traceback

logger = logging.getLogger(__name__)

_results_lock = threading.Lock()
_results: dict = {}


class TaskResult:
    __slots__ = ("id", "status", "result", "error", "_ready", "_done_at")

    def __init__(self, task_id: str):
        self.id = task_id
        self.status = "PENDING"
        self.result = None
        self.error = None
        self._ready = False
        self._done_at = None

    def ready(self):
        return self._ready

    def successful(self):
        return self.status == "SUCCESS"

    def get(self):
        if self.error:
            return {"error": self.error}
        return self.result


def _run_wrapper(task_result: TaskResult, func, args, kwargs, app):
    """Run func inside the Flask app context and store the result."""
    with app.app_context():
        try:
            result = func(*args, **kwargs)
            task_result.result = result
            task_result.status = "SUCCESS"
        except Exception as e:
            logger.exception("Background task %s failed", task_result.id)
            task_result.error = str(e)
            task_result.status = "FAILURE"
        finally:
            task_result._ready = True
            task_result._done_at = time.time()


def run_background(func, *args, app, **kwargs):
    """
    Run *func* in a background thread, returning a TaskResult handle.

    Usage (in a Flask route):
        from app.background import run_background
        result = run_background(my_task, arg1, arg2, app=current_app._get_current_object())
        task_id = result.id
    """
    from flask import current_app

    if app is None:
        app = current_app._get_current_object()

    task_id = uuid.uuid4().hex
    tr = TaskResult(task_id)

    with _results_lock:
        _results[task_id] = tr

    t = threading.Thread(
        target=_run_wrapper,
        args=(tr, func, args, kwargs, app),
        daemon=True,
    )
    t.start()
    return tr


def get_task(task_id: str):
    """Return the TaskResult for *task_id*, or None if unknown."""
    return _results.get(task_id)


def cleanup_old_tasks(max_age_seconds=3600):
    """Remove task results older than *max_age_seconds* (called lazily)."""
    now = time.time()
    to_del = []
    with _results_lock:
        for tid, tr in _results.items():
            if tr._done_at and (now - tr._done_at) > max_age_seconds:
                to_del.append(tid)
        for tid in to_del:
            del _results[tid]
