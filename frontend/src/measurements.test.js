import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {fileURLToPath} from 'node:url';
import React from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {chartData,chartDomain,displayTrend} from './measurements.js';
import {automationAvailability} from './automation.js';
import {workspacePage} from './workspace.js';
import {createApi} from './api.js';

const require=createRequire(import.meta.url);
const compiled=require('esbuild').buildSync({stdin:{contents:"export {default as Charts,TraceChart} from './src/MeasurementChart.jsx'; export {default as DataManagement} from './src/DataManagement.jsx';",loader:'jsx',resolveDir:fileURLToPath(new URL('../',import.meta.url))},bundle:true,platform:'node',format:'cjs',write:false,external:['react','lucide-react'],loader:{'.css':'empty'}}).outputFiles[0].text;
const module={exports:{}};new Function('require','module','exports',compiled)(require,module,module.exports);
const {Charts,TraceChart,DataManagement}=module.exports;
const render=(Component,props)=>renderToStaticMarkup(React.createElement(Component,props));
const points=[[1,1,-.1,1e-6],[2,2,.1,-1e-6],[3,3,0,0]];
const metadata={id:'cv-'+'a'.repeat(32),state:'completed',sequence_id:'synthetic_cv',step_id:'cv_step',point_count:3,sample_count:300,stop_confirmed:true,statistics:{potential_v_min:-.1,potential_v_max:.1}};

test('CV and current-time charts use correct units and retain acquisition order',()=>{
  assert.deepEqual(chartData(points).map(p=>p.x),[-.1,.1,0]);
  assert.deepEqual(chartData(points,'time').map(p=>p.x),[1,2,3]);
  assert.deepEqual(chartData(points).map(p=>p.y),[1,-1,0]);
  assert.deepEqual(chartData([[1,NaN,0,0],[1,0,0,Infinity]]),[]);
  assert.deepEqual(chartData(null),[]);
  assert.deepEqual(chartDomain([NaN,Infinity]),[0,1]);
  assert.ok(chartDomain([1,1])[0]<1);
});
test('display smoothing never mutates data, reorders potentials or averages across scan reversals',()=>{
  const input=chartData([[1,1,-.1,1e-6],[2,2,0,1e-6],[3,3,.1,1e-6],[4,4,0,10e-6],[5,5,-.1,10e-6]]);
  const before=structuredClone(input),trend=displayTrend(input,'cv',3);
  assert.deepEqual(input,before);
  assert.equal(trend[0].y,1);assert.equal(trend[1].y,1);
  assert.equal(trend.at(-1).y,10);
  assert.deepEqual(trend.map(p=>p.index),[1,2,3,3,4,5]);
  assert.deepEqual(displayTrend([]),[]);
});
test('measurement results show both actual-data charts with provenance and stop status',()=>{
  const html=render(Charts,{metadata,points});
  assert.match(html,/CV · 电位—电流/);assert.match(html,/时间—电流/);
  assert.match(html,/名义换算值/);assert.match(html,/服务器接收时间/);
  assert.match(html,/已确认/);assert.match(html,/polyline/);
  assert.doesNotMatch(html,/开始测量|启动仪器|运行实验/);
});
test('empty or disconnected charts never claim live acquisition data',()=>{
  const empty=render(TraceChart,{points:[],title:'CV'});
  assert.match(empty,/等待仪器返回/);assert.doesNotMatch(empty,/measurement-svg-wrap/);
  assert.match(render(Charts,{metadata:{...metadata,state:'running'},points,live:true,fresh:false}),/最后接收的数据/);
});
test('data workspace remains read-only and searchable and has no hardware control',()=>{
  assert.equal(workspacePage('#data'),'data');
  const html=render(DataManagement,{onSelect:()=>{}});
  assert.match(html,/实验数据管理/);assert.match(html,/搜索测量数据/);assert.match(html,/筛选采集状态/);
  assert.doesNotMatch(html,/运行实验|启动仪器|开始测量|停止实验/);
});
test('CV capability never bypasses administrator, freshness or general execution policy',()=>{
  const base={user:{role:'admin'},enabled:true,fresh:true,runtime:{state:'idle'},measurementCount:1,measurementEnabled:true};
  assert.equal(automationAvailability(base).allowed,true);
  for(const patch of [{user:{role:'viewer'}},{enabled:false},{fresh:false},{measurementEnabled:false},{errors:['invalid parameters']}])
    assert.equal(automationAvailability({...base,...patch}).allowed,false);
});
test('dataset query is encoded and downloads use the authenticated server route',async()=>{
  const calls=[];
  const api=createApi('',{fetcher:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>({measurements:[]}),blob:async()=>new Blob(['data']),headers:{get:()=> 'attachment; filename="synthetic.csv"'}};}});
  await api.measurements({query:'a&b',state:'completed',limit:30,offset:0});
  assert.match(calls[0].url,/query=a%26b/);
  assert.equal(calls[0].options.credentials,'same-origin');
  const file=await api.downloadMeasurement(metadata.id,'csv');
  assert.equal(file.filename,'synthetic.csv');assert.match(calls[1].url,/download\?format=csv/);
});
