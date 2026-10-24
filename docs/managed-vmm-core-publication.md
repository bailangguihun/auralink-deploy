# Current successor + V2 modules: finite Core publication binding

This is one finite transition from a committed `R25_MANAGED_VMM_CORE_SUCCESSOR_V1` with an INSTALLED `R25_VMM_MODULE_REVISION_V2` chain to `R25_MANAGED_VMM_CORE_PUBLICATION_V1`. It does not permit another publication from the new schema. Every plan binds the actual predecessor hashes; schema names alone do not authorize replacement. The older successor/module entry points retain their original restrictions.

## Deployment sequence and authority

Core #31, Deploy #37 and Web #40 implement the owned-upload feature. Their main merges and CPU evidence are preparation, not production acceptance. The adapter must also receive normal CODEOWNER review and main→release promotion. Reuse automatic package-only workflows, with complete formal Core and same-run Deploy controller/extension artifact verification. The unchanged fixed inventories are 25 controller files and four extension files. PR CI artifacts are not formal release artifacts.

1. Verify the current stopped installation, Core COMMITTED record, complete overlay/role/Core-successor/module-V2 chain, application identities, no in-flight or unresolved UNKNOWN work, current media/history and completed catalog repair. Keep VMM disabled/STOPPED/CLOSED.
2. Stage only verified private formal artifacts. Prepare and approve the **combined finite control binding**. In one existing shared-lock transaction it installs the reviewed managed VMM service, proof loader and Core publication controls, changes only the role service digest and six handoff digests, and records a new `CORE_PUBLICATION_ACTIVE.json`. The VMM lifecycle module must be byte-identical. Old Core remains compatible with the new service. Java, AI and Web remain running during this binding; only the existing watchdog is coordinated.
3. With that binding verified INSTALLED, use the **original Core publisher** to stage/publish the bound Core artifact. It performs its existing consistent backup, temporary Java/watchdog maintenance, publication and authenticated read-only acceptance. Bind and inspect the actual transaction ID; no alternate launcher or database restore is introduced.
4. Check the new Core release/JAR, authenticated capability response, upload source constraint and stopped managed contract. No Creation/provider POST is needed. Only then, with separately authorized concrete Web deployment scope, promote Web main→release. Web promotion automatically deploys production and must remain pending before this boundary.
5. Rebind a fresh operating tool bundle to the installed identities, including reviewed diagnostic tooling; obtain fresh CLOSED startup and independent human OPEN confirmation. The user performs the production upload test. No sample is automatically generated.

No model weights, AI rebuild, migration or repeated catalog projection repair belongs to this change. Existing 3–30 seconds, concurrency one, independent OPEN authorization, consumption and UNKNOWN handling remain unchanged.

## Immutable inputs and exact plan

Use the installed root and interpreter from the current handoff. These commands intentionally require real released identities; they contain no reusable approval:

```bash
R=/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z
PY=/root/miniconda3/bin/python3
: "${DEPLOY_RELEASE_SHA:?verified reviewed release}"
: "${REQUEST_SHA256:?actual private request digest}"
BUNDLE="$R/operations/r25-controller-candidates/$DEPLOY_RELEASE_SHA"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" prepare-managed-vmm-publication --request-sha256 "$REQUEST_SHA256"
```

Directories are root:root 0700; private files are 0600, regular, single-link and have no symlink components. The request is `runtime/core-publications/publication-requests/<request-sha>.json`, schema `R25_MANAGED_VMM_CORE_PUBLICATION_REQUEST_V1`. It has the same exact fields as the older successor request: `deploy,vmm_deploy,core,web_commit,evidence,compatibility,compatibility_evidence_sha256,review_evidence_sha256`. `deploy` and `vmm_deploy` must be the same release/run/attempt with separately verified archive/manifest/tool digests. `core` names the actual formal commit/run/attempt. Web binding, application configuration, AI provenance and policy settings remain unchanged. `publication-reviews/<review-sha>.json` contains real review evidence, never a synthesized review or startup approval.

Preparation writes only request-scoped private sidecars in `publication-material/<request-sha>/` and `core-followup-plans/<request-sha>/UNSIGNED_PLAN.json`; it does not approve, pause supervision, install or load a model. It reads under the original shared lease. Use the existing bounded ownership-aware coordination procedure if watchdog holds that lease; do not delete/replace locks, kill unknown holders, or leave watchdog paused while waiting for approval.

