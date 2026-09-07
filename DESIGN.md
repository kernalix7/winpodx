# WinPodX Design System

Canonical visual contract for the Qt desktop GUI. The GUI is a **Windows 11
Settings app** shell (NavigationView pane + single-column SettingsCard pages)
with a **Windows 11 Start** treatment for app pickers, following the OS
light/dark scheme. Everything in this file is what ships and is pinned by a
test; anything aspirational lives in a plan file, not here.

Surfaces:

- **Desktop (source of truth):** PySide6/Qt6 Widgets, Fusion base style,
  `QPalette` + QSS from `src/winpodx/gui/theme.py` / `_theme_qss.py`, painted
  controls for what QSS cannot draw (`ToggleSwitch`, `RingGauge`, nav pill). No
  third-party Fluent library.
- **Web (marketing only):** static `web/`. Not the GUI; not covered here.

Contract tests:

| Concern | Test |
|---------|------|
| Tokens, scheme rebuild, inner-control QSS | `tests/test_gui_theme.py`, `tests/test_gui_theme_manager.py`, `tests/test_gui_inner_controls.py` |
| Shell geometry, compact rail, indicator, a11y names | `tests/test_main_window_shell.py` |
| Page frame, common edges, card rhythm (all 8 pages) | `tests/test_gui_layout_contract.py` |
| Dashboard, Applications, Settings, secondary pages, launcher | `tests/test_main_window_*.py`, `tests/test_launcher.py` |
| Painted controls | `tests/test_gui_toggle_switch.py`, `tests/test_gui_ring_gauge.py` |

## 1. Atmosphere & Identity

WinPodX should feel like a Windows 11 system app that happens to run on Linux
-- the same room the Windows apps it launches live in. That means: Mica-flat
surfaces separated by fill contrast rather than lines; one accent used for
every interactive cue; restrained 14/12 type with a single 28px title; 36px
nav items; 68px settings rows; 32px controls with a slightly darker bottom
edge; motion limited to a sliding selection pill, a toggle thumb, and gauge
arcs. Nothing decorative: no shadows, no gradients as fills, no uppercase
labels, no saturated tiles, no emoji.

The reference is the real Windows 11 Settings app (WinUI 3 /
`Common_themeresources_any.xaml`) and the Start menu. Values here are lifted
from those resources, not invented. WinPodX is not a Microsoft product and
ships no Microsoft trademark assets; Selawik (OFL) stands in for Segoe UI.

### Licensing and assets

- WinPodX remains MIT (Kim DaeHyun). Copied MIT code or assets keep their
  notices (`THIRD_PARTY_LICENSES.md`).
- **Selawik** (`src/winpodx/gui/fonts/`, SIL OFL 1.1) is Microsoft's
  open-source Segoe UI replacement and may be bundled. Segoe UI itself is
  used only when already installed on the host.
- Fluent UI System Icons are MIT; if copied, keep the notice. Icons ship as
  bundled SVGs under `src/winpodx/gui/icons/` and load through
  `winpodx.gui.icons.load_icon`. No emoji as icons.
- Do not bundle Microsoft logos or other trademark assets. Do not add
  QFluentWidgets (GPL-3.0) or any Fluent Qt library.

## 2. Color

Two schemes, **light and dark**, both first-class. The scheme follows the OS
(`theme_manager.detect_system_scheme`: `WINPODX_COLOR_SCHEME` env override ->
Qt `QStyleHints.colorScheme()` -> palette lightness -> light). `theme.rebuild()`
mutates `C.*` and every QSS token in place; `ThemeManager.scheme_changed` fans
out to the `_restyle_*` hooks. Import-time default is light (Windows 11
default).

Values are the WinUI 3 theme resources (`Common_themeresources_any.xaml`)
flattened onto the app background; alpha layers are pre-composited to solids
where Qt needs a solid.

### Palette

