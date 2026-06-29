# SPDX-License-Identifier: MIT
"""Structural guards for the GUI "Refresh Apps" worker-QThread teardown.

This teardown has regressed into a crash twice (v0.2.0.10 → .11 → the
QObject::~QObject SIGSEGV). A live-Qt thread test would be flaky in CI, so we
lock the invariants at the source level instead:

  * the parentless, Python-owned DiscoveryWorker must have EXACTLY ONE delete
    path — so ``worker.finished.connect(worker.deleteLater)`` must NOT exist
    (the second path raced shiboken's main-thread C++ delete -> double-free);
  * ``_cleanup_refresh_worker`` must ``thread.wait()`` before dropping the refs,
    so the only delete happens after the worker thread is provably dead;
  * the start guard must bail on a live ``self._refresh_thread`` (not just the
    ``_refresh_state`` string), so a rapid re-click can't overwrite the ref and
    drop the last Python ref to a still-finishing worker;
  * the window must join in-flight worker threads on close.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APPS = REPO_ROOT / "src" / "winpodx" / "gui" / "_main_window_apps.py"
MAIN = REPO_ROOT / "src" / "winpodx" / "gui" / "main_window.py"


def _apps_src() -> str:
    return APPS.read_text(encoding="utf-8")


def test_worker_has_single_delete_path() -> None:
    src = _apps_src()
    # The worker is deleted ONLY via the Python ref-drop in
    # _cleanup_refresh_worker; a worker.deleteLater would be the second,
    # racing path that double-freed on the worker thread.
    assert "worker.finished.connect(worker.deleteLater)" not in src
    # thread.deleteLater on thread.finished is still correct (QThread is
    # parent-owned, deleted on the main thread after it stops).
    assert "thread.finished.connect(thread.deleteLater)" in src


def test_cleanup_waits_before_dropping_refs() -> None:
    src = _apps_src()
    idx = src.index("def _cleanup_refresh_worker")
    body = src[idx : idx + 2200]
    wait_at = body.find("thread.wait()")
    null_at = body.find("self._refresh_worker = None")
    assert wait_at != -1, "cleanup must join the worker thread"
    assert null_at != -1
    assert wait_at < null_at, "must wait() BEFORE dropping the Python ref"


def test_start_guard_checks_live_thread_ref() -> None:
    src = _apps_src()
    assert "self._refresh_thread is not None" in src


def test_window_joins_worker_threads_on_close() -> None:
    src = MAIN.read_text(encoding="utf-8")
    assert "def closeEvent" in src
    assert "_join_worker_threads" in src
    # Both worker threads are joined.
    assert '"_refresh_thread"' in src and '"_info_thread"' in src
    assert "thread.wait()" in src