The new plan includes `publication_request_sha256`; a fresh preparation after recovery gets separate immutable derived material rather than overwriting the previous request. The new plan binds the current Core record and process identities; exact controller/file preimages and postimages; original control pointer; original Core-successor approval/journal; current module marker and all 13 parent proof files; full transitive module/role/overlay proof; role/service/token; current lifecycle/gate bytes and existing process/configuration evidence. No old singleton approval or marker is overwritten. Binding snapshots all fixed control preimages, but writes only changed targets.

After approval of the actual unsigned bytes, save those bytes to `publication-approvals/<plan-sha>.json` through the established approval channel:

```bash
: "${APPROVAL_SHA256:?fresh exact control-binding approval}"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" preflight-managed-vmm-publication --approval-sha256 "$APPROVAL_SHA256"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" bind-managed-vmm-publication --approval-sha256 "$APPROVAL_SHA256"
```

The new pointer is CAS-bound to the old control pointer. Original `CORE_SUCCESSOR_ACTIVE.json`, `MODULE_REVISION_ACTIVE.json`, all historical approvals/journals and application accepted records stay intact. The new loader checks current after-images independently, then verifies the full old consumer proof chain against exact private archived preimages using an explicit read-only argument. There is no global read override and no filesystem restoration to make validation pass. Runtime legitimate ledger evolution and later approved Core/Web publication are not frozen to an installation snapshot; new control/policy/role/service digests remain exact.

## Core publication, recovery and uncertainty

After the combined binding is INSTALLED and separately authorized Core maintenance scope is confirmed:

```bash
: "${CORE_SHA:?bound formal Core release}"
: "${CORE_RUN:?bound formal run}"
: "${CORE_ATTEMPT:?bound formal attempt}"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" stage --commit "$CORE_SHA" --run-id "$CORE_RUN" --run-attempt "$CORE_ATTEMPT"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" publish --commit "$CORE_SHA" --run-id "$CORE_RUN" --run-attempt "$CORE_ATTEMPT"
"$PY" -B "$R/operations/r25-publisher/core_publish.py" status
```

Before application publication starts, recover only this original binding transaction:

```bash
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" recover-managed-vmm-publication --approval-sha256 "$APPROVAL_SHA256"
```

- No journal: NOT_STARTED, no maintenance action.
- PREPARED/INSTALL_INTENT/VERIFY_INTENT/RECOVERY_REQUIRED/RESTORE_INTENT: validate retained artifacts, unchanged parent proof, stopped VMM, unchanged ledgers and the exact original Core/process identities. Restore only fixed control targets whose actual digest equals the bound before/after digest. CAS the control pointer back, remove only this transaction's new marker, verify original controls and restore existing supervision. Third-party drift is refused. Interrupted recovery can re-enter the same transaction.
- RESTORED: verify before-images and report no-op. The consumed plan cannot be installed again; another attempt requires a fresh preparation/approval.
- INSTALLED, including fsync followed by response loss: verify the new binding and current application identity; do not roll it back. Later consumption and UNKNOWN remain untouched. A committed record is not downgraded to RECOVERY_REQUIRED when only its response was lost.
- Once Core publication has started, reconcile the original `core_publish.py status` and transaction journal. Use `recover --transaction <actual-id>` only for that original incomplete publication. Do not start another publish after an ambiguous outcome. An already COMMITTED Core rollback is a separate approved application action, not controller recovery; keep the new compatible wrapper/binding and all current data/ledgers.

No path writes lifecycle/gate, restores database or ledger snapshots, refunds authorizations, opens admission or retries generation. If unchanged evidence or safe recovery cannot be established, retain CLOSED/maintenance and report the exact failure. Verify GPU release, original watchdog identity/survival, Core/Guide/Critic/Web health, read-only authenticated APIs and historical media after maintenance.

## Offline evidence and limits

`test_managed_vmm_publication.py` exercises real successor and V1→V2 module transactions, then real preparation/bind/proof-overlay/recovery with files, manifests, flock, metadata, CAS and fsync. Tests cover unsupported/repeated predecessor, parent/source/metadata drift, original-lock contention, UNKNOWN refusal, changed-file/marker/journal interruptions, interrupted recovery, durable commit response loss, later consumption preservation, and compatible Core/Web state evolution. Java/PG/process observations and formal release identifiers in fixtures are explicitly synthetic. The inherited publisher failure suite remains mandatory. These tests cannot establish production deployment or user upload generation.

FileNotFoundError in the earlier operating session is separate: its original filename/traceback were not retained. Additional phase/errno/path diagnostics improve evidence, not a proven root-cause repair. Do not recreate production proof files or claim the long operating window is fixed based on diagnostics alone.
