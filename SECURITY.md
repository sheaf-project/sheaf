# Security Policy

Sheaf handles deeply personal identity data — GDPR Article 9 special category data. If you find a vulnerability, please report it responsibly and we'll fix it as fast as we can.

## Reporting a vulnerability

**Do not open a public issue for security vulnerabilities.**

Please report vulnerabilities by emailing **sheaf-security@lupine.systems**, or by opening a private security advisory on GitHub.

Include:
- Description of the vulnerability
- Steps to reproduce
- Impact assessment (what could an attacker do?)
- Suggested fix, if you have one

We will acknowledge your report within 48 hours and aim to provide a fix timeline within a week.

## Scope

**In scope:**
- Authentication or authorisation bypass
- Data exposure (accessing another user's data)
- Encryption weaknesses or key exposure
- Injection (SQL, command, path traversal, XSS)
- CSRF or session fixation
- Privilege escalation

**Out of scope:**
- Self-hosted instances with default/weak credentials, or compromise of an instance via a lateral move from an unrelated vulnerability (e.g. outdated or misconfigured OS or other services on the same system). The security of the machine used for selfhosting is the responsibility of its own admins and nobody else.
- Rate limiting on self-hosted instances (not enforced by default)
- Vulnerabilities in dependencies that don't affect Sheaf's usage of them
- Calling lack of built-in HTTPS support a bug or vulnerability - the documentation clearly states the need for a reverse proxy to handle TLS termination and for performance reasons.

## Disclosure policy

We believe in coordinated disclosure. Once a fix is available:

1. We'll release the fix
2. We'll credit the reporter (unless they prefer anonymity)
3. We'll publish a brief advisory describing the issue and fix

We ask reporters to give us reasonable time to fix issues before public disclosure. We understand how security research works and will always collaborate with good-faith efforts at responsible disclosure.
