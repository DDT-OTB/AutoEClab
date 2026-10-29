import React,{useEffect,useState} from 'react';
import {appApi as api} from './api.js';

const labels={prepared:'已准备',starting:'准备启动',running:'执行中',stopping:'正在停止',completed:'已完成',stopped:'已停止',failed:'异常结束',interrupted:'异常中断',pending:'待执行',not_run:'未执行',unknown:'无完整状态记录'};
export default function RunResultsSummary({runId,liveStatus,revision=0,onClear}) {
  const [data,setData]=useState(null),[error,setError]=useState('');
  const current=liveStatus?.execution?.current;
  const liveRevision=current?.run_id===runId?[current.state,current.completed_step_count,current.active_step_id].join(':'):'';
  useEffect(()=>{
    if(!runId)return;
    let closed=false,pending=false,latest=null;
    setData(null);setError('');
    async function read(){
      if(closed || pending)return;pending=true;
      try{const value=await api.runResults(runId);latest=value;if(!closed){setData({id:runId,...value});setError('');}}
      catch(failure){if(!closed)setError(failure.message);}
      finally{pending=false;}
    }
    read();
    const timer=setInterval(()=>{if(latest?.execution?.controller_alive)read();},2000);
    return()=>{closed=true;clearInterval(timer);};
  },[runId,liveRevision,revision]);
  if(!runId)return null;
  const value=data?.id===runId?data:null;
  return <section className="run-results-summary" aria-label="本次实验全部结果">
    <div className="run-results-heading"><div><span className="eyebrow">EXPERIMENT RESULTS</span><h3>{value?.sequence?.id || '本次运行结果'}</h3><code>{runId}</code></div><button type="button" className="subtle-button" onClick={onClear}>查看所有实验数据</button></div>
    {error?<p className="measurement-warning">流程快照暂不可用：{error}。下方仍按该运行编号查询测量数据。</p>:value?<>
      <p className="measurement-data-note">{labels[value.execution.state] || value.execution.state} · {value.steps.length} 个动作 · {value.measurement_count} 条测量记录。以下流程为执行时保存的版本。</p>
      {value.execution.message && <p className="measurement-data-note">{value.execution.message}</p>}
      <details className="run-result-steps"><summary>查看本次动作与执行状态</summary><ol>{value.steps.map(step=><li key={step.id}><span>{step.index}</span><div><strong>{step.description || step.id}</strong><small>{step.device} · {step.id}</small></div><em className={'measurement-state state-'+step.state}>{labels[step.state] || step.state}</em></li>)}</ol></details>
    </>:<p className="measurement-data-note">正在读取本次运行的流程快照…</p>}
  </section>;
}
