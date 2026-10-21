# Optional managed VMM role

This extension is a reviewed, incremental R25 controller change. It does not replace the installation with a V1 controller or start another supervisor. VMM remains disabled after binding. Existing Java, Guide/Critic, c5ea Next and PostgreSQL identities remain authoritative. See [controller transaction](vmm-controller-update.md) for approval-bound prepare/bind/recovery commands.

## Bound implementation

Use the clean legacy installation `b115f0724e16f16128563dc996c5a10082783074`, `VMM/app.py`, its `duration_contract.py`, and the existing Python 3.9.21 environment. Do not use the AI repository's old fixed-30-second service. The managed bootstrap reuses the legacy WSGI generation function and the controlled Round8.1 environment: loopback 5001, offline HF flags, fixed MusicGen/T5/checkpoint/CLIP references, native library path, and original output root. No model module is imported during controller preparation, status, or tests. The original launcher is evidence for the environment and startup budget, not a second manager to run alongside R25.

Runtime policy separates **process alive**, **model ready**, and **dispatch permitted**. A loopback listener must belong to the recorded PID, boot ID, start ticks, executable, argv, uid, cwd and process group. A foreign listener or reused PID is a refusal, never an adoption or kill. A separate inherited process-lifetime lease prevents a second child after a crash between spawn and PID-record persistence. This is an ownership guard, not a supervisor. The original shared deployment lock covers short control/state operations only; it is not inherited by VMM and is not held through initialization or music generation.

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

`runtime/config/vmm-gate-command.json` is mode 0600. Its schema is `R25_VMM_GATE_COMMAND_V1`; fields include `mode`, current `role_sha256`, exact `before_gate_sha256`, and an ACCEPTANCE `permit` (`owner_id`, `source_painting_id`, `image_sha256`, `duration`; optional exact creation/step/retry). Approval is external; the CLI does not approve its own digest. Close admission first, prepare the record, obtain explicit approval, then invoke the exact digest. Commands are consumed durably before their action. The service lock performs a second compare-and-swap of the accounting record. Interrupted commands are not automatically repeated.

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
