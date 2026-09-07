# Support

English | [中文](SUPPORT.zh.md)

The issue tracker accepts reproducible bugs and concrete feature requests only. Everything else has a faster home below — routing your question to the right channel gets you a quicker answer.

## Where to get help

1. **Usage, configuration, and tuning questions** → GitHub Discussions, Q&A category. Search existing discussions before asking.
2. **Reproducible bugs and concrete feature requests** → GitHub Issues. The issue templates guide you through the required fields.
3. **Security vulnerabilities** → private reporting as described in the [security policy](SECURITY.md). Never a public issue.
4. **`examples/chat` usage questions** → also Discussions, at demo-level community support.
5. **Django or channels questions unrelated to this layer** → their own communities (the [Django Forum](https://forum.djangoproject.com/), the [Django Discord](https://chat.djangoproject.com/), the [channels support docs](https://channels.readthedocs.io/en/latest/support.html)). Issues filed here about them will be redirected there.

## What makes a bug report actionable

- Exact reproduction steps, ideally on a clean project or `examples/chat`.
- Environment: Python version, distribution/kernel, free space in `/dev/shm`, number of worker processes, and your django-channels-shm configuration (`prefix`, `capacity`, `shm_size`, …).
- Expected behavior versus actual behavior, with logs or error output attached.

The bug issue form walks you through these fields.

## What we can't help with

Vague problem reports ("it's slow!", "messages randomly drop!") are closed with a pointer to this page. Apologies if that comes across as harsh — maintenance time is limited, and keeping the issue tracker for reproducible problems serves everyone better.

For production-only issues you cannot reproduce locally: first eliminate the environment (connect directly to the ASGI server without the reverse proxy, compare package versions on both ends, and try to shrink the case down to `examples/chat`). If the problem still will not localize, bring the environment details to Discussions and we will dig together.

There is no commercial support or SLA — support here is community best-effort.
