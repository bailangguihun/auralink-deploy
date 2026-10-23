# Reviewed catalog projection data repair

The external `r25/catalog_projection_transaction.py` consumes a freshly approved
private plan. It adds no installed module, supervisor, provider call or VMM
lifecycle action. It depends on the reviewed Core bulk renderer and its exact
manifest/SQL. Production data and credentials never belong in Git.

The plan binds the installed root, current accepted Core release, unchanged
policy/config, original deployment lock device/inode, disabled/STOPPED/CLOSED
gate/lifecycle, exact tool and imported source bytes, PostgreSQL binary identity,
media proofs and protected VMM files. Sources are root-owned mode 0600 regular
single-link files without symlink components; explicit Python invocation executes
those exact compiled bytes. Existing installed controls retain their own modes.

Prepare the complete affected-record manifest from a current canonical/JPA
snapshot and verified actual files. All public tables have row-count/SHA-256
preimages. Only entirely missing compatible triples are eligible. Conflicts and
partial rows are reported, never overwritten. The Core coverage gate must pass
after repair before a future import can be called Creation-ready.

Invocation (the private handoff supplies concrete paths and digest):

```text
python3 -B catalog_projection_transaction.py check --plan PLAN.json --sha256 PLAN_SHA256
python3 -B catalog_projection_transaction.py apply --plan PLAN.json --sha256 PLAN_SHA256 --approval APPROVAL.txt
python3 -B catalog_projection_transaction.py recover --plan PLAN.json --sha256 PLAN_SHA256
```

`check` is read-only for the application/database and writes only private check
diagnostics. `apply` requires the complete exact confirmation produced by
`approval_text`; the operator may record a real approval, never fabricate one.
The handoff must include the actual unsigned plan digest before seeking it.
An old startup, OPEN, installation or business approval grants no data repair.

The original shared lease spans preflight, durable consumption, owned Java and
watchdog stop, the installed database/private-attachment consistency backup,
one SQL submission, read-only reconciliation and unchanged-artifact resume.
Guide/Critic/Web versions and files remain unchanged; the maintenance window
briefly removes Core availability. Do not begin if work or UNKNOWN state is
active. Every SQL call retains timing, exit and redacted stdout/stderr privately.
No automatic second write is permitted, even after a client exception.

Recovery boundaries:

- Before consumption: a static refusal makes no service or data change.
- After consumption, before SQL: restore the unchanged Java/watchdog baseline
  after verifying the exact preimage; the approval remains consumed.
- SQL error with exact preimage: all new rows rolled back; resume unchanged
  services. Sequence gaps are allowed and never reset.
- Lost ACK/connection with exact full postimage and unchanged historical rows:
  record COMMITTED_VERIFIED and resume; do not submit SQL again.
- Original PostgreSQL write backend still present, mixed rows, changed history,
  unreadable evidence, or failed identity checks: UNKNOWN. Keep writers in the
  documented maintenance state and investigate using `recover` under the same
  lock. Never infer rollback from client termination or restore the old database.
- Resume/health failure: RECOVERY_REQUIRED, without another automatic resume
  attempt. Explicit recovery only uses the unchanged accepted artifact.

The backup is evidence and a disaster-recovery resource, not an automatic undo
mechanism. After commit, users may reference the new rows; any later removal
requires a new dependency-aware reviewed plan. This approval authorizes neither
VMM initialization/OPEN nor website submission or generation.

Release sequence: Core bulk tooling PR and Deploy transaction PR to `main`,
responsible-owner review and required CI, then each repository's normal
`main` → `release` promotion. No application deployment or model packaging is
needed. Stage only reviewed external tools as new private files, verify the
concrete production plan again, then obtain its fresh explicit data-repair
approval. The exact production manifest/plan stays in the private handoff.
