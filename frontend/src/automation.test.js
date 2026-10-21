import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {createApi} from './api.js';
import {automationAvailability,preparedIsCurrent,sequenceKey} from './automation.js';

const require=createRequire(import.meta.url);
const compiled=require('esbuild').buildSync({stdin:{contents:"export {default as AutomationControl,ExecutionConfirmation} from './src/AutomationControl.jsx';",loader:'jsx',resolveDir:fileURLToPath(new URL('../',import.meta.url))},bundle:true,platform:'node',format:'cjs',write:false,external:['react','lucide-react']}).outputFiles[0].text;
const module={exports:{}};new Function('require','module','exports',compiled)(require,module,module.exports);
const {AutomationControl,ExecutionConfirmation}=module.exports;
const render=(component,props)=>renderToStaticMarkup(React.createElement(component,props));
const sequence={schema_version:2,id:'synthetic_run',description:'Synthetic automation',steps:[{device:'peristaltic_1',operation:'inject',fluid:'water',volume_ml:1}]};
const settings={user:{role:'admin'},enabled:true,fresh:true,runtime:{state:'idle'},errors:[]};
const prepared={run_id:'synthetic-id',sequence,preview:{estimated_timed_seconds:5,plan:{steps:[{...sequence.steps[0],id:'step_01',rpm:100}]}}};

test('only a connected administrator with a supported valid plan can prepare automation',()=>{
  assert.equal(automationAvailability(settings).allowed,true);
  for(const change of [{user:{role:'viewer'}},{enabled:false},{fresh:false},{current:{controller_alive:true}},{runtime:{controller_alive:true}},{runtime:{state:'checking'}},{measurementCount:1},{errors:['Invalid volume']},{busy:true}]) {
    assert.equal(automationAvailability({...settings,...change}).allowed,false);
  }
});
test('viewer monitoring never renders start or stop controls even while a task runs',()=>{
  const html=render(AutomationControl,{user:{role:'viewer'},sequence,status:{execution:{enabled:true,current:{state:'running',controller_alive:true,can_stop:true,sequence_id:sequence.id,started_by:'synthetic_admin'}}},fresh:true,errors:[],names:{}});
  assert.match(html,/只读监测/);assert.match(html,/自动化执行中/);
  assert.doesNotMatch(html,/运行当前编排|停止当前实验|确认启动设备/);
});
test('administrator sees stop for the current run and start is disabled while it owns hardware',()=>{
  const html=render(AutomationControl,{user:{role:'admin'},sequence,status:{execution:{enabled:true,current:{state:'running',controller_alive:true,can_stop:true,sequence_id:sequence.id,started_by:'synthetic_admin'}}},fresh:true,errors:[],names:{}});
  assert.match(html,/停止当前实验/);assert.match(html,/运行当前编排/);
  assert.match(html,/<button class="primary-button" disabled=""/);
});
test('disconnected execution monitoring does not claim a live running state and retains the stop request control',()=>{
  const html=render(AutomationControl,{user:{role:'admin'},sequence,status:{execution:{enabled:true,current:{state:'running',controller_alive:true,can_stop:true,sequence_id:sequence.id,started_by:'synthetic_admin'}}},fresh:false,errors:[],names:{}});
  assert.match(html,/运行状态连接待恢复/);assert.doesNotMatch(html,/自动化执行中/);assert.match(html,/停止当前实验/);
});
test('confirmation lists actual server-prepared pump amounts and rejects a subsequently changed draft',()=>{
  const html=render(ExecutionConfirmation,{prepared,currentSequence:sequence,names:{peristaltic_1:'Water pump'}});
  assert.match(html,/Water pump/);assert.match(html,/1 mL/);assert.match(html,/100 rpm/);assert.match(html,/确认启动设备/);
  assert.equal(preparedIsCurrent(prepared,sequence),true);
  assert.equal(sequenceKey(sequence),sequenceKey(Object.fromEntries(Object.entries(sequence).reverse())));
  const changed={...sequence,description:'Changed after preparation'};
  assert.equal(preparedIsCurrent(prepared,changed),false);
  const invalid=render(ExecutionConfirmation,{prepared,currentSequence:changed,names:{}});
  assert.match(invalid,/编排内容已修改/);assert.match(invalid,/<button class="primary-button" disabled=""/);
});
test('automation requests carry CSRF, fixed run identity and version without client PID or shell commands',async()=>{
  const calls=[];
  const api=createApi('',{fetcher:async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>url.endsWith('/session')?{csrf_token:'synthetic-csrf'}:{}};}});
  await api.session();await api.prepareExecution(sequence);await api.startExecution('run-id','snapshot-version');await api.stopExecution('run-id');
  for(const call of calls.slice(1)) {assert.equal(call.options.headers['X-CSRF-Token'],'synthetic-csrf');assert.equal(call.options.headers['X-Pump-Console'],'1');}
  assert.deepEqual(JSON.parse(calls[2].options.body),{run_id:'run-id',version:'snapshot-version'});
  assert.deepEqual(JSON.parse(calls[3].options.body),{run_id:'run-id'});
});
