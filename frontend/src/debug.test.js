import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {workflowToEditor,workflowSequence,setWorkflowMode,flattenSequence} from './workflow.js';
import {draftErrors} from './model.js';
import {cellVolumes} from './cellSafety.js';
import {defaultPreflight,preflightErrors} from './preflight.js';
import {runSnapshot} from './workspace.js';
import {createApi} from './api.js';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';

const require=createRequire(import.meta.url);
const compiled=require('esbuild').buildSync({stdin:{contents:"export {BlockCanvas,BlockPalette} from './src/WorkflowBuilder.jsx'; export {DebugExecutionConfirmation} from './src/AutomationControl.jsx';",loader:'jsx',resolveDir:fileURLToPath(new URL('../',import.meta.url))},bundle:true,platform:'node',format:'cjs',write:false,external:['react','lucide-react']}).outputFiles[0].text;
const module={exports:{}};new Function('require','module','exports',compiled)(require,module,module.exports);
const {BlockCanvas,BlockPalette,DebugExecutionConfirmation}=module.exports;
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));

const read=file=>JSON.parse(fs.readFileSync(new URL('../../'+file,import.meta.url)));
const templates=Object.fromEntries(['peristaltic_1','syringe_c','valve'].map(id=>[id,read('tests/fixtures/demo-lab/config/templates/'+id+'.json')]));
const action={device:'peristaltic_1',operation:'inject',volume_ml:1,fluid:'water'};
const debug={schema_version:3,mode:'debug',initial_cell_volume_ml:20,id:'component_debug',description:'Multi-block debugging',notes:[],blocks:[
  {id:'first',description:'First component actions',steps:[action,{...action,volume_ml:2}]},
  {id:'second',description:'Further component actions',steps:[{...action,volume_ml:3}]},
]};

test('multi-block debug survives editing, file import and run snapshots without an inserted prefix',()=>{
  const draft=workflowToEditor(debug);
  assert.deepEqual(workflowSequence(draft),debug);
  assert.deepEqual(runSnapshot(debug),debug);
  assert.equal(draft.blocks.length,2);
  assert.equal(flattenSequence(debug).steps.length,3);
  assert.deepEqual(draftErrors(draft,templates),[]);
  assert.deepEqual(preflightErrors(debug),[]);
});
test('changing modes keeps ordinary actions while experimental mode enforces prep again',()=>{
  const experiment=setWorkflowMode(workflowToEditor(debug),'experiment');
  assert.equal(experiment.blocks[0].type,'preflight');
  assert.equal(experiment.blocks.length,3);
  assert.equal('initial_cell_volume_ml' in experiment,false);
  const roundtrip=setWorkflowMode(experiment,'debug');
  assert.equal(roundtrip.blocks.length,2);
  assert.deepEqual(flattenSequence(workflowSequence(roundtrip)).steps,flattenSequence(debug).steps);
  assert.equal(preflightErrors({...debug,mode:'experiment'}).length,1);
  assert.equal(preflightErrors({...debug,blocks:[debug.blocks[0],defaultPreflight(),debug.blocks[1]]}).length,1);
});
test('debug budget starts at the declared liquid amount and rejects overflow or missing input',()=>{
  const volume=cellVolumes(debug);
  assert.equal(volume.initial_volume_ml,20);assert.equal(volume.peak_volume_ml,26);
  assert.match(draftErrors(workflowToEditor({...debug,initial_cell_volume_ml:39}),templates).join(' '),/超过 40/);
  const draft=workflowToEditor(debug);draft.initial_cell_volume_ml='';
  assert.match(draftErrors(draft,templates).join(' '),/初始池内液量/);
  assert.throws(()=>workflowToEditor({...debug,mode:'other'}));
});
test('homing is an explicit parameterless debug action, never an implicit formal exemption',()=>{
  const value={...debug,blocks:[{...debug.blocks[0],steps:[{device:'syringe_c',operation:'initialize'}]}]};
  assert.deepEqual(draftErrors(workflowToEditor(value),templates),[]);
  assert.match(draftErrors(setWorkflowMode(workflowToEditor(value),'experiment'),templates).join(' '),/仅适用于调试/);
  value.blocks[0].steps[0].volume_ml=1;
  assert.match(draftErrors(workflowToEditor(value),templates).join(' '),/不接受体积/);
});
test('debug start uses the protected endpoint with both acknowledgements explicitly serialized',async()=>{
  let call;
  const api=createApi('',{fetcher:async(path,options)=>{call={path,options};return {ok:true,json:async()=>({accepted:true})};}});
  await api.startDebugExecution('synthetic_run','synthetic_version',true,false);
  assert.equal(call.path,'/api/execution/debug-start');
  assert.equal(call.options.credentials,'same-origin');
  assert.equal(call.options.headers['X-Pump-Console'],'1');
  assert.deepEqual(JSON.parse(call.options.body),{run_id:'synthetic_run',version:'synthetic_version',confirm_setup:true,confirm_homing_discharge:false});
});

test('debug canvas shows every block and permits optional preparation removal',()=>{
  const blocks=workflowToEditor(debug).blocks;
  const html=render(BlockCanvas,{blocks,debug:true,templates,shortNames:{},runningIndex:-1});
  assert.match(html,/调试开始/);assert.match(html,/调试结束/);
  assert.equal([...html.matchAll(/class="function-block-card/g)].length,2);
  assert.match(html,/disabled="" aria-label="上移功能块"/);
  assert.equal([...html.matchAll(/disabled="" aria-label="上移功能块"/g)].length,1);
  const prep=workflowToEditor({...debug,blocks:[defaultPreflight(),...debug.blocks]}).blocks;
  const optional=render(BlockCanvas,{blocks:prep,debug:true,templates,shortNames:{},runningIndex:-1});
  assert.match(optional,/前置准备 · 已选/);
  assert.equal([...optional.matchAll(/aria-label="删除功能块"/g)].length,3);
});
test('debug palette offers explicit homing and optional prep without old shipped presets',()=>{
  const common={blocks:[],templates,measurementTemplates:{},activeBlock:workflowToEditor(debug).blocks[0],shortNames:{},debug:true};
  const html=render(BlockPalette,{...common,mode:'actions'});
  assert.match(html,/注射泵回零/);
  const library=render(BlockPalette,{...common,mode:'blocks'});
  assert.match(library,/可选：添加前置准备/);
  assert.doesNotMatch(library,/水洗|排至废液 1|排至废液 2|加入电解液/);
});
test('debug execution confirmation freezes all actions and starts disabled pending live acknowledgement',()=>{
  const prepared={run_id:'synthetic',operation:'debug_sequence',sequence:debug,preview:{plan:{steps:[{id:'first',device:'peristaltic_1',operation:'inject',volume_ml:1},{id:'second',device:'syringe_c',action:'initialize_syringe',operation:'initialize'}]},warnings:[],cell_safety:cellVolumes(debug)}};
  const html=render(DebugExecutionConfirmation,{prepared,currentSequence:debug,names:{},fresh:true});
  assert.match(html,/确认执行调试流程/);assert.match(html,/2 个功能块 · 2 个调试动作/);
  assert.equal([...html.matchAll(/type="checkbox"/g)].length,2);
  assert.match(html,/disabled=""[^>]*>.*?启动调试流程/s);
  assert.doesNotMatch(html,/人工空池确认|开始前置功能块/);
});
