"""Qt worker objects used by the main winpodx window."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot

log = logging.getLogger(__name__)


class DiscoveryWorker(QObject):
    """Run Windows app discovery and desktop-entry sync off the UI thread."""

    succeeded = Signal(int)
    failed = Signal(str, str)
    finished = Signal()

    @Slot()
    def run(self) -> None:
        try:
            from winpodx.core import discovery as discovery_mod
            from winpodx.core.config import Config
        except ImportError as exc:
            self.failed.emit("module_missing", str(exc))
            self.finished.emit()
            return

        try:
            cfg = Config.load()
            apps = discovery_mod.discover_apps(cfg)
        except Exception as exc:  # noqa: BLE001 - worker surfaces all errors to UI
            kind = getattr(exc, "kind", None)
            if not kind:
                kind = "pod_not_running" if _looks_like_pod_down(exc) else "unexpected"
            self.failed.emit(kind, str(exc))
            self.finished.emit()
            return

        try:
            persisted = discovery_mod.persist_discovered(apps)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit("unexpected", str(exc))
            self.finished.emit()
            return

        try:
            sync_desktop_entries(apps)
        except Exception:  # noqa: BLE001 - best-effort
            log.debug("GUI refresh: desktop-entry sync failed", exc_info=True)

        try:
            from winpodx.desktop.icons import refresh_icon_cache

            refresh_icon_cache()
        except Exception:  # noqa: BLE001 - cache refresh is best-effort
            log.debug("GUI refresh: icon-cache refresh failed", exc_info=True)

        try:
            count = len(persisted)
        except TypeError:
            count = len(apps)
        self.succeeded.emit(count)
        self.finished.emit()


class InfoWorker(QObject):
    """Gather static info plus live health probes off the UI thread."""

    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, cfg) -> None:
        super().__init__()
        self.cfg = cfg

    @Slot()
    def run(self) -> None:
        try:
            from winpodx.core import checks
            from winpodx.core.info import gather_info

            snapshot = gather_info(self.cfg)
            try:
                probes = checks.run_all(self.cfg)
                snapshot["health"] = [
                    {
                        "name": p.name,
                        "status": p.status,
                        "detail": p.detail,
                        "duration_ms": p.duration_ms,
                    }
                    for p in probes
                ]
                snapshot["health_overall"] = checks.overall(probes)
            except Exception:  # noqa: BLE001 - health is opt-in; never block info
                log.debug("health probes failed during info refresh", exc_info=True)
                snapshot["health"] = []
                snapshot["health_overall"] = "fail"
            self.done.emit(snapshot)
        except Exception as e:  # noqa: BLE001 - surface to UI via signal
            self.failed.emit(str(e))


def sync_desktop_entries(discovered) -> None:
    """Bidirectionally sync .desktop entries after GUI discovery."""
    from winpodx.core.app import list_available_apps
    from winpodx.desktop.entry import install_desktop_entry, remove_desktop_entry
    from winpodx.utils.paths import applications_dir

    discovered_slugs = {d.slug or d.name for d in discovered}
    available = {a.name: a for a in list_available_apps()}
    for slug in discovered_slugs:
        info = available.get(slug)
        if info is not None:
            try:
                install_desktop_entry(info)
            except Exception:  # noqa: BLE001
                log.debug("install_desktop_entry failed for %s", slug, exc_info=True)

    apps_dir = applications_dir()
    if apps_dir.exists():
        for entry in apps_dir.glob("winpodx-*.desktop"):
            stem = entry.stem
            if not stem.startswith("winpodx-"):
                continue
            slug = stem[len("winpodx-") :]
            if slug in {"", "gui", "launcher"}:
                continue
            if slug in available:
                continue
            try:
                remove_desktop_entry(slug)
            except Exception:  # noqa: BLE001
                log.debug("remove_desktop_entry failed for %s", slug, exc_info=True)


def _looks_like_pod_down(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(tok in text for tok in ("pod", "container", "connection refused", "not running"))
