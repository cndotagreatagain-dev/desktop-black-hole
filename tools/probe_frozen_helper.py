"""Probe real frozen helper pipes in an isolated fake Codex profile, no model call."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from packaged_status_setup import install_bundle
from codex_status_bridge import status_directory, read_small


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('bundle',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    args.output=args.output.resolve()
    args.output.mkdir(parents=True,exist_ok=True)
    root=args.output/'isolated-profile'
    root.mkdir(exist_ok=True)
    installation=install_bundle(root,args.bundle)
    directory=status_directory(root)
    manifest=read_small(directory/'installation.json')
    env=os.environ.copy()
    env['PATH']=os.pathsep.join([str(Path(os.environ['SystemRoot'])/'System32'),os.environ['SystemRoot']])
    env.pop('PYTHONPATH',None);env.pop('PYTHONHOME',None)
    records=[]
    for event in ('SessionStart','UserPromptSubmit','PreCompact','Interrupt','SessionEnd'):
        payload=dict(hook_event_name=event,session_id='frozen-helper-self-test',turn_id='qa-turn',
                     source='startup',prompt='PRIVATE_MUST_NOT_BE_STORED')
        started=time.perf_counter()
        child=subprocess.run([manifest['helper'],'--directory',str(directory)],
            input=json.dumps(payload).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            env=env,cwd=args.output,timeout=3,creationflags=subprocess.CREATE_NO_WINDOW)
        elapsed=(time.perf_counter()-started)*1000
        assert child.returncode==0 and child.stdout.strip()==b'{}', (child.returncode,child.stdout,child.stderr)
        files=list((directory/'sessions').glob('*.json'))
        assert len(files)==1, 'Windowed helper did not receive its inherited stdin pipe'
        value=read_small(files[0])
        assert 'PRIVATE' not in files[0].read_text()
        assert value.get('owner'), 'Could not verify real invoking Codex ancestor'
        records.append(dict(event=event,elapsed_ms=elapsed,returncode=child.returncode,
                            bytes=files[0].stat().st_size))
    (args.output/'helper-result.json').write_text(json.dumps(dict(installation=installation,
        real_frozen_pipe_test=True,not_a_live_codex_hook_lifecycle_test=True,events=records),indent=2),encoding='utf8')
    print(json.dumps(records,indent=2))


if __name__=='__main__':main()
