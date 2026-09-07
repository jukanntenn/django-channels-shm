# Security Policy

English | [中文](SECURITY.zh.md)

## Reporting a vulnerability

Report vulnerabilities privately — never in a public issue, pull request, or discussion.

1. **Preferred:** use GitHub's private vulnerability reporting (open the repository's **Security** tab, then click **Report a vulnerability**). The form reaches the maintainer directly and supports coordinated disclosure through a GitHub Security Advisory, including a possible CVE.
2. **Alternative:** email jukanntenn@outlook.com.

## What to include

Enough to triage the report — no lengthy writeup is needed:

- A short description of the issue and the component involved (Python layer, Rust native module, wakeup mechanism, or `examples/chat`).
- A minimal proof of concept or reproduction steps.
- Your environment: Python version, distribution/kernel, and the django-channels-shm commit you run.
- The impact in one or two sentences: what an attacker gains and what it requires.

## What to expect

- Acknowledgment within about 3 days (solo maintainer, best-effort — no hard SLA).
- Coordinated disclosure via a GitHub Security Advisory once the report is confirmed; details stay private until a fix is released.
- The reporter is kept informed of progress and credited in the advisory unless they prefer to stay anonymous.

## Scope and trust boundary

Everything in this repository is in scope, including `examples/chat` (issues there are triaged at demo severity).

django-channels-shm is an IPC layer for cooperating processes on a single machine within one trust domain. The shared-memory region is created with mode `0o600`, so it is accessible to processes of the same UID (and to root) — another process running as the same user can read or tamper with messages. That is by design and matches POSIX IPC semantics; it is not a vulnerability. Isolation between users or between tenants on a shared host is a deployment responsibility and out of scope.

## Supported versions

| Version | Supported |
| ------- | --------- |
| `main` (pre-release 0.x) | ✅ |

There are no backport branches. This policy evolves as tagged releases appear.