| Role | Light | Dark | Python | WinUI resource |
|------|-------|------|--------|----------------|
| App background (Mica base) | `#F3F3F3` | `#1F1F1F` | `C.BASE` | `SolidBackgroundFillColorBase` |
| Layer / card | `#FFFFFF` | `#2B2B2B` | `C.SURFACE0` | `CardBackgroundFillColorDefault` over base |
| Layer alt (dialog body, inputs' active bg) | `#F9F9F9` | `#191919` | `C.MANTLE` | `LayerFillColorAlt` |
| Control fill | `#FDFDFD` | `#333333` | `p.control_fill` | `ControlFillColorDefault` |
| Control hover / pressed | `#F9F9F9` / `#F3F3F3` | `#3A3A3A` / `#272727` | `p.control_hover` / `p.control_pressed` | `ControlFillColorSecondary/Tertiary` |
| Control disabled fill | 30% `#F9F9F9` | 4% white | `p.control_disabled` | `ControlFillColorDisabled` |
| Hover wash (subtle) | 3.7% black | 6% white | `p.subtle_hover` | `SubtleFillColorSecondary` |
| Card stroke | 5.8% black | 5.5% white | `p.card_stroke` | `CardStrokeColorDefault` |
| Control stroke top / bottom | 5.8% / 16.2% black | 9.3% / 7% white | `stroke_top` / `stroke_bottom` (in `_theme_qss`) | `ControlElevationBorderBrush` |
| Divider | 8% black | 8% white | `p.divider` | `DividerStrokeColorDefault` |
| Strong stroke (tracks, off-toggle) | `#D9D9D9` | `#3D3D3D` | `C.SURFACE2` | -- |
| Text primary | `#1B1B1B` | `#FFFFFF` | `C.TEXT` | `TextFillColorPrimary` |
| Text secondary | `#5D5D5D` | `#C5C5C5` | `C.SUBTEXT1` | `TextFillColorSecondary` |
| Text tertiary | `#8A8A8A` | `#8B8B8B` | `C.SUBTEXT0` | `TextFillColorTertiary` |
| Text disabled | 36% | 36% | `p.text_disabled` | `TextFillColorDisabled` |
| Accent | `#0067C0` | `#60CDFF` | `C.BLUE` | `AccentFillColorDefault` |
| Accent hover / pressed | `#1975C5` / `#3183CA` | `#7BD4FF` / `#4FB8E8` | `C.SKY` / `C.SAPPHIRE` | `AccentFillColorSecondary/Tertiary` |
| On-accent text | `#FFFFFF` | `#000000` | `C.CRUST` | `TextOnAccentFillColorPrimary` |
| Nav pane | `#EBEBEB` | `#1A1A1A` | `p.nav_pane` | Mica alt |
| Nav hover / selected | 5% / 6% black | 5.5% / 7.5% white | `p.nav_hover` / `p.nav_selected` | `NavigationViewItemBackground*` |
| Success / Caution / Critical | `#0F7B0F` / `#9D5D00` / `#C42B1C` | `#6CCB5F` / `#FCE100` / `#FF99A4` | `C.GREEN` / `C.YELLOW` / `C.RED` | `SystemFillColor*` |

### Rules

- **One accent.** Rings, pills, primary buttons, focus underline, links -- all
  `C.BLUE`. Never a second brand colour; never per-item rainbow.
- Status colours (`GREEN/YELLOW/RED`) appear only as small glyphs, badges and
  InfoBar tints (`rgba(status, 0.12)` fill + `0.22` stroke). Never as large
  filled panels.
- Letter avatars: `rgba(accent, 0.14)` fill + accent glyph + `0.22` stroke.
- Depth comes from **fill contrast** (base -> card -> control), not from
  visible lines. If a stroke reads as a line at arm's length it is too strong.
- Read colours through `theme.C.*` at build/paint time so `rebuild()` works;
  never bind a QSS string at import in a module that builds widgets later.
- Never introduce a colour missing from this table. Extend the table first.

## 3. Typography

### Face

`theme.ui_font()`: **Segoe UI Variable / Segoe UI** when installed on the
host, else the bundled **Selawik** (Microsoft's open-source, metric-compatible
Segoe UI replacement, SIL OFL 1.1, `src/winpodx/gui/fonts/`), else the desktop
font. The **point size always follows the desktop** (KDE/GNOME) setting so
scaling and user preference are respected. `GLOBAL_STYLE` sets no
`font-family`. Mono (terminal/log only): `'JetBrains Mono', 'Fira Code',
monospace`.

### Scale (WinUI type ramp, px at 100%)

| Level | Size / weight | Python | Usage |
|-------|---------------|--------|-------|
| Title | 28 / 600 | `FONT_HERO` | Page title (`QLabel#pageTitle`) |
| Subtitle | 20 / 600 | `FONT_TITLE` | Hero state line, dialog title |
| Header | 16 / 600 | `FONT_HEADER` | Rare |
| Body Strong | 14 / 600 | `FONT_SUBHEAD` | Group headers, nav profile name, "Pinned"/"Recent" |
| Body | 14 / 400 | `FONT_BODY` | Row titles, controls, nav items (selected is **not** bold) |
| Caption | 12 / 400 | `FONT_CAPTION` | Descriptions, subtitles, captions, tooltips |

### Rules

- Two families max (UI sans + mono). No uppercase section labels, no letter
  spacing tricks -- WinUI hierarchy is weight and colour, not case.
- Secondary copy is `SUBTEXT1`, tertiary `SUBTEXT0`; never lighten text by
  shrinking it below 12.
- User-facing strings go through `tr()`; reuse existing catalog keys.
- Word-wrapped labels inside a resizable `QScrollArea` keep a fixed wrap width
  (`src/winpodx/gui/AGENTS.md`).

## 4. Spacing & Layout

This section is the **measurable contract**. `tests/test_gui_layout_contract.py`
measures real widget geometry offscreen and fails when a page drifts from it.
If you need a value that is not here, add it here first.

### Base unit — 4px grid, WinUI scale

| Token | Value | Python | Usage |
|-------|-------|--------|-------|
| `space.xs` | 4px | `SPACE_XS` | Card-to-card stack gap; icon gaps |
| `space.s` | 8px | `SPACE_S` | Inside a row: icon to text, control clusters |
| `space.m` | 12px | `SPACE_M` | Text column gap inside a card; toolbar gaps |
| `space.l` | 16px | `SPACE_L` | Card inner padding (all sides) |
| `space.xl` | 24px | `SPACE_XL` | Group to group; title to first block |
| `space.xxl` | 32px | `SPACE_XXL` | Large section breaks |
| `space.xxxl` | 48px | `SPACE_XXXL` | Rare |

Everything must land on a multiple of 4. A 6px gap is a bug (it is usually
`spacing:4 + 2x1px border` -- put the stroke *inside* the card or subtract it).

Radius: `RADIUS_XS` 2 / `S` 4 / `M` 4 / `L` 8 / `XL` 8 / `XXL` 8
(WinUI: **4px controls, 8px containers**). Controls: `CONTROL_HEIGHT_W11` 32,
`HIT_TARGET` 44 (only where a test pins it: hero primary, reverse-open toggle).

### Shell frame (every page, no exceptions)

| Measure | Value | Where |
|---------|-------|-------|
| **Title bar** | **32**, full width, `nav_pane` colour; 16px app icon + 12px "WinPodX" left, three 46x32 caption buttons right (close hover `#C42B1C`); drag = `startSystemMove`, double-click = maximize; 6px border = `startSystemResize` | `TITLE_BAR_H`, `CAPTION_BTN_W`, `RESIZE_MARGIN` -- `_title_bar.py` / `_frameless.py`; `WINPODX_NATIVE_TITLEBAR=1` restores WM decorations and hides the bar |
| Content layer | `C.BASE` with an 8px top-left radius under the title bar (WinUI "layer") | `QWidget#contentLayer` |
| **Window minimum** | `max(preferred x MIN_SHRINK_RATIO, rail + margins + widest page floor)` x `preferred_h x MIN_SHRINK_RATIO` -- derived, never a pixel constant; re-evaluated on every reflow with captions squeezed to the candidate column | `MIN_SHRINK_RATIO` 0.6, `_shell_geometry._apply_window_minimum` |
| Nav overlay | below `NAV_COMPACT_BELOW` an opened pane **overlays** the content (slot stays 48, pane 320 raised, 1px edge) and light-dismisses on outside press or page switch | `_shell_geometry.py`, `#navSlot`, `#navOverlayEdge` |
| Wrapped captions | `mark_fluid_wrap(label)`: width = `(column - structural inset) x WRAP_RATIO`, floor 24 average chars, re-derived per reflow (`fit_fluid_wraps`) -- fixed width only because of the #553 heightForWidth guard | `WRAP_RATIO` 0.92 |
| Empty-state panel | `CONTENT_MAX_WIDTH x EMPTY_PANEL_RATIO` wide, labels pinned to that minus padding | `EMPTY_PANEL_RATIO` 0.4 |
| Nav pane width | 320 / 48 compact; hamburger `#navToggle` (32x36) pins the user's choice across resizes | `NAV_PANE_WIDTH` / `NAV_PANE_COMPACT` / `_main_window_navpane_toggle.py` |
| Nav avatar | 64 expanded / 32 compact (re-rendered, never clipped) | `AVATAR_EXPANDED` / `AVATAR_COMPACT` |
| Nav item height | 36 | `NAV_ITEM_HEIGHT` |
| Nav item gap | 2 | pane layout spacing |
| Nav pane top padding | 16 | pane layout margin |
| Selection pill | 3x16, 4px from the pane edge, vertically centred | `NAV_INDICATOR` |
| **Page top margin** | **28** | `PAGE_MARGIN_TOP` -- set once in `main_window._build_ui` |
| **Page left margin** | **36** | `PAGE_MARGIN_X` -- set once in `main_window._build_ui` |
| **Page right margin** | **36** | pages that own a scroll area set `body right margin = 36 - scrollbar gutter`; pages without a scroll area set 36 |
| Content max width | 1000 | `CONTENT_MAX_WIDTH`; column is **left-anchored**, never centred, never split 50/50 |
| Title (28/600) to subtitle (12) | 4 | `make_page_header` |
| Subtitle to first block | 24 | page body top spacing |

**Rule:** pages set `setContentsMargins(0, 0, <right>, 24)` on their outer
layout and nothing else. Only the shell adds the 28/36 frame. A page that sets
its own left/top margin doubles the frame -- that is the "cramped title" bug.

**Every page's first content pixel must be at the same x (356 at 1100x720
with the 320 pane) and the same y (title top 48).** The contract test asserts
this for all 8 pages.

