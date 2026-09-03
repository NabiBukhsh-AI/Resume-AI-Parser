# Security Policy

## Supported versions

| Version | Supported |
| --- | --- |
| 2.x | Yes |
| 1.x | No — it was never functional; upgrade to 2.x |

## Reporting a vulnerability

Please report privately through
[GitHub Security Advisories](https://github.com/NabiBukhsh-AI/Resume-AI-Parser/security/advisories/new)
rather than opening a public issue. Include reproduction steps and the impact you see; you can
expect an acknowledgement within a few days.

## Handling personal data

This project processes résumés, which are personal data under GDPR and similar regimes. The
defaults are chosen accordingly, and you should know what they do:

- **Uploads are processed in memory** and never written to disk by the application.
- **Logs are redacted** — emails, phone numbers, inline URL credentials and document bodies are
  scrubbed before reaching a sink (`observability.redact_pii`, on by default).
- **Error responses do not echo request payloads.**
- **The result cache** is content-addressed and stores parsed output. It is in-memory by
  default; enabling `cache.directory` writes structured résumé data to disk. Set a TTL, or
  disable the cache, if that conflicts with your retention policy.
- **Document text is sent to your configured LLM provider.** Review that provider's data
  retention and training policies before processing real candidate data, and configure zero
  retention where your provider offers it.

## Deployment notes

- Set `RESUME_PARSER_API_KEY`, or terminate authentication at your gateway. With it unset, the
  API is open by design.
- The built-in rate limiter is **per-process**. With more than one worker or instance, set
  `RESUME_PARSER_SERVER__RATE_LIMIT_PER_MINUTE=0` and rate-limit at the ingress.
- Set `RESUME_PARSER_SERVER__CORS_ORIGINS` to exact origins. It is empty by default, which
  disables CORS entirely.
- Keep `debug` off in production — the settings model rejects the combination outright.
