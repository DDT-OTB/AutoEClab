import React, {useEffect,useRef,useState} from 'react';
import {AlertCircle, CheckCircle2, Clock3, LoaderCircle, LockKeyhole, Play, RotateCcw, ShieldCheck, Square, X} from 'lucide-react';
import {appApi as api} from './api.js';
import {automationAvailability,calibrationAllowsStart,calibrationLabels,executionLabels,preparedIsCurrent} from './automation.js';
import {fluidLabels,formatDuration,operationLabels} from './model.js';
import CellPreparation from './CellPreparation.jsx';
import {cellAllowsStart,cellPreparationLabels,cellVolumes} from './cellSafety.js';
import {flattenSequence} from './workflow.js';

export function ExecutionConfirmation({prepared,currentSequence,pending,error,onStart,onClose,names,blockedReason,calibration,onCalibrate,calibrationBusy,onStop,canStop,cellPreparation,onDrain,onEmptyConfirm,cellBusy,canStopDrain,fresh=true}) {
  const [confirmDischarge,setConfirmDischarge]=useState(false);
  const checked=calibration || prepared.calibration;
  const calibrationReady=calibrationAllowsStart(checked);
  const cell=cellPreparation || prepared.cell_preparation;
  const preflightRunning=calibrationBusy || checked?.state==='running' || cellBusy || cell?.state==='running';
  const current=preparedIsCurrent(prepared,currentSequence);
  const steps=prepared.preview.plan.steps;
  return <div className="modal-backdrop automation-backdrop"><section className="modal automation-confirmation" role="dialog" aria-modal="true" aria-label="确认启动自动化实验">
    <div className="modal-header"><h2><ShieldCheck size={19}/>确认本次实验</h2><button className="icon-button" onClick={onClose} disabled={pending || preflightRunning} aria-label="关闭运行确认"><X size={18}/></button></div>
    <p className="automation-confirmation-intro">确认后，服务器将实际操作下列泵阀。运行使用本次固定的 JSON 与设备参数，编排中的后续修改不会改变本次任务。</p>
    <div className="automation-plan-heading"><strong>{prepared.sequence.id}</strong><span>{steps.length} 步 · 计时动作约 {formatDuration(prepared.preview.estimated_timed_seconds)}</span></div>
    <ol className="automation-confirm-steps">{steps.map((step,index)=><li key={step.id}><span>{String(index+1).padStart(2,'0')}</span><div><strong>{names[step.device] || step.device}</strong><small>{operationLabels[step.operation] || step.action}{step.fluid?' · '+(fluidLabels[step.fluid] || step.fluid):''}{step.volume_ml!==undefined?' · '+step.volume_ml+' mL':''}{step.route?' · '+step.route:''}{step.channel?' · 通道 '+step.channel:''}{step.rpm?' · '+step.rpm+' rpm':''}</small></div></li>)}</ol>
    {checked?.required && <section className={'syringe-calibration state-'+checked.state} aria-label="本次实验注射泵回零校验"><div><strong>注射泵回零校验</strong><span role="status">{calibrationLabels[checked.state] || '未校验'}</span></div><p>每次实验都需先完成本次机械回零、零位与状态核验。回零会移动活塞并改变阀位，可能排出残留液体。重新上电后需重新准备校验。</p>
      {checked.state==='passed'?<p className="calibration-passed"><CheckCircle2 size={15}/>本次校验通过，零位 0、缓冲区为空且设备已就绪。此记录只供本次启动使用。</p>:<><label><input type="checkbox" checked={confirmDischarge} onChange={event=>setConfirmDischarge(event.target.checked)} disabled={calibrationBusy || checked.state==='running' || cellBusy || cell?.state==='running' || !fresh}/>已确认本次回零排液安全，管路及接液容器已就绪</label><div className="calibration-actions"><button className="outline-button" onClick={()=>{onCalibrate(confirmDischarge);setConfirmDischarge(false);}} disabled={!confirmDischarge || pending || calibrationBusy || checked.state==='running' || cellBusy || cell?.state==='running' || !fresh || !current}><RotateCcw size={14}/>{calibrationBusy || checked.state==='running'?'正在回零校验…':'执行本次回零校验'}</button>{canStop && <button className="automation-stop" onClick={onStop}><Square size={13}/>停止回零</button>}</div></>}
      {checked.message && checked.state!=='passed' && <p>{checked.message}</p>}
    </section>}
    <CellPreparation state={cell} policy={prepared.preview.safety_policy} busy={cellBusy} onDrain={onDrain} onConfirm={onEmptyConfirm} onStop={onStop} canStop={canStopDrain} disabled={pending || calibrationBusy || checked?.state==='running' || !current || !fresh}/>
    {prepared.preview.cell_safety && <p className="automation-confirmation-note">正式流程按空池起始，预计峰值 {prepared.preview.cell_safety.peak_volume_ml} / {prepared.preview.cell_safety.capacity_ml} mL。水、硫酸、电解液统一计入总量。</p>}
    <p className="automation-confirmation-note"><Clock3 size={14}/>体积按标定与步数换算，进度为估算。控制器会先预检所有参与设备，任何一步失败都会中止流程并尝试停止设备。</p>
    {!current && <p className="automation-error" role="alert">编排内容已修改，请关闭并重新准备运行。</p>}
    {error && <p className="automation-error" role="alert">{error}</p>}
    {blockedReason && !pending && <p className="automation-error" role="alert">{blockedReason}</p>}
    <div className="automation-dialog-actions"><button className="outline-button" onClick={onClose} disabled={pending || preflightRunning}>返回检查</button><button className="primary-button" onClick={onStart} disabled={pending || !current || !!blockedReason || !calibrationReady || !cellAllowsStart(cell)}>{pending?<LoaderCircle size={15} className="refresh-spinning"/>:<Play size={15}/>} {pending?'正在提交启动…':'确认启动设备'}</button></div>
  </section></div>;
}