### Group and card rhythm (SettingsCard)

| Measure | Value |
|---------|-------|
| Group header (14/600) to first card | 8 |
| Card to card in the same group | **4** (border included in the 4) |
| Group to next group | 24 |
| Card min height | 68 (single-line header + description) |
| Card padding | 16 all sides |
| Icon | 20, `SUBTEXT1` |
| Icon to text column | 12 |
| Header (14/400) to description (12, `SUBTEXT1`) | 2 |
| Text column to action | >= 16, action right-aligned to the card's inner edge |
| Action control height | 32 (`ToggleSwitch` 44 hit box, 20 visual) |

Cards in a group **share a common right edge** with every other card and group
on the page (the content column edge). No card is narrower than the column.

### Page-specific

- **Dashboard hero** (About card): 56 icon, name (14/600), state (20/600),
  detail (12), primary (32 tall, 44 hit) on the left; three `RingGauge` 72px
  on the right with 24 gaps, vertically centred. Below 640 content px the rings
  drop under the text at 56px, centred. Hero padding 16.
- **Applications**: search row 32 tall; "Pinned"/"Recent" headers (14/600) with
  8 below; tiles 120x72 in a grid with 8 gaps, left-anchored; sections separated
  by 24.
- **Tools / Info / Devices / License / Logs**: pure SettingsCard groups as above.

