# Community runtime candidate

The foundation publication narrative below records the original candidate and
first-install contract, not the current production installation status. Inspect
the current managed proof chain before operating. The schema 2 image/growth
upgrade is not deployed by this change; see the [upgrade contract](community-learning-upgrade.md)
for its media configuration, explicit migration and required finite successor.
The [host adapter](community-host-publication.md) adds an explicitly bound
operator CLI and finite first-install transaction, without authorizing their
production execution or a migration. The separate finite publication successor adds exact proof verification and
the optional original-watchdog hook; VMM execution policy and modules remain unchanged. Modules ship as a separate package-only artifact.

## Release and process binding

`community_bundle.verify_archive` validates Core's separate Community bundle:
real release SHA/run/attempt, archive digest, complete file inventory, sizes,
individual digests, safe paths and regular files. It does not extract files.
The bundle contains locked production dependencies, source and explicit SQL;
it contains neither runtime credentials nor a Node executable.

`community_lifecycle.Role` accepts an immutable reviewed host binding:
release root and manifest hash/provenance, canonical Node executable/hash/exact
version, private JSON settings path/hash, and a loopback port. The supported
tested Node majors are 22 and 24, with minimum 22.13.0. Every shipped file is
verified before starting. Schema 1 receives exactly the four COMMUNITY_*
settings. A schema 2 bundle may add only COMMUNITY_MEDIA_ROOT, bound to the
private media directory beside its settings file; native OS/architecture/libc
must match its manifest. No NODE_OPTIONS, database credentials or provider keys
are inherited from the caller environment.

The host adapter calls the library as the intended service UID/GID and
prepare private 0700 roots and 0600 files owned by that identity. This candidate
does not change Linux users, sudo policy, groups or ownership on the server.

Every lifecycle call requires the caller-supplied verifier of the **original
installed shared deployment lease**. Supply the installed guard's verifier,
not a no-op. No alternate lock is created. The Node child closes inherited
descriptors and never holds that lease during normal HTTP traffic.

Start persists an operation record and current pointer before spawning once.
It binds Linux boot ID, PID/start ticks, executable, argv, cwd and effective UID.
Health requires the exact child to own the IPv4 loopback listener and return
the expected contract. A startup crash before recording identity remains
UNKNOWN; the library will neither adopt an unverified pid nor start another.

Stop persists STOP_INTENT and signals the verified pidfd once. Community stops
new HTTP admission and drains active handlers, including disconnected clients'
transactions. A deadline returns STOPPING, never a fabricated success or
SIGKILL. A lost stop receipt is reconciled from the retained process identity;
STOP_INTENT never causes an automatic signal resend. Missing/changed identity,
changed boot or foreign listener fails closed. A new explicit start is allowed
only after a verified STOPPED/EXITED state, with all previous run records kept.

## Fault tests and acceptance limits

The Deploy tests run actual Node child processes and Linux `/proc`/pidfd
checks, including shared-fd noninheritance, port collision, file drift,
unverified identity, lost start/stop receipts and retained prior proofs.
The child in these tests is a lifecycle fixture, not the real Community DB
service. Core's companion integration test separately runs the actual JAR,
actual Node service and native PostgreSQL, including in-flight transaction
drain and restart persistence. Neither test is production acceptance.

## Historical foundation publication plan

1. The original WSL/SSH inspection of current proof chain, service identity,
   Node/Python, loopback port, private roots, resources and HBA is recorded in
   [the host contract](community-host-publication.md). Historical README text
   is not used as the installed contract.
2. Review the added host adapter and finite new-directory installation
   transaction, using the installed guard's actual lease verifier.
   The original plan required installing this adapter. Persistent plans register with
   the original watchdog, with one initial start plus two total restarts; the
   older foreground runner is only for explicit zero-restart plans.
3. Bind the actual release artifacts, explicit community-only migration,
   separate migrator/runtime role privileges, backup/restore boundaries and
   the server-only Web COMMUNITY_INTERNAL_ORIGIN setting. No runtime DDL.
4. Prepare the concrete maintenance/publish/recovery plan. Preserve all existing
   rows and forward Community writes; never restore a historical whole database.
5. Publish compatible Core, Community and Web in order, then verify two-account
   business behavior and preservation of the existing application baseline.

The original plan required the [finite successor](community-publication-successor.md)
before publishing the changed Core. Those first-install prerequisites do not
establish the current installed state. Schema 2 requires the separately reviewed
successor described in the [upgrade contract](community-learning-upgrade.md),
bound to current proofs, media settings and the migration receipt. Community
has no GPU/VMM dependency.
