import test from 'node:test';
import assert from 'node:assert/strict';
import {createMeasurementResource} from './measurementResource.js';
import {workspaceHash,workspaceLocation} from './workspace.js';
import {storageText} from './measurements.js';

const value=(id,version,state='completed')=>({measurement:{id,version,state,updated_at:version},chart:{points:[],trend_segments:[]}});
test('unchanged measurement revisions reuse charts while a new revision replaces the snapshot',async()=>{
  let calls=0;
  const resource=createMeasurementResource({measurement:async id=>value(id,String(++calls))});
  const one=await resource.read('synthetic','1');
  assert.equal(await resource.read('synthetic','1'),one);assert.equal(calls,1);
  const two=await resource.read('synthetic','2');assert.equal(calls,2);assert.equal(two.measurement.version,'2');
  await resource.read('synthetic','2',{force:true});assert.equal(calls,3);
});
test('concurrent chart requests share one fetch and failed requests remain retryable',async()=>{
  let calls=0,resolve;
  const resource=createMeasurementResource({measurement:id=>{calls++;return new Promise(done=>{resolve=()=>done(value(id,'1'));});}});
  const one=resource.read('synthetic','1'),two=resource.read('synthetic','1');
  await Promise.resolve();assert.equal(calls,1);resolve();assert.deepEqual(await one,await two);
  let fail=true;
  const retry=createMeasurementResource({measurement:async id=>{if(fail)throw new Error('Synthetic network failure');return value(id,'1');}});
  await assert.rejects(retry.read('synthetic','1'));fail=false;await retry.read('synthetic','1');
});
test('active fallback polling refreshes and cache memory remains bounded',async()=>{
  let calls=0;
  const resource=createMeasurementResource({measurement:async id=>{calls++;return value(id,String(calls),'running');}},{limit:2});
  await resource.read('a');await resource.read('a');assert.equal(calls,2);
  await resource.read('b');await resource.read('c');assert.equal(resource.peek('a'),null);
  resource.invalidate('c');assert.equal(resource.peek('c'),null);
});
test('run and dataset deep links survive refresh and reject unsafe identifiers',()=>{
  const runId='20261029T123456123456Z-abcdefabcdef',measurementId='cv-'+'a'.repeat(32);
  const hash=workspaceHash('data',{runId,measurementId});
  assert.deepEqual(workspaceLocation(hash),{page:'data',runId,measurementId});
  assert.deepEqual(workspaceLocation('#data?run_id=../../private&measurement_id=bad'),{page:'data',runId:null,measurementId:null});
  assert.equal(workspaceHash('run',{runId,measurementId}),'#run');
});
test('storage labels distinguish committed samples, sealing, saved exports and recovered partial data',()=>{
  assert.match(storageText({state:'running',storage_state:'recording',point_count:10}),/已提交 10/);
  assert.match(storageText({state:'finalizing'}),/正在保存/);
  assert.match(storageText({state:'completed',csv_ready:true,point_count:10}),/数据已保存/);
  assert.match(storageText({state:'interrupted',csv_ready:true}),/已恢复已提交/);
});
