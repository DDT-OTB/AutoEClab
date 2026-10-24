import React, { useEffect,useMemo,useRef,useState } from 'react';
import ApparatusDiagram from './ApparatusDiagram.jsx';
import EquipmentMonitor from './EquipmentMonitor.jsx';
import { equipmentDevices } from './equipment.js';

export default function EquipmentOverview({ templates, measurementTemplates, status, fresh, streaming, onRefresh, refreshing, role,onOpenMeasurement }) {
  const [selectedId,setSelectedId]=useState(null);
  const measurementFocus=useRef(null);
  const measuring=fresh && status.runtime?.active_step?.device==='electrochemical_station';
  const focusKey=measuring?status.runtime?.run_id+':'+status.runtime?.active_step?.id:null;
  useEffect(()=>{if(focusKey && measurementFocus.current!==focusKey)setSelectedId('electrochemical_station');measurementFocus.current=focusKey;},[focusKey]);
  const items=useMemo(()=>equipmentDevices(templates,measurementTemplates,status,fresh),[templates,measurementTemplates,status,fresh]);
  const selectedItem=items.find(item=>item.id===selectedId) || null;
  return <section className="equipment-overview" aria-label="装置流程与设备监测">
    <div className="section-heading equipment-heading"><div><span className="eyebrow">EQUIPMENT WORKSPACE</span><h2>装置流程与设备监测</h2></div><div className="equipment-feed-status"><span className={'status-dot '+(fresh?'green':'gray')}/>{streaming && fresh?'实时推送':fresh?'自动更新':'等待连接'}<span className="divider">/</span>{status.sampled_at?'更新于 '+new Date(status.sampled_at).toLocaleTimeString('zh-CN'):'连接中'}</div></div>
    <div className={'device-grid equipment-workspace'+(selectedId==='electrochemical_station'?' station-selected':'')}>
      <ApparatusDiagram items={items} runtime={status.runtime} fresh={fresh} selectedId={selectedItem?.id} onSelect={setSelectedId}/>
      <EquipmentMonitor items={items} selectedItem={selectedItem} fresh={fresh} onSelect={setSelectedId} onRefresh={onRefresh} refreshing={refreshing || !!status.device_scan?.in_progress} role={role} measurement={status.measurement} onOpenMeasurement={onOpenMeasurement}/>
    </div>
  </section>;
}
