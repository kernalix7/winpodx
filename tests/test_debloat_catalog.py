# SPDX-License-Identifier: MIT
"""Tests for the debloat catalog + selection resolver (#247 phase 1)."""

from __future__ import annotations

import pytest

from winpodx.core.debloat import (
    DebloatCatalogError,
    build_run_script,
    format_catalog_listing,
    load_catalog,
    resolve_selection,
)


class TestLoadShippedCatalog:
    """Load the real ``data/debloat/items.toml`` that ships in the repo."""

    def test_loads_without_error(self):
        catalog = load_catalog()
        assert len(catalog.items) >= 11, "catalog should have at least the 11 items from #247"

    def test_required_items_present(self):
        catalog = load_catalog()
        for required in (
            "telemetry",
            "ads",
            "onedrive",
            "sysmain",
            "web_search",
            "widgets",
            "scheduled_tasks",
            "startup_programs",
            "visual_effects",
            "search_indexing",
            "transparency",
        ):
            assert required in catalog.items, f"item {required!r} missing"

    def test_all_referenced_scripts_exist(self):
        catalog = load_catalog()
        for name, item in catalog.items.items():
            assert item.script_path.exists(), (
                f"items.{name}: script {item.script_path} does not exist"
            )

    def test_preset_membership_is_monotone(self):
        """normal subset of full subset of performance subset of speed."""
        catalog = load_catalog()
        normal = set(catalog.items_for_preset("normal"))
        full = set(catalog.items_for_preset("full"))
        performance = set(catalog.items_for_preset("performance"))
        speed = set(catalog.items_for_preset("speed"))

        assert normal.issubset(full), "normal must be subset of full"
        assert full.issubset(performance), "full must be subset of performance"
        assert performance.issubset(speed), "performance must be subset of speed"

    def test_all_risks_are_valid(self):
        catalog = load_catalog()
        for item in catalog.items.values():
            assert item.risk in ("low", "medium", "high")


class TestLoadCustomCatalog:
    """Spin up a tmp catalog + scripts dir to exercise validation paths."""

    @pytest.fixture
    def _make_catalog(self, tmp_path, monkeypatch):
        scripts_dir = tmp_path / "scripts" / "windows" / "debloat"
        scripts_dir.mkdir(parents=True)
        (scripts_dir / "alpha.ps1").write_text("Write-Host 'alpha'\n")
        (scripts_dir / "beta.ps1").write_text("Write-Host 'beta'\n")

        data_dir = tmp_path / "data" / "debloat"
        data_dir.mkdir(parents=True)

        # Point bundle_dir() at our tmp tree so the catalog's script
        # existence check looks at our fake scripts dir.
        monkeypatch.setattr("winpodx.core.debloat.bundle_dir", lambda: tmp_path)
        return data_dir / "items.toml"

    def test_minimal_catalog_loads(self, _make_catalog):
        _make_catalog.write_text(
            """
[items.alpha]
label = "Alpha"
description = "Alpha item"
script = "alpha.ps1"
risk = "low"

[presets]
normal = ["alpha"]
"""
        )
        catalog = load_catalog(catalog_path=_make_catalog)
        assert "alpha" in catalog.items
        assert catalog.items_for_preset("normal") == ["alpha"]

    def test_missing_required_field_raises(self, _make_catalog):
        _make_catalog.write_text(
            """
[items.alpha]
label = "Alpha"
description = "Alpha"
script = "alpha.ps1"
# risk missing

[presets]
normal = ["alpha"]
"""
        )
        with pytest.raises(DebloatCatalogError, match="missing required field 'risk'"):
            load_catalog(catalog_path=_make_catalog)

    def test_unknown_risk_raises(self, _make_catalog):
        _make_catalog.write_text(
            """
[items.alpha]
label = "Alpha"
description = "Alpha"
script = "alpha.ps1"
risk = "extreme"

[presets]
normal = ["alpha"]
"""
        )
        with pytest.raises(DebloatCatalogError, match="risk="):
            load_catalog(catalog_path=_make_catalog)

    def test_missing_script_raises(self, _make_catalog):
        _make_catalog.write_text(
            """
[items.gamma]
label = "Gamma"
description = "Gamma"
script = "gamma.ps1"
risk = "low"

[presets]
normal = ["gamma"]
"""
        )
        with pytest.raises(DebloatCatalogError, match="does not exist on disk"):
            load_catalog(catalog_path=_make_catalog)

    def test_preset_with_unknown_item_raises(self, _make_catalog):
        _make_catalog.write_text(
            """
[items.alpha]
label = "Alpha"
description = "Alpha"
script = "alpha.ps1"
risk = "low"

[presets]
normal = ["alpha", "ghost"]
"""
        )
        with pytest.raises(DebloatCatalogError, match="unknown item 'ghost'"):
            load_catalog(catalog_path=_make_catalog)

    def test_invalid_toml_raises(self, _make_catalog):
        _make_catalog.write_text("not = valid = toml\n")
        with pytest.raises(DebloatCatalogError, match="not valid TOML"):
            load_catalog(catalog_path=_make_catalog)


class TestResolveSelection:
    @pytest.fixture
    def catalog(self):
        return load_catalog()

    def test_default_resolves_to_normal_preset(self, catalog):
        assert resolve_selection(catalog, preset=None, items=None) == catalog.items_for_preset(
            "normal"
        )

    def test_preset_choice(self, catalog):
        sel = resolve_selection(catalog, preset="performance", items=None)
        assert sel == catalog.items_for_preset("performance")

    def test_explicit_items_wins_over_preset(self, catalog):
        """When both are provided, explicit list wins (preset is ignored)."""
        sel = resolve_selection(catalog, preset="speed", items=["telemetry", "ads"])
        assert sel == ["telemetry", "ads"]

    def test_unknown_preset_raises(self, catalog):
        with pytest.raises(DebloatCatalogError, match="Unknown preset"):
            resolve_selection(catalog, preset="ultra", items=None)

    def test_unknown_item_raises(self, catalog):
        with pytest.raises(DebloatCatalogError, match="Unknown item"):
            resolve_selection(catalog, preset=None, items=["telemetry", "ghost"])

    def test_dedupe_preserves_order(self, catalog):
        sel = resolve_selection(
            catalog, preset=None, items=["telemetry", "ads", "telemetry", "ads"]
        )
        assert sel == ["telemetry", "ads"]


class TestBuildRunScript:
    @pytest.fixture
    def catalog(self):
        return load_catalog()

    def test_payload_contains_header_and_footer(self, catalog):
        payload = build_run_script(catalog, ["telemetry"])
        assert "winpodx debloat" in payload
        assert "=== done:" in payload

    def test_payload_includes_each_selected_item_body(self, catalog):
        payload = build_run_script(catalog, ["telemetry", "ads"])
        # Each item script's header banner should appear once.
        assert "[telemetry]" in payload
        assert "[ads]" in payload

    def test_unknown_item_via_resolve_then_build_raises(self, catalog):
        with pytest.raises(DebloatCatalogError):
            resolve_selection(catalog, preset=None, items=["nope"])


class TestFormatCatalogListing:
    def test_lists_all_items_and_presets(self):
        catalog = load_catalog()
        rendered = format_catalog_listing(catalog)
        for name in catalog.items:
            assert name in rendered
        for preset in catalog.preset_names:
            assert preset in rendered
