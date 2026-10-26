# Community host adapter and publication boundary

Status: review candidate, **NOT_DEPLOYED**. This adapter installs only
the separate Community files. It cannot publish the required new Core JAR.
Do not promote Web while that prerequisite remains unresolved.

## Observed predecessor (2026-10-26, original WSL/SSH profile)

- Core `925f11a77093b65274d4dec50736498ccd2ade2f`, Deploy
  `4ba943092ee836e480a825b21480e2dcfd8f7426`, Web
  `50233681fb5629f361e381eac253c1cac7e5a0b5` are installed. These differ from
  the merged Community source heads; main merges are not release artifacts.
- Root is `/root/autodl-tmp/artlive-r25-native-pg16/handoff-production-20260923T062101Z`.
  Current finite publication proof is
  `4bf9cd78d1e90bda298e0e586c9d729cf777f72535810f16a2ad621107cf600f`.
  Its `R25_MANAGED_VMM_CORE_PUBLICATION_BINDING_V1` journal is INSTALLED.
  Historical module revision `f4a26cc7e354f8180680d3dfda562efa5a967d7b36fb1e773f464cbaeaabd40b`
  is retained, not replaced or reapproved.
- Original guard `operations/core_release_guard.py` has SHA-256
  `4dd5a0c31fde71210904d22e5a96c45b10bbc324e9ee03e96e2983f1334e4bb5`,
  owner 1000:1000, mode 0700. Its real `production().verify_lease(fd)` is used.
  The full installed proof chain was also checked with this new host factory,
  read-only. Its self-import is registered under the verified original module
  name; an ambient cached guard is refused. Loading only an alias failed and
  was corrected before submission; the original refusal evidence is retained.
- The one global lease is
  `/root/autodl-tmp/auralink-web-deploy/runtime/frontend-deployment.lock`,
  root:root 0600, observed device 2304/inode 2233008486. Recheck before use.
  Normal R25 watchdog was alive. No watchdog pause or lock replacement was done.
- Existing services/operator run as root:root. The initial adapter retains
  this service UID/GID (0:0), and does **not** claim a new non-root Linux sandbox.
  Database identity is separate and restricted. A non-root host transition
  would require separate path traversal/ownership/privilege review.
- `/root/miniconda3/bin/node` is v20.20.2, SHA-256
  `2eaa3208fe7f63e355bea9b2de2b10e042f58867f798eda72d99928bada72792`.
  It is not a supported Community runtime. Leave Web's executable unchanged.
  Stage a separate verified Node 22/24 executable, minimum 22.13, root:root
  0700; bind its exact source, version and bytes in the final artifact plan.
- Conda Python 3.10.20 lacks `os.pidfd_open`. System canonical
  `/usr/bin/python3.10`, 3.10.12, SHA-256
  `a2f33a6e006989270f4340528eb61f8f97366e00a5d1b602ac8672ea44fc56ae`
  has both `os.pidfd_open` and `signal.pidfd_send_signal`. Bind this interpreter
  for Community only. Existing wrappers retain their original interpreter.
- Proposed loopback endpoint is `http://127.0.0.1:19086`, observed unused.
  Core remains 19080, Web 19083; never bind a public listener.
- `runtime/community`, the dedicated database/roles and Web origin setting
  do not exist yet. They are proposed new targets, not observed installations.
  The host volume had about 99 GiB free; capacity is a preflight condition,
  not a reservation. Bind a 256 MiB V8 old-space limit (not a total RSS claim).

Private original evidence, raw metadata, full proof-chain validation, process
identities, health and query receipts remain in the actual migration HANDOFF.
No credentials or real user records are copied into this repository.

## Installation, recovery, supervision

`community_host.py` requires a canonical exact plan, separate approval bytes,
full official service inventory/provenance, private settings, runtime identity,
current parent proof and preserved preimages. The CLI verifies its full released
controller inventory before importing modules. Install never starts a service.

The only installation targets are a new root-private `runtime/community` tree:
`installations/<plan sha>/release`, `node`, `settings.json`, process and journal
records, and a CAS-created `ACTIVE.json`. Files are 0600, executable 0700,
directories 0700. Symlinks, hardlinks, foreign extra files and drift are refused.
Original Core/AI/VMM modules, policy, consumption and UNKNOWN records remain
byte-identical. The finite first-install contract refuses arbitrary successors.

