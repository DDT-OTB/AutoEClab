# Authentication and deployment boundaries

All data APIs and SSE subscriptions require a server-side session. There is no self-registration endpoint. Administrators provision accounts and reset temporary passwords; viewers can inspect state and preview plans without saving experiments or managing accounts.

Experiment rename, duplicate and delete routes also require administrator access, CSRF protection and the observed file version. Paths cannot escape the sequence directory or follow file symlinks. Existing JSON and Markdown are backed up before modification, with rollback attempted on write failure. A running controller's active experiment cannot be overwritten, renamed or deleted. Backups remain local and are excluded from Git.

Function-block library creation, editing, renaming and deletion also require administrator authorization, CSRF and request protection. Updates and deletes require the observed template version. Custom templates and overrides of bundled defaults are atomically stored in private `data/function_blocks/` files with backups; they are excluded from Git. Library changes never modify existing experiment instances or immutable execution snapshots, and template validation never opens serial devices. Full experiments still enforce routing, cell capacity and startup preparation independently of reusable templates.

Passwords are stored using scrypt with a random salt. Session identifiers are stored only as SHA-256 digests. Temporary credentials expire after 24 hours and require a password change before data access. Password changes and resets revoke previous sessions. Passwords must exceed eight characters and include lowercase letters and digits.

State-changing requests require a CSRF token, the request-protection header and same-origin validation. The account list is restricted to administrators and contains no password hashes or session tokens. Login failures are limited per account and verified source address.

The Python process binds to loopback. Campus access uses an HTTPS reverse proxy, an exact origin allowlist, replaced forwarding headers and a separate `Secure; HttpOnly; SameSite=Strict` cookie. Only a local proxy is trusted. Local SSH access uses a separate loopback cookie.

Automation preparation, start and stop require administrator authorization enforced by the backend, a valid session, CSRF protection and an exact request schema. Viewers may monitor executions but cannot operate instruments. Public configurations default to disabled hardware execution. CV actions remain parameter-planning only; a sequence containing an unsupported measurement cannot start.

Execution consumes a server-generated immutable snapshot, expires unused preparations and checks template changes. A fixed Python worker reuses the installed controller's preflight, serial lock and stop/close cleanup. Clients cannot supply executable paths, PIDs, hardware settings or serial commands. Repeated starts for the same execution are idempotent. Stop signals use Linux PID handles and exact process identity; a reused PID cannot redirect a stop to another process. A failed stop acknowledgement remains an explicit fault. Raw snapshots and controller output remain in ignored runtime directories.

Every experiment using the syringe requires a successful mechanical homing certificate bound to that exact preparation. Calibration requires administrator access and explicit confirmation of safe discharge. Certificates cannot be borrowed by another experiment and are consumed on start; failed, stopped, expired or superseded calibration cannot authorize a run. Recovery sends a complete manufacturer-defined `ZR` string, never a bare resume `R` or software-zero `z`. Device identity, zero position, valve state, an empty command buffer and readiness must all verify. Calibration never automatically resumes a failed experiment.

Every experiment also requires a successful policy-defined startup drain and administrator confirmation that the cell is empty. Timed drainage alone never authorizes start or claims sensor feedback. Requests select an allowed waste route, acknowledge discharge conditions and carry an idempotency identifier. Confirmation records are bound to the prepared experiment and invalidated by subsequent control activity, expiry or preparation changes. The server validates a 40 mL total-liquid storage budget after every planned step; clients cannot increase capacity or skip preparation through request fields. Safety policy is frozen with the snapshot and checked again at start and by the worker.

Explicit read-only instrument status queries also require administrator access and a device-specific allowlist. During a managed run, refreshing state reads the controller journal instead of competing for serial ports. Background SSE and polling never start operations or extend session expiry. Operator requests and accepted execution IDs are audited.

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
