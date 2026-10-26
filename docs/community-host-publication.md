# Community host adapter and publication boundary

Status: review candidate, **NOT_DEPLOYED**. The host adapter installs only the separate Community files. The new finite
[publication successor](community-publication-successor.md) prepares the
compatible Core binding and original-watchdog hook. Neither is installed.
Keep Web promotion pending until approved backend publication is verified.

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

Persistent operation now uses `register` and the original R25 watchdog's
optional Community tick. The finite publication successor installs the exact
hook; a host plan binds `restart_budget: 2`, the successor proof, full seven-file
controller inventory, Python, Node and private settings. Registration does not
start Node. The original watchdog starts it on a subsequent tick under the
original guard's real verifier and inherited lease descriptor. No new daemon,
lock, PID file replacement or HTTP-level deployment lock is introduced.

Each registration permits **one initial start plus two automatic restarts total**.
The persistent attempts list consumes a slot before each start. Terminal exit,
SSH loss, watchdog replacement, `enable`, register receipt loss and reboot do
not reset it. A dead recorded child can restart only after verified EXITED and
an empty listener. A saved process from another boot is reconciled as exited
only with no listener; its original identity remains in its run record. Missing
process identity, unknown start decision or a foreign listener requires
investigation. Never adopt a PID or automatically replay a start/stop intent.

Ordinary Node HTTP continues if the CLI/operator terminal exits. If the original
watchdog dies, Node can continue but crash recovery/health/drain observation is
unavailable until the existing watchdog is recovered. Host reboot still needs
the existing R25 application-baseline recovery; this patch adds **no OS boot
service** and does not enlarge that baseline's restart budget. Once the original
watchdog resumes, Community reconciles its own retained state within its same
three-attempt lifetime budget. Exhaustion requires a separately reviewed future
registration transition; deleting state or reinstalling to reset it is forbidden.

A continuous 60-second STARTING/unhealthy observation requests graceful drain;
only verified exit can permit a remaining restart. STOP_INTENT sends the exact
pidfd one SIGTERM; STOPPING/UNKNOWN is never reported as STOPPED or followed by
SIGKILL. Maintenance `watchdog-drain` and `disable` persist desired STOPPED
before signalling, so maintenance cannot accidentally auto-restart Community.
Core's original publisher and the original application/java stop entry call
this drain before stopping Java. Pending/unknown drain refuses further stop.
After maintenance, `enable` is explicit and retains the remaining budget.
Community failure is diagnosed independently and cannot trigger Core/AI restart.

Normal HTTP does not consult VMM or hold the deployment lease. Installation
still requires the stopped VMM baseline. The older foreground `supervise` is
retained for zero-restart plans only; it is not the ordinary persistent path.
Do not combine that runner with watchdog registration.

Logs rotate by operation, retaining all old logs. At 8 MiB the observer drains
and disables automatic start; it never truncates or deletes logs. This is a
threshold, not a hard disk quota. Retention/free space need operator review.
The 256 MiB Node old-space limit is not a total RSS cap.

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

## Core publication successor and coordinated dependencies

The installed POLICY `1821f4153285ca632d9f80074322f50f127831549932b61a54f8f035b45efee7`
rejects the new Core candidate, and `capture_core_publication` still refuses
`publication_already_bound`. Those refusals remain. The separate finite
successor verifies the entire retained proof against immutable before-images,
limits control changes, adds a new proof marker and keeps VMM behavior unchanged.
See [exact contract and recovery](community-publication-successor.md).

1. Deploy #42 (`a87efa8…`) and Web #47 (`4f21143…`) are merged. Review only the
   new finite-successor/watchdog change, then normal Core/Deploy main→release
   merge-commit promotions. Keep Core `R25_DEPLOY_ENABLED` off; Web release
   pushes may deploy and must remain pending.
2. Reuse automatic formal Core JAR/Community packages and same-run Deploy
   controller/VMM/Community artifacts. Verify full inventories, guards, source
   SHA/run/attempt, archive and manifest digests. No duplicate build dispatch.
3. Bind current predecessor, lifecycle renderer, application/process/data/media,
   original lock, Node/Python, explicit SQL/minimal grants, settings and backups
   into concrete plans. Request applicable production authorization only then.
4. Apply the finite binding first, verify INSTALLED, then compatible Core through
   its original publisher with controlled maintenance and backup. No model work.
5. Explicit Community roles/schema transaction, host installation, registration,
   verified original-watchdog readiness and compatibility; then the one private
   Web origin setting. Only after backend verification and its authorized scope,
   promote Web and use its automatic production deployment/environment approval.
6. Read-only health/identity checks and separately scoped two-account business
   acceptance. Rooms/voice/coins/uploads/shared media/AI remain disabled.

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
uses `recover` with the same inputs. Persistent plans next use `register` with
the same exact plan/bundle arguments; no operation ID is manually assembled.
`observe` reads the current role, `disable` records stop and drains; `enable`
resumes only a drained registration without resetting attempts. `watchdog-tick`
and `watchdog-drain` are original-watchdog/publisher entries requiring its real
inherited `--lease-fd`; operators must not invent that descriptor. `bind-web`
is the one setting action.
No CLI command calls Core publication, VMM, a provider or schema SQL.

## Evidence to reuse

Core real-JAR/auth/gallery/Node/native-PG run 40260513884 and final Core CI
40261594915 are accepted preparation. Standard Maven has 100 environment-gated
skips; they were not executed passes. Failed run 40260113674's invalid RESULT
must not be reused. Deploy 40261861869 had 585 passes, zero skips. The new host
adds real retained-lease tests, install/CAS/receipt failures, private runtime
validation and real Node/native PostgreSQL drain preservation. These are
isolated integration tests, not production acceptance.
