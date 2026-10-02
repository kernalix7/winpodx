# SPDX-License-Identifier: MIT
"""Regressions for optional Qt cleanup at real pytest fixture boundaries."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


def _copy_shared_fixtures(pytester: pytest.Pytester) -> None:
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text(encoding="utf-8"))


def test_deferred_objects_are_destroyed_before_isolation_is_restored(
    pytester: pytest.Pytester,
) -> None:
    pytest.importorskip("PySide6.QtWidgets")
    _copy_shared_fixtures(pytester)
    pytester.makepyfile(
        """
        import os
        from types import SimpleNamespace

        import pytest
        import shiboken6
        from PySide6.QtCore import QObject
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        objects = []
        observed = []
        expected = []
        boundary = SimpleNamespace(value="original")

        @pytest.fixture
        def scheduled_objects(monkeypatch):
            monkeypatch.setattr(boundary, "value", "patched")
            deleted = QObject()
            live = QObject()
            objects.extend([deleted, live])
            expected.append((os.environ["HOME"], os.environ["XDG_CONFIG_HOME"], "patched"))
            deleted.destroyed.connect(
                lambda: observed.append(
                    (os.environ["HOME"], os.environ["XDG_CONFIG_HOME"], boundary.value)
                )
            )
            yield
            deleted.deleteLater()

        def test_schedule(scheduled_objects):
            assert all(shiboken6.isValid(obj) for obj in objects)

        def test_next_boundary():
            assert not shiboken6.isValid(objects[0]), "scheduled C++ object survived teardown"
            assert shiboken6.isValid(objects[1]), "unscheduled object must remain alive"
            assert observed == expected, "destruction ran after boundary mocks were restored"
            objects[1].deleteLater()
        """
    )
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=2)


def test_shared_fixtures_do_not_import_optional_qt(pytester: pytest.Pytester) -> None:
    _copy_shared_fixtures(pytester)
    pytester.makepyfile(
        """
        import importlib.abc
        import sys

        class NoQt(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "PySide6" or fullname.startswith("PySide6."):
                    raise AssertionError("non-GUI fixture imported optional Qt")
                return None

        sys.meta_path.insert(0, NoQt())

        def test_without_qt():
            assert "PySide6.QtCore" not in sys.modules
        """
    )
    result = pytester.runpytest_subprocess("-q")
    result.assert_outcomes(passed=1)
