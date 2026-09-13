import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readdir, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { attach } from './index.mjs';

test('DSH contract: initial snapshot, nested busy, completion, disposal and metadata only', async () => {
  const dir=await mkdtemp(join(tmpdir(),'black-hole-dsh-'));
  const handlers=new Map(), cleanups=[];
  let agents=[{id:'root',status:'idle',prompt:'PRIVATE_DO_NOT_SAVE'}];
  const ctx={agents:{list:()=>agents}, on:(e,f)=>handlers.set(e,f),
    effect:f=>cleanups.push(f()), logger:{warn:console.warn}};
  attach(ctx,dir);
  async function snapshot(predicate) {
    for(let i=0;i<100;i++) {
      const files=(await readdir(dir)).filter(f=>f.endsWith('.json'));
      if(files.length) {
        const text=await readFile(join(dir,files[0]),'utf8');
        assert.ok(!text.includes('PRIVATE') && !text.includes('root'));
        const value=JSON.parse(text);
        if(predicate(value)) return value;
      }
      await new Promise(r=>setTimeout(r,10));
    }
    assert.fail('No expected status file');
  }
  try {
    await snapshot(v=>v.agents===1 && v.running===0);
    agents[0].status='running'; handlers.get('agent/status')({agent:agents[0],status:'running'});
    await snapshot(v=>v.running===1);
    agents.push({id:'child',status:'running'}); handlers.get('agent/created')({agent:agents[1]});
    agents[0].status='idle'; handlers.get('agent/status')({agent:agents[0],status:'idle'});
    await snapshot(v=>v.agents===2 && v.running===1);
    const child=agents.pop(); handlers.get('agent/disposed')({agent:child});
    await snapshot(v=>v.agents===1 && v.running===0);
    await Promise.all(cleanups.map(f=>f()));
    assert.deepEqual(await readdir(dir),[]);
  } finally {await Promise.all(cleanups.map(f=>f())); await rm(dir,{recursive:true,force:true});}
});
