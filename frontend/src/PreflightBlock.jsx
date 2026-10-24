import React from 'react';
import {CheckCircle2,LockKeyhole,ShieldCheck} from 'lucide-react';
import policy from '../../config/safety_policy.json' with {type:'json'};

export const preflightStepLabels={home_if_used:'注射泵回零与状态核验',drain_to_waste:'电解池启动前排空',confirm_empty:'现场确认空池'};
export function PreflightEditor({block,onChange,safetyPolicy=policy}) {
  const route=block.steps[1].route;
  const direct=safetyPolicy.cell.drain_mode==='direct';
  return <div className="preflight-editor"><p className="preflight-fixed-note"><LockKeyhole size={14}/>必需的前置功能块，固定在实验开头，步骤及顺序由系统校验。</p>
    <ol><li><strong>按需回零并核验注射泵</strong><p>正式实验使用注射泵时执行机械回零，核验设备身份、零位、阀位、缓冲与就绪状态。</p></li>
      <li><strong>{direct?'直接排空电解池':'切阀并排空电解池'}</strong>{direct?<p>排液泵 2 → 废液瓶，不切换注射泵入口阀。</p>:<label className="parameter-field"><span>启动排空的废液流路</span><select value={route} aria-label="前置排空流路" onChange={event=>{const steps=structuredClone(block.steps);steps[1].route=event.target.value;onChange({steps});}}>{safetyPolicy.cell.allowed_waste_routes.map(value=><option key={value} value={value}>{value==='waste_1'?'废液 1':'废液 2'}</option>)}</select></label>}<p>标称抽出 {safetyPolicy.cell.predrain_volume_ml} mL，计时设置继承排液泵配置。</p></li>
      <li><strong>现场确认空池后继续实验</strong><p>执行到这里会暂停，管理员检查电解池后确认。方案文件不保存“已经排空”的确认结果。</p></li></ol>
  </div>;
}
function stage(state,doneLabel) {
  return ['passed','consumed'].includes(state)?{type:'passed',label:doneLabel}:state==='running'?{type:'running',label:'执行中'}:['failed','invalidated','unknown'].includes(state)?{type:'failed',label:'未通过'}:{type:'pending',label:'待执行'};
}
export function PreflightProgress({info,calibration,cell,children}) {
  const home=info?.requires_syringe===false?{type:'skipped',label:'本方案无需执行'}:stage(calibration?.state,'已核验');
  const drain=['awaiting_confirmation','passed','consumed'].includes(cell?.state)?{type:'passed',label:'排液已完成'}:stage(cell?.state,'排液已完成');
  const confirm=['passed','consumed'].includes(cell?.state)?{type:'passed',label:'已人工确认'}:{type:'pending',label:cell?.state==='awaiting_confirmation'?'等待现场确认':'待排空完成'};
  return <section className="preflight-progress" aria-label="前置准备功能块执行状态"><div className="preflight-progress-heading"><ShieldCheck size={18}/><strong>前置准备功能块</strong><code>{info?.block_id}</code></div>
    <ol>{[[home,'注射泵回零与核验',info?.requires_syringe===false?'正式流程未使用注射泵。':'机械回零、设备身份及就绪状态核验。'],[drain,'电解池排空',(info?.route==='waste'?'废液瓶（直排）':info?.route==='waste_2'?'废液 2':'废液 1')+' · 标称 '+(info?.predrain_volume_ml ?? policy.cell.predrain_volume_ml)+' mL'],[confirm,'人工确认空池','计时排液不能代替现场确认，当前没有液位实测。']].map(([value,label,note],index)=><li key={label} className={'preflight-stage state-'+value.type}><span>{value.type==='passed'?<CheckCircle2 size={17}/>:index+1}</span><div><strong>{label}</strong><p>{note}</p></div><small>{value.label}</small></li>)}</ol>{children}
  </section>;
}
