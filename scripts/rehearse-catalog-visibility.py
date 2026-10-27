#!/usr/bin/env python3
"""Offline replay of a private installed-proof capture; never connects remotely.

The complete control chain and original guard execute their real source bytes.
Filesystem identities are supplied by a separately verified read-only capture.
Database/media/process adapters are explicitly isolated. No fixture record may
be installed as production evidence. Use --output in a private evidence folder.
"""
import argparse
import base64
from contextlib import ExitStack
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'r25'))
import vmm_controller as v


class Capture:
    def __init__(self, path):
        self.capture=json.loads(Path(path).read_bytes())
        self.files={Path(k):{**entry,'bytes':base64.b64decode(entry['raw'])} for k,entry in self.capture['files'].items()}
        for entry in self.files.values():
            assert hashlib.sha256(entry['bytes']).hexdigest()==entry['sha256']
            assert entry['links']==1
        self.queries=self.capture['queries']
        self.original={p:e['bytes'] for p,e in self.files.items()}
        self.base=v.ROOT;self.store=self.base/'runtime/vmm-controller'
        self.real_exists=Path.exists;self.real_symlink=Path.is_symlink;self.real_bytes=Path.read_bytes
        self.scoped=lambda p: Path(p).is_relative_to('/root/autodl-tmp')
        self.effects=[]
    def read(self,path,owner=(0,0),mode=0o600,expected=None,**kw):
        path=Path(path)
        if 'expected_owner' in kw:owner=kw['expected_owner']
        if path not in self.files:raise FileNotFoundError(str(path))
        e=self.files[path]
        assert (e['uid'],e['gid'],e['mode'],e['links'])==(*owner,mode,1),('captured_file_identity',str(path))
        raw=e['bytes']
        assert expected is None or v.sha(raw)==expected,('captured_digest',str(path))
        return raw
    def put(self,path,raw,owner=(0,0),mode=0o600):
        self.files[Path(path)]=dict(bytes=raw,uid=owner[0],gid=owner[1],mode=mode,links=1,sha256=v.sha(raw))
    def exists(self,path):
        p=Path(path)
        return p in self.files or any(p in x.parents for x in self.files) if self.scoped(p) else self.real_exists(p)
    def bytes(self,path):return self.read(path,owner=(self.files[path]['uid'],self.files[path]['gid']),mode=self.files[path]['mode']) if path in self.files else self.real_bytes(path)
    def install_patches(self,stack):
        stack.enter_context(patch.object(Path,'exists',lambda p:self.exists(p)))
        stack.enter_context(patch.object(Path,'is_symlink',lambda p:False if self.scoped(p) else self.real_symlink(p)))
        stack.enter_context(patch.object(Path,'read_bytes',lambda p:self.bytes(p)))
        stack.enter_context(patch.object(v,'read',self.read))
    def load(self,path,name,raw):
        m=type(sys)(name);m.__file__=str(path);sys.modules[name]=m
        exec(compile(raw,str(path),'exec',dont_inherit=True),m.__dict__)
        m.__r25_source_sha256__=v.sha(raw)
        if hasattr(m,'read'):m.read=self.read
        if hasattr(m,'read_file'):m.read_file=self.read
        if hasattr(m,'read_bound'):m.read_bound=self.read
        if hasattr(m,'regular'):
            m.regular=lambda p,expected=None:self.read(p,owner=(self.files[Path(p)]['uid'],self.files[Path(p)]['gid']),mode=self.files[Path(p)]['mode'],expected=expected)
        if hasattr(m,'source_module'):m.source_module=self.load
        return m


def candidate(c):
    # Deliberately synthetic provenance. These artifacts are CPU test inputs,
    # never formal release artifacts, candidate approval or production files.
    provenance=dict(repository='bailangguihun/auralink-deploy',ref='refs/heads/release',commit='f'*40,run_id='1',run_attempt='1')
    pack=runpy.run_path(str(REPO/'scripts/package-vmm-extension.py'))
    raw,mr=pack['materialize'](REPO,provenance)
    manifest=json.loads(mr);folder=c.base/'operations/r25-vmm-candidates'/provenance['commit']
    c.put(folder/'manifest.json',mr);c.put(folder/'vmm-extension.zip',raw)
    for item in manifest['files']:c.put(folder/item['path'],(REPO/'r25'/item['path']).read_bytes())
    deploy={**provenance,'archive_sha256':v.sha(raw),'manifest_sha256':v.sha(mr),'tool_sha256':v.sha((REPO/'r25/vmm_controller.py').read_bytes())}
    pc=runpy.run_path(str(REPO/'scripts/package-r25-release.py'))
    payload={n:(REPO/'r25'/n).read_bytes() for n in pc['FILES']}
    raw=pc['archive_bytes'](payload);mr=v.encoded(pc['manifest_for'](payload,raw,provenance))
    folder=c.base/'operations/r25-controller-candidates'/provenance['commit']
    c.put(folder/'r25-controller.zip',raw);c.put(folder/'r25-controller-manifest.json',mr)
    for n,b in payload.items():c.put(folder/n,b)
    ctrl={**provenance,'archive_sha256':v.sha(raw),'manifest_sha256':v.sha(mr),'tool_sha256':v.sha(payload['upgrade_assistant_controller.py'])}
    return deploy,ctrl