### Scroll ownership (mandatory)

- One vertical `QScrollArea` per page; `ScrollBarAlwaysOff` horizontally.
- The scrollbar lives in the shell's right margin: the scroll area extends to
  the window edge; the *body* keeps the 36px right margin (minus the 10px
  scrollbar gutter) so cards never sit under the scrollbar.
- `QMainWindow`, nav pane and page header never scroll.

### Breakpoints

Measured on **page content width** (`pages.width()`), logical px:

| Band | Content width | Layout |
|------|---------------|--------|
| Wide | >= 640 | Hero two-zone; settings rows single line |
| Narrow | < 640 | Hero stacked; rows stack copy over action |
| Nav compact | window < 1100 | 48px rail |

There is no 740/1100 content band any more. A default 1100x720 window gives
~744 content px and **must** render the wide layout.

### Rules

- Numbers come from tokens; never type `12` when you mean `SPACE_M`.
- Borders are part of the box: when a layout gap must be 4, the sum of
  `spacing + borders` is 4.
- Do not centre content; WinUI left-anchors and lets the right side breathe.
- Do not "balance" a page with a 50/50 split; the single column is the design.

## 5. Components

Every entry is **what ships** and is measured by a test. Do not describe
unimplemented ideas here; put them in a plan file.

### AppShell (NavigationView)

- **Structure:** `QHBoxLayout` -- `QFrame#navPane` | `pageFrame`(`QStackedWidget`).
  `main_window.py` is the thin shell; `NavPaneMixin` (`_main_window_navpane.py`)
  builds the pane; pages are private mixins. No top strip.
