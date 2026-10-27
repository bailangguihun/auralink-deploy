# Finite Huaniao visibility successor

This is a control-only successor for the restored 12 batches / 486 UUIDs in the
original Huaniao import. It is not an import rollback or a Core publication.
No application, model, schema, Community registration or VMM consumer changes.
Normal review and release are required before production installation.

## Observed contract and supported predecessor

The original withdrawal removed exactly 12 rows from
`catalog_batch_current_revisions`. All other retained data survived and public
Gallery/detail reads reflected withdrawal. The installed Core guard nevertheless
refused `business_retained_data_drift`: its immutable 11-table retention manifest
includes the current-selection table. The old attempt was precisely restored;
its approval and execution/recovery evidence must never be reused.

This successor admits only the actual COMMITTED Core `96babcf2bf34e135c889d8f3a76a9bac98d908e5`,
INSTALLED repair `07c09799684c1f18683dbe9d326bc4e5738c9b3091c9aa946e626109b17a9d7c`,
POLICY `d1af33be9dd10650998d52c6f7f6a43a1cbb158ccdef8bce398d95598ac49679`,
original retention `cb78ec66b9656548a9b8554cb3dce9b8d653bb3b8982ea7066e6cbdc745511ec`,
and target manifest `a393ff317c50b814acbf0c684fb0f74674840df491babed5a3a6707a90fea5b1`.
The manifest binds exact UUIDs, source/media identities, batch/revision and
selected_at values and original import receipts. Counts or titles cannot select
a replacement dataset. A different predecessor requires separate review.

## Two mutable controls, immutable predecessor

Only these existing files change:

1. `operations/r25-publisher/vmm_controller.py`, from the verified new formal
   extension bundle. This is the control-proof adapter, not the VMM consumer.
2. `runtime/handoff-control.env`: replace only `R25_VMM_CONTROLLER_SHA256`.

The original Core guard, core_business, POLICY, retention manifest, accepted
records, lifecycle adapter, VMM modules/role/token, all predecessor pointers and
proofs, Community host plan/registration/controller/lease binding remain intact.
This matters: changing the operational guard would also break Community's bound
original lease verifier.

A new private `CATALOG_VISIBILITY_ACTIVE.json` points to a content-addressed
approval and append-only phase history under `runtime/vmm-controller/catalog-visibility/`.
All input/before/after files are private, single-link and non-symlink. The formal
controller and VMM extension must share release/run/attempt and paired guard;
complete archive/member/extracted-file inventories are checked. Unchanged
VMM lifecycle/service and guard bytes must equal the installed bytes.

The adapter checks the live two-file afterimage, then verifies the entire old
repair/module/role chain using only the bound before-images of those two files.
Every other live file and old proof remains subject to the old verifier.
The effective policy uses a **new** content-addressed retention manifest only
in the delisted phase. That manifest is derived from the immutable old one by
changing exactly the current-selection aggregate from 13 to 1, with the exact
before/after digest. All ten other tables, metadata and media entries are equal.
The original `core_business.verify_compatibility`, guard, payload, configuration,
media and asset checks remain active. Neither old manifest nor POLICY is edited.

## Preparation and production command boundary

Use the released `operations/r25-vmm-candidates/<release>/vmm_controller.py`
with explicit verified Python and `-I -B`; never execute a different unbound copy.
These commands are supported after staging the complete paired formal bundles:

```text
prepare-visibility --request-sha256 <request>
preflight-visibility --approval-sha256 <unsigned-plan>
apply-visibility --approval-sha256 <approved-plan>
recover-visibility --approval-sha256 <same-unfinished-plan>
restore-visibility --approval-sha256 <same-committed-plan>
```

The request is root:root 0600 at
`runtime/vmm-controller/catalog-visibility-inputs/<request>/request.json`, with
`manifest.json`, fresh `preimages.json` (complete public table fingerprints),
and `review.json`. It binds the two formal bundles, review digest, and actual
private host identities: original production-target configuration, psql,
credential-file identity (never credential content in reports), original Python
and original shared lock device/inode/owner/mode. No alternative DB/credential,
SQL text, callback or executable can be selected on the production CLI.

Prepare creates `UNSIGNED_PLAN.json`, both SQL files and durable exact selection
and control before-images. It does not create approval. The existing explicit
approval channel supplies the exact canonical approved plan at
`catalog-visibility-approvals/<plan>.json`. Preflight needs no SQL write. Record
the actual final unsigned SHA only after released bytes and fresh live preimages
exist; a fixture package or source commit is not a formal artifact identity.
No new Core packaging, application deployment, Web promotion or model operation.

