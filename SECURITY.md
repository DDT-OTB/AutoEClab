# Authentication and deployment boundaries

All data APIs and SSE subscriptions require a server-side session. There is no self-registration endpoint. Administrators provision accounts and reset temporary passwords; viewers can inspect state and preview plans without saving experiments or managing accounts.

Experiment rename, duplicate and delete routes also require administrator access, CSRF protection and the observed file version. Paths cannot escape the sequence directory or follow file symlinks. Existing JSON and Markdown are backed up before modification, with rollback attempted on write failure. A running controller's active experiment cannot be overwritten, renamed or deleted. Backups remain local and are excluded from Git.

Passwords are stored using scrypt with a random salt. Session identifiers are stored only as SHA-256 digests. Temporary credentials expire after 24 hours and require a password change before data access. Password changes and resets revoke previous sessions. Passwords must exceed eight characters and include lowercase letters and digits.

State-changing requests require a CSRF token, the request-protection header and same-origin validation. The account list is restricted to administrators and contains no password hashes or session tokens. Login failures are limited per account and verified source address.

The Python process binds to loopback. Campus access uses an HTTPS reverse proxy, an exact origin allowlist, replaced forwarding headers and a separate `Secure; HttpOnly; SameSite=Strict` cookie. Only a local proxy is trusted. Local SSH access uses a separate loopback cookie.

The browser cannot start, stop, initialize, move or issue arbitrary commands to hardware. Read-only instrument status queries require an explicit administrator request and a device-specific allowlist. CV actions remain parameter-planning only.

## Source privacy

Real user databases, credentials, certificates, local configuration, internal server addresses and experiment data are excluded from the repository. Public deployment templates use documentation addresses. Fixture flow rates, volumes, ports and serial identifiers are artificial.

Private configuration belongs in ignored `config/server.json` and `deploy/local/`. Runtime databases, experiment files and logs belong in ignored `data/` and `logs/`. Generate certificates on the deployment host; distribute public trust material separately from Git.

Run `python3 scripts/check_repository.py` before publication. If a real secret is accidentally published, revoke it and review Git history; deleting it in a later commit does not remove previous copies.

## Account administration

Use the administrator account-management screen after initial setup. The command line is also available for initial provisioning and recovery:

```bash
PYTHONPATH=backend python3 -m pump_console.auth add-user --username admin --role admin
PYTHONPATH=backend python3 -m pump_console.auth reset-password --username admin
```

Commands read passwords interactively without echoing them. Do not place real credentials in source files, URLs, command arguments or issue descriptions.

## References

- [OWASP Password Storage](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)
- [OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
