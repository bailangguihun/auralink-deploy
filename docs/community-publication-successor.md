# Finite Core publication successor and original Community supervision

Status: implementation for review, NOT_DEPLOYED. A new main review is needed;
Deploy #42 and Web #47 are already merged. This is not production authorization.

## Supported exact predecessor

Read-only original-host evidence on 2026-10-26 verified the complete installed
proof chain, then inventoried the V1 publication's 32 private proof files:
approval, review, INSTALLED journal, original CONTROL_UPGRADE_ACTIVE before-image
and all 28 target before-images. The installed parent is
`4bf9cd78d1e90bda298e0e586c9d729cf777f72535810f16a2ad621107cf600f`,
`R25_MANAGED_VMM_CORE_PUBLICATION_BINDING_V1`. Its raw policy and predecessors
are immutable inputs, not new approvals. Actual Core is `925f11a…`, transaction
`98afb5f4ce206bbb5d1fb0a7a579de4e` COMMITTED; VMM disabled/STOPPED/CLOSED.

The new finite schema is `R25_MANAGED_VMM_CORE_PUBLICATION_SUCCESSOR_BINDING_V1`,
policy followup `R25_MANAGED_VMM_CORE_PUBLICATION_SUCCESSOR_V1`. It accepts only a
committed V1 publication with exact bound identities, terminal application
transactions, no in-flight/UNKNOWN work and the original lease. It does not
turn the old binder into a reusable publisher or accept a successor of itself.

Preparation captures every old proof digest and live preimage. Installation
runs the entire previous V1/module/role proof verifier against the new
transaction's immutable before-images. The V1 verifier's historical argument
is used only by this separately validated outer transition. Its current/live
validation remains unchanged otherwise. Both full same-run release bundles,
module inventories and paired guards remain mandatory.

## Narrow writable set

Only these nine existing control targets may differ:

- Private publisher copies: `core_business.py`, `core_release_guard.py`,
  `core_runtime.py`, `upgrade_assistant_controller.py`, `vmm_controller.py`.
- Operational guard copy, POLICY, handoff, and lifecycle adapter.

All other targets retain exact before=after. In particular VMM lifecycle,
service, role, token, consumption/UNKNOWN, AI, Java launcher, Web launcher and
application settings remain unchanged. The `vmm_controller.py` change verifies
the new historical proof; it adds no Community start to the VMM consumer.

The lifecycle renderer accepts only observed SHA
`0bfd456125b9a3f09b8398aade49615f9db1a1127617ea99ac748613a40be919`,
reproducible from the existing assistant lifecycle fixture plus unchanged VMM
extension. It adds one Community method and edits only watchdog tick, Java stop
and application stop. AST tests preserve every other method. New CoreRuntime
calls the drain hook before original Java/watchdog maintenance. It still uses
the original publisher, original shared lease and process-ownership checks.

The old `CORE_PUBLICATION_ACTIVE.json` and every old proof remain unchanged.
The new transaction uses a fresh approval folder and
`CORE_PUBLICATION_SUCCESSOR_ACTIVE.json`; CONTROL_UPGRADE_ACTIVE uses the
original before-image CAS. The plan binds exact rendered policy/handoff and
only approved candidate identities. This is not a digest-in-place patch.

## Transaction and recovery

CLI entries in the verified formal controller bundle:
`prepare-managed-vmm-publication-successor`,
`preflight-managed-vmm-publication-successor`,
`bind-managed-vmm-publication-successor`,
`recover-managed-vmm-publication-successor`.
Prepare requires a canonical content-addressed
`R25_MANAGED_VMM_CORE_PUBLICATION_SUCCESSOR_REQUEST_V1` request containing real
Core/controller/VMM release identities, preserved Web binding and review/
compatibility evidence. It creates unsigned material, never approval.
Bind requires fresh exact plan bytes through the existing approval channel.
The original capture/publication_already_bound refusal is retained.

