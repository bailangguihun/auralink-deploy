#!/usr/bin/env python3
"""Package a fixed Community lifecycle library; no install or server calls."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

ROOT=Path(__file__).resolve().parents[1]
MODULES=('community_bundle.py','community_lifecycle.py','community_host.py','community_host_cli.py','community_migration.py','community_supervision.py','r25_storage.py')


def materialize(root, provenance):
    if (provenance['repository'] != 'bailangguihun/auralink-deploy' or provenance['ref'] != 'refs/heads/release'
        or not re.fullmatch('[a-f0-9]{40}',provenance['commit'])
        or any(not re.fullmatch('[1-9][0-9]{0,19}',provenance[k]) for k in ('run_id','run_attempt'))):
        raise ValueError('exact release provenance required')
    output,files=io.BytesIO(),[]
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as archive:
        for name in MODULES:
            path=root/'r25'/name
            if not path.is_file() or path.is_symlink():raise ValueError('regular module required')
            raw=path.read_bytes();compile(raw,name,'exec')
            item=zipfile.ZipInfo(name,(1980,1,1,0,0,0));item.create_system=3;item.external_attr=0o100600<<16
            archive.writestr(item,raw)
            files.append(dict(path=name,bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest()))
    raw=output.getvalue()
    manifest=dict(schema='ARTLIVE_COMMUNITY_CONTROLLER_BUNDLE_V1',**provenance,
                  archive_sha256=hashlib.sha256(raw).hexdigest(),files=files)
    return raw,(json.dumps(manifest,sort_keys=True,separators=(',',':'))+'\n').encode()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['create','verify'])
    for name in ('repository','ref','commit','run-id','run-attempt'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();raw,manifest=materialize(ROOT,{k:getattr(args,k) for k in ('repository','ref','commit','run_id','run_attempt')})
    folder=ROOT/'dist/community-controller'
    if args.mode=='create':
        folder.mkdir(parents=True,exist_ok=False)
        for name,data in [('community-controller.zip',raw),('manifest.json',manifest)]:
            with (folder/name).open('xb') as f:f.write(data)
    elif (folder/'community-controller.zip').read_bytes()!=raw or (folder/'manifest.json').read_bytes()!=manifest:
        raise ValueError('bundle differs from reviewed source/run')
    print('COMMUNITY_CONTROLLER=NOT_DEPLOYED PACKAGE_'+args.mode.upper()+'_PASS')


if __name__=='__main__':main()
