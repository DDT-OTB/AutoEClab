import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {equipmentDevices} from './equipment.js';
import {draftErrors} from './model.js';
import {defaultSafetyPolicy} from './cellSafety.js';
import {defaultPreflight} from './preflight.js';
import {workflowToEditor} from './workflow.js';
import {canDeleteMeasurement} from './measurements.js';
import {createApi} from './api.js';

const require=createRequire(import.meta.url);
const compiled=require('esbuild').buildSync({stdin:{contents:"export {default as Apparatus} from './src/ApparatusDiagram.jsx'; export {default as Monitor} from './src/EquipmentMonitor.jsx'; export {MeasurementDeleteButton} from './src/DataManagement.jsx';",loader:'jsx',resolveDir:fileURLToPath(new URL('../',import.meta.url))},bundle:true,platform:'node',format:'cjs',write:false,external:['react','lucide-react']}).outputFiles[0].text;
const module={exports:{}};new Function('require','module','exports',compiled)(require,module,module.exports);
const {Apparatus,Monitor,MeasurementDeleteButton}=module.exports;
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
const ids=['peristaltic_1','peristaltic_2','peristaltic_3','syringe_c','valve'];
const templates=Object.fromEntries(ids.map(id=>[id,JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/demo-lab/config/templates/'+id+'.json',import.meta.url)))]));
templates.valve.device.routes={water:5,electrolyte:6};templates.valve.action.role='syringe_inlet_selector';
templates.syringe_c.action.allowed_fluids=['water','electrolyte'];templates.syringe_c.action.source_valve='valve';
templates.peristaltic_2.action.routing_mode='direct';
const cv=JSON.parse(fs.readFileSync(new URL('../../config/action_templates/cyclic_voltammetry.json',import.meta.url)));cv.device.execution_supported=true;
const status={devices:ids.map(id=>({id,port_present:true})),runtime:{state:'idle',controller_alive:false}};
const items=equipmentDevices(templates,{electrochemical_station:cv},status,true);
const policy={...defaultSafetyPolicy,cell:{...defaultSafetyPolicy.cell,drain_mode:'direct',allowed_waste_routes:['waste']}};

test('new diagram places the selector on the syringe inlet and draws only one waste bottle',()=>{
  const html=render(Apparatus,{items,runtime:status.runtime,fresh:true,onSelect:()=>{}});
  assert.match(html,/六通阀位于注射泵吸液侧/);assert.match(html,/5 号水，6 号电解液/);
  assert.match(html,/六通入口阀/);assert.match(html,/单一废液瓶/);
  assert.equal([...html.matchAll(/class="waste-bottle-drawing/g)].length,1);
  assert.doesNotMatch(html,/废液 1|废液 2|CHundefined|CHnull/);
});
test('CV graphs are embedded inside the workstation status details',()=>{
  const metadata={id:'cv-'+'b'.repeat(32),sequence_id:'synthetic',step_id:'cv',state:'completed',point_count:2,sample_count:200,stop_confirmed:true};
  const points=[[1,1,0,0],[2,2,.1,1e-6]];
  const html=render(Monitor,{items,selectedItem:items.find(item=>item.id==='electrochemical_station'),fresh:true,role:'admin',measurement:{metadata,points,trend_segments:[points]}});
  assert.match(html,/设备状态详情/);assert.match(html,/电化学工作站测量数据/);
  assert.match(html,/CV 曲线/);assert.match(html,/时间—电流/);
  assert.match(html,/data-trace="full-data-trend"/);
  assert.doesNotMatch(html,/class="measurement-results"/);
});
test('inlet workflows require explicit matching fluid selection while direct drains need no selector',()=>{
  const preflight=defaultPreflight();preflight.steps[2].route='waste';
  const sequence={schema_version:2,id:'source_test',description:'Synthetic inlet workflow',preflight,steps:[{device:'peristaltic_2',operation:'withdraw',fluid:'waste_liquid',volume_ml:1}]};
  assert.deepEqual(draftErrors(sequence,templates,policy),[]);
  sequence.steps.push({device:'syringe_c',operation:'inject',fluid:'water',volume_ml:1});
  assert.ok(draftErrors(sequence,templates,policy).some(text=>text.includes('相符入口')));
  sequence.steps.splice(1,0,{device:'valve',operation:'switch_route',route:'water'});
  assert.deepEqual(draftErrors(sequence,templates,policy),[]);
  assert.equal(workflowToEditor({...sequence,preflight:defaultPreflight()},policy).blocks[0].steps[2].route,'waste');
});
test('only an administrator can request deletion and active records stay disabled',()=>{
  const record={id:'cv-'+'a'.repeat(32),sequence_id:'synthetic',state:'completed',version:'a'.repeat(64)};
  assert.equal(render(MeasurementDeleteButton,{user:{role:'viewer'},record}),'');
  assert.ok(canDeleteMeasurement({role:'admin'},record));
  const disabled=render(MeasurementDeleteButton,{user:{role:'admin'},record:{...record,state:'running'}});
  assert.match(disabled,/disabled=""/);assert.match(disabled,/采集结束/);
  assert.doesNotMatch(render(MeasurementDeleteButton,{user:{role:'admin'},record}),/disabled=""/);
});
test('measurement delete sends the observed version with CSRF protection and no client actor',async()=>{
  const calls=[];const api=createApi('',{fetcher:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({csrf_token:'synthetic-csrf'})};}});
  await api.login('synthetic','synthetic123');await api.deleteMeasurement('cv-'+'a'.repeat(32),'b'.repeat(64));
  const call=calls.at(-1);assert.equal(call.options.method,'DELETE');
  assert.equal(call.options.headers['X-CSRF-Token'],'synthetic-csrf');
  assert.deepEqual(JSON.parse(call.options.body),{expected_version:'b'.repeat(64)});
});
