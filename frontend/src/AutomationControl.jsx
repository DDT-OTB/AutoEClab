import React, {useEffect,useRef,useState} from 'react';
import {LoaderCircle,LockKeyhole,Play,ShieldCheck,Square,X} from 'lucide-react';
import {appApi as api} from './api.js';
import {automationAvailability,executionLabels,preparedIsCurrent} from './automation.js';
import {operationLabels} from './model.js';

import {PreflightProgress} from './PreflightBlock.jsx';
import {canAutoAdvance,preflightInfo} from './preflight.js';

export function DebugExecutionConfirmation({prepared,currentSequence,pending,error,onClose,names,onStart,fresh=true}) {
  const [setup,setSetup]=useState(false),[discharge,setDischarge]=useState(false);
  const current=preparedIsCurrent(prepared,currentSequence);
  const steps=prepared.preview.plan.steps,homing=steps.some(step=>step.action==='initialize_syringe');
  return <div className="modal-backdrop automation-backdrop"><section className="modal automation-confirmation" role="dialog" aria-modal="true" aria-label="确认执行调试流程">
    <div className="modal-header"><h2><ShieldCheck size={19}/>执行调试流程</h2><button className="icon-button" onClick={onClose} disabled={pending} aria-label="关闭调试执行"><X size={18}/></button></div>
    <p className="automation-confirmation-intro">按编排顺序执行全部动作，不自动加入回零或排空。运行使用本次固定的方案与设备参数。</p>
    <div className="automation-plan-heading"><strong>{prepared.sequence.id}</strong><span>{prepared.sequence.blocks?.length ?? 1} 个功能块 · {steps.length} 个调试动作</span></div>
    {prepared.preview.warnings?.map((warning,index)=><p key={index} className="measurement-warning">{warning}</p>)}
    <p className="automation-notice">初始池内液量：{prepared.sequence.initial_cell_volume_ml ?? 0} mL；预计峰值：{prepared.preview.cell_safety?.peak_volume_ml ?? '—'} mL。</p>
    <ol className="automation-confirm-steps">{steps.map((step,index)=><li key={step.id}><span>{index+1}</span><div><strong>{names[step.device] || step.device}</strong><small>{operationLabels[step.operation] || step.action}{step.volume_ml!==undefined?' · '+step.volume_ml+' mL':''}{step.route?' · '+step.route:''}</small></div></li>)}</ol>
    <label className="preflight-acknowledgement"><input type="checkbox" checked={setup} disabled={pending || !fresh} onChange={event=>setSetup(event.target.checked)}/>已现场确认初始池内液量、液体来源、管路和接液条件与本次调试一致</label>
    {homing && <label className="preflight-acknowledgement"><input type="checkbox" checked={discharge} disabled={pending || !fresh} onChange={event=>setDischarge(event.target.checked)}/>已确认显式回零会排出注射器旧液，当前接液位置与余量允许机械回零</label>}
    {!current && <p className="automation-error" role="alert">载入的流程已修改，请关闭并重新校验。</p>}
    {error && <p className="automation-error" role="alert">{error}</p>}
    <div className="automation-dialog-actions"><button className="outline-button" onClick={onClose} disabled={pending}>返回</button><button className="primary-button" onClick={()=>onStart(setup,discharge)} disabled={pending || !fresh || !current || !setup || (homing && !discharge)}>{pending?<LoaderCircle size={15} className="refresh-spinning"/>:<Play size={15}/>}启动调试流程</button></div>
  </section></div>;
}

