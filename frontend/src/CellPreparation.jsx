import React,{useState} from 'react';
import {CheckCircle2,Droplets,Square} from 'lucide-react';
import {cellPreparationLabels,defaultSafetyPolicy} from './cellSafety.js';

export default function CellPreparation({state,policy=defaultSafetyPolicy,busy,onDrain,onConfirm,onStop,canStop,disabled}) {
  const [route,setRoute]=useState(''),[discharge,setDischarge]=useState(false),[empty,setEmpty]=useState(false);
  const value=state || {required:true,state:'required'};
  const running=busy || value.state==='running';
  const locked=['running','awaiting_confirmation','passed'].includes(value.state);
  return <section className={'syringe-calibration cell-preparation state-'+value.state} aria-label="本次实验启动前排空"><div><strong>启动前排空与空池确认</strong><span role="status">{cellPreparationLabels[value.state] || '未排空准备'}</span></div>
    <p>每次实验正式启动前，先切换废液流路，再由排液泵按 {policy.cell.predrain_volume_ml} mL 及模板余量排液。完成后需管理员现场确认电解池已排空；当前没有液位传感器。</p>
    {value.state==='passed'?<p className="calibration-passed"><CheckCircle2 size={15}/>已由管理员人工确认空池，正式流程从预计 0 mL 开始。此记录只对本次实验有效。</p>:<>
      <label className="cell-route-field">排空废液流路<select aria-label="启动前排空废液流路" value={route || value.route || ''} onChange={event=>setRoute(event.target.value)} disabled={disabled || running || locked}><option value="">请选择接收废液的容器</option>{policy.cell.allowed_waste_routes.map(name=><option key={name} value={name}>{name==='waste_1'?'废液 1 · 通道 5':'废液 2 · 通道 6'}</option>)}</select></label>
      {!running && <label><input type="checkbox" checked={discharge} onChange={event=>setDischarge(event.target.checked)} disabled={disabled}/>已确认所选废液流路和接液容器允许本次排液</label>}
      <div className="calibration-actions"><button className="outline-button" onClick={()=>{onDrain(route || value.route,discharge);setDischarge(false);setEmpty(false);}} disabled={disabled || running || !discharge || !(route || value.route)}><Droplets size={14}/>{running?'正在执行排空…':['awaiting_confirmation','failed','invalidated'].includes(value.state)?'再次执行排空':'执行本次排空'}</button>{canStop && <button className="automation-stop" onClick={onStop}><Square size={13}/>停止排空</button>}</div>
      {value.state==='awaiting_confirmation' && <div className="empty-cell-confirm"><p>计时排液已完成，尚不能据此断言空池。如果仍有残液，请再次排空。</p><label><input type="checkbox" checked={empty} onChange={event=>setEmpty(event.target.checked)} disabled={disabled || running}/>已现场确认本次电解池为空</label><button className="primary-button" onClick={()=>onConfirm(empty)} disabled={disabled || running || !empty}>确认空池</button></div>}
    </>}
    {value.message && <p>{value.message}</p>}
  </section>;
}
