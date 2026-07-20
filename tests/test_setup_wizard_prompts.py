# SPDX-License-Identifier: MIT
"""Tests for the extended setup wizard (#255 PR 7 completion):
edition / locale / tuning prompts + the full-provision gate.

0.6.0 item B: ``--create-only`` was removed; ``_run_full_provision`` is now
a thin wrapper over ``core.provisioner.finish_provisioning``. The non-
container short-circuit it kept is what ``test_full_provision_noop_*`` pins.
"""

from __future__ import annotations

from unittest.mock import patch

from winpodx.cli.setup_cmd import _prompt_edition_locale_tuning, _run_full_provision
from winpodx.core.config import Config


def _cfg() -> Config:
    cfg = Config()
    cfg.pod.backend = "podman"
    return cfg


def test_wizard_prompts_set_all_locale_edition_tuning_fields() -> None:
    """Each answered prompt maps to the matching cfg.pod field."""
    cfg = _cfg()
    answers = iter(
        [
            "ltsc11",  # edition / win_version
            "German",  # language
            "en-US",  # region
            "de-DE",  # keyboard
            "performance",  # tuning_profile
        ]
    )
    with patch("builtins.input", lambda _prompt: next(answers)):
        _prompt_edition_locale_tuning(cfg)

    assert cfg.pod.win_version == "ltsc11"
    assert cfg.pod.language == "German"
    assert cfg.pod.region == "en-US"
    assert cfg.pod.keyboard == "de-DE"
    assert cfg.pod.tuning_profile == "performance"


def test_wizard_prompts_enter_keeps_defaults() -> None:
    """Empty input (Enter) keeps the existing cfg defaults."""
    cfg = _cfg()
    before = (
        cfg.pod.win_version,
        cfg.pod.language,
        cfg.pod.region,
        cfg.pod.keyboard,
        cfg.pod.tuning_profile,
    )
    with patch("builtins.input", lambda _prompt: ""):
        _prompt_edition_locale_tuning(cfg)

    after = (
        cfg.pod.win_version,
        cfg.pod.language,
        cfg.pod.region,
        cfg.pod.keyboard,
        cfg.pod.tuning_profile,
    )
    assert before == after


def test_wizard_rejects_unknown_tuning_profile_keeps_default() -> None:
    """A bogus tuning profile is rejected, default preserved."""
    cfg = _cfg()
    cfg.pod.tuning_profile = "auto"
    answers = iter(["11", "English", "en-001", "en-US", "turbo-nonsense"])
    with patch("builtins.input", lambda _prompt: next(answers)):
        _prompt_edition_locale_tuning(cfg)
    assert cfg.pod.tuning_profile == "auto"


def test_full_provision_noop_for_non_container_backend() -> None:
    """manual backends have no container provision flow -- the
    helper must return immediately without touching wait-ready etc."""
    cfg = _cfg()
    cfg.pod.backend = "manual"
    # If it tried to import/run _wait_ready it'd need a real pod; the
    # early return keeps it a pure no-op. No exception = pass.
    _run_full_provision(cfg)


def _fake_results(**overrides) -> dict:
    base = {
        "wait_ready": "ok",
        "apply_fixes": {},
        "discovery": "5 apps",
        "reverse_open": "skipped",
    }
    base.update(overrides)
    return base


class TestFullProvisionDiscoveryWarning:
    """#753: finish_provisioning can "succeed" while discovery finds zero
    apps (or fails outright) -- best-effort by design. Without a warning,
    setup prints the generic "complete" banner even though the Windows app
    menu will be empty, and the user has no idea why."""

    def test_warns_when_discovery_finds_zero_apps(self, capsys) -> None:
        cfg = _cfg()
        with patch(
            "winpodx.core.provisioner.finish_provisioning",
            return_value=_fake_results(discovery="0 apps"),
        ):
            _run_full_provision(cfg)
        out = capsys.readouterr().out
        assert "WARNING" in out
        assert "app menu may be empty" in out
        assert "app refresh" in out

    def test_warns_when_discovery_fails(self, capsys) -> None:
        cfg = _cfg()
        with patch(
            "winpodx.core.provisioner.finish_provisioning",
            return_value=_fake_results(discovery="failed: agent unreachable"),
        ):
            _run_full_provision(cfg)
        out = capsys.readouterr().out
        assert "WARNING" in out

    def test_no_warning_when_discovery_finds_apps(self, capsys) -> None:
        cfg = _cfg()
        with patch(
            "winpodx.core.provisioner.finish_provisioning",
            return_value=_fake_results(discovery="5 apps"),
        ):
            _run_full_provision(cfg)
        out = capsys.readouterr().out
        assert "WARNING" not in out