- **Pane:** 320px (`NAV_PANE_WIDTH`), 48px rail below a 1100px window
  (`NAV_PANE_COMPACT`); no right border; bg `p.nav_pane`. Top block: 32px app
  avatar + "WinPodX" (14/600) + pod status line (`pod_dot`, `pod_label`,
  `agent_dot`, `rdp_dot`, packed left with 4px gaps). Then `QLineEdit#navSearch`
  (32px). Then 8 `QPushButton#navItem` rows (36px, 4px radius, icon 16, label
  14/400 -- selected is **not** bold), gap 2. Footer: 1px divider +
  `btn_start` / `btn_stop` as ghost `#navFooterItem` rows (36px).
- **Selection:** `QFrame#navIndicator` 3x16 accent pill, 4px from the pane edge,
  vertically centred on the checked row; 150ms OutCubic slide on switch,
  instant on show/compact re-layout (`_sync_nav_indicator(animate=False)`).
- **States:** rest transparent / hover `p.nav_hover` / pressed `p.subtle_hover`
  / checked `p.nav_selected`.
- **Accessibility:** every nav button, the profile button, Start and Stop have
  `accessibleName` set at build time (compact mode clears text, not names).
  Alt+1..N switch pages; Ctrl+F focuses search.
- **Contract test:** `tests/test_main_window_shell.py`.

### PageHeader

- **Structure:** `make_page_header(title, subtitle, actions_widget)` ->
  `QLabel#pageTitle` (28/600, `C.TEXT`) + optional subtitle (12, `SUBTEXT1`),
  gap 4; header margins `(0, 16, 0, 0)`; actions right-aligned to the content
  column edge. Body starts 24 below.
- **Contract:** every page's title label maps to `(356, 60)` at 1100x720
  (`TITLE_BAR_H 32 + PAGE_MARGIN_TOP 28`; `tests/test_gui_layout_contract.py`),
  and stays there after the page body scrolls -- the header is pinned in the
  shell, pages register it via `_register_page_header(index, title, subtitle,
  actions)`.

### SettingsCard / SettingsGroup

- **Structure:** `make_settings_card(icon, title, description, action=, chevron=,
  object_name=)` -> `QFrame#settingsCard`: `[icon 20] [title 14/400 over
  description 12 SUBTEXT1, gap 2] [stretch] [action]`. Min height 68, padding
  16, icon->text 12, text->action >= 16. `make_settings_group(title)` ->
  header (14/600) with 8 below, cards stacked with **4** between (border
  included), groups 24 apart. Cards fill the content column (left 356, width
  708 at 1100) -- never a 50/50 split.
- **Surface:** fill `C.SURFACE0`, 1px `p.card_stroke`, radius 8 (`RADIUS_L`),
  no shadow. Hover (interactive rows only): `p.subtle_hover`.
- **Actions:** `QLineEdit`/`QComboBox`/`QPushButton` 32px; `ToggleSwitch`
  (44 hit box); chevron rows use a bare 32px ghost glyph (`chevron_button_qss`).
- **Restyle:** `restyle_settings_cards(root)` re-reads `theme.*` on scheme change.
- **Used by:** Dashboard rows, Settings groups, Tools, Info, Devices, License,
  Logs, reverse-open panel.

### PodStatusHero (Dashboard About card)

- **Structure:** `QFrame#podStatusHero`, padding 16. Left column: 56px app icon,
  "WinPodX" (14/600), `#podStatusLabel` (20/600), `#podStatusDetail` (12),
  `#podPrimaryAction` (`BTN_PRIMARY`, 32 visual / 44 min height). Right:
  `#podMetricsCluster` = three `RingGauge` (RAM, CPU, Disk C:) 72px with 24 gaps,
  vertically centred. Below 640 content px (`_WIDE_PX`) the layout is
  `TopToBottom` and rings drop under the text at 56px.
- **Copy:** running -> detail "Pod is ready!", recovery row "Protected --
  monitoring active" (never the same string twice).
- **Contract test:** `tests/test_main_window_dashboard.py`.

### RingGauge

- **Structure:** painted `QWidget` (`_ring_gauge.py`): 72px (56 compact), 6px
  stroke, track `C.SURFACE1`, arc `C.BLUE` starting at 12 o'clock clockwise,
  round caps; centre = value (14/600), caption below (12 `SUBTEXT1`). Disk arc
  turns `C.RED` at `critical_pct`. n/a = empty track + "--".
