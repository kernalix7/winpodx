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
    """Empty input (Enter) keeps whatever the prompt displayed as the default."""
    from winpodx.utils.locale import detect_install_locale

    cfg = _cfg()
    before_edition = cfg.pod.win_version
    before_tuning = cfg.pod.tuning_profile

    with patch("builtins.input", lambda _prompt: ""):
        _prompt_edition_locale_tuning(cfg)

    assert cfg.pod.win_version == before_edition
    assert cfg.pod.tuning_profile == before_tuning
    # The locale fields default to empty meaning "detect from the host", so
    # what Enter accepts is the detected value the prompt showed -- and it is
    # stored, so it cannot change later under a different host locale (#791).
    assert (cfg.pod.language, cfg.pod.region, cfg.pod.keyboard) == detect_install_locale()


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


def _host_state(*, complete: bool = False, fixable: bool = True):
    from winpodx.setup_wizard.host_state import HostState

    return HostState(
        in_kvm_group=complete,
        kvm_group_exists=fixable,
        dev_kvm_present=complete,
        dev_kvm_readable=complete,
        subuid_configured=complete,
        subgid_configured=complete,
        kvm_module_persistent=complete,
    )


def test_setup_host_status_exit_codes(monkeypatch) -> None:
    from winpodx.setup_wizard import __main__ as wizard_main

    monkeypatch.setattr(wizard_main, "_print_status", lambda _state: None)
    monkeypatch.setattr(wizard_main, "detect_host_state", lambda: _host_state(complete=True))
    assert wizard_main.main(["--status"]) == 0

    monkeypatch.setattr(wizard_main, "detect_host_state", lambda: _host_state())
    assert wizard_main.main(["--status"]) == 1


def test_setup_host_without_fixable_items_exits_zero(monkeypatch) -> None:
    from winpodx.setup_wizard import __main__ as wizard_main
    from winpodx.setup_wizard.host_state import HostState

    monkeypatch.setattr(wizard_main, "_print_status", lambda _state: None)
    monkeypatch.setattr(
        wizard_main,
        "detect_host_state",
        lambda: HostState(
            in_kvm_group=True,
            kvm_group_exists=False,
            dev_kvm_present=False,
            dev_kvm_readable=False,
            subuid_configured=True,
            subgid_configured=True,
            kvm_module_persistent=False,
        ),
    )
    monkeypatch.setattr(
        wizard_main,
        "apply_via_pkexec",
        lambda _state: (_ for _ in ()).throw(AssertionError("nothing should be applied")),
    )
    assert wizard_main.main([]) == 0


def test_setup_host_declined_prompt_exits_one(monkeypatch) -> None:
    from winpodx.setup_wizard import __main__ as wizard_main

    monkeypatch.setattr(wizard_main, "_print_status", lambda _state: None)
    monkeypatch.setattr(wizard_main, "detect_host_state", lambda: _host_state())
    monkeypatch.setattr(wizard_main, "_confirm", lambda _prompt: False)
    monkeypatch.setattr(
        wizard_main,
        "apply_via_pkexec",
        lambda _state: (_ for _ in ()).throw(AssertionError("declined apply must not run")),
    )
    assert wizard_main.main([]) == 1


def test_setup_host_apply_rechecks_and_returns_new_state(monkeypatch) -> None:
    from winpodx.setup_wizard import __main__ as wizard_main

    before = _host_state()
    after = _host_state(complete=True)
    states = iter((before, after))
    applied = []
    monkeypatch.setattr(wizard_main, "_print_status", lambda _state: None)
    monkeypatch.setattr(wizard_main, "detect_host_state", lambda: next(states))
    monkeypatch.setattr(wizard_main, "apply_via_pkexec", applied.append)
    assert wizard_main.main(["--apply"]) == 0
    assert applied == [before]


