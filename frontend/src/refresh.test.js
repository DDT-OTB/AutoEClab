import test from 'node:test';
import assert from 'node:assert/strict';
import { deviceActivity } from './monitor.js';
import { refreshAllStatus, refreshMessage } from './refresh.js';

test('recent hardware results are shown and expired or disconnected readings are hidden', () => {
  const device={hardware:{state:'idle',stale:false}};
  assert.equal(deviceActivity(device,{state:'idle'},true).label,'空闲');
  assert.equal(deviceActivity({...device,hardware:{state:'busy',stale:false}},{state:'idle'},true).label,'工作中');
  assert.equal(deviceActivity({...device,hardware:{state:'idle',stale:true}},{state:'idle'},true).state,'unknown');
  assert.equal(deviceActivity(device,{state:'idle'},false).state,'unknown');
  assert.equal(deviceActivity(device,{state:'unresponsive',controller_alive:true},true).state,'unknown');
});
test('controller execution takes precedence over previous hardware readings', () => {
  const device={activity:{state:'executing',action:'drain'},hardware:{state:'idle',stale:false}};
  assert.equal(deviceActivity(device,{state:'running'},true).label,'正在抽出');
});
test('admin refresh uses the protected endpoint and viewer refresh only reads data', async () => {
  let hardwareReads=0;
  const api={refreshStatus:async()=>{hardwareReads++;return {scan:{state:'completed'}};},
    health:async()=>({ok:true}),status:async()=>({devices:[]}),runs:async()=>({runs:[]}),templates:async()=>({templates:{}})};
  await refreshAllStatus(api,true);
  assert.equal(hardwareReads,1);
  const result=await refreshAllStatus(api,false);
  assert.equal(hardwareReads,1);
  assert.equal(result.scan.state,'display_only');
});
test('skipped and failed refreshes never claim a successful hardware read', () => {
  assert.match(refreshMessage({state:'skipped'},[]),/未发送硬件查询/);
  assert.match(refreshMessage({state:'failed'},[]),/未完成/);
});
test('station identity is counted as a reply and prior controller errors do not hide fresh readings',()=>{
  assert.match(refreshMessage({state:'completed'},[{hardware:{state:'connected',stale:false}},{hardware:{state:'error',error_code:7,stale:false}},{hardware:{state:'port_missing',stale:false}}]),/2 台收到应答，1 台/);
  const device={activity:{state:'error'},hardware:{state:'connected',stale:false}};
  assert.equal(deviceActivity(device,{state:'error',controller_alive:false},true).label,'已连接');
});
