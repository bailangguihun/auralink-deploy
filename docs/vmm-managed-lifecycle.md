# Optional managed VMM role

Ordinary website OPEN uses the separately approved operation identity described in
[OPEN operations](vmm-open-operations.md). V2 single-request ACCEPTANCE remains a
distinct contract; neither approval substitutes for the other.

This extension is a reviewed, incremental R25 controller change. It does not replace the installation with a V1 controller or start another supervisor. VMM remains disabled after binding. Existing Java, Guide/Critic, c5ea Next and PostgreSQL identities remain authoritative. See [controller transaction](vmm-controller-update.md) for approval-bound prepare/bind/recovery commands.

## Bound implementation

Use the clean legacy installation `b115f0724e16f16128563dc996c5a10082783074`, `VMM/app.py`, its `duration_contract.py`, and the existing Python 3.9.21 environment. Do not use the AI repository's old fixed-30-second service. The managed bootstrap reuses the legacy WSGI generation function and the controlled Round8.1 environment: loopback 5001, offline HF flags, fixed MusicGen/T5/checkpoint/CLIP references, native library path, and original output root. No model module is imported during controller preparation, status, or tests. The original launcher is evidence for the environment and startup budget, not a second manager to run alongside R25.

Runtime policy separates **process alive**, **model ready**, and **dispatch permitted**. A loopback listener must belong to the recorded PID, boot ID, start ticks, executable, argv, uid, cwd and process group. A foreign listener or reused PID is a refusal, never an adoption or kill. A separate inherited process-lifetime lease prevents a second child after a crash between spawn and PID-record persistence. This is an ownership guard, not a supervisor. The original shared deployment lock covers short control/state operations only; it is not inherited by VMM and is not held through initialization or music generation.

Stop pins the target with a Linux pidfd before its final identity check and signal, with no numeric-PID signal fallback. The installed R25 Python 3.10 exports `signal.pidfd_send_signal` but omits `os.pidfd_open`; glibc 2.35 also omits the named wrapper. A small stdlib ctypes adapter uses only the verified Linux x86_64 `pidfd_open` syscall ABI (434). Other unsupported platforms or syscall errors fail closed; no runtime upgrade is implied. CPU tests exercise both Python and direct-syscall paths, including ENOSYS. Production kernel/syscall permission remains a later preflight gate. Stop proves exit from boot/start ticks/stat, because exe/cwd may vanish earlier, and waits for only the previously owned socket inodes within the same stop budget. A new listener inode is an immediate refusal.

## Modes and controls

After an independently reviewed role configuration binding, the existing bound `operations/lifecycle_adapter.py` gains:

```text
vmm-enable      allow this optional role, without starting a model
vmm-start       explicit process start; admission remains CLOSED
vmm-status      process/model/dispatch status and durable counts
vmm-close       close admission; retain running/in-flight work and ledger
vmm-stop        intentional stop; close admission, refuse interruption of in-flight work
vmm-disable     close and disable; no watchdog restart
vmm-recover     bounded process recovery, never request replay
vmm-acceptance --vmm-command-sha256 <reviewed digest>
vmm-open       --vmm-command-sha256 <reviewed digest>
vmm-reconcile  --vmm-command-sha256 <reviewed digest>
```

Invoke only through the installed, digest-bound lifecycle with `R25_ROOT` and `ARTLIVE_R25_HANDOFF_CONFIG` set to the existing installation. The public/private application endpoint does not expose these operator commands. A disabled minimal binding requires no token, model assets, output directory or listener.

`runtime/config/vmm-gate-command.json` is mode 0600. Approval is external; the CLI does not approve its own digest. Close admission first, prepare the record, obtain explicit approval, then invoke the exact digest. Commands are consumed durably before their action. The service lock performs a second compare-and-swap of the accounting record. Interrupted commands are not automatically repeated.

### One-request acceptance window identity (V2)

New `vmm-acceptance` commands require exactly:

```json
{
  "schema": "R25_VMM_GATE_COMMAND_V2",
  "mode": "acceptance",
  "role_sha256": "<current bound role SHA-256>",
  "before_gate_sha256": "<exact unused CLOSED gate bytes SHA-256>",
  "permit": {
    "owner_id": "<authorized existing account>",
    "source_painting_id": "<authorized existing catalog painting>",
    "image_sha256": "<authorized image bytes SHA-256>",
    "duration": 3
  },
  "window": {
    "startup_plan_sha256": "<actual separately approved startup plan SHA-256>",
    "first_ready_sha256": "<actual FIRST_READY.json SHA-256>"
  }
}
```

