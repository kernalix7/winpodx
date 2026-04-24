# Security Policy

**English** | [한국어](docs/SECURITY.ko.md)

## Supported Versions

| Version | Supported |
|---------|-----------|
| Latest  | Yes       |

## Reporting a Vulnerability

Please report security vulnerabilities through GitHub Security Advisories:

**[Report a vulnerability](https://github.com/kernalix7/winpodx/security/advisories/new)**

**Do NOT open a public issue for security vulnerabilities.**

### What to Include

- **Description**: A clear description of the vulnerability
- **Steps to Reproduce**: Detailed steps to reproduce the issue
- **Impact**: The potential impact of the vulnerability
- **Affected Components**: Which modules or files are affected
- **Environment**:
  - Operating System and version
  - Python version
  - FreeRDP version
  - Desktop Environment
  - Display Server (X11/Wayland)

## Response Timeline

| Step | Timeframe |
|------|-----------|
| Acknowledgment | Within 48 hours |
| Assessment | Within 7 days |
| Fix | Within 30 days |

## Scope

The following areas are considered in scope for security reports:

- **Command injection in subprocess calls**: Unsanitized input passed to shell commands
- **Credential exposure in config files**: Passwords or secrets stored in plaintext
- **RDP session hijacking**: Unauthorized access to active RDP sessions
- **Path traversal in UNC conversion**: Manipulation of Windows/Linux path translation
- **Privilege escalation via backend management**: Unauthorized privilege gain through container or VM backends

## Out of Scope

The following are considered out of scope:

- Attacks requiring physical access to the machine
- Social engineering attacks
- Vulnerabilities in third-party dependencies (report these to the upstream project)

## Security Best Practices

This project follows these security practices:

- **Input validation on subprocess arguments**: All arguments passed to subprocess calls are validated and sanitized
- **Askpass preferred over plaintext passwords**: Interactive password prompts (askpass) are used instead of storing passwords in plaintext
- **XDG-compliant file permissions**: Configuration files follow XDG Base Directory Specification with appropriate file permissions
- **No secrets in code or git**: Secrets, credentials, and API keys are never committed to the repository
- **TLS-only RDP**: SecurityLayer=2 (TLS) enforced on the RDP channel; NLA disabled only because RDP is bound to 127.0.0.1
- **Windows build pinning**: Feature updates blocked via registry policy; security updates install normally
- **Container-isolated RDP**: RDP port is bound to 127.0.0.1 only; not exposed to the network

## Host <-> Guest Trust Model

winpodx treats the Windows guest as a **semi-trusted** component, not a trusted
extension of the host. This section documents how that trust boundary is drawn
and enforced, specifically for the app-discovery channel introduced in v0.1.7.

### Provisioning scope and threat assumptions

winpodx provisions the Windows guest itself inside a rootless Podman container
(or, optionally, Docker / libvirt). Because the host controls the image, the
unattended-install answer file, and the OEM post-install scripts, a freshly
provisioned guest starts in a known-good state.

However, JSON emitted by `scripts/windows/discover_apps.ps1` is treated as
**semi-trusted input**: it is not attacker-controlled network data, but it is
also not implicitly trusted code. The guest can be corrupted through entirely
legitimate Windows-side activity, for example:

- A malicious `.lnk` dropped into the user's Start Menu folder.
- A rogue `AppxManifest.xml` from a sideloaded UWP package.
- Supply-chain compromise of a Windows application the user installed
  themselves (installer trojan, auto-update hijack, etc.).

Any of these can cause the discovery script to emit hostile metadata. The host
side must therefore validate everything the guest sends before it is allowed to
influence the host filesystem or any executed command line.

### Host-side validation pipeline

The host enforces the trust boundary with a layered set of guards. Each of
these rejects malformed guest output before it can be written to disk or
interpolated into a subprocess argument list:

- **`_CONTAINER_NAME_RE`** — the container name pulled from the host config is
  scrubbed against an allowlist regex before it is passed as a subprocess arg.
- **`_AUMID_RE`** — the UWP launch URI must match
  `^[A-Za-z0-9._-]+![A-Za-z0-9._-]+$`, i.e. a bare
  `PackageFamilyName!AppId`. Anything else is refused.
- **`_WM_CLASS_RE`** — the WM class hint used for desktop-entry
  `StartupWMClass=` must be a safe Linux identifier.
- **Slug regex** — every slug derived from a guest-supplied app name is
  sanitized to `[a-z0-9_-]{1,64}` before it is used as a filesystem path
  component.
- **`_MAX_APPS` (500)** — a hard cap on the total number of discovered
  entries accepted per run.
- **`_MAX_ICON_BYTES` (1 MiB per icon)** — a per-icon size cap.
- **`_MAX_PATH_LEN`** — a cap on the length of guest-provided path strings.
- **Host-side stdout cap (64 MiB)** — the subprocess reader bounds the total
  bytes consumed from the guest, so a misbehaving guest cannot OOM the host.
- **PNG magic byte + `QImage.loadFromData` sanity check** — every icon must
  begin with the PNG signature and must successfully parse through Qt's image
  decoder before it is written under `~/.local/share/icons/hicolor/`.
- **`_safe_rmtree`** — refuses to delete any path outside
  `~/.local/share/winpodx/apps/`.
- **`_is_within`** — refuses to write any path outside the winpodx user data
  directory.
- **List-args subprocess only** — all subprocess invocations pass an argv
  list; `shell=True` is never used on guest-derived strings.

### Secret flow direction

Secrets flow **host -> guest only**. The RDP password is generated on the host
by `core/compose.py` `generate_password`, written into the guest through
unattended-install compose environment variables, and rotated on a
host-controlled schedule.

The discovery JSON carries **no credentials** in either direction. The
`guest -> host` channel is strictly typed as `{app metadata, icon bytes}` and
nothing else, so a compromised guest cannot exfiltrate host secrets through
this channel because the channel has no field that could carry them.

### Guest-compromise impact (possible vs. not possible)

If an attacker fully compromises the Windows guest, the bounded impact via the
discovery channel is:

**Possible:**

- Bogus `.desktop` entries appearing in the user's app menu (with names
  already sanitized through the slug regex).
- A PNG crafted specifically to exercise the `QImage` parser.
- Broken launches for malicious AUMIDs that fail `_AUMID_RE` validation (the
  launch simply never happens).

**NOT possible via the discovery channel:**

- Host code execution outside the PNG parser itself (which runs inside Qt's
  or the stdlib's own image-decoding sandbox).
- Filesystem writes outside `~/.local/share/winpodx/apps/` and
  `~/.local/share/icons/hicolor/`.
- Command injection into FreeRDP, Podman, Docker, or virsh argument lists.
- Credential exfiltration (no credentials traverse the guest -> host
  direction).

Additionally, the Windows guest runs under **rootless Podman** by default, so
even a full guest-kernel RCE does not grant host root. A guest-compromise
attacker is confined to the unprivileged user namespace that hosts the guest
container.

## Attribution

We appreciate responsible disclosure and will credit reporters in release notes (unless anonymity is preferred).
