import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createApi } from './api.js';
import { editorAfterFileChange, experimentKind, fileNameError, filterExperiments, uniqueExperimentId } from './experiments.js';

const require=createRequire(import.meta.url);
const compiled=require('esbuild').buildSync({stdin:{contents:"export { default as ExperimentManager, ExperimentFileDetails, ExperimentFileList } from './src/ExperimentManager.jsx';",loader:'jsx',resolveDir:fileURLToPath(new URL('../',import.meta.url))},bundle:true,platform:'node',format:'cjs',write:false,external:['react','lucide-react']}).outputFiles[0].text;
const module={exports:{}};
new Function('require','module','exports',compiled)(require,module,module.exports);
const {ExperimentManager,ExperimentFileDetails,ExperimentFileList}=module.exports;
const items=[
  {id:'water_demo',filename:'water_demo.json',description:'Synthetic water transfer',step_count:3,modified_at:'2030-01-02T01:00:00Z',size_bytes:1024,editable:true,contains_measurement:false,has_markdown:true,version:'a'},
  {id:'cv_demo',filename:'cv_demo.json',description:'Synthetic voltammetry',step_count:1,modified_at:'2030-01-03T01:00:00Z',size_bytes:500,editable:true,contains_measurement:true,has_markdown:false,version:'b'},
  {id:'invalid_demo',filename:'invalid_demo.json',description:'',step_count:0,modified_at:'2030-01-01T01:00:00Z',size_bytes:15,editable:false,file_error:'Invalid JSON',contains_measurement:false,has_markdown:false,version:'c'},
];
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));

test('file search combines case-insensitive filename, description, type and sorting without mutating the catalog',()=>{
  assert.deepEqual(filterExperiments(items,{query:' WATER ',kind:'fluidics'}).map(item=>item.id),['water_demo']);
  assert.deepEqual(filterExperiments(items,{query:'VOLTAMMETRY'}).map(item=>item.id),['cv_demo']);
  assert.deepEqual(filterExperiments(items,{kind:'review'}).map(item=>item.id),['invalid_demo']);
  assert.deepEqual(filterExperiments(items,{sort:'modified'}).map(item=>item.id),['cv_demo','water_demo','invalid_demo']);
  assert.deepEqual(filterExperiments(items,{sort:'steps'}).map(item=>item.id),['water_demo','cv_demo','invalid_demo']);
  assert.equal(items[0].id,'water_demo');
});
test('file naming prevents collisions and non-English or traversal names',()=>{
  for(const name of ['../outside','中文','bad/name','1abc','a'.repeat(81),'water_demo']) assert.ok(fileNameError(name,'old_name',items));
  assert.equal(fileNameError('fresh_demo','old_name',items),'');
  assert.equal(uniqueExperimentId('water_demo',items),'water_demo_2');
  assert.equal(experimentKind({...items[0],step_count:0}),'draft');
});
test('renaming the loaded file preserves unsaved action edits and deleting retains the editor buffer',()=>{
  const draft={id:'water_demo',description:'Unsaved description',steps:[{device:'peristaltic_1',volume_ml:7}]};
  const state={draft,base:{id:'water_demo',version:'old'},dirty:true};
  const result=editorAfterFileChange(state,'rename',{previous_id:'water_demo',sequence:{id:'renamed_demo'},version:'new'});
  assert.equal(result.draft.id,'renamed_demo');
  assert.equal(result.draft.description,'Unsaved description');
  assert.equal(result.draft.steps,draft.steps);
  assert.equal(result.base.version,'new');assert.equal(result.dirty,true);
  const removed=editorAfterFileChange(state,'delete',{id:'water_demo'});
  assert.equal(removed.draft,draft);assert.equal(removed.base.id,null);assert.equal(removed.dirty,true);
  assert.equal(editorAfterFileChange({...state,draft:{...draft,id:'save_as_draft'}},'rename',{previous_id:'water_demo',sequence:{id:'renamed_demo'},version:'new'}).draft.id,'save_as_draft');
});
test('file list and read-only detail provide inspection without exposing destructive controls',()=>{
  const table=render(ExperimentFileList,{items,selectedId:'water_demo',activeId:'water_demo',onSelect:()=>{},onOpen:()=>{}});
  assert.match(table,/water_demo.json/);assert.match(table,/已加载/);assert.match(table,/aria-pressed="true"/);
  const html=render(ExperimentFileDetails,{item:items[0],content:'{}',tab:'info',user:{role:'viewer'},setTab:()=>{}});
  assert.match(html,/查看实验流程/);assert.match(html,/下载 JSON/);assert.match(html,/同名 Markdown/);
  assert.doesNotMatch(html,/删除方案|重命名|复制方案/);
  const admin=render(ExperimentFileDetails,{item:items[0],content:'{}',tab:'info',user:{role:'admin'},setTab:()=>{}});
  assert.match(admin,/删除方案/);assert.match(admin,/重命名/);assert.match(admin,/复制方案/);
  const ready=render(ExperimentFileDetails,{item:items[0],content:'{}',tab:'info',user:{role:'viewer'},setTab:()=>{},onUse:()=>{}});
  assert.match(ready,/载入实验运行/);assert.doesNotMatch(ready,/确认启动设备/);
});
test('file manager exposes search, filter and empty-state controls as a full workspace',()=>{
  const html=render(ExperimentManager,{items:[],user:{role:'viewer'},activeId:null,onReload:async()=>[],onOpen:()=>{},onNew:()=>{},onImport:()=>{},onBack:()=>{}});
  assert.match(html,/aria-label="实验方案文件管理"/);
  assert.match(html,/aria-label="搜索实验方案"/);assert.match(html,/aria-label="筛选方案类型"/);
  assert.match(html,/还没有实验方案/);assert.match(html,/只读访问/);
  assert.match(html,/<button[^>]*disabled=""[^>]*>.*?新建方案<\/button>/s);
});
test('file rename, duplicate and delete send the observed version with CSRF and request protection',async()=>{
  const calls=[];
  const api=createApi('',{fetcher:async(url,options)=>{calls.push({url,options});return {ok:true,status:200,json:async()=>url.endsWith('/session')?{csrf_token:'file-csrf'}:{}};}});
  await api.session();await api.renameExperiment('water_demo','next_demo','observed-version');await api.duplicateExperiment('water_demo','copy_demo','observed-version');await api.deleteExperiment('water_demo','observed-version');
  for(const call of calls.slice(1)) {
    assert.equal(call.options.headers['X-CSRF-Token'],'file-csrf');assert.equal(call.options.headers['X-Pump-Console'],'1');
    assert.equal(JSON.parse(call.options.body).expected_version,'observed-version');
  }
  assert.equal(calls[3].options.method,'DELETE');assert.equal(calls[3].url,'/api/experiments/water_demo');
});
