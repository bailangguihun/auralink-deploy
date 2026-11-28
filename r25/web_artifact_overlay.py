"""Render a prebuilt Web publisher against the exact installed R25 inputs.

This command produces a review candidate. It has no installation operation.
"""

import argparse
import ast
import json
import os
from pathlib import Path

from web_selection_recovery_overlay import digest, read_input, replace_once


ENTRY = "deploy-web.sh"
SERIALIZER = "serialized_deploy_web.py"
INNER = "deploy-web.original-1b21f25705b3.sh"
CANDIDATE_INNER = "deploy-web.prebuilt-v1.sh"
ARTIFACT = "web_artifact.py"
BASELINES = {
    ENTRY: "ab1335aa5a655b4891f73fa5f2702af71bf50f7e6bb20ec7593ab01fe530d17e",
    SERIALIZER: "d00cfd26d2f39b84cc944effdb1c35c3cd4823b3fa86cc54a37e4d9e167eb3a5",
    INNER: "1b21f25705b3928810bb329c2e434e0f9f0b93e798aec8697794124902a9e3b2",
}


def edit(source, before, after):
    return replace_once(source, before.encode(), after.encode())


def render_inner(inner):
    inner = edit(inner, 'preflight() {\n', '''preflight() {
  if [[ "$MODE" == "deploy-artifact" ]]; then
    [[ "$REF" =~ ^[0-9a-f]{40}$ ]] || fail "invalid_artifact_commit"
    SHA="$REF"
  else
''')
    inner = edit(inner, '  SHORT_SHA="${SHA:0:12}"', '''  fi
  if [[ "$MODE" == "deploy-artifact" ]]; then
    [[ -x "$NODE" && -x "$SUPERVISOR" ]] || fail "artifact_runtime_missing"
    require_command curl
    require_command ss
    require_command ln
    require_command mv
  fi
  SHORT_SHA="${SHA:0:12}"''')
    inner = edit(inner, '  git -C "$SOURCE" checkout --detach "$SHA"',
                 '  [[ "$MODE" != "deploy-artifact" ]] || fail "verified_artifact_release_missing"\n'
                 '  git -C "$SOURCE" checkout --detach "$SHA"')
    return edit(inner, '  deploy)\n    run_deploy', '  deploy|deploy-artifact)\n    run_deploy')


