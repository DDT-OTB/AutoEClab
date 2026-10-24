import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { activeFlowDevice, confirmedWasteRoute, equipmentDevices, equipmentStatusSource, equipmentSummary } from './equipment.js';

const require=createRequire(import.meta.url);
const compiled=require('esbuild').buildSync({
  stdin:{contents:"export { default as EquipmentOverview } from './src/EquipmentOverview.jsx'; export { default as EquipmentMonitor } from './src/EquipmentMonitor.jsx'; export { default as ApparatusDiagram, InstrumentNode } from './src/ApparatusDiagram.jsx';",loader:'jsx',resolveDir:fileURLToPath(new URL('../',import.meta.url))},
  bundle:true,platform:'node',format:'cjs',write:false,external:['react','lucide-react'],
}).outputFiles[0].text;
const module={exports:{}};
new Function('require','module','exports',compiled)(require,module,module.exports);
const { EquipmentOverview,EquipmentMonitor,ApparatusDiagram,InstrumentNode }=module.exports;
const ids=['peristaltic_1','peristaltic_2','peristaltic_3','syringe_c','valve'];
const templates=Object.fromEntries(ids.map(id=>[id,JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/demo-lab/config/templates/'+id+'.json',import.meta.url)))]));
const cv=JSON.parse(fs.readFileSync(new URL('../../config/action_templates/cyclic_voltammetry.json',import.meta.url)));
const measurementTemplates={electrochemical_station:cv};
const baseStatus=()=>({devices:ids.map(id=>({id,port_present:true})),runtime:{state:'idle',controller_alive:false}});
const itemsFor=(status,fresh=true)=>equipmentDevices(templates,measurementTemplates,status,fresh);
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
const runningStatus=()=>{
  const status=baseStatus();
  const step={device:'peristaltic_1',state:'executing',action:'dose',operation:'inject',volume_ml:10,progress_fraction:.4,elapsed_seconds:8};
  status.runtime={state:'running',controller_alive:true,active_step:step};
  status.devices[0].activity=step;
  return status;
};

test('overview counts physical devices separately from the reserved workstation',()=>{
  const items=itemsFor(baseStatus());
  assert.deepEqual(equipmentSummary(items),{total:5,reserved:1,working:0,connected:0,idle:0,attention:0,unknown:5});
  assert.equal(items.find(item=>item.id==='electrochemical_station').activity.state,'not_integrated');
  assert.equal(items[0].name,'泵 1 · 超纯水');
});
test('hardware activity is not a program flow estimate and stale or disconnected feedback is unknown',()=>{
  const status=baseStatus();
  status.devices[0].hardware={state:'busy',stale:false};
  assert.equal(itemsFor(status)[0].activity.state,'busy');
  assert.equal(equipmentSummary(itemsFor(status)).working,1);
  assert.equal(activeFlowDevice(itemsFor(status),status.runtime,true),null);
  status.devices[0].hardware.stale=true;
  assert.equal(itemsFor(status)[0].activity.state,'unknown');
  status.devices[0].hardware.stale=false;
  assert.equal(itemsFor(status,false)[0].activity.state,'unknown');
  assert.equal(equipmentStatusSource(itemsFor(status,false)[0],false),'状态连接待恢复');
});
test('only a fresh executing program animates its flow path',()=>{
  const status=runningStatus();
  assert.equal(activeFlowDevice(itemsFor(status),status.runtime,true),'peristaltic_1');
  const html=render(ApparatusDiagram,{items:itemsFor(status),runtime:status.runtime,fresh:true,onSelect:()=>{}});
  assert.equal([...html.matchAll(/pipe-working/g)].length,1);
  assert.match(html,/正在注液/);
  assert.equal(activeFlowDevice(itemsFor(status,false),status.runtime,false),null);
  status.runtime.state='unresponsive';
  assert.equal(activeFlowDevice(itemsFor(status),status.runtime,true),null);
});
test('waste route highlighting requires a fresh query or a completed valve action in this run',()=>{
  const status=baseStatus(), valve=status.devices.find(item=>item.id==='valve');
  valve.last_observation={channel:5,at:'2026-10-19T00:00:00Z'};
  assert.equal(confirmedWasteRoute(itemsFor(status),status.runtime,true),null);
  valve.hardware={state:'idle',channel:6,stale:false,queried_at:'2026-10-20T00:00:00Z'};
  assert.equal(confirmedWasteRoute(itemsFor(status),status.runtime,true).channel,6);
  valve.hardware.stale=true;
  assert.equal(confirmedWasteRoute(itemsFor(status),status.runtime,true),null);
  status.runtime={state:'running',controller_alive:true};
  valve.activity={state:'completed'};
  assert.equal(confirmedWasteRoute(itemsFor(status),status.runtime,true).source,'controller');
  assert.equal(confirmedWasteRoute(itemsFor(status),status.runtime,false),null);
  valve.activity={state:'executing'};
  assert.equal(confirmedWasteRoute(itemsFor(status),status.runtime,true),null);
});
test('default layout renders the apparatus and six selectable status rows',()=>{
  const html=render(EquipmentOverview,{templates,measurementTemplates,status:baseStatus(),fresh:true,streaming:true,role:'admin'});
  assert.match(html,/device-grid equipment-workspace/);
  assert.match(html,/aria-label="实验装置工作流图"/);
  assert.match(html,/aria-label="实时设备状态监测"/);
  assert.match(html,/电解池居中，注液设备在左，排液设备在右，测量仪器在上方/);
  assert.equal([...html.matchAll(/class="equipment-monitor-row /g)].length,6);
  assert.match(html,/5 台设备/);
  assert.match(html,/测量预留/);
  assert.doesNotMatch(html,/启动泵|停止泵|开始测量/);
});
test('selected device shows live program progress and hides device template settings',()=>{
  const status=runningStatus(), items=itemsFor(status);
  const html=render(EquipmentMonitor,{items,selectedItem:items[0],fresh:true,role:'admin'});
  assert.match(html,/全部设备/);
  assert.match(html,/设备状态详情/);
  assert.match(html,/控制程序事件/);
  assert.match(html,/aria-valuenow="40"/);
  assert.match(html,/目标体积/);
  assert.doesNotMatch(html,/设备模板参数|模板转速/);
  assert.match(html,/不是流量传感器实测值/);
  assert.doesNotMatch(html,/class="equipment-monitor-row /);
});
test('expired syringe feedback is not displayed as a current piston or valve reading',()=>{
  const status=baseStatus();
  status.devices.find(item=>item.id==='syringe_c').hardware={state:'idle',stale:true,position_steps:1234,valve_position:'i',queried_at:'2026-10-19T00:00:00Z'};
  const items=itemsFor(status), item=items.find(item=>item.id==='syringe_c');
  const html=render(EquipmentMonitor,{items,selectedItem:item,fresh:true,role:'admin'});
  assert.match(html,/读数已过期/);
  assert.doesNotMatch(html,/1234 步|活塞位置|注射泵阀位/);
});
test('workstation detail is reserved and cannot imply live measurement data',()=>{
  const items=itemsFor(baseStatus());
  const html=render(EquipmentMonitor,{items,selectedItem:items.find(item=>item.virtual),fresh:true,role:'viewer'});
  assert.match(html,/测量接口尚未接入/);
  assert.match(html,/当前暂无实时测量数据/);
  assert.match(html,/更新显示/);
  assert.doesNotMatch(html,/progressbar|启动测量/);
});

test('illustrated device selection supports both mouse and keyboard without a hardware action',()=>{
  const calls=[], item=itemsFor(baseStatus())[0];
  const element=InstrumentNode({item,node:{id:item.id,x:24,y:188,kind:'peristaltic'},prefix:'test',selectedId:item.id,onSelect:id=>calls.push(id)});
  assert.equal(element.props.role,'button');
  assert.equal(element.props.tabIndex,0);
  assert.equal(element.props['aria-pressed'],true);
  let prevented=0;
  for(const key of ['Enter',' ','ArrowRight']) element.props.onKeyDown({key,preventDefault:()=>prevented++});
  element.props.onClick();
  assert.equal(prevented,2);
  assert.deepEqual(calls,[item.id,item.id,item.id]);
});
test('the illustrated valve and waste bottle only show a confirmed current route',()=>{
  const status=baseStatus(), valve=status.devices.find(item=>item.id==='valve');
  valve.hardware={state:'idle',channel:5,stale:false};
  const renderDiagram=fresh=>render(ApparatusDiagram,{items:itemsFor(status,fresh),runtime:status.runtime,fresh,onSelect:()=>{}});
  assert.match(renderDiagram(true),/data-confirmed-channel="5"/);
  assert.equal([...renderDiagram(true).matchAll(/waste-bottle-drawing waste-bottle-confirmed/g)].length,1);
  valve.hardware.stale=true;
  assert.match(renderDiagram(true),/data-confirmed-channel="unknown"/);
  assert.doesNotMatch(renderDiagram(true),/waste-bottle-drawing waste-bottle-confirmed/);
  valve.hardware.stale=false;
  assert.match(renderDiagram(false),/data-confirmed-channel="unknown"/);
});
test('a busy pump may show a moving mechanism while untracked liquid flow remains unknown',()=>{
  const status=baseStatus();
  status.devices[0].hardware={state:'busy',stale:false};
  const draw=fresh=>render(ApparatusDiagram,{items:itemsFor(status,fresh),runtime:status.runtime,fresh,onSelect:()=>{}});
  assert.equal([...draw(true).matchAll(/instrument-working/g)].length,1);
  assert.doesNotMatch(draw(true),/pipe-working/);
  assert.doesNotMatch(draw(false),/instrument-working|pipe-working/);
});
test('workstation details show real connection identity, last query and historical data distinction',()=>{
  const status=baseStatus();
  status.devices.push({id:'electrochemical_station',port_present:true,port:'/dev/synthetic-station',baudrate:115200,hardware:{state:'connected',stale:false,product:'EC110 synthetic',firmware:'synthetic-v1',queried_at:'2026-10-24T09:00:00Z'}});
  const items=equipmentDevices(templates,{electrochemical_station:{...cv,device:{...cv.device,execution_supported:true}}},status,true);
  assert.equal(equipmentSummary(items).connected,1);
  const html=render(EquipmentMonitor,{items,selectedItem:items.find(item=>item.id==='electrochemical_station'),fresh:true,role:'admin',measurement:{metadata:{id:'cv-synthetic',sequence_id:'synthetic',step_id:'cv',state:'completed',point_count:1},points:[[1,0,0,0]]}});
  assert.match(html,/已连接/);assert.match(html,/EC110 synthetic/);assert.match(html,/synthetic-v1/);
  assert.match(html,/最近设备查询/);assert.match(html,/历史测量记录/);assert.match(html,/不代表当前设备读数/);
});
