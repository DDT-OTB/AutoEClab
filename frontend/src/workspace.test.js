import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {runSnapshot,workspacePage} from './workspace.js';
import {draftErrors} from './model.js';
import {flattenSequence} from './workflow.js';

const require=createRequire(import.meta.url);
const compiled=require('esbuild').buildSync({stdin:{contents:"export {default as App} from './src/App.jsx'; export {default as RunWorkspace} from './src/RunWorkspace.jsx';",loader:'jsx',resolveDir:fileURLToPath(new URL('../',import.meta.url))},bundle:true,platform:'node',format:'cjs',write:false,external:['react','lucide-react'],loader:{'.css':'empty'}}).outputFiles[0].text;
const module={exports:{}};new Function('require','module','exports',compiled)(require,module,module.exports);
const {App,RunWorkspace}=module.exports;
const read=path=>JSON.parse(fs.readFileSync(new URL('../../'+path,import.meta.url)));
const sequence=read('config/examples/demo_blocks.json');
const legacy=read('tests/fixtures/demo-lab/config/sequences/demo_rinse_with_predrain.json');
const templates=Object.fromEntries(['peristaltic_1','peristaltic_2','peristaltic_3','syringe_c','valve'].map(id=>[id,read('tests/fixtures/demo-lab/config/templates/'+id+'.json')]));
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
const app=(page,extra={})=>render(App,{user:{username:'synthetic_admin',role:'admin'},initialPage:page,...extra});

test('workspace links resolve to execution, combined plans or history with a safe default',()=>{
  for(const page of ['run','plans','history'])assert.equal(workspacePage('#'+page),page);
  for(const page of ['','#unknown','#constructor','#__proto__'])assert.equal(workspacePage(page),'run');
});
test('loaded execution flow is independent of later edits to a design or imported file',()=>{
  const design=structuredClone(sequence),loaded=runSnapshot(design);
  assert.deepEqual(loaded,sequence);
  design.blocks[1].steps[0].volume_ml=25;
  design.blocks.reverse();
  assert.equal(loaded.blocks[1].steps[0].volume_ml,10);
  const second=runSnapshot(loaded);second.blocks[0].description='Edited separately';
  assert.notEqual(second.blocks[0].description,loaded.blocks[0].description);
  const old=runSnapshot(legacy);
  assert.deepEqual(old.blocks[1].steps,legacy.steps);
  assert.equal(old.schema_version,3);
  for(const value of [null,[],{...sequence,id:{}},{...sequence,description:{}},{...sequence,schema_version:99}])assert.throws(()=>runSnapshot(value));
});
test('loaded run snapshots retain complete capacity, route and measurement validation',()=>{
  const loaded=runSnapshot(sequence);
  assert.deepEqual(draftErrors(loaded,templates),[]);
  loaded.blocks[1].steps[0].volume_ml=41;
  assert.match(draftErrors(loaded,templates).join(' '),/超过 40/);
  const drain=runSnapshot({...sequence,blocks:[{id:'no_route',description:'Missing waste route',steps:[{device:'peristaltic_2',operation:'withdraw',fluid:'waste_liquid',volume_ml:10}]}]});
  assert.match(draftErrors(drain,templates).join(' '),/选择废液流路/);
});
test('execution page contains loading and monitoring without a design canvas or file manager',()=>{
  const html=app('run');
  assert.match(html,/aria-label="实验流程载入"/);assert.match(html,/导入流程 JSON/);
  assert.match(html,/aria-label="装置流程与设备监测"/);assert.match(html,/aria-label="自动化运行控制"/);
  assert.doesNotMatch(html,/class="builder-grid"|aria-label="实验方案文件管理"|class="history-section"/);
  assert.doesNotMatch(html,/设备模板|设备模版|查看设备默认参数/);
  assert.match(html,/aria-current="page" aria-label="实验运行"/);
});
test('plan file management and editing share one workspace and stay separate from instrument monitoring',()=>{
  const files=app('plans');
  assert.match(files,/aria-label="实验方案文件管理"/);assert.match(files,/文件管理/);assert.match(files,/流程编辑/);
  assert.doesNotMatch(files,/class="builder-grid"|class="equipment-overview"|aria-label="自动化运行控制"|file-page-heading/);
  const editor=app('plans',{initialPlanView:'editor'});
  assert.match(editor,/class="builder-grid"/);assert.match(editor,/管理功能块模板/);assert.match(editor,/载入运行/);
  assert.doesNotMatch(editor,/aria-label="实验方案文件管理"|class="equipment-overview"|aria-label="自动化运行控制"/);
  assert.doesNotMatch(editor,/设备模板|查看设备默认参数/);
});
test('history is a separate page and both mobile and desktop provide the two main workspaces',()=>{
  const html=app('history');
  assert.match(html,/class="history-section"/);
  assert.doesNotMatch(html,/class="builder-grid"|class="equipment-overview"|aria-label="实验流程载入"/);
  assert.match(html,/aria-label="主导航"/);assert.match(html,/class="mobile-workspace-nav"/);
  for(const label of ['实验运行','实验方案','运行记录'])assert.match(html,new RegExp('aria-label="'+label+'"'));
});
test('loaded process preview is read-only while retaining every action and current-step feedback',()=>{
  const html=render(RunWorkspace,{sequence,source:{label:'Synthetic loaded snapshot'},items:[],templates,shortNames:{},runningIndex:3});
  assert.equal(flattenSequence(sequence).steps.length,9);
  assert.match(html,/5 个功能块 · 9 个动作/);assert.match(html,/只读/);assert.match(html,/执行中/);
  assert.doesNotMatch(html,/<(input|textarea)\b|step-actions|编辑第/);
  assert.match(html,/使用载入时的流程副本/);
});