The two window fields are validated identities, not a nonce. They are included before the acceptance-plan digest is produced, avoiding a plan/command hash cycle. The controller derives the directory `runtime/vmm-startup-windows/<startup_plan_sha256>` under its installed root; the caller cannot supply a path. It requires a private 0700 directory and private 0600 regular single-link records, with no symlink path components. It hashes the actual `STARTUP_PLAN.json` and `FIRST_READY.json` and verifies the six READY evidence records referenced by `SINGLE_REQUEST_ACCEPTANCE_UNSIGNED_PLAN.json`. That acceptance plan must bind the exact command digest, current role, fixed one-request/no-retry/3-second limits, and original process/deadline fields. The exact four-field `ACCEPTANCE_APPROVAL.json` must approve that plan digest and its one-workflow/one-creation/one-music scope. Printed templates and startup approval alone do not satisfy it.

The startup plan must declare `acceptance_gate_schema=R25_VMM_GATE_COMMAND_V2`, the correct installed root, and unchanged 900-second startup / 600-second READY / one-initialization / zero-generation startup limits. The live execution record must remain `OBSERVING`, not completed, with exactly one enable/start and zero generation calls. Original startup/READY monotonic and wall deadlines must remain valid. Recorded boot/PID/start ticks/uid/exe/cwd/argv/process-group/session identity must equal the actual owned process; authenticated current readiness must still be READY and CLOSED with no dispatch, in-flight or uncertain request. No status check loads a model or performs generation. Current readiness is bounded and both original deadlines are checked again after observation and after durable lifecycle consumption. The original startup intent must precede the lifecycle's actual process-start time, which must precede FIRST_READY. The controller passes the same validated absolute deadlines as internal arguments to GateStore.configure; the service rechecks them inside its independent gate lock immediately before recording ACCEPTANCE. Lock contention cannot turn an expired approval into admission. This does not add a public request field or change generation behavior. The acceptance runner also rechecks the original deadline after control returns and before any business POST. A control delay is not authority to submit late, reset the clock or retry.

Before configuring ACCEPTANCE, lifecycle accounting atomically persists both the existing `gate_commands` digest and `gate_windows[startup_plan_sha256] = {command_sha256, first_ready_sha256}` in the same fsynced record. The exact old consumed command remains refused. A changed command, permit, READY file or newly written approval cannot rearm an already consumed startup window. Failure after consumption but before gate configuration remains consumed and requires investigation, not automatic retry. Existing request accounting, UNKNOWN results and reconciliation semantics are unchanged; no ledger is cleared or refunded.

Unconsumed V1 acceptance commands are now refused: their static account/source/image/duration tuple cannot prove a distinct independently approved window. Unknown command/window/permit fields are refused, so adding ignored salt cannot create authority. V1 `open` and `reconcile` retain their separate reviewed contract, with exact command keys (OPEN permits only an optional null `permit`; reconciliation requires `request_id` and `reason`). This patch does not authorize OPEN.

This is a reviewed controller protocol change, not a local-tool-only workaround. The production lifecycle and service modules and their digest bindings must be updated through an independently reviewed compatible controller update before a V2 producer can run. Existing overlay revision approval must not be rewritten or replayed to install new bytes. Package provenance and a concrete supported incremental update/recovery plan remain release prerequisites. A producer must fail preflight against an installed consumer without this declared protocol, before any initialization.

For an already committed managed Core successor, the bounded [module revision contract](vmm-module-revision.md) provides `prepare-modules`, `preflight-modules`, `bind-modules` and `recover-modules`. It preserves the original overlay, role and successor approvals, updates only five controlled files, and never restores lifecycle/request ledgers. Installation remains disabled/stopped/CLOSED and requires a separately approved concrete plan.

For reconcile, specify the existing `request_id` and a factual `reason`; it acknowledges uncertainty but never deletes its request, refunds its count or permits that request ID to execute again. UNKNOWN remains visible. A new request always requires separate applicable authorization.

## Admission and in-flight semantics