export function ExecutionConfirmation({prepared,currentSequence,pending,error,onClose,names,blockedReason,onAdvance,onStop,canStop,fresh=true,automatic=false}) {
  const [discharge,setDischarge]=useState(false),[empty,setEmpty]=useState(false);
  useEffect(()=>{setEmpty(false);},[prepared.run_id,prepared.cell_preparation?.run_id]);
  const current=preparedIsCurrent(prepared,currentSequence),phase=prepared.workflow?.phase || 'awaiting_start';
  const running=['homing','draining'].includes(phase),waiting=['awaiting_confirmation','ready_to_start'].includes(phase);
  const failed=['failed','stopped','unknown'].includes(phase),ready=phase==='awaiting_start' || ['ready_to_home','ready_to_drain'].includes(phase);
  const info=prepared.preview.preflight || preflightInfo(prepared.sequence);
  const mainSteps=prepared.preview.plan.steps;
  return <div className="modal-backdrop automation-backdrop"><section className="modal automation-confirmation" role="dialog" aria-modal="true" aria-label="确认执行实验流程">
    <div className="modal-header"><h2><ShieldCheck size={19}/>执行实验流程</h2><button className="icon-button" onClick={onClose} disabled={pending || running} aria-label="关闭流程执行"><X size={18}/></button></div>
    <p className="automation-confirmation-intro">前置功能块通过后，才会执行正式实验。每次运行都使用本次固定的方案与设备参数。</p>
    {prepared.preview.warnings?.map((warning,index)=><p key={index} className="measurement-warning">{warning}</p>)}
    <div className="automation-plan-heading"><strong>{prepared.sequence.id}</strong><span>前置准备 + {mainSteps.length} 个实验动作</span></div>
    <PreflightProgress info={info} calibration={prepared.calibration} cell={prepared.cell_preparation}>
      {waiting?<label className="preflight-acknowledgement"><input type="checkbox" checked={empty} disabled={pending || !fresh} onChange={event=>setEmpty(event.target.checked)}/>已现场检查，确认本次电解池已排空</label>:ready && !automatic?<label className="preflight-acknowledgement"><input type="checkbox" checked={discharge} disabled={pending || !fresh} onChange={event=>setDischarge(event.target.checked)}/>已确认本次回零排液和所选废液流路的接液条件安全</label>:null}
    </PreflightProgress>
    {running && <p className="automation-notice">前置功能块正在执行，完成后进入下一步骤。</p>}
    {waiting && <p className="automation-notice">流程暂停在人工确认步骤；确认后将开始后续实验动作。</p>}
    <details className="preflight-main-preview"><summary>后续实验动作 · {mainSteps.length} 步</summary><ol className="automation-confirm-steps">{mainSteps.map((step,index)=><li key={step.id}><span>{index+1}</span><div><strong>{names[step.device] || step.device}</strong><small>{operationLabels[step.operation] || step.action}{step.volume_ml!==undefined?' · '+step.volume_ml+' mL':''}{step.route?' · '+step.route:''}{step.rpm?' · '+step.rpm+' rpm':''}</small></div></li>)}</ol></details>
    {!current && <p className="automation-error" role="alert">载入的流程已修改，请关闭并重新准备。</p>}
    {(error || prepared.workflow?.message) && <p className="automation-error" role="alert">{error || prepared.workflow.message}</p>}
    <div className="automation-dialog-actions"><button className="outline-button" onClick={onClose} disabled={pending || running}>返回</button>{canStop && <button className="automation-stop" onClick={onStop}><Square size={14}/>停止当前步骤</button>}
      <button className="primary-button" disabled={pending || running || failed || !current || !fresh || (waiting?!empty:!discharge) || (!waiting && !ready)} onClick={()=>onAdvance(waiting?false:discharge,waiting?empty:false)}>{pending || running?<LoaderCircle size={15} className="refresh-spinning"/>:<Play size={15}/>} {waiting?'确认空池并继续流程':running?'前置步骤执行中…':prepared.workflow?.authorized?'继续前置流程':'开始前置功能块'}</button>
    </div>
  </section></div>;
}