INSTALL_INTENT is durable before materialization. Interruption requires the
same plan's explicit recover: verify each before/after image, finish incomplete
copies, reconcile a lost pointer/receipt. A foreign pointer is never overwritten.
An orphan before its journal exists requires manual read-only reconciliation.
No deletion of evidence, resetting approval or historical snapshot restoration.

The foreground `supervise` command starts one explicitly identified operation
and observes it under short acquisitions of the original guard's lease. It is
an operator-session runner, **not** registration in the existing watchdog and
not a promise of unattended operation. It never auto-restarts Community. Node
closes every inherited lease FD, and serves HTTP without taking that lock.
Community reads do not consult VMM/GPU state. Install is restricted to a stopped
VMM baseline, as required by the current maintenance scope.

SIGINT/TERM/HUP requests graceful drain. STOP_INTENT signals the verified pidfd
once, then only observes. STOPPING/UNKNOWN is not STOPPED. A killed runner,
lost start identity or reboot must be reconciled from retained boot/PID/ticks/
argv/listener proofs; do not adopt a PID, repeat a signal or start another run.
Core/AI/Web supervision is never paused by this runner. Normal deployment-lock
contention waits for release; it does not replay an application operation.

Logs rotate by operation: each fresh explicit run has a new retained log.
At 8 MiB the observer requests graceful drain; it does not truncate an open FD
or automatically restart to hide the limit. This threshold is not a hard disk
quota. Archive/retention needs operator review; no history is automatically
deleted. Check free disk and bound actual usage before approving a session.

## Explicit database preparation

`community_migration.py` renders three distinct steps, not a startup migration:

1. A transaction creates `artlive_community_migrator` (NOLOGIN object owner) and
   `artlive_community_runtime` (LOGIN, connection limit 8). Both have no superuser,
   CREATEDB, CREATEROLE, INHERIT, replication or BYPASSRLS. A new private random
   runtime secret supplies a SCRAM verifier; never reuse Core credentials.
2. CREATE DATABASE `artlive_community` owned by the migrator. PostgreSQL cannot
   include this in the schema transaction. Lost receipt means inspect owner,
   encoding, roles and schema; never DROP/recreate or blindly retry.
3. One schema transaction verifies the exact released `001_foundation.sql`
   digest, creates `community.*`, removes public database CONNECT/TEMP and
   public schema CREATE in **this new database only**, sets minimum grants and
   an immutable migration receipt. No seeds/prototype accounts or cross-schema
   foreign keys. Runtime gets USAGE/SELECT, INSERT+UPDATE profiles/posts/operations,
   INSERT comments, INSERT+DELETE reactions. It cannot delete operation history,
   mutate the receipt, CREATE schema/tables or TEMP tables.

Read-only inspection confirmed native PostgreSQL 16.15, the retained
`/usr/sbin/runuser -u artlivepg25 -- env -i ...` administrative boundary and
`runtime/postgres/ops.env`. Its existing backup administrator is
`artlive_r25_backup`, authenticated with the original 999:999/0600
`runtime/postgres/backup.pgpass`; it is not a Community runtime credential.
The actual psql is `/root/autodl-tmp/artlive-r25-native-pg16/pgsql-16.15/bin/psql`,
SHA-256 `79f8aee6787fce828a7738d9beaba419599bbbccdc626d23f1a7bb5fbd618ebb`.
The private Unix socket is
`/root/autodl-tmp/artlive-r25-native-pg16/pg-sockets/7694f4069c83c06c`, port 55640.
Both local and loopback HBA rules already require SCRAM for all roles/databases,
so **no HBA change is needed**. The observed `runtime/postgres/data/pg_hba.conf`
SHA-256 is `b061836dd7ca38cc8426c744d7e90dbb9acd83d699ead2101cf99e5abce2b620`.
Only SELECT in BEGIN READ ONLY was used to verify this administrative access.

Use that retained entry for explicitly approved provisioning, with its exact
binary/config/passfile metadata and HBA preimages bound in the final plan.
Do not copy the backup or Core credentials into Community settings. The Core
application account cannot CREATE ROLE/DB and is not the Community migrator.
The renderer does not itself grant administration or execute SQL.
Verify roles/memberships/ACLs, object ownership, complete schema,
receipt and unchanged original database/media before resume. Provisioning and
schema have independent one-submit intent records; ambiguous state must be
read-only reconciled before any further write.

Backup: before maintenance, verify the original publisher's consistent Core
backup and inventory; back up any existing Community schema/records separately.
Validate dump SHA, TOC and restore into an isolated database. The native host
test does this without restoring production. After Community writes exist,
retain forward data/journals and deploy only a compatible service, or disable
the route. No whole-database historical rollback or deletion of uncertain work.

