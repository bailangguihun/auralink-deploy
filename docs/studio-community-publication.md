# Studio V017 and Community package publication

This is a finite managed successor of the installed Knowledge Core binding and
Community supervision recovery. It is not a general purpose release bypass.
Packaging, CI, PR approval and installation remain separate events. Production
installation still requires the exact reviewed plan and formal release artifacts.

## Release scope and order

1. Build the Deploy controller, VMM extension and Community controller from the
   same successful `release` workflow run. Verify the complete manifests before
   staging inactive private files in the established candidate directories.
2. Stage the Core release JAR and its accepted candidate metadata through the
   existing incoming boundary. The targeted business release is Core
   `130493f4b91c3a7cf3dedb43ec1260c01ddac91e`; its independent Community bundle
   comes from the same commit. No generated command or arbitrary SQL is accepted.
3. Stage `V017__creation_history_deletion.sql` at
   `runtime/core-publications/studio-migrations/` with SHA-256
   `f6ef87fbc9d4e8dcb6c6c93b249d58976f5fd5b68d27f3edfccb4b0a13d540ee`.
   Preserve V001–V016 and their recorded source identities.
4. Use `upgrade_assistant_controller.py prepare-studio-publication`, review the
   exact plan, then `preflight-studio-publication` and `bind-studio-publication`.
   The request schema is `R25_STUDIO_CORE_PUBLICATION_REQUEST_V1`, with the same
   fields as the Knowledge publication request, including the formal Community
   **controller** deployment. Preparation itself does not authorize installation.
5. Use the original Core publisher. Its existing quiescence/backup transaction
   performs V016 → V017 before starting the new Core. Only after the actual
   candidate CURRENT and transaction are COMMITTED may the Community package
   upgrade proceed. A waiting GitHub production job is not proof of deployment.
6. Use the new verified `community_host_cli.py package-prepare`, then authorize
   that exact `UNSIGNED_PLAN.json` as `approval.json` in its package-upgrades
   directory, and invoke `package-install`. Common arguments retain the original
   `--plan`, `--plan-sha256`, plus `--bundle-manifest-sha256`,
   `--knowledge-publication-sha256`, `--supervision-upgrade-sha256` and
   `--studio-publication-sha256`. Preparation takes `--archive`, `--manifest` and
   `--review-sha256`; installation also takes `--package-plan` and
   `--package-plan-sha256`. Use the original verified system Python with `-I -B`.
7. Promote and deploy the reviewed Web release using the established managed Web
   entry. Verify actual selected commit/BUILD_ID, service health and Community
   reference images/links. Model generation is separate acceptance coverage.

## Preservation and failure handling

The original shared lease and watchdog are retained. The Studio binding snapshots
old controls and CURRENT for historical proof validation; actual CURRENT is
independently validated against the approved baseline or candidate. It does not
replace old approvals, ledgers, source bundles, VMM state, AI configuration or
Community registration. VMM remains DISABLED / STOPPED / CLOSED.

The Studio schema contract permits only V016 and V017. At V017, verification checks
both the exact migration ledger and the nullable timestamp column/valid partial
index. A failed SQL transaction rolls back atomically. If the response is lost,
recovery reads the actual ledger/schema and never resends SQL or restores the live
database. Application recovery preserves the forward schema and deletion marks.

Community replacement creates a separate immutable release and process history.
The release directory is keyed by the exact package plan, so a fresh approved
attempt of the same archive cannot overwrite a prior failed attempt's V2 ledger.
It reuses the original Node executable, private settings and database. The
original V2 restart algorithm is preserved byte for byte; a separate V2 ledger
tracks the new release. While the shared lease is held, the watchdog cannot make
competing lifecycle decisions. The old ledger is durably stopped before draining;
the new activation marker is selected only after owned health verification.

An interrupted installation requires explicit `package-recover` with the same
plan. An existing activation is verified and reconciled. An owned failed candidate
is drained and the old package receives one recorded explicit resumption, without
erasing history. A missing spawn receipt is UNKNOWN and cannot be replayed.
If activation succeeded and its recorded process subsequently exited, recovery
commits the verified activation receipt; the original watchdog then uses the
normal bounded V2 restart policy. Recovery itself does not replay that spawn.
An unfinished earlier package transaction blocks preparation of a fresh attempt.
Neither recovery path executes DDL, restores data, signals a foreign process,
deletes a lock or starts Java/Next outside their original owners.

## Validation

- `test_studio_publication.py`: complete retained controller proof chain, new
  candidate CURRENT, tampered parent/candidate rejection and interrupted-copy
  recovery. OS/JVM effects use the existing explicit fixture boundaries.
- `test_community_package_upgrade.py`: actual isolated Node process swaps, failed
  replacement recovery, lost activation response, unknown spawn refusal, fresh
  supervisor selection, formal Core context and artifact drift.
- `scripts/rehearse-studio-migration.py`: independent native PG16 cluster with
  all pinned V001–V017 source files. Tests atomic SQL failure, lost response after
  real COMMIT, historical rows, deletion timestamps, repeat verification and
  physical schema drift. CI and formal packaging run this against the pinned Core
  release source. It connects to no existing database and calls no provider.

Local Node tests must run with an interpreter supporting both `os.pidfd_open`
and `signal.pidfd_send_signal`. On the development host this is
`/usr/bin/python3.10`, not the Conda interpreter. No test success substitutes for
CODEOWNER approval, an exact installation plan or production acceptance.