export default function AutomationControl({user,sequence,status,health,fresh,errors,measurementCount,busy,names,onPlan,onRefresh,onStarted,onRestore,onProgress,templates={},visible=true,hasSequence=true}) {
  const [prepared,setPrepared]=useState(null),[pending,setPending]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [automatic,setAutomatic]=useState(false),[resumable,setResumable]=useState(null);
  const pendingRef=useRef(false),currentSequence=useRef(sequence);currentSequence.current=sequence;
  const preparedRef=useRef(prepared);preparedRef.current=prepared;
  const completedRuns=useRef(new Set());
  const current=status.execution?.current,enabled=status.execution?.enabled ?? health?.hardware_execution_enabled;
  const availability=automationAvailability({user,enabled,fresh,current,runtime:status.runtime,errors:hasSequence?errors:['请先加载或导入一个实验流程。'],measurementCount,measurementEnabled:health?.measurement_execution_enabled,busy:busy || !!pending});
  useEffect(()=>{if(current?.run_id)setNotice('');},[current?.run_id]);
  function accept(value) {
    if(preparedRef.current?.run_id!==value.run_id)return;
    if(completedRuns.current.has(value.run_id))return;
    onProgress?.(value);
    if(value.workflow?.main_started){completedRuns.current.add(value.run_id);onStarted?.(value.sequence,value.run_id);preparedRef.current=null;setPrepared(null);setAutomatic(false);setNotice(value.workflow.phase==='failed'?'流程启动或执行失败，请查看运行反馈。':'已提交流程，等待控制器预检与运行反馈。');}
    else setPrepared(previous=>previous?.run_id===value.run_id?{...previous,...value}:value);
  }
  useEffect(()=>{
    if(!prepared)return;
    let disposed=false,fetching=false;
    const poll=async()=>{if(fetching)return;fetching=true;try{const value=await api.preparation(prepared.run_id);if(!disposed)accept(value);}catch(failure){if(!disposed){setError(failure.message);setAutomatic(false);setPrepared(previous=>previous?{...previous,workflow:{...previous.workflow,phase:'unknown',needs_advance:false}}:previous);}}finally{fetching=false;}};
    poll();const timer=setInterval(poll,1000);return()=>{disposed=true;clearInterval(timer);};
  },[prepared?.run_id]);
  useEffect(()=>{
    const id=current?.target_run_id;if(!id || prepared || user?.role!=='admin'){setResumable(null);return;}
    let disposed=false;
    api.preparation(id).then(value=>{if(!disposed && value.workflow?.authorized && !value.workflow.main_started && value.workflow.phase!=='failed')setResumable(value);}).catch(()=>{});
    return()=>{disposed=true;};
  },[current?.target_run_id,prepared?.run_id]);
  async function prepare() {
    if(pendingRef.current || !hasSequence || !availability.allowed)return;
    pendingRef.current=true;setPending('prepare');setError('');setNotice('');setAutomatic(false);
    try{const result=await api.prepareExecution(sequence);if(!preparedIsCurrent(result,currentSequence.current)){setError('载入流程已变化，请重新准备。');return;}preparedRef.current=result;setPrepared(result);onProgress?.(result);onPlan(result.preview);}
    catch(failure){setError(failure.message);}finally{pendingRef.current=false;setPending('');}
  }
  async function advance(discharge=false,empty=false) {
    if(pendingRef.current || user?.role!=='admin' || !fresh || !preparedIsCurrent(prepared,currentSequence.current))return;
    pendingRef.current=true;setPending('advance');setError('');
    try{const result=await api.advanceWorkflow(prepared.run_id,prepared.version,discharge,empty);accept(result);setAutomatic(!result.workflow?.main_started);await onRefresh();}
    catch(failure){setError(failure.message);setAutomatic(false);}finally{pendingRef.current=false;setPending('');}
  }
  async function startDebug(setup,discharge) {
    if(pendingRef.current || user?.role!=='admin' || !fresh || !preparedIsCurrent(prepared,currentSequence.current))return;
    pendingRef.current=true;setPending('debug-start');setError('');
    try {
      await api.startDebugExecution(prepared.run_id,prepared.version,setup,discharge);
      accept(await api.preparation(prepared.run_id));await onRefresh();
    }catch(failure){setError(failure.message);}
    finally{pendingRef.current=false;setPending('');}
  }
  useEffect(()=>{if(!pending && canAutoAdvance(prepared,sequence,fresh,automatic))void advance();},[prepared?.workflow?.phase,prepared?.workflow?.needs_advance,automatic,fresh,pending,sequence]);
  async function stop() {
    if(pendingRef.current || !current?.can_stop || user?.role!=='admin')return;
    pendingRef.current=true;setPending('stop');setError('');setAutomatic(false);
    try{const result=await api.stopExecution(current.run_id);setNotice(result.stop_requested?'已请求停止，等待控制器清理反馈。':'当前控制器已结束。');await onRefresh();}
    catch(failure){setError(failure.message);}finally{pendingRef.current=false;setPending('');}
  }
  async function restore() {
    if(pendingRef.current || !resumable || user?.role!=='admin')return;
    pendingRef.current=true;setPending('restore');setError('');setAutomatic(false);
    try{const value=await api.preparation(resumable.run_id);onRestore?.(value);preparedRef.current=value;setPrepared(value);onProgress?.(value);setResumable(null);}
    catch(failure){setError(failure.message);}finally{pendingRef.current=false;setPending('');}
  }
  const active=fresh && current?.controller_alive;
  const displayTitle=!fresh?'运行状态连接待恢复':current?.operation==='calibrate_syringe'?'正在执行前置回零步骤':current?.operation==='drain_cell'?'正在执行前置排空步骤':current?executionLabels[current.state] || '运行状态待确认':enabled?'实验流程控制':'配置与离线预演';
  return <>{visible && <section className={'automation-control '+(active?'automation-active':'')} aria-label="自动化运行控制">
    <div className="automation-control-main"><span className="automation-shield"><ShieldCheck size={20}/></span><div><strong>{displayTitle}</strong><p>{active?current.sequence_id+' · 操作人 '+current.started_by:hasSequence?'已载入流程：'+sequence.id:'尚未载入实验流程'}</p></div><div className="automation-control-buttons">
      {user?.role==='admin'?<>{current?.can_stop && <button className="automation-stop" onClick={stop} disabled={!!pending || current.stop_requested}><Square size={14}/>停止当前实验</button>}{resumable && <button className="outline-button" onClick={restore} disabled={!!pending || !fresh}>恢复未完成流程</button>}<button className="primary-button" onClick={prepare} disabled={!availability.allowed} title={availability.reason}><Play size={15}/>{pending==='prepare'?'后端校验中…':'运行已载入流程'}</button></>:<span className="automation-viewer"><LockKeyhole size={13}/>只读监测</span>}
    </div></div><p className="automation-control-note">{availability.reason}</p>
    {current?.message && !active && fresh && <p className={'automation-result '+(current.state==='failed'?'is-failed':'')} role="status"><span>{current.message}</span></p>}
    {current?.cleanup_failed && <p className="automation-error" role="alert">设备停止回读失败，请现场确认。</p>}
    {notice && <p className="automation-notice" role="status">{notice}</p>}{error && !prepared && <p className="automation-error" role="alert">{error}</p>}
  </section>}
    {prepared && user?.role==='admin' && (prepared.operation==='debug_sequence'?<DebugExecutionConfirmation key={prepared.run_id} prepared={prepared} currentSequence={sequence} pending={!!pending} error={error} onStart={startDebug} onClose={()=>{preparedRef.current=null;setPrepared(null);setError('');}} names={names} fresh={fresh}/>:<ExecutionConfirmation key={prepared.run_id} prepared={prepared} currentSequence={sequence} pending={!!pending} error={error} onAdvance={advance} onClose={()=>{preparedRef.current=null;setPrepared(null);setAutomatic(false);setError('');}} names={names} onStop={stop} canStop={current?.target_run_id===prepared.run_id && current?.can_stop && !current?.stop_requested} fresh={fresh} automatic={automatic}/>)}
  </>;
}