def test_setup_host_maps_pkexec_errors_to_exit_codes(monkeypatch) -> None:
    from winpodx.setup_wizard import __main__ as wizard_main

    monkeypatch.setattr(wizard_main, "_print_status", lambda _state: None)
    monkeypatch.setattr(wizard_main, "detect_host_state", lambda: _host_state())
    cases = (
        (wizard_main.PkexecUnavailable("missing"), 2),
        (wizard_main.PkexecAuthDenied("denied"), 3),
        (wizard_main.PkexecScriptFailed("failed"), 4),
    )
    for error, expected in cases:
        monkeypatch.setattr(
            wizard_main,
            "apply_via_pkexec",
            lambda _state, error=error: (_ for _ in ()).throw(error),
        )
        assert wizard_main.main(["--apply"]) == expected


def test_setup_host_confirm_accepts_yes_and_handles_eof(monkeypatch) -> None:
    from winpodx.setup_wizard import __main__ as wizard_main

    monkeypatch.setattr("builtins.input", lambda _prompt: " YES ")
    assert wizard_main._confirm("apply") is True
    monkeypatch.setattr("builtins.input", lambda _prompt: (_ for _ in ()).throw(EOFError()))
    assert wizard_main._confirm("apply") is False


def test_setup_host_confirm_handles_keyboard_interrupt(monkeypatch) -> None:
    from winpodx.setup_wizard import __main__ as wizard_main

    monkeypatch.setattr(
        "builtins.input", lambda _prompt: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    assert wizard_main._confirm("apply") is False


def test_setup_host_print_status_reports_fixable_items(capsys) -> None:
    from winpodx.setup_wizard import __main__ as wizard_main

    wizard_main._print_status(_host_state())

    output = capsys.readouterr().out
    assert "[--]  you are in kvm group" in output
    assert "Fixable via pkexec" in output
    assert "kvm-group-membership" in output


def test_setup_host_print_status_reports_complete_and_unfixable_states(capsys) -> None:
    from winpodx.setup_wizard import __main__ as wizard_main
    from winpodx.setup_wizard.host_state import HostState

    wizard_main._print_status(_host_state(complete=True))
    assert "Host is fully set up." in capsys.readouterr().out

    state = HostState(
        in_kvm_group=True,
        kvm_group_exists=False,
        dev_kvm_present=False,
        dev_kvm_readable=False,
        subuid_configured=True,
        subgid_configured=True,
        kvm_module_persistent=False,
    )
    wizard_main._print_status(state)
    output = capsys.readouterr().out
    assert "wizard cannot fix it" in output
    assert "enable VT-x / AMD-V in BIOS" in output


def test_setup_host_apply_prints_logout_note_when_membership_is_pending(
    monkeypatch, capsys
) -> None:
    from winpodx.setup_wizard import __main__ as wizard_main

    before = _host_state()
    after = _host_state()
    states = iter((before, after))
    monkeypatch.setattr(wizard_main, "_print_status", lambda _state: None)
    monkeypatch.setattr(wizard_main, "detect_host_state", lambda: next(states))
    monkeypatch.setattr(wizard_main, "apply_via_pkexec", lambda state: None)

    assert wizard_main.main(["--apply"]) == 1
    assert "log out + back in" in capsys.readouterr().out


def test_setup_host_pkexec_errors_are_printed_to_stderr(monkeypatch, capsys) -> None:
    from winpodx.setup_wizard import __main__ as wizard_main

    monkeypatch.setattr(wizard_main, "_print_status", lambda _state: None)
    monkeypatch.setattr(wizard_main, "detect_host_state", lambda: _host_state())
    monkeypatch.setattr(
        wizard_main,
        "apply_via_pkexec",
        lambda _state: (_ for _ in ()).throw(wizard_main.PkexecUnavailable("no pkexec")),
    )

    assert wizard_main.main(["--apply"]) == 2
    error = capsys.readouterr().err
    assert "Error: no pkexec" in error
    assert "sudo usermod -aG kvm $USER" in error
