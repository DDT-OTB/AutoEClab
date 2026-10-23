import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {defaultPreflight,isPreflight,preflightErrors,canAutoAdvance} from './preflight.js';
import {workflowToEditor,workflowSequence,flattenSequence,importBlock,exportBlock} from './workflow.js';
import {createApi} from './api.js';

const sequence=JSON.parse(fs.readFileSync(new URL('../../config/examples/demo_blocks.json',import.meta.url)));
test('legacy plans acquire a visible mandatory block without modifying original experiments',()=>{
  const old={schema_version:3,id:'old_plan',description:'Legacy block experiment',blocks:[{id:'water',description:'Water injection',steps:[{device:'peristaltic_1',operation:'inject',fluid:'water',volume_ml:1}]}]};
  const before=structuredClone(old),editor=workflowToEditor(old),saved=workflowSequence(editor);
  assert.ok(isPreflight(saved.blocks[0]));assert.deepEqual(preflightErrors(saved),[]);
  assert.deepEqual(old,before);assert.equal(flattenSequence(saved).steps.length,1);
  assert.equal(flattenSequence(saved).steps[0].id,'block_002_step_001');
});
test('preflight presence and order are enforced by type rather than names or stored confirmation flags',()=>{
  const value=structuredClone(sequence);
  assert.deepEqual(preflightErrors(value),[]);
  value.blocks.shift();assert.match(preflightErrors(value).join(' '),/前置准备/);
  value.blocks.unshift({...defaultPreflight(),id:'any_valid_id'});assert.deepEqual(preflightErrors(value),[]);
  value.blocks[0].steps[2].confirmed=true;assert.match(preflightErrors(value).join(' '),/不可修改/);
  assert.throws(()=>importBlock(exportBlock({...defaultPreflight(),steps:defaultPreflight().steps.reverse()})));
});
test('preflight JSON contains no reusable operator acknowledgement and supports only the waste route setting',()=>{
  const block=defaultPreflight();block.steps[1].route='waste_2';
  const value=importBlock(exportBlock(block));assert.equal(value.steps[1].route,'waste_2');
  assert.doesNotMatch(JSON.stringify(value),/confirmed|confirm_discharge|empty_confirmed/);
  block.steps[1].route='waste_3';assert.throws(()=>importBlock(exportBlock(block)));
});
test('automatic advancement is limited to the authorized preparation and never confirms the empty cell',()=>{
  const prepared={sequence,workflow:{authorized:true,phase:'ready_to_drain',needs_advance:true}};
  assert.equal(canAutoAdvance(prepared,sequence,true,true),true);
  for(const change of [{workflow:{authorized:false,phase:'ready_to_drain',needs_advance:true}},{workflow:{authorized:true,phase:'awaiting_confirmation',needs_advance:true}},{workflow:{authorized:true,phase:'failed',needs_advance:true}}])assert.equal(canAutoAdvance({...prepared,...change},sequence,true,true),false);
  assert.equal(canAutoAdvance(prepared,sequence,false,true),false);
  assert.equal(canAutoAdvance(prepared,sequence,true,false),false);
  assert.equal(canAutoAdvance(prepared,{...sequence,description:'Changed'},true,true),false);
});
test('workflow requests carry a frozen identity and separate live operator acknowledgements',async()=>{
  const calls=[];
  const api=createApi('',{fetcher:async(path,options)=>{calls.push({path,options});return {ok:true,json:async()=>path.endsWith('/session')?{csrf_token:'synthetic-csrf'}:{}};}});
  await api.session();await api.advanceWorkflow('run','version',true,false);await api.advanceWorkflow('run','version');await api.advanceWorkflow('run','version',false,true);
  assert.deepEqual(calls.slice(1).map(call=>JSON.parse(call.options.body)),[
    {run_id:'run',version:'version',confirm_discharge:true,empty_confirmed:false},
    {run_id:'run',version:'version',confirm_discharge:false,empty_confirmed:false},
    {run_id:'run',version:'version',confirm_discharge:false,empty_confirmed:true}]);
  for(const call of calls.slice(1)){assert.equal(call.path,'/api/execution/advance');assert.equal(call.options.headers['X-CSRF-Token'],'synthetic-csrf');}
});
