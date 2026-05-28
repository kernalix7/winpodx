# SPDX-License-Identifier: MIT
"""Pin the unified dep-check contract (0.6.0 item D).

``utils/deps.py:check_all`` is the single source of truth for host dep
detection. Every other consumer (the setup wizard, GUI Quick Start,
``winpodx doctor``) must delegate to it rather than reimplement its own
``shutil.which`` loop. These tests lock that contract so a regression
re-introducing a hardcoded freerdp list lights up in CI.

The shell side of ``install.sh`` keeps a minimal pre-venv probe (genuine
shell-unique requirement); that exception is intentional and out of scope
for these tests.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from winpodx.utils.deps import (
    OPTIONAL_DEPS,
    DepCheck,
    check_all,
    check_kvm,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---- check_all surface ---------------------------------------------------


def test_check_all_returns_every_canonical_key() -> None:
    # Required keys: every OPTIONAL_DEPS entry + freerdp + kvm.
    out = check_all()
    expected = set(OPTIONAL_DEPS.keys()) | {"freerdp", "kvm"}
    assert set(out.keys()) == expected


def test_check_all_values_are_dep_check_instances() -> None:
    for name, dep in check_all().items():
        assert isinstance(dep, DepCheck), f"{name!r}: {type(dep).__name__}"
        assert isinstance(dep.found, bool)
        assert isinstance(dep.name, str) and dep.name
        # `note` may be empty for some surfaces but is always a string.
        assert isinstance(dep.note, str)


def test_kvm_dep_check_keys_off_dev_kvm() -> None:
    # The kvm DepCheck must report based on /dev/kvm presence (the QEMU
    # signal), not heuristics. Patch Path.exists to fake both states.
    with patch.object(Path, "exists", return_value=True):
        dep = check_kvm()
    assert dep.found is True
    assert dep.path == "/dev/kvm"

    with patch.object(Path, "exists", return_value=False):
        dep = check_kvm()
    assert dep.found is False
    assert dep.path == ""


# ---- consumers delegate to deps.check_freerdp ----------------------------


def test_deps_quickcheck_delegates_to_check_freerdp() -> None:
    # Replace check_freerdp with a fake and verify the quickcheck consumes
    # *its* result rather than its own shutil.which loop.
    from winpodx.core import deps_quickcheck
    from winpodx.core.config import Config

    cfg = Config()

    fake = DepCheck(name="xfreerdp3", found=True, path="/usr/bin/xfreerdp3", note="FreeRDP 3+")
    with patch("winpodx.utils.deps.check_freerdp", return_value=fake):
        out = deps_quickcheck.collect_first_run_checks(cfg)
    assert out["freerdp"] == "OK"

    fake = DepCheck(name="xfreerdp", found=False, note="FreeRDP 3+ is required")
    with patch("winpodx.utils.deps.check_freerdp", return_value=fake):
        out = deps_quickcheck.collect_first_run_checks(cfg)
    assert "missing" in out["freerdp"]


def test_doctor_check_freerdp_delegates() -> None:
    # `_check_freerdp` in doctor must consume deps.check_freerdp so the
    # accepted binary set stays single-sourced.
    from winpodx.cli.doctor import _check_freerdp

    fake = DepCheck(name="xfreerdp3", found=True, path="/usr/bin/xfreerdp3", note="FreeRDP 3+")
    with patch("winpodx.utils.deps.check_freerdp", return_value=fake):
        finding = _check_freerdp()
    assert finding.severity == "ok"
    assert "/usr/bin/xfreerdp3" in finding.title

    fake = DepCheck(name="xfreerdp", found=False, note="FreeRDP 3+ is required")
    with patch("winpodx.utils.deps.check_freerdp", return_value=fake):
        finding = _check_freerdp()
    assert finding.severity == "fail"


def test_doctor_check_kvm_delegates() -> None:
    from winpodx.cli.doctor import _check_kvm

    fake = DepCheck(name="kvm", found=True, path="/dev/kvm", note="Hardware virtualization")
    with patch("winpodx.utils.deps.check_kvm", return_value=fake):
        finding = _check_kvm()
    assert finding.severity == "ok"

    fake = DepCheck(name="kvm", found=False, note="Hardware virtualization")
    with patch("winpodx.utils.deps.check_kvm", return_value=fake):
        finding = _check_kvm()
    assert finding.severity == "fail"


# ---- regression: no consumer re-hardcodes the freerdp binary list --------

# Use ast to inspect actual function calls instead of grepping source text;
# the legacy regression was specifically `shutil.which("xfreerdp...")` in code,
# and the binary names legitimately appear in comments and docstrings that
# explain the delegation. ast-based checks won't fail on mentions.

_FORBIDDEN_FREERDP_BINARIES = {
    "xfreerdp3",
    "xfreerdp",
    "wlfreerdp3",
    "wlfreerdp",
    "sdl-freerdp3",
    "sdl-freerdp",
}


def _shutil_which_string_args(source: str) -> set[str]:
    """Return every constant string argument passed to ``shutil.which(...)``."""
    import ast

    tree = ast.parse(source)
    args: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "which":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    args.add(arg.value)
    return args


def test_deps_quickcheck_does_not_shutil_which_freerdp() -> None:
    src = (REPO_ROOT / "src" / "winpodx" / "core" / "deps_quickcheck.py").read_text()
    overlap = _shutil_which_string_args(src) & _FORBIDDEN_FREERDP_BINARIES
    assert not overlap, (
        f"deps_quickcheck.py calls shutil.which on freerdp binary names {overlap}; "
        "delegate to winpodx.utils.deps.check_freerdp instead"
    )


def test_doctor_does_not_shutil_which_freerdp() -> None:
    src = (REPO_ROOT / "src" / "winpodx" / "cli" / "doctor.py").read_text()
    overlap = _shutil_which_string_args(src) & _FORBIDDEN_FREERDP_BINARIES
    assert not overlap, (
        f"cli/doctor.py calls shutil.which on freerdp binary names {overlap}; "
        "delegate to winpodx.utils.deps.check_freerdp instead"
    )
