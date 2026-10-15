# Historical Security Changes

This file records earlier configuration-hardening work, not current setup
instructions or a security certification.

Early changes moved deployment credentials out of tracked configuration and
introduced environment-based session keys. Later changes removed the development
key fallback and moved deployed secrets into a private file outside the web root.

The former `config.json` migration instructions and pending-feature list are
obsolete. In particular:
- `SECRET_KEY` is required in development as well as production.
- Current deployment and frontend configuration use `.env`.
- Password requirements, session settings, and request limiting are documented
  in the current guide rather than tracked here as future work.

For current behavior and limitations, see
[Security Implementation](SECURITY_IMPLEMENTATION.md).
For setup and private configuration, see [Configuration](CONFIG.md).

If credentials were previously committed, removing a file from Git's index does
not remove it from existing history. Rotate exposed credentials; merely ignoring
the file does not make those credentials safe.