Before execution verify stopped/disabled/CLOSED VMM and no work, current Core and
application identities, original guard/lock and healthy owned services, complete
manifest/SQL derivation, exact preimages and original media checks. Archive fresh
preimages and all 12 selected_at rows durably. Ordinary forward user data must not
be restored to an old snapshot. The SQL refuses drift and updates only the 12
exact selection rows in one repeatable-read transaction, with original per-batch
advisory identities, table lock, exact UUID and source-count checks, exact row
count and unchanged other-table checks.

## Ordering, maintenance and uncertainty

Hold the original shared deployment lease and call its real verifier. Pause only
the strictly owned original watchdog, using the original lifecycle CLI and its
fixed inherited FD 42. No Core, AI, Web, Community or PostgreSQL stop/redeployment.
Community restart registration/budget stays one initial start plus two total
restarts. Do not wait for human review with supervision paused.

Install the two controls with exact CAS and retain both before-images. Verify the
original guard against unchanged data. Persist SQL_INTENT and an exclusive write
intent, then submit the bound delist SQL once. Read the actual postimage before
DELIST_VERIFY_INTENT. Call the original full `require_backend_eligible`, then
start/status the original watchdog; only then record COMMITTED. Verify sustained
supervision independently after releasing the lease. The watchdog is the same
installed implementation, not another supervisor.

An exception is not proof that a SQL child exited. Preserve private stdout,
stderr, exit/unknown records; reconcile current heads, retained catalogue and
matching pg_stat_activity before any action. Partial/foreign states refuse.
No automatic SQL retries. A lost committed receipt completes verification and
supervision without replay. An unresolved SQL_INTENT whose exact preimage is
confirmed and whose child is absent retires the approval as RESTORED, without
submitting it again. A known pre-SQL interrupted two-control install can continue
only its own transaction. Foreign pointers/bytes refuse before further writes.

Run `recover-visibility` promptly on the same unfinished transaction after an
interruption; do not invoke apply again or start services against mixed controls.
Keep VMM stopped, retain supervision/maintenance evidence and investigate if
reconciliation cannot prove either exact state. There is no historical DB,
ledger, consumption or budget rollback.

If this change must be undone, `restore-visibility` only reinserts the 12 exact
saved `(batch_id, revision, selected_at)` rows, preserving forward user records.
It verifies the original guard, restores the two exact control before-images,
then starts/verifies the original watchdog. New transaction/consumption records
and the terminal marker remain as evidence. Restoration itself is resumable;
unknown restore results are read back, never blindly repeated. A terminal
RESTORED approval cannot delist again.

## Verification and release gate

Local checks include finite derivation/transaction/lease/receipt tests. The
private replay scripts additionally execute actual captured 429-file predecessor
proof bytes, accepted Core record and original guard. Native PostgreSQL uses the
exact 13 selection rows and 486 UUIDs, applies SQL, checks the actual guard data
predicate, forks/runs/stops the original watchdog implementation, and restores
the exact controls/selections. Repeat for lost SQL receipt and interrupted
restoration; preserve a new forward-history record throughout.

CPU fixture non-selection tables, media I/O and external service health are
explicit fixture boundaries. Separately, the original host's complete guard is
run read-only in an isolated process with the proposed control/postimage view:
real JAR/payload, media, assets, accepted records and full proof chain must pass.
The isolated in-memory candidate is never installed and has no production SQL,
approval or READY record. This evidence is not a production withdrawal.

After actual approved production execution, check all 486 absent from Gallery,
search/recommendations, daily artwork, Studio/Assistant painting selectors,
applicable statistics and direct detail reads; compare unaffected UUIDs. Check
favorites and Community references become unavailable while original rows,
content and successful generated results remain. Check old-tab stale results,
fresh entry and cache/re-entry. Use authenticated read-only browser operations;
no workflow/creation/generation. Record PASS/FAIL/NOT_RUN and actual final
watchdog/app/VMM/GPU state. SQL success alone never marks the task complete.

The historical watchdog exit cause is separate. A read-only observation on this
repair turn found the previously restarted PID absent even after the data had
been restored and all eleven retention aggregates matched. Do not claim this
visibility successor diagnoses that exit or repairs it merely by adding tests.