## One bounded Web setting

The actual `c5ea` launcher executes `env -i`; inherited environment variables
are discarded. Its controller and selector hashes are already proof-bound.
Do not edit those files to inject arbitrary environment.

The companion Web adapter reads only root:root 0600 single-link
`/root/autodl-tmp/auralink-web-deploy/runtime/community-origin.json` on the server.
Canonical contents are exactly one key, `COMMUNITY_INTERNAL_ORIGIN`, targeting
the approved IPv4 loopback port. No database URL, token or NEXT_PUBLIC setting.
The server validates path components/metadata on each read; no persistent cache.
Missing means unavailable; malformed/private-identity failure is a scoped 503.
The existing explicit server env is retained for isolated/dev environments.

`bind-web` requires actual owned Community READY and the unchanged approved
Core/maintenance preconditions. It creates only this absent setting and writes
a receipt. A lost receipt verifies exact bytes, never starts/redeploys anything.
Foreign existing content is refused. Release promotion and automatic Web
deployment remain separately authorized; this action does not merge release.

## Unresolved Core publication boundary — do not bypass

Current installed POLICY is SHA-256
`1821f4153285ca632d9f80074322f50f127831549932b61a54f8f035b45efee7`.
The original publisher calls `candidate_matches`: a new Core commit/JAR is
rejected as `business_candidate_not_approved`. Existing
`capture_core_publication` refuses `publication_already_bound` after the
installed proof. The old VMM applied proof pins the POLICY and control-file
afterimages. Reusing the earlier binder, changing only a hash or falsifying its
predecessor cannot publish Community's compatible Core.

The host adapter deliberately does not modify that installed VMM consumer or
weaken its proof checks. A separately reviewed finite **Core publication**
successor compatible with these retained proofs is still required. Its exact
permitted control-file changes must be resolved before a coordinated production
plan can be signed. The new Node role and Web setting cannot solve that guard
restriction. The regression keeps the refusal and parent/consumption records.

## Release order and approvals still outstanding

1. Review the new host and server-only Web configuration PRs. Core #34, Deploy
   #41 and Web #46 are already merged; do not request their review again.
2. Resolve/review the finite Core publication prerequisite above. Then normal
   same-repository main-to-release promotions. Core `R25_DEPLOY_ENABLED` was
   absent in repository and production environment; keep it off. The Community
   workflow is package-only, but Core's separate JAR publisher is conditional.
3. Reuse the automatic Core service/JAR and Deploy controller packages from
   their actual release runs. Verify outer/inner inventories and actual
   run/attempt/manifest digests; no fabricated hashes or duplicate dispatch.
4. Bind current proof/process/database/media preimages, new Node runtime,
   private settings, SQL, verified backup and maintenance/recovery into the
   final plan. Only then request its concrete production authorization.
5. Compatible Core through its original publisher; Community role/database/
   schema/install and supervised readiness; backend compatibility; private Web
   origin; authorized Web release/environment deployment; read-only health.
   Any two-account business acceptance requires its applicable explicit scope.

No final production plan digest exists yet. All commands below are interfaces,
not instructions to run with placeholders or old approvals:

```
/usr/bin/python3.10 -I -B <verified-bundle>/community_host_cli.py check \
  --plan <canonical-plan> --plan-sha256 <actual-sha> \
  --bundle-manifest-sha256 <actual-released-manifest-sha> \
  --archive <service.zip> --manifest <service-manifest> \
  --settings <private-settings> --node <private-staged-node>
```

After exact authorization: `install` adds `--approval`; interrupted install
uses `recover` with the same inputs. A fresh approved foreground `supervise`
uses the same plan bindings plus `--operation-id <fresh-32-hex>`; `observe` and
`stop` reconcile existing process evidence. `bind-web` is the one setting action.
No CLI command calls Core publication, VMM, a provider or schema SQL.

## Evidence to reuse

Core real-JAR/auth/gallery/Node/native-PG run 40260513884 and final Core CI
40261594915 are accepted preparation. Standard Maven has 100 environment-gated
skips; they were not executed passes. Failed run 40260113674's invalid RESULT
must not be reused. Deploy 40261861869 had 585 passes, zero skips. The new host
adds real retained-lease tests, install/CAS/receipt failures, private runtime
validation and real Node/native PostgreSQL drain preservation. These are
isolated integration tests, not production acceptance.
