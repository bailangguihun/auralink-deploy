# Finite repair publication after a restored Core attempt

Status: review preparation only. No production write, SQL, installation or
second Core publication is authorized by this change.

## Observed predecessor and terminal state

Read-only original-host verification on 2026-10-26 confirmed successor
`c0d664b070a2cc5510f98472ab34bd91e9f54564451cc48dd2d952d6d2406c55`
is INSTALLED, while Core transaction `4b88699f583cd859506e6b175479a3b9` is
RESTORED. CURRENT still points to COMMITTED transaction
`98afb5f4ce206bbb5d1fb0a7a579de4e`, Core
`925f11a77093b65274d4dec50736498ccd2ade2f`. The attempted candidate was
`3bb2a548d77e77dd3a034de5ee975b840d4b8d06` with the two-Clock Spring startup
failure; Core #36 fixes that wiring. Its prior publication authorization is
consumed. These facts are time-specific and must be reverified before planning
or execution. No old binder, marker, approval, digest or journal is reset.

The new `R25_MANAGED_VMM_CORE_PUBLICATION_REPAIR_BINDING_V1` accepts only the
installed `R25_MANAGED_VMM_CORE_PUBLICATION_SUCCESSOR_BINDING_V1`, an exact
RESTORED transaction bound to that policy's failed candidate, and CURRENT equal
to the transaction's before_current. It is not a successor of itself. A new Core
commit AND different JAR digest are required; the recovery payload remains the
original baseline, never the unsuccessful candidate.

It binds all 32 predecessor control proofs plus the failed Core journal,
snapshot, schema journal, forward-recovery record, backup manifest and HTTP
acceptance record. The journal's accepted-record hash must name the failed
candidate under the original policy. Snapshot identities, verified-backup
receipt, preserved forward schema, no database restore, no automatic replay,
terminal app transactions and current/active pointers are checked. During
binding these pointers must remain exact; later original-publisher publication
may advance CURRENT/ACTIVE while every failed-transaction proof stays immutable.

Read-only candidate capture was also executed in memory against the actual host
using the installed guard's real lease verifier. It accepted this exact chain;
no candidate file replaced an installed module and no unsigned final plan was
fabricated. A busy-lock observation was retained and a bounded read-only lease
wait completed; no watchdog pause or alternate lock was used.

## Minimal permitted controls and recovery

Only seven existing targets may change: publisher core_business.py,
core_release_guard.py, upgrade_assistant_controller.py and vmm_controller.py;
operational guard; POLICY; handoff. Every other target in the complete 28-file
inventory is exact before=after. In particular core_runtime, lifecycle adapter,
VMM lifecycle/service/role, tokens, settings, AI/Web, historical proofs,
consumption/UNKNOWN, business/media/accounting are unchanged.

The repair verifier invokes the entire successor and earlier module proof
chain against immutable before-images. Its historical mapping is private to
this validated outer transition; ordinary live checks remain in force. Both
complete same-run controller and VMM inventories and paired guards are required.
A new CORE_PUBLICATION_REPAIR_ACTIVE marker and approval directory retain the
new transaction. All earlier markers and evidence remain unchanged. The
original CONTROL_UPGRADE_ACTIVE CAS and lease are retained.

Commands in the future verified release bundle:

- `prepare-managed-vmm-publication-repair --request-sha256 <fresh request>`
- `preflight-managed-vmm-publication-repair --approval-sha256 <fresh plan>`
- `bind-managed-vmm-publication-repair --approval-sha256 <fresh plan>`
- `recover-managed-vmm-publication-repair --approval-sha256 <same unfinished plan>`

Prepare uses `R25_MANAGED_VMM_CORE_PUBLICATION_REPAIR_REQUEST_V1`, the existing
publication request fields and original private approval channels. It produces
unsigned material only. Bind first verifies ownership, exact preimages, stopped
VMM/no work, accepted application records and process identities; only then may
the original watchdog be paused briefly for the atomic control update. Java is
not restarted during binding. Original watchdog and health must resume.

Interrupted installation is not rerun. Existing recovery accepts only approved
before/after controls and its own CAS, restores only that unfinished control
transaction, and preserves the earlier INSTALLED successor and RESTORED Core.
Recovery itself can resume after interruption. Foreign drift/pointers refuse.
A lost INSTALLED receipt is reconciled as committed, including later UNKNOWN
records; it is never downgraded or refunded. A RESTORED repair approval cannot
be rebound. New Core publication remains a separate single-call authorization
through the original publisher, with fresh consistent backup and normal
forward-schema-preserving recovery. Never restore historical DB/ledger state.