Before stopping the watchdog: check ownership, terminal journals, preserved
files/data/media, process identities, exact predecessor/proofs, original lock
and all before-images. Preserve old proofs and copy transaction before-images.
Under the same held lock: CAS the control pointer, bounded original watchdog
pause, replace only changed controls, verify full afterimage and write the new
marker; resume the original watchdog, verify services, then persist INSTALLED.
No Java restart occurs during binding. Actual Core publication is a subsequent
separately bound maintenance action with backup and at most one publish call.

Incomplete install: normal bind refuses; recovery validates only approved
before/after images and restores this unfinished transaction's controls/CAS.
Interruption during recovery resumes that same transaction. No old proof or
business/ledger snapshot is restored. Foreign drift/pointer/UNKNOWN refuses
recovery and preserves evidence. Once INSTALLED is durable, a lost return value
only verifies committed state; it cannot downgrade or refund the transaction.
Later consumption and UNKNOWN records remain intact. A restored transaction
cannot be rebound with its consumed approval.

## Persistent availability boundary

Community is registered in the **existing detached R25 watchdog**. There is no
new daemon or OS service. Root-private WATCHDOG.json pins plan, controller
manifest, CLI and supported system Python identities. The full seven-file
Community controller bundle is verified before imports. The watchdog passes
its held original lease FD; the child invokes the original installed guard's
real verifier, never another lock. Node inherits no lease or unrelated env.

The approved persistent host plan has `restart_budget: 2`: one initial start
plus two total automatic restarts. Attempts are persisted before launch and
never reset by terminal/SSH loss, `register`, `enable`, watcher replacement or
reboot. A durable decision with no matching process receipt stays UNKNOWN;
known receipt loss reconciles exactly one run. No arbitrary PID adoption.
Continuous unhealthiness for 60 seconds causes one graceful drain. No SIGKILL,
no repeated ambiguous signal; a new attempt requires verified exit and no
listener. Budget exhaustion disables start. Logs >=8 MiB also disable after
drain; old operation logs/records remain. These are bounded recovery policies,
not unlimited availability guarantees or hard resource quotas.

Terminal/CLI exit leaves Node and the original watchdog alive. Original
watchdog failure removes automatic observation until its documented recovery.
Host reboot requires the existing R25 baseline/watchdog recovery; this does not
add OS autostart or increase that recovery budget. Only a recorded prior-boot
process plus no listener can be reconciled to exit. START_INTENT without owned
identity and foreign listeners remain blocked.

`disable`/maintenance drain persists desired STOPPED before signalling. Pending
or unknown drain prevents Java stop. Later explicit `enable` needs a drained
state and unused original budget. Community failure is logged separately and
cannot cause Core/AI restart. HTTP traffic stays independent of VMM/GPU and the
global deployment lease.

## Remaining coordinated publication dependencies

1. New adapter review and normal Core/Deploy main→release promotions. Keep
   Core repo/environment `R25_DEPLOY_ENABLED` absent/false; Community packaging
   is separate from the conditional JAR publisher. Keep Web release pending.
2. Reuse actual automatic Core JAR and Community service artifacts; pair Deploy
   controller/VMM/Community from their actual release run/attempt. Verify all
   outer/inner manifests, full inventories and guards; no duplicate dispatch.
3. Bind actual current preimages, new private Node22/24 >=22.13 executable,
   system Python, explicit community-only SQL/minimal grants, private config,
   consistent backups/restore rehearsal, maintenance and recovery into plans.
   The final concrete hashes cannot exist before those released bytes exist.
4. Approved finite binding → compatible Core through original publisher →
   Community schema/install/register/readiness → read-only backend verification
   → private server-only Web origin → authorized Web promotion/environment run.
5. Business acceptance has its own scope. No rooms, voice, coins, uploads/shared
   media, AI, VMM startup or paid generation is part of this publication adapter.

Tests use actual files, original guard/flock, inventories, control transactions,
Node and pidfd. Java/PostgreSQL process boundaries in the successor fixtures are
explicitly synthetic; production read-only facts are separate. Reuse Core
real-JAR 40260513884, Core CI40261594915, Deploy603/0skip40267834395 and Web
168-unit/15-shell40267865584 where unchanged. Maven's100 gated skips are not
passes; failed40260113674 RESULT is invalid. New tests are preparation, never
production installation or acceptance.
