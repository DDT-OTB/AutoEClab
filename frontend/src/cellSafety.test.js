import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {cellAllowsStart,cellVolumes} from './cellSafety.js';
import {createApi} from './api.js';
const inject=(volume_ml,device='peristaltic_1',fluid='water')=>({device,operation:'inject',volume_ml,fluid});
const withdraw=volume_ml=>({device:'peristaltic_2',operation:'withdraw',volume_ml,fluid:'waste_liquid'});
test('all liquids share the cell limit and intermediate peaks cannot hide behind a final drain',()=>{
  const value={steps:[inject(30),inject(15,'syringe_c','electrolyte'),withdraw(100)]};
  const result=cellVolumes(value);
  assert.equal(result.final_volume_ml,0);assert.equal(result.peak_volume_ml,45);assert.equal(result.errors.length,1);
  assert.match(result.errors[0],/第 2 步/);
  assert.equal(cellVolumes({steps:[inject(10),inject(15,'syringe_c','electrolyte'),inject(15,'peristaltic_3','sulfuric_acid')]}).errors.length,0);
});
test('sequential withdrawals release capacity while every experiment still requires empty-cell evidence',()=>{
  assert.deepEqual(cellVolumes({steps:[withdraw(100),inject(30),withdraw(25),inject(35)]}).steps.map(row=>row.after_ml),[0,30,5,40]);
  assert.equal(cellVolumes({steps:Array.from({length:400},()=>inject(.1))}).peak_volume_ml,40);
  for(const state of ['required','running','awaiting_confirmation','failed','invalidated','unknown','consumed'])assert.equal(cellAllowsStart({required:true,state}),false);
  assert.equal(cellAllowsStart({required:true,state:'passed'}),true);
  assert.equal(cellAllowsStart(null),false);
});
test('syringe rounding is accounted for conservatively before a boundary fill',()=>{
  const sequence={steps:[inject(24.998),inject(15.002,'syringe_c','electrolyte')]};
  const result=cellVolumes(sequence,undefined,{syringe_c:{device:{kind:'syringe',capacity_ml:25,stroke_steps:12000}}});
  assert.ok(result.peak_volume_ml>40);assert.equal(result.errors.length,1);
});
test('drain API sends explicit routing, operator confirmation and an idempotency identifier',async()=>{
  const calls=[];
  const api=createApi('',{fetcher:async(url,options)=>{calls.push({url,options});return{ok:true,status:200,json:async()=>url.endsWith('/session')?{csrf_token:'synthetic'}:{}};}});
  await api.session();await api.drainCell('prepared','version','waste_2',true,'request-id');await api.confirmEmpty('prepared','version',true);
  assert.deepEqual(JSON.parse(calls[1].options.body),{run_id:'prepared',version:'version',route:'waste_2',confirm_discharge:true,request_id:'request-id'});
  assert.deepEqual(JSON.parse(calls[2].options.body),{run_id:'prepared',version:'version',empty_confirmed:true});
  assert.equal(calls[1].options.headers['X-CSRF-Token'],'synthetic');
});
test('drain completion is displayed as awaiting confirmation rather than a sensed empty cell',()=>{
  const require=createRequire(import.meta.url);
  const compiled=require('esbuild').buildSync({stdin:{contents:"export {default} from './src/CellPreparation.jsx';",loader:'jsx',resolveDir:fileURLToPath(new URL('../',import.meta.url))},bundle:true,platform:'node',format:'cjs',write:false,external:['react','lucide-react']}).outputFiles[0].text;
  const module={exports:{}};new Function('require','module','exports',compiled)(require,module,module.exports);
  const html=renderToStaticMarkup(React.createElement(module.exports.default,{state:{required:true,state:'awaiting_confirmation',route:'waste_1'}}));
  assert.match(html,/待现场确认/);assert.match(html,/当前没有液位传感器/);assert.match(html,/已现场确认本次电解池为空/);
  assert.match(html,/确认空池/);assert.match(html,/disabled=""/);
});