def material(c,manifest_raw,preimages_raw):
    rt=v.Runtime();deploy,ctrl=candidate(c)
    policy=json.loads(c.read(rt.preserved()['policy']))
    before={k:c.read(p,o,m) for k,(p,o,m) in v.visibility_paths(rt).items()}
    after={'vmm_controller.py':(REPO/'r25/vmm_controller.py').read_bytes(),'handoff':v.visibility_handoff(before['handoff'],deploy['tool_sha256'])}
    old=policy['upgrade']['compatibility'];ret=v.encoded(v.visibility_retention(json.loads(c.read(old['retention_manifest_path']))))
    dest=rt.store/'catalog-visibility-retention'/(v.sha(ret)+'.json');c.put(dest,ret)
    preserved={}
    for n in ('policy','core-current','original-controller-active'):
        p=rt.preserved()[n];preserved[str(p)]={k:c.files[p][k] for k in ('mode','sha256')};preserved[str(p)]['owner']=[0,0]
    for name,owner,mode in [('operations/core_release_guard.py',[1000,1000],0o700),('operations/lifecycle_adapter.py',[1000,1000],0o700),('operations/r25-publisher/core_business.py',[0,0],0o600)]:
        p=c.base/name;preserved[str(p)]=dict(owner=owner,mode=mode,sha256=v.sha(c.read(p,owner,mode)))
    m=v.visibility_manifest(manifest_raw);pre=json.loads(preimages_raw);review=v.encoded({'isolated_fixture':True})
    plan=dict(schema=v.VISIBILITY_SCHEMA,deploy=deploy,controller_deploy=ctrl,review_sha256=v.sha(review),manifest_sha256=v.VISIBILITY_MANIFEST,
        policy_sha256=v.VISIBILITY_POLICY,parent=v.VISIBILITY_PARENT,current=json.loads(c.read(rt.preserved()['core-current'])),preserved=preserved,
        files={k:dict(before_sha256=v.sha(before[k]),after_sha256=v.sha(after[k])) for k in before},
        retention_before=dict(path=old['retention_manifest_path'],sha256=v.VISIBILITY_RETENTION),retention_after=dict(path=str(dest),sha256=v.sha(ret)),
        preimages_sha256=v.sha(preimages_raw),sql_sha256=v.sha(v.visibility_sql(m,pre)),restore_sql_sha256=v.sha(v.visibility_sql(m,pre,restore=True)),private_owner=[0,0],host={})
    ident=v.sha(v.encoded(plan));folder=v.visibility_folder(rt,ident)
    for n,b in {'approval.json':v.encoded(plan),'manifest.json':manifest_raw,'preimages.json':preimages_raw,'review.json':review,
                'delist.sql':v.visibility_sql(m,pre),'restore.sql':v.visibility_sql(m,pre,restore=True),
                **{'before/'+n:b for n,b in before.items()},**{'after/'+n:b for n,b in after.items()}}.items():c.put(folder/n,b)
    return rt,plan,folder,ident


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('capture','manifest','preimages','output'):p.add_argument('--'+n,type=Path,required=True)
    a=p.parse_args();c=Capture(a.capture);cases=[]
    with ExitStack() as stack:
        c.install_patches(stack)
        rt,plan,folder,ident=material(c,a.manifest.read_bytes(),a.preimages.read_bytes())
        v.visibility_proof(rt,plan,folder,phase='before');cases.append('actual installed predecessor full proof chain accepted')
        for n,(path,owner,mode) in v.visibility_paths(rt).items():c.put(path,c.read(folder/'after'/n),owner,mode)
        c.put(rt.store/'CATALOG_VISIBILITY_ACTIVE.json',v.encoded(dict(schema=v.VISIBILITY_MARKER,approval_sha256=ident)))
        c.put(folder/'journal.json',v.encoded(dict(schema=v.VISIBILITY_SCHEMA,approval_sha256=ident,phase='COMMITTED',history=['fixture'])))
        guard_path=c.base/'operations/core_release_guard.py'
        g=c.load(guard_path,'core_release_guard',c.read(guard_path,(1000,1000),0o700))
        effective=g.policy();assert effective['upgrade']['compatibility']['retention_manifest_sha256']==plan['retention_after']['sha256']
        record=g.validate_backend_identity(c.base,plan['current']['release']['artifact_sha256'])
        assert record['commit_sha']==v.VISIBILITY_CORE
        cases.append('original guard policy and actual accepted Core record pass with two-control successor')
        policy=copy.deepcopy(effective);before_policy=json.loads(c.read(rt.preserved()['policy']))
        assert before_policy['upgrade']['compatibility']['retention_manifest_sha256']==v.VISIBILITY_RETENTION
        policy['upgrade']['compatibility']=before_policy['upgrade']['compatibility']
        # Negative checks traverse the actual proof, never a mocked verifier.
        for label,modify in [('wrong predecessor',lambda p:p.update(parent='a'*64)),('unrelated policy digest',lambda p:p.update(policy_sha256='b'*64))]:
            bad=copy.deepcopy(plan);modify(bad)
            try:v.visibility_proof(rt,bad,folder)
            except v.Refused:cases.append(label+' refused')
            else:raise AssertionError(label)
        before=c.read(rt.preserved()['policy']);c.put(rt.preserved()['policy'],before+b' ')
        try:g.policy()
        except Exception:cases.append('original policy drift refused')
        else:raise AssertionError('drift accepted')
        c.put(rt.preserved()['policy'],before)
        for path,raw in c.original.items():
            if path not in {x[0] for x in v.visibility_paths(rt).values()}:assert c.files[path]['bytes']==raw, str(path)
        cases.append('all captured historical proofs and unrelated controls byte-for-byte preserved')
    result=dict(result='PASS',cases=cases,captured_files=len(c.original),source_guard_sha256=v.sha(c.original[guard_path]),
                production_writes=0,formal_release=False,limitations=['virtual file identities from live read-only capture','no DB transaction or media/process acceptance in this proof-only phase'])
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