def render_serializer(serializer, artifact, staged_inner):
    serializer = edit(serializer, "import sys\n", "import sys\nimport tarfile\nimport types\n")
    serializer = edit(serializer, "LOCK=ROOT/", (
        f"ARTIFACT=ROOT/'bin/{ARTIFACT}'\n"
        f"ARTIFACT_SHA='{digest(artifact)}'\n"
        f"ARTIFACT_INNER=ROOT/'bin/{CANDIDATE_INNER}'\n"
        f"ARTIFACT_INNER_SHA='{digest(staged_inner)}'\n"
        "LOCK=ROOT/"
    ))
    serializer = edit(serializer, "        require(len(sys.argv)==3 and sys.argv[1]", '''        action = sys.argv[1] if len(sys.argv) > 1 else ''
        artifact_mode = action == 'deploy-artifact'
        if action in {'prepare-artifact', 'deploy-artifact'}:
            require(len(sys.argv) == (5 if artifact_mode else 3), 'artifact_usage')
            check(ARTIFACT, 0o700, ARTIFACT_SHA)
            raw = ARTIFACT.read_bytes()
            require(hashlib.sha256(raw).hexdigest() == ARTIFACT_SHA, 'deployer_hash')
            package = types.ModuleType('verified_web_artifact')
            exec(compile(raw, str(ARTIFACT), 'exec', dont_inherit=True), package.__dict__)
            if action == 'prepare-artifact':
                package.prepare(ROOT, sys.argv[2])
                print('DEPLOY_WEB=INCOMING_READY')
                return 0
            check(ARTIFACT_INNER, 0o400, ARTIFACT_INNER_SHA)
            expected = package.commit_for(sys.argv[2])
        else:
            require(len(sys.argv)==3 and sys.argv[1]''')
    serializer = edit(serializer, "            except BlockingIOError:raise RuntimeError('frontend_deployment_busy')",
                      '''            except BlockingIOError:raise RuntimeError('frontend_deployment_busy')
            opened, named = os.fstat(fd), LOCK.lstat()
            require((opened.st_dev, opened.st_ino) == (named.st_dev, named.st_ino), 'lease_inode')''')
    serializer = edit(serializer, "            if sys.argv[1]!='deploy':",
                      "            if action not in {'deploy', 'deploy-artifact'}:")
    serializer = edit(serializer,
                      "            expected=subprocess.check_output(['/usr/bin/git','-C',str(SOURCE),'rev-parse','--verify',sys.argv[2]+'^{commit}'],text=True).strip()",
                      '''            intent = {'requested_ref': sys.argv[2]}
            inner_command = [str(INNER), 'deploy', sys.argv[2]]
            if artifact_mode:
                package.materialize(ROOT, sys.argv[2], sys.argv[3], sys.argv[4])
                intent.update(artifact_sha256=sys.argv[3], expected_build_id=sys.argv[4])
                if old['commit_sha'] == expected:
                    require(old['build_id'] == sys.argv[4], 'current_artifact_build_id')
                    print('DEPLOY_WEB=ARTIFACT_CURRENT commit='+expected+' build='+sys.argv[4])
                    return 0
                inner_command = [str(ARTIFACT_INNER), 'deploy-artifact', expected]
            else:
                expected=subprocess.check_output(['/usr/bin/git','-C',str(SOURCE),'rev-parse','--verify',sys.argv[2]+'^{commit}'],text=True).strip()
            intent['expected_commit'] = expected''')
    serializer = edit(serializer,
                      "            transition.record('DEPLOY_INTENT',dict(requested_ref=sys.argv[2],expected_commit=expected))",
                      "            transition.record('DEPLOY_INTENT',intent)")
    serializer = edit(serializer, "            result=subprocess.run(['/bin/bash',str(INNER),'deploy',sys.argv[2]],stdin=subprocess.DEVNULL,",
                      "            result=subprocess.run(['/bin/bash', *inner_command],stdin=subprocess.DEVNULL,")
    serializer = edit(serializer,
                      "            data=updated_selection(old,frontend,expected,selector)",
                      '''            data=updated_selection(old,frontend,expected,selector)
            if artifact_mode:
                require(json.loads(data)['build_id'] == sys.argv[4], 'selected_artifact_build_id')''')
    serializer = edit(serializer,
                      "            print('DEPLOY_WEB=PUBLIC_READINESS_NOT_PROVEN requires=actual_browser_origin_acceptance')",
                      '''            if artifact_mode:
                print('DEPLOY_WEB=ARTIFACT_COMMITTED commit='+expected+' build='+sys.argv[4])
            print('DEPLOY_WEB=PUBLIC_READINESS_NOT_PROVEN requires=actual_browser_origin_acceptance')''')
    serializer = edit(serializer, "    except (OSError,RuntimeError) as e:",
                      "    except (OSError, RuntimeError, ValueError, tarfile.TarError) as e:")
    return edit(serializer, "str(e) if isinstance(e,RuntimeError) else type(e).__name__",
                "str(e) if isinstance(e,RuntimeError) or type(e).__name__ == 'ArtifactRefused' else type(e).__name__")