## Release and installation dependency order

1. Review Core's full-application gate and this adapter normally. Reuse Core
   #36 and all unchanged Community/Web evidence. Promote same-repository main
   to release with merge commits; no old PR review repetition. Keep Core repo
   and production-environment R25_DEPLOY_ENABLED absent/false. Keep Web pending.
2. Reuse automatic formal workflows. Verify real Core JAR and its full eager
   startup result, outer/inner archive, manifest, file inventory and actual
   release/run/attempt. Community service has its own workflow. Keep Deploy
   controller/VMM/Community packages from the same new run and attempt, verify
   complete inventories and paired guards. Never dispatch duplicate packaging.
3. Fresh stopped-state read capture binds actual successor/RESTORED transaction,
   current files, original lease identity, process identities, full business and
   media preimages, retained proofs and reviewed artifacts. Prepare fresh exact
   unsigned repair binding and subsequent original-Core publication scope.
   New final plan digests cannot exist before reviewed release bytes exist.
4. After exact production approval: finite repair binding INSTALLED, then
   original Core publisher at most once with backup/maintenance/recovery. An
   uncertain result is reconciled read-only before any action. Only compatible
   Core COMMITTED supplies real postimages for Community Host preparation.
5. Community Host now accepts this finite parent as well as the earlier ones,
   and requires a bound COMMITTED Core journal, matching CURRENT and approved
   core_commit. The existing detached watchdog/real guard and budget are
   unchanged: one initial start plus TWO TOTAL automatic restarts, never reset
   by registration, terminal exit or reboot. Reuse native Node/Python facts and
   separate runtime/migrator SQL preparation, revalidating identities.
6. Community schema/install/register/READY and backend verification require
   their own concrete scope; private Web origin and Web promotion/deployment
   remain later separately authorized steps. No business or paid tests here.

## Validation boundary

### Accepted Core policy and installed control policy are different identities

The first production repair binding reached `VERIFY_INTENT`, then the original
accepted-record guard refused `assistant_verified_v2_rollback_record` (exit 65).
Its approved recovery restored that binding's seven controls and CAS; the prior
successor remains INSTALLED, the prior failed Core transaction remains RESTORED,
and Core publication was never called. This is a control-plan defect, not a new
JAR startup failure. Keep the failed approval consumed and all original reports.

For this finite repair only, both `assistant.baseline_policy_sha256` and
`assistant.baseline_payload_fingerprint` must be copied from the exact retained
predecessor's **accepted baseline**, because its candidate Core never committed.
`core_followup.predecessor_policy_sha256` still names the immediate installed
control policy. Those two policy hashes must not be conflated. The renderer,
preflight binding check and standalone guard all enforce this distinction.
The guard verifies the predecessor's content-addressed bytes and recursively
checks the existing finite proof chain before accepting the preserved baseline.
The original `release_record()` identity/hash/schema/repository/policy/payload
checks remain unchanged; no accepted record is rewritten to fit a new policy.

The added CPU regression invokes that actual accepted-record verifier during
installation and lost-receipt reconciliation. Before the fix it fails at the
same post-write boundary with the production error; after the fix it passes.
Negative tests reject baseline policy/fingerprint drift and modified or unbound
accepted records. A separate interrupted-binding test restores its own controls,
prepares a different unsigned plan, and proves the prior RESTORED approval and
proofs remain immutable and cannot be replayed. This is isolated evidence, not
another production installation or Core COMMITTED result.

After review and release, use fresh same-run Deploy artifacts and a fresh plan
bound to the restored live preimages. The already verified Core JAR and its full
startup evidence remain reusable if their inputs have not changed; a new Core
build is not needed for this control-only fix. Never edit the failed plan or
reuse its approval. The original publisher still requires its own fresh scope
following successful INSTALLED verification, including a new consistent backup.

New transaction tests exercise actual files, guard entry, original flock, full
proof chains and bundle inventories, CAS, unsupported/changed predecessors,
drift, partial installation/recovery, lost commit/marker receipts and later
Core commit/UNKNOWN preservation. Java/database process boundaries are explicit
CPU fixtures; this is not a production publication test. Community tests use
actual Node and pidfd plus the retained guard, including the new parent and
unchanged persistent budget. Use system Python with pidfd APIs; the local Conda
Python lacks pidfd_send_signal and its failed host-test run is retained as an
environment failure, not a product pass. No installed VMM consumer behavior is
changed, and the historical watchdog exit investigation remains separate.
