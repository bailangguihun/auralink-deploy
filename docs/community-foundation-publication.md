# Community foundation publication proposal

The initial candidate added no install command. The subsequent reviewed-host
candidate is described in [Community host publication](community-host-publication.md).
Its optional original-watchdog integration requires the separate
[finite publication successor](community-publication-successor.md). Neither
candidate authorizes production installation or migration.

## Components

- Core repository: community-service/, a separate Node process and explicit
  community schema migration outside the Core JAR and Flyway V001–V016.
- Web: /community pages and a constrained server-side /api/community proxy.
- Existing Core: authoritative authenticated profile and gallery reads.
- Matching Core candidate: current-account checks in JWT validation. Publish
  this compatible Core change before relying on Community account revocation.
- AI and VMM: no runtime dependency for this first slice.

## Publication capabilities to establish

Read actual HANDOFF and installed proof chains. Historical README language
about an uninstalled V1 publisher does not override installed successors.

1. Pin actual releases, runtime executables, supervision, module proofs and
   the existing shared deployment lock.
2. Confirm service identity, community-only data roots, loopback port,
   memory/disk budget and log rotation.
3. Add and review the new Node process's start/health/drain/stop/recovery
   contract. Existing Core JAR publishing does not cover it.
4. Identify Web launcher's server-only settings boundary. Permit exactly
   COMMUNITY_INTERNAL_ORIGIN with a validated loopback destination.
5. Bind actual package source SHA, lockfile/dependencies, Node runtime,
   migration digest, private config and compatible frontend before packing.
6. Use an operator migrator and separate non-DDL community runtime role.
   Never broaden or reuse Core's database credentials.

## Coordinated order

1. Complete isolated candidates, interoperation and lifecycle fault tests.
2. Produce real release artifacts and exact migration/configuration scope.
3. Obtain the authorization applicable to that concrete production plan once.
4. Publish the reviewed compatible Core authentication change. Apply approved
   schema/role changes, bind the service, verify health and
   authenticated behavior.
5. Bind Web's internal origin and deploy the compatible frontend.
6. Verify SHAs, two-account isolation, source visibility, persistence/restart
   and preservation of the existing baseline.

If Web arrives before the service is configured it shows unavailable.
This is compatibility behavior, not successful community deployment.

## Recovery

Drain writes before stopping or replacing Community. Reconcile uncertain
outcomes against journals without repeating them. Before writes exist,
restore only verified control files from the current transaction. After
writes exist, preserve the forward schema and records; use a compatible
service or disable the Community route while retaining data.

Do not restore the historical whole database, clear consumption/UNKNOWN,
reset wallets, or alter VMM's on-demand policy. Normal community HTTP traffic
must not acquire the global deployment lock.

## Scope still outstanding

Gallery cards currently link to the original and revalidate metadata.
Shared/private thumbnails, rooms, voice, coins and AI are not enabled.
Knowledge integration follows the user's AutoDL-data requirement; RAGFlow is
not introduced.

Native PostgreSQL and actual Core integration now have separate tests in the
Core repository. The follow-up [runtime contract](community-runtime-contract.md)
describes the new library, package validation, process fault tests and remaining
host-specific binding. Installed lifecycle checks still need their own evidence.
PGlite and HTTP fixtures do not establish those facts.