- **API:** `set_value(pct, text)`; `_pct`, `_detail`, `accessibleName` =
  caption, `accessibleDescription` = value (+ "WARNING" when critical);
  `set_compact(bool)`. Attribute names `_bar_ram/_bar_cpu/_bar_disk` are kept
  on the Dashboard for the existing test seams.
- **Motion:** 250ms OutCubic arc animation; skipped while hidden.
- **Colour rule:** one accent for all rings -- no per-gauge rainbow.
- **Contract test:** `tests/test_gui_ring_gauge.py`.

### StatBar

Retained in `_stat_bar.py` for reuse; not used on the Dashboard.

### ToggleSwitch

- **Structure:** `ToggleSwitch(QCheckBox)` (`_toggle_switch.py`), painted:
  40x20 track (`TOGGLE_W/H`), 12px thumb (14 hover), 4px inset, 150ms slide;
  on = `C.BLUE` track + `C.CRUST` thumb; off = `C.SURFACE0` track + 1px
  `C.SURFACE2` + `SUBTEXT1` thumb; disabled 36%. Focus = 1px `C.TEXT` ring
  outside the track. No text; `accessibleName` = the row title.
- **Where:** every boolean setting (Dashboard reverse-open, Settings toggles,
  reverse-open panel). Plain `QCheckBox` (20px, checkmark SVG) is for dialogs.
- **Contract test:** `tests/test_gui_toggle_switch.py`.

### AppTile (Start tile)

- **Structure:** `_AppTile(QFrame#appTileBtn)` 120x72 fixed (one-line name;
  two-line names grow), 32px icon over a 104px-wide wrapped label; `START_TILE`
  QSS (transparent, 4px radius, `p.subtle_hover` hover, 2px `C.TEXT` focus
  ring). Letter fallback = 14% accent tint + accent glyph, never a saturated
  fill.
- **Grid:** `floor(avail / 128)` columns, 3..6, left-anchored, 8px gaps; row
  height = max tile height. Rebuilds clear with `hide()` + `deleteLater()`.
- **Keyboard:** StrongFocus, Enter/Space launch once, no autorepeat relaunch.

### RecommendedRow

- `_launcher_rows.RecommendedRow(QFrame#recommendedRow)` 40px: icon 24 + name
  14 + caption 12 ("Running"/"Recent"); StrongFocus, Enter/Space/click, focus
  ring, tab order after the pinned tiles.

### Button / Input / Combo

- All 32px, radius 4, **elevation border**: 1px `stroke_top` with a darker
  1px `stroke_bottom` (`ControlElevationBorderBrush`). Primary = accent fill,
  on-accent text, darker bottom edge. Secondary = `p.control_fill`. Ghost =
  transparent, hover `p.subtle_hover`. Disabled = 4% fill + 36% text.
- `QLineEdit` focus = 2px accent **underline** (`ControlFillColorInputActive`
  bg), not a full frame. `QComboBox` chevron = bundled `chevron-down-{light,
  dark}.svg` 12x8.

### Inner controls (GLOBAL_STYLE)

- Menu: 32px items, hover `p.subtle_hover` (never accent), radius 8, 4px
  padding, separators `p.divider`. Tooltip: `C.SURFACE0`, 1px stroke, 12px.
- Checkbox 20px + `checkmark-on-accent.svg`; radio 20px + `radio-dot.svg`;
  list rows 36px with a 3px accent pill on selection; progress 4px; slider
  ring thumb; spinbox chevrons; tab underline 3px accent.
- Focus everywhere = `2px solid C.TEXT` (`FOCUS_RING`), `outline: none`.
- Dialog: `C.MANTLE` body, 20/600 title, bottom `QDialogButtonBox` strip
  (`p.control_fill`, 1px top divider, 24px padding, buttons 32x96+).
- **Contract test:** `tests/test_gui_inner_controls.py`.

### IconLoader

- `gui/icons/load_icon(name, colour, size)`: SVG recolour + render + cache.
  Sizes 16 (nav), 20 (SettingsCard), 24 (rows), 32 (tiles), 56 (hero).
  Pass `theme.C.*` at call time so restyle can re-tint.

### Fonts

- `theme.ui_font(base)`: "Segoe UI Variable" / "Segoe UI" when installed,
  else the bundled **Selawik** (`gui/fonts/*.ttf`, SIL OFL 1.1, Microsoft's
  metric-compatible Segoe UI replacement), else the desktop font. The point
  size always follows the desktop (KDE/GNOME) setting. `GLOBAL_STYLE` sets no
  `font-family`.