The private service exposes only non-generating `/health`, token-protected `GET /internal/vmm/status`, and token-protected `POST /api/generate_with_image`. Other legacy routes are not forwarded. Core remains the user/ownership authority and supplies database-derived creation/step/retry/owner/source IDs; no browser token or arbitrary paths/URLs are accepted. Compatible Core changes are in the separate managed-dispatch PR.

Default CLOSED rejects all generation, including legacy callers. ACCEPTANCE binds one trusted account, one gallery source, exact image bytes and integer duration. The first qualifying UUID is durably reserved before the legacy function is called; the permit immediately closes. Concurrency is one. Duplicate UUIDs never execute again. Process death after reservation becomes UNKNOWN on recovery, which closes dispatch. A lost response is not evidence that generation did not occur.

An intentional stop persists `desired=STOPPED` before waiting/refusing in-flight work; watchdog will not restart it. Repeat stop only after the same recorded execution finishes, or use explicitly reviewed uncertainty handling. There is no forced kill/retry mechanism. Process startup readiness uses a finite monotonic budget (maximum 900s, inherited from the controlled launcher's 450×2s envelope); restart allowance is finite (0–3), durable and configurable. These are control limits, not measured startup performance. Explicit start resets the **process-start** budget only, never generation accounting.

The existing `stop-services` and Java/watchdog backup-pause contract remain unchanged; they are not an implicit VMM stop/drain API. Before future maintenance while VMM is enabled, explicitly close admission, inspect/drain the recorded in-flight work, and use `vmm-stop` only when a VMM stop is intended. Reopening requires the reviewed gate command. A Core interruption after dispatch can leave an UNKNOWN outcome even if VMM continues; never infer safe retry from a completed backup or restart. Automatic maintenance coordination would need a separately reviewed save/restore contract for VMM desired state. This PR does not claim that ongoing generation is automatically drained by every legacy maintenance entry point.

## Source, assets and output protection

Service configuration binds exact source hashes, interpreter hash, canonical model references and all eight existing asset files. CLIP's actual default cache is `/root/.cache/clip/ViT-B-32.pt`; the source-table checksum is enforced before importing the model, because HF offline flags alone do not stop CLIP downloads. Missing assets, mismatched hashes, writable source/asset trust chains, unexpected references or endpoint fail closed. The currently observed shared 0777 directories / 0666 weights do not meet this trust contract. A future approved private-copy or precise permission plan and full local hashes are required; this PR does not chmod/chown anything.

The existing output root remains `/root/autodl-tmp/auralink/backend/temp_uploads`. Only safe `vmm_generated_<digits>.wav` leaves are returned. The service checks actual PCM WAV duration and hashes; Core retains its existing stricter media staging, ownership and ±0.05s duration validation. No `full_path` or unrestricted download route reaches the browser. Original image+duration JSON, 3–30 seconds/default10, generation timeouts and retry policy are unchanged.

## Release gates and recovery

Feature CI and package-only workflow do not install or activate anything. The old 25-file controller archive remains its exact inventory; an additional four-file VMM extension archive has its own manifest and exact release/run/attempt provenance. There are no formal hashes until the reviewed release workflow runs.

Controller metadata binding uses exact before/after hashes, the existing lock, saved preimages and journals. It leaves the old policy and accepted/current application records unchanged. A tightly bounded effective-controller overlay verifies the new lifecycle/guard bytes without rewriting historical evidence. Role/service configuration has a separate approved, disabled binding transaction. Recovery restores only this transaction's metadata after drift checks; it never restores the database, deletes media or replays tasks. Stop/restore watchdog through its original wrapper outside the lock-held copying section. A partial transaction must use its recorded recovery entry, not a second installation.

Before later activation: obtain full asset/dependency identities and resource evidence; prepare a new reviewed Core functional-upgrade binding and private token/acceptance tuple; inspect queued/running music tasks and unresolved dispatches; keep service CLOSED during initialization. The currently installed Gallery Core-only candidate binding does not authorize this new Core JAR. Do not replay the earlier successor/first-install procedures. Public OPEN mode and GPU startup require later approval.

## Validation boundary

CPU fixtures exercise actual child processes, loopback HTTP, filesystem/flock/CAS/fsync, synthetic WAV, request accounting and failure recovery. They contain no production credential, model import, weights or GPU work, and are not selectable by production CLI/config. GPU memory, startup duration, model quality and real playback remain unverified. Durable initial-creation idempotency across refresh/tabs, catalog eligibility and snapshot consistency remain separate follow-ups.
