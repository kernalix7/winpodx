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

## Attribution

We appreciate responsible disclosure and will credit reporters in release notes (unless anonymity is preferred).
