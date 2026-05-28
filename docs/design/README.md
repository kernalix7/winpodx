# docs/design/

Engineering-internal documentation: design specs, reference docs, roadmaps, security reviews, and machine-readable schemas. Intended audience is contributors and maintainers, not end users — user-facing docs live one level up in [`../`](../) (see [`../README.md`](../README.md)).

A document belongs here when any of these are true:
- It describes how the code is structured or sequenced (rather than how to use the product).
- It refers to specific files, functions, or commit-level decisions.
- It records a decision (review, audit, ADR) made at a point in time.
- It is consumed by tooling, not humans (schemas, machine-readable specs).

## Index

### Design specs (`*_DESIGN.md`)
Proposals and rationale for major subsystems. Each documents the problem, the chosen design, and the trade-offs considered.
- [AGENT_V2_DESIGN.md](AGENT_V2_DESIGN.md) — host-side HTTP client + guest `agent.ps1` listener (the `/health`, `/exec`, `/events`, `/apply`, `/discover` channel).
- [AGENT_FIRST_INSTALL_DESIGN.md](AGENT_FIRST_INSTALL_DESIGN.md) — install-time agent bring-up, OEM bind mount, first-boot ordering.
- [GUEST_SYNC_DESIGN.md](GUEST_SYNC_DESIGN.md) — version-stamp comparison + windowless agent transport for upgrading the guest payload.
- [REVERSE_OPEN_DESIGN.md](REVERSE_OPEN_DESIGN.md) — Linux "Open with" → Windows handler reverse path (#48).

### Reference (process / interface)
Long-form references that describe code paths or interface contracts.
- [LIFECYCLE.md](LIFECYCLE.md) ([한국어](LIFECYCLE.ko.md)) — end-to-end pod lifecycle: install, sysprep, migrate, apply chain, multi-session, discovery, transport selection. "Who fires it, what it does, where the code lives."
- [TRANSPORT_ABC.md](TRANSPORT_ABC.md) — host→guest `Transport` abstract base class, the `FreerdpTransport` / `AgentTransport` contracts, dispatch policy.

### Roadmaps
Per-release cut-lists with execution order and deferred-item tracking.
- [ROADMAP-0.6.0.md](ROADMAP-0.6.0.md) — Consolidation & UX release: unify provisioning, thin AppImage, command-taxonomy reorg.

### Reviews / audits
Point-in-time decisions and their rationale.
- [ROTATION_SECURITY_REVIEW_2026-05-07.md](ROTATION_SECURITY_REVIEW_2026-05-07.md) — security review of the password-rotation flow.

### Schemas
Machine-readable specs consumed by tooling.
- [install_failure.schema.json](install_failure.schema.json) — JSON Schema for the install-failure telemetry blob.

## Conventions

- **Naming:** design proposals carry the `_DESIGN.md` suffix. Reference / interface / roadmap / review / schema files use a descriptive name without the suffix (their content type is documented by the section they live under here).
- **Dating:** reviews and audits include the date in the filename (`*_YYYY-MM-DD.md`). Roadmaps include the target version (`ROADMAP-X.Y.Z.md`).
- **No `.ko.md` mirrors required.** Korean mirrors are for user-facing docs only; engineering docs are English-only unless a specific doc is dual-authored (LIFECYCLE is currently the only exception).
- **No behaviour-change content.** A design doc describing a change shouldn't ship in the same PR as the change itself land; either the design comes first (the PR that lands the change refers back to it) or the doc is co-authored with the change but marked clearly as describing what just landed.
