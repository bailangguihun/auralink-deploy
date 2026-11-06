# Knowledge publication after the committed repair and catalog visibility change

This finite binding admits one new Core release after the installed `R25_MANAGED_VMM_CORE_PUBLICATION_REPAIR_V1` and committed catalog visibility transaction. It is not a general release-policy editor. The original repair, failed Core attempt, visibility SQL/retention proof, accepted records, Community registration and lifetime restart ledger remain immutable. A repeated knowledge transition is refused.

## Scope and release inputs

Use reviewed main → release promotions and formal release artifacts. The controller, VMM extension and Community controller packages must share one Deploy repository/ref/commit/run/attempt; the three archive and manifest hashes are verified independently. The existing inventories remain 25 controller files, four VMM files and seven Community files. PR artifacts cannot substitute for release artifacts.

The new Core must have a different commit and JAR digest. Preparation compares the actual previous and candidate JARs: all bundled libraries, application YAML resources and migrations must be byte-identical. Installed PostgreSQL migration manifest/migrator, application configuration, AI artifact and provenance, VMM service/lifecycle/role/token and catalog retention remain bound. Web search configuration is refused; the paired Core release keeps web search disabled. Compatibility evidence must also include the real old/new payload comparison and read-only startup/capability checks.

Community remains running during Java maintenance because its original lifetime restart budget may already be exhausted. The installed binding authorizes a new source-verified Community CLI to use the exact old Host plan and lease with the reviewed new guard. It cannot install/register/enable/rebind that plan. A `preserve` observation verifies the original process identity, socket ownership, readiness, healthy response, bounded private log and durable state. It never starts, stops, resets or rewrites Community. Core publication and failure recovery require the same preserved identity/checkpoint. Explicit full shutdown retains the original drain behavior. If the process changes, loses readiness or reaches its log limit, maintenance refuses; there is no budget refill or automatic replacement.

## Request, review and exact binding

Use the current installed production root/interpreter. Private directories are 0700 and files 0600 with validated owners, single links and no symlink components. Put the request at `runtime/core-publications/publication-requests/<request-sha256>.json` with exactly these fields:

- `schema`: `R25_KNOWLEDGE_CORE_PUBLICATION_REQUEST_V1`.
- `deploy`: `repository,ref,commit,run_id,run_attempt,archive_sha256,manifest_sha256,tool_sha256`; tool is `upgrade_assistant_controller.py`.
- `vmm_deploy`: the same fields, tool is `vmm_controller.py`.
- `community_deploy`: the same release identity and Community archive/manifest hashes, without `tool_sha256`. Stage the archive and manifest in `operations/r25-community-candidates/<release>/` and the verified seven sources plus manifest in `operations/community-controller/<manifest-sha256>/`.
- `core`: formal `commit,run_id,run_attempt`; its three existing candidate files are staged through the existing incoming contract.
- `web_commit,evidence`: retain the predecessor policy values. This field is the retained compatibility binding; separately deploy the approved new Web release only after Core acceptance.
- `compatibility`: the effective committed visibility retention manifest path/hash, not the superseded original retention manifest.
- `compatibility_evidence_sha256,review_evidence_sha256`: hashes of real evidence. Store the review JSON at `publication-reviews/<review-sha256>.json`. Record actual CODEOWNER/CI/release reviews and the existing user deployment authorization; never synthesize approval votes or claim an unreviewed plan digest was approved.

```bash
: "${R:?installed production root}"
: "${PY:?installed trusted interpreter}"
: "${DEPLOY_RELEASE_SHA:?verified formal Deploy release}"
: "${REQUEST_SHA256:?verified private request digest}"
BUNDLE="$R/operations/r25-controller-candidates/$DEPLOY_RELEASE_SHA"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" prepare-knowledge-publication --request-sha256 "$REQUEST_SHA256"
```

Preparation reads under the original shared lease and writes only new request-scoped material and `core-followup-plans/<request-sha256>/UNSIGNED_PLAN.json`. It does not authorize or install the plan. Review the concrete plan against the deployment authorization and verified artifacts, then place its exact bytes in `publication-approvals/<plan-sha256>.json` using the established approval channel. Existing user authorization may cover this deployment; do not invent a new permission requirement. Existing GitHub protected-branch and environment approvals still apply.

```bash
: "${APPROVAL_SHA256:?reviewed exact binding digest}"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" preflight-knowledge-publication --approval-sha256 "$APPROVAL_SHA256"
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" bind-knowledge-publication --approval-sha256 "$APPROVAL_SHA256"
```

Binding archives fixed before-images, including the original Core CURRENT; updates only reviewed controls; CAS-updates the control pointer; records `runtime/vmm-controller/KNOWLEDGE_PUBLICATION_ACTIVE.json`; and observes the live Community through the new verified CLI before restoring watchdog supervision. Old proof files and old Community pointers stay unchanged. Runtime verifies historical visibility through exact retained bytes and independently validates actual Core CURRENT, its accepted record and transaction. A historical view never grants arbitrary new release authority.

## Publication and recovery

After INSTALLED verification, use the existing `core_publish.py stage`, `publish` and `status` with the bound formal Core commit/run/attempt. Keep VMM disabled/STOPPED/CLOSED. Perform authenticated read-only knowledge and Assistant capability acceptance, preserve existing media/history, and verify Core/Guide/Critic/Web/Community health plus original Community PID and budget. Then promote/deploy the approved Web release and check the rendered knowledge UI.

Before application publication begins, an incomplete binding can be reconciled only with:

```bash
"$PY" -B "$BUNDLE/upgrade_assistant_controller.py" recover-knowledge-publication --approval-sha256 "$APPROVAL_SHA256"
```

Recovery restores only this transaction's exact before/after control targets and CAS pointer, removes only its new marker, and preserves all original proofs and Community state. Foreign drift refuses recovery. A restored approval is consumed; a durable INSTALLED receipt is verified and cannot be rolled back by this command. After Core publication begins, reconcile the original Core transaction and use its existing recovery entry point; do not start another publication after an ambiguous result. No database restore, new SQL migration, generation request or VMM admission change is part of this procedure.

## Verification

`test_knowledge_publication.py` exercises the actual full repair → committed Core → visibility → knowledge proof chain, strict file identities, formal package verification, unsigned prepare, install, candidate CURRENT, drift refusal and recovery. Its Java, database, process-supervision and production-host identifiers are explicit synthetic boundaries, not production acceptance. `test_knowledge_community_preserve.py` spends all three attempts on disposable real Node processes and checks the final running process and durable bytes are preserved, including failures and CLI scope. Run both plus the existing full R25 suite before release.
