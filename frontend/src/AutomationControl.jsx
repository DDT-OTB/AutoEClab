import React, {useEffect,useRef,useState} from 'react';
import {AlertCircle, CheckCircle2, Clock3, LoaderCircle, LockKeyhole, Play, ShieldCheck, Square, X} from 'lucide-react';
import {appApi as api} from './api.js';
import {automationAvailability,executionLabels,preparedIsCurrent} from './automation.js';
import {fluidLabels,formatDuration,operationLabels} from './model.js';

export function ExecutionConfirmation({prepared,currentSequence,pending,error,onStart,onClose,names,blockedReason}) {
  const current=preparedIsCurrent(prepared,currentSequence);
  const steps=prepared.preview.plan.steps;
  return <div className="modal-backdrop automation-backdrop"><section className="modal automation-confirmation" role="dialog" aria-modal="true" aria-label="确认启动自动化实验">
    <div className="modal-header"><h2><ShieldCheck size={19}/>确认本次实验</h2><button className="icon-button" onClick={onClose} disabled={pending} aria-label="关闭运行确认"><X size={18}/></button></div>
    <p className="automation-confirmation-intro">确认后，服务器将实际操作下列泵阀。运行使用本次固定的 JSON 与设备参数，编排中的后续修改不会改变本次任务。</p>
    <div className="automation-plan-heading"><strong>{prepared.sequence.id}</strong><span>{steps.length} 步 · 计时动作约 {formatDuration(prepared.preview.estimated_timed_seconds)}</span></div>
    <ol className="automation-confirm-steps">{steps.map((step,index)=><li key={step.id}><span>{String(index+1).padStart(2,'0')}</span><div><strong>{names[step.device] || step.device}</strong><small>{operationLabels[step.operation] || step.action}{step.fluid?' · '+(fluidLabels[step.fluid] || step.fluid):''}{step.volume_ml!==undefined?' · '+step.volume_ml+' mL':''}{step.route?' · '+step.route:''}{step.channel?' · 通道 '+step.channel:''}{step.rpm?' · '+step.rpm+' rpm':''}</small></div></li>)}</ol>
    <p className="automation-confirmation-note"><Clock3 size={14}/>体积按标定与步数换算，进度为估算。控制器会先预检所有参与设备，任何一步失败都会中止流程并尝试停止设备。</p>
    {!current && <p className="automation-error" role="alert">编排内容已修改，请关闭并重新准备运行。</p>}
    {error && <p className="automation-error" role="alert">{error}</p>}
    {blockedReason && !pending && <p className="automation-error" role="alert">{blockedReason}</p>}
    <div className="automation-dialog-actions"><button className="outline-button" onClick={onClose} disabled={pending}>返回检查</button><button className="primary-button" onClick={onStart} disabled={pending || !current || !!blockedReason}>{pending?<LoaderCircle size={15} className="refresh-spinning"/>:<Play size={15}/>} {pending?'正在提交启动…':'确认启动设备'}</button></div>
  </section></div>;
}

export default function AutomationControl({user,sequence,status,health,fresh,errors,measurementCount,busy,names,onPlan,onRefresh,onStarted}) {
  const [prepared,setPrepared]=useState(null),[pending,setPending]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const pendingRef=useRef(false), currentSequence=useRef(sequence);
  currentSequence.current=sequence;
  const current=status.execution?.current;
  const enabled=status.execution?.enabled ?? health?.hardware_execution_enabled;
  const availability=automationAvailability({user,enabled,fresh,current,runtime:status.runtime,errors,measurementCount,busy:busy || !!pending});
  useEffect(()=>{if(current?.run_id) setNotice('');},[current?.run_id]);
  async function prepare() {
    if(pendingRef.current || !availability.allowed) return;
    pendingRef.current=true;setPending('prepare');setError('');setNotice('');
    try {const result=await api.prepareExecution(sequence);setPrepared(result);onPlan(result.preview);}
    catch(failure) {setError(failure.message);}
    finally {pendingRef.current=false;setPending('');}
  }
  async function start() {
    if(pendingRef.current || !availability.allowed || !preparedIsCurrent(prepared,currentSequence.current) || user?.role!=='admin') return;
    pendingRef.current=true;setPending('start');setError('');
    try {await api.startExecution(prepared.run_id,prepared.version);onStarted?.(prepared.sequence);setPrepared(null);setNotice('启动请求已接受，正在等待控制器预检与运行反馈。');await onRefresh();}
    catch(failure) {setError(failure.message);await onRefresh();}
    finally {pendingRef.current=false;setPending('');}
  }
  async function stop() {
    if(pendingRef.current || !current?.can_stop || user?.role!=='admin') return;
    pendingRef.current=true;setPending('stop');setError('');setNotice('');
    try {const result=await api.stopExecution(current.run_id);setNotice(result.stop_requested?'已提交停止请求，等待控制器完成停泵与关闭清理。':'控制器已经结束，请查看运行结果。');await onRefresh();}
    catch(failure) {setError(failure.message);}
    finally {pendingRef.current=false;setPending('');}
  }
  const active=fresh && current?.controller_alive;
  return <section className={'automation-control '+(active?'automation-active':'')} aria-label="自动化运行控制">
    <div className="automation-control-main"><span className="automation-shield"><ShieldCheck size={20}/></span><div><strong>{!fresh?'运行状态连接待恢复':current?executionLabels[current.state] || '运行状态待确认':enabled?'管理员自动化控制':'配置与离线预演'}</strong><p>{active?current.sequence_id+' · 操作人 '+current.started_by:'当前编排：'+sequence.id}</p></div><div className="automation-control-buttons">
      {user?.role==='admin' ? <>{current?.can_stop && <button className="automation-stop" onClick={stop} disabled={!!pending || current.stop_requested}><Square size={14}/>{pending==='stop' || current.stop_requested?'等待停止反馈…':'停止当前实验'}</button>}<button className="primary-button" onClick={prepare} disabled={!availability.allowed} title={availability.reason}>{pending==='prepare'?<LoaderCircle size={15} className="refresh-spinning"/>:<Play size={15}/>} {pending==='prepare'?'后端校验中…':'运行当前编排'}</button></> : <span className="automation-viewer"><LockKeyhole size={13}/>只读监测</span>}
    </div></div>
    <p className="automation-control-note">{availability.reason}</p>
    {current?.message && !active && fresh && <p className={'automation-result '+(current.state==='failed'?'is-failed':'')} role="status">{current.state==='failed'?<AlertCircle size={14}/>:<CheckCircle2 size={14}/>}<span>{current.message}</span></p>}
    {current?.cleanup_failed && <p className="automation-error" role="alert">设备停止回读失败，请现场确认。界面不会将其显示为已安全停止。</p>}
    {notice && <p className="automation-notice" role="status">{notice}</p>}{error && !prepared && <p className="automation-error" role="alert">{error}</p>}
    {prepared && user?.role==='admin' && <ExecutionConfirmation prepared={prepared} currentSequence={sequence} pending={pending==='start'} error={error} onStart={start} onClose={()=>{setPrepared(null);setError('');}} names={names} blockedReason={availability.allowed?null:availability.reason}/>}
  </section>;
}