export default function AutomationControl({user,sequence,status,health,fresh,errors,measurementCount,busy,names,onPlan,onRefresh,onStarted,templates={}}) {
  const [prepared,setPrepared]=useState(null),[pending,setPending]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [calibration,setCalibration]=useState(null);
  const [cellPreparation,setCellPreparation]=useState(null);
  const drainRequest=useRef(null);
  const pendingRef=useRef(false), currentSequence=useRef(sequence);
  currentSequence.current=sequence;
  const current=status.execution?.current;
  const enabled=status.execution?.enabled ?? health?.hardware_execution_enabled;
  const availability=automationAvailability({user,enabled,fresh,current,runtime:status.runtime,errors,measurementCount,busy:busy || !!pending});
  useEffect(()=>{if(current?.run_id) setNotice('');},[current?.run_id]);
  useEffect(()=>{
    if(!prepared) return;
    let disposed=false,fetching=false;
    drainRequest.current=null;
    const poll=async()=>{if(fetching) return;fetching=true;try {const value=await api.preparation(prepared.run_id);if(!disposed){setCalibration(value.calibration);setCellPreparation(value.cell_preparation);}}catch(failure){if(!disposed){setError(failure.message);setCalibration(previous=>previous?.required?{...previous,state:'unknown'}:previous);setCellPreparation(previous=>({...previous,required:true,state:'unknown'}));}}finally{fetching=false;}};
    poll();const timer=setInterval(poll,1000);return()=>{disposed=true;clearInterval(timer);};
  },[prepared?.run_id]);
  async function prepare() {
    if(pendingRef.current || !availability.allowed) return;
    pendingRef.current=true;setPending('prepare');setError('');setNotice('');
    try {const result=await api.prepareExecution(sequence);setPrepared(result);setCalibration(result.calibration);setCellPreparation(result.cell_preparation);onPlan(result.preview);}
    catch(failure) {setError(failure.message);}
    finally {pendingRef.current=false;setPending('');}
  }
  async function start() {
    if(pendingRef.current || !availability.allowed || !calibrationAllowsStart(calibration) || !cellAllowsStart(cellPreparation) || !preparedIsCurrent(prepared,currentSequence.current) || user?.role!=='admin') return;
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
  async function calibrate(confirmDischarge) {
    if(pendingRef.current || user?.role!=='admin' || !fresh || !preparedIsCurrent(prepared,currentSequence.current) || confirmDischarge!==true) return;
    pendingRef.current=true;setPending('calibrate');setError('');
    try {const value=await api.calibrateExecution(prepared.run_id,prepared.version,true);setCalibration(value.calibration);await onRefresh();}
    catch(failure) {setError(failure.message);}
    finally {pendingRef.current=false;setPending('');}
  }
  async function drain(route,confirmDischarge) {
    if(pendingRef.current || user?.role!=='admin' || !fresh || !preparedIsCurrent(prepared,currentSequence.current) || confirmDischarge!==true) return;
    pendingRef.current=true;setPending('drain');setError('');
    drainRequest.current ||= crypto.randomUUID();
    try {const value=await api.drainCell(prepared.run_id,prepared.version,route,true,drainRequest.current);drainRequest.current=null;setCellPreparation(value.cell_preparation);await onRefresh();}
    catch(failure) {setError(failure.message);}
    finally {pendingRef.current=false;setPending('');}
  }
  async function confirmEmpty(confirmed) {
    if(pendingRef.current || user?.role!=='admin' || confirmed!==true || !fresh || !preparedIsCurrent(prepared,currentSequence.current)) return;
    pendingRef.current=true;setPending('empty');setError('');
    try {const value=await api.confirmEmpty(prepared.run_id,prepared.version,true);setCellPreparation(value.cell_preparation);}
    catch(failure) {setError(failure.message);}
    finally {pendingRef.current=false;setPending('');}
  }
  const active=fresh && current?.controller_alive;
  const currentCalibrated=active && current?.sequence_id===sequence.id && current?.syringe_calibration_passed;
  const visibleCalibration=prepared?(calibration?.state || 'required'):currentCalibrated?'passed':'required';
  const displayTitle=!fresh?'运行状态连接待恢复':current?.operation==='drain_cell'?({completed:'启动前排液已完成，待确认空池',failed:'启动前排空失败',stopped:'排空已中止'}[current.state] || '正在执行启动前排空'):current?.operation==='calibrate_syringe'?({completed:'本次回零校验已通过',failed:'回零校验失败',stopped:'回零校验已中止'}[current.state] || '正在进行注射泵回零校验'):current?.state==='failed' && !active?'上次实验未完成':current?executionLabels[current.state] || '运行状态待确认':enabled?'管理员自动化控制':'配置与离线预演';
  const volumes=cellVolumes(sequence,undefined,templates);
  const currentCellReady=active && current?.sequence_id===sequence.id && current?.cell_preparation_passed;
  const visibleCellPreparation=prepared?(cellPreparation?.state || 'required'):currentCellReady?'passed':'required';
  return <section className={'automation-control '+(active?'automation-active':'')} aria-label="自动化运行控制">
    <div className="automation-control-main"><span className="automation-shield"><ShieldCheck size={20}/></span><div><strong>{displayTitle}</strong><p>{active?current.sequence_id+' · 操作人 '+current.started_by:'当前编排：'+sequence.id}</p></div><div className="automation-control-buttons">
      {user?.role==='admin' ? <>{current?.can_stop && <button className="automation-stop" onClick={stop} disabled={!!pending || current.stop_requested}><Square size={14}/>{pending==='stop' || current.stop_requested?'等待停止反馈…':'停止当前实验'}</button>}<button className="primary-button" onClick={prepare} disabled={!availability.allowed} title={availability.reason}>{pending==='prepare'?<LoaderCircle size={15} className="refresh-spinning"/>:<Play size={15}/>} {pending==='prepare'?'后端校验中…':'运行当前编排'}</button></> : <span className="automation-viewer"><LockKeyhole size={13}/>只读监测</span>}
    </div></div>
    <p className="automation-control-note">{availability.reason}</p>
    <div className="calibration-overview" aria-label="电解池容量与启动前准备"><span>容量上限 {volumes.capacity_ml} mL</span><small>本方案预计峰值 {volumes.peak_volume_ml} mL · {currentCellReady?'运行中任务空池准备':'启动前排空'}：{cellPreparationLabels[visibleCellPreparation]} · 需人工确认空池</small></div>
    {flattenSequence(sequence).steps.some(step=>step.device==='syringe_c') && <div className="calibration-overview" aria-label="注射泵实验前校验状态"><span className={visibleCalibration==='passed'?'is-passed':''}>本次回零：{calibrationLabels[visibleCalibration]}</span><small>每次实验前必需；点击“运行当前编排”完成校验。{status.execution?.syringe_calibration?.last_homing?.initialized_at && ' 最近回零：'+new Date(status.execution.syringe_calibration.last_homing.initialized_at).toLocaleString('zh-CN')}</small></div>}
    {current?.message && !active && fresh && <p className={'automation-result '+(current.state==='failed'?'is-failed':'')} role="status">{current.state==='failed'?<AlertCircle size={14}/>:<CheckCircle2 size={14}/>}<span>{current.message}</span></p>}
    {current?.cleanup_failed && <p className="automation-error" role="alert">设备停止回读失败，请现场确认。界面不会将其显示为已安全停止。</p>}
    {notice && <p className="automation-notice" role="status">{notice}</p>}{error && !prepared && <p className="automation-error" role="alert">{error}</p>}
    {prepared && user?.role==='admin' && <ExecutionConfirmation prepared={prepared} currentSequence={sequence} pending={pending==='start'} error={error} onStart={start} onClose={()=>{setPrepared(null);setCalibration(null);setCellPreparation(null);setError('');}} names={names} blockedReason={availability.allowed?null:availability.reason} calibration={calibration} onCalibrate={calibrate} calibrationBusy={pending==='calibrate'} onStop={stop} canStop={current?.operation==='calibrate_syringe' && current?.target_run_id===prepared.run_id && current?.can_stop && !current?.stop_requested} cellPreparation={cellPreparation} onDrain={drain} onEmptyConfirm={confirmEmpty} cellBusy={pending==='drain' || pending==='empty'} canStopDrain={current?.operation==='drain_cell' && current?.target_run_id===prepared.run_id && current?.can_stop && !current?.stop_requested} fresh={fresh}/>}
  </section>;
}