def render_independent_serializer(serializer):
    serializer = edit(serializer, 'import tarfile\n', 'import tarfile\nimport re as _re\n')
    serializer = edit(serializer, "        artifact_mode = action == 'deploy-artifact'", """        independent = action in {'prepare-independent-artifact', 'deploy-independent-artifact'}
        if independent:
            action = action.replace('-independent', '')
        artifact_mode = action == 'deploy-artifact'""")
    serializer = edit(serializer, "            if action == 'prepare-artifact':", '''            if independent and not artifact_mode:
                independent_guard()
            if action == 'prepare-artifact':''')
    serializer = edit(serializer, "                print('DEPLOY_WEB=INCOMING_READY')", '''                print('DEPLOY_WEB=INDEPENDENT_INCOMING_READY' if independent else 'DEPLOY_WEB=INCOMING_READY')''')
    serializer = edit(serializer, "                package.materialize(ROOT,", '''                if independent:
                    core = independent_guard()
                    try:
                        core.require_independent_web_publication(fd)
                    except core.Refused as error:
                        raise RuntimeError(str(error)) from None
                package.materialize(ROOT,''')
    serializer = edit(serializer, 'def main(', '''def independent_guard():
    check(HANDOFF, 0o600)
    values = settings(HANDOFF)
    path = R25 / 'operations/core_release_guard.py'
    digest = values.get('R25_CORE_RELEASE_GUARD_SHA256', '')
    require(values.get('R25_CORE_RELEASE_GUARD') == str(path) and _re.fullmatch('[0-9a-f]{64}', digest),
            'independent_web_guard_binding')
    require(not any(item.is_symlink() for item in (path, *path.parents)), 'independent_web_guard_path')
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and stat.S_IMODE(info.st_mode) == 0o700 and
            (info.st_uid, info.st_gid, info.st_nlink) == (1000, 1000, 1), 'independent_web_guard_identity')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == digest, 'independent_web_guard_digest')
    core = types.ModuleType('verified_independent_web_guard')
    core.__file__ = str(path)
    exec(compile(raw, str(path), 'exec', dont_inherit=True), core.__dict__)
    require(callable(getattr(core, 'require_independent_web_publication', None)),
            'independent_web_controller_upgrade_required')
    return core


def main(''')
    return serializer


def render(source, artifact):
    if any(digest(source[name]) != sha for name, sha in BASELINES.items()):
        raise ValueError("unreviewed_web_publisher_baseline")
    inner = render_inner(source[INNER])
    serializer = render_serializer(source[SERIALIZER], artifact, inner)
    entry = edit(source[ENTRY], BASELINES[SERIALIZER], digest(serializer))
    ast.parse(serializer)
    ast.parse(artifact)
    return {
        ENTRY: (entry, 0o700), SERIALIZER: (serializer, 0o700),
        CANDIDATE_INNER: (inner, 0o400), ARTIFACT: (artifact, 0o700),
    }


def render_independent_upgrade(source, artifact):
    files = render(source, artifact)
    previous = {name: digest(contents) for name, (contents, _) in files.items()}
    serializer = render_independent_serializer(files[SERIALIZER][0])
    entry = edit(files[ENTRY][0], previous[SERIALIZER], digest(serializer))
    ast.parse(serializer)
    files.update({ENTRY: (entry, 0o700), SERIALIZER: (serializer, 0o700)})
    return files, previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-directory", type=Path, required=True)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--independent-upgrade", action="store_true")
    args = parser.parse_args()
    source = {name: read_input(args.source_directory / name) for name in BASELINES}
    artifact = Path(__file__).with_name(ARTIFACT).read_bytes()
    if args.independent_upgrade:
        output, previous = render_independent_upgrade(source, artifact)
    else:
        output, previous = render(source, artifact), BASELINES
    args.output_directory.mkdir(mode=0o700)
    files = []
    for name, (contents, mode) in output.items():
        path = args.output_directory / name
        with path.open("xb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(contents)
        files.append({"file": name, "previous_sha256": previous.get(name),
                      "sha256": digest(contents), "mode": f"{mode:04o}"})
    manifest = {"status": "RENDERED_NOT_INSTALLED", "files": files, "preserved_inner_sha256": BASELINES[INNER]}
    with (args.output_directory / "candidate.json").open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2)
        stream.write("\n")
    print(json.dumps(manifest))


if __name__ == "__main__":
    main()