## 6. Motion & Interaction

WinUI timings, `QEasingCurve.OutCubic`, all skipped when the widget is hidden.

| Interaction | Duration | Where |
|-------------|----------|-------|
| Nav selection pill slide | 150ms | `_move_nav_indicator` |
| ToggleSwitch thumb | 150ms | `ToggleSwitch._animate_to` |
| RingGauge arc | 250ms | `RingGauge` arc fraction |
| Hover / pressed fills | instant (QSS) | all controls |

### Rules

- Animate **painter state** (arc fraction, thumb offset) or a tiny indicator's
  `pos`; never layout, size, margins, or page geometry.
- Every interactive element has rest / hover / pressed / focus / disabled
  fills from the WinUI table; hover is `p.subtle_hover` or `p.control_hover`,
  never accent.
- Page switches are instant.
- Tests finish animations deterministically (`anim.setCurrentTime(duration)`)
  or assert the end value; the harness settles 400ms before a real grab.

## 7. Depth & Surface

**Strategy: WinUI layering -- no shadows, no blur.**

| Layer | Recipe |
|-------|--------|
| Base | `C.BASE` (app), `p.nav_pane` (pane) |
| Card | `C.SURFACE0` + 1px `p.card_stroke` + radius 8; cards in a group stack with 4px gaps and read as one surface |
| Control | `p.control_fill` + **elevation border** (1px `stroke_top`, darker 1px `stroke_bottom`) + radius 4 -- the bottom edge is what makes controls read as raised |
| Accent control | `C.BLUE` + darker bottom edge (`accent_bottom`) |
| Flyout / menu / dialog | `C.SURFACE0` (menu) / `C.MANTLE` (dialog) + 1px stroke + radius 8 |
| InfoBar | `rgba(status, 0.12)` fill + `rgba(status, 0.22)` stroke, radius 4 |

Do not use `QGraphicsDropShadowEffect`, `add_shadow`, gradients as fills, or
top-light hairlines. Mica/Acrylic are not faked.

## 8. Accessibility

WCAG 2.2 AA-equivalent behaviour where Qt permits. Each line is enforced by
a test or a fixed token.

- **Contrast:** 4.5:1 body, 3:1 large text and non-text. `SUBTEXT0` /
  `p.text_disabled` are for captions and disabled states only, never for
  essential copy.
- **Keyboard:** every interactive element reachable by Tab in reading order;
  Enter/Space activate tiles and recommended rows; Alt+1..N switch pages;
  Ctrl+F focuses search.
- **Focus:** `2px solid C.TEXT` ring on every control (`FOCUS_RING`); toggle
  and tiles paint their own ring. Never colour-only focus.
- **Names:** painted widgets and icon-only controls set `accessibleName` at
  build time (`RingGauge`, `ToggleSwitch`, nav buttons, profile button, Start,
  Stop, chevron rows); descriptions carry live values (`"62%"`, `"... WARNING"`).
  Compact mode clears button *text*, never names.
- **Non-colour status cues:** state is always a word plus an icon; disk
  critical adds "WARNING" to the description; badges carry text.
- **Targets:** 32px controls with 44px hit boxes where a test pins them (hero
  primary, reverse-open toggle); 36px nav rows; 40px recommended rows.
- **Scaling:** `HighDpiScaleFactorRoundingPolicy.PassThrough`; font size
  follows the desktop; no clipping of primary controls at 125-200%.
- **Motion:** nothing essential is animation-only; animations are skipped when
  hidden.
- **Text:** `tr()` for every user string; CJK/long labels wrap at a fixed
  width or elide; no horizontal scrolling of primary content.
- **Themes:** light and dark both meet the contrast rules above; dark strokes
  are alpha-white so they stay visible on `#2B2B2B`.

### Debt register

| ID | Severity | What | Status |
|----|----------|------|--------|
| DD-001 | Minor | Web `web/style.css` still uses the old GitHub-dark palette | Open, out of GUI scope |
| DD-002 | Minor | A few `_main_window_*` modules still bind QSS string names at import for surfaces that are only built once; they are covered by `_restyle_*` hooks on scheme change | Open; convert to `theme.*` reads when touched |
| DD-003 | Minor | Combo popup list and QMessageBox use Qt-native geometry (not WinUI 32px rows) on some platform themes | Open |
