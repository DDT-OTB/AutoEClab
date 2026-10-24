import React,{useMemo} from 'react';
import {ArrowRight,Boxes,FileJson,FolderOpen,Upload} from 'lucide-react';
import {workflowToEditor} from './workflow.js';
import {stepSummary} from './cv.js';
import {formatDuration,operationLabels} from './model.js';
import {isPreflight,preflightInfo} from './preflight.js';
import {PreflightProgress} from './PreflightBlock.jsx';
import {isDebug,hasPreparation} from './workflowMode.js';

export default function RunWorkspace({sequence,source,plan,items,busy,onLoad,onImport,onPlans,onEdit,errors=[],templates,shortNames,runningIndex=-1,preparation}) {
  const blocks=useMemo(()=>sequence?workflowToEditor(sequence).blocks:[],[sequence]);
  const actionCount=blocks.filter(block=>!isPreflight(block)).reduce((total,block)=>total+block.steps.length,0);
  let offset=0;
  return <section className="run-loader" aria-label="实验流程载入">
    <div className="run-loader-heading"><span className="run-loader-icon"><FileJson size={22}/></span><div><span className="eyebrow">EXPERIMENT EXECUTION</span><h2>{sequence?sequence.id:'载入本次实验流程'}</h2><p>{sequence?source?.label || sequence.description:'选择已保存的方案，或导入完整的流程 JSON。'}</p></div><button type="button" className="subtle-button" onClick={onPlans}><FolderOpen size={14}/>实验方案</button></div>
    <div className="run-loader-actions"><label><span>从方案文件载入</span><select value={source?.id || ''} disabled={!!busy} aria-label="载入实验方案" onChange={event=>onLoad(event.target.value)}><option value="" disabled>选择实验方案</option>{items.filter(item=>item.editable!==false).map(item=><option key={item.id} value={item.id}>{item.id}</option>)}</select></label><button type="button" className="outline-button" onClick={onImport} disabled={!!busy}><Upload size={15}/>导入流程 JSON</button>{sequence && <button type="button" className="subtle-button" onClick={onEdit}>到方案中编辑<ArrowRight size={14}/></button>}</div>
    {sequence && <><div className="run-loaded-meta"><span><Boxes size={13}/>{blocks.length} 个功能块 · {actionCount} 个动作</span>{plan && <span>计时动作约 {formatDuration(plan.estimated_timed_seconds)}</span>}<span>使用载入时的流程副本</span></div>
      {isDebug(sequence) && <p className="run-debug-mode"><strong>调试模式</strong> · {hasPreparation(sequence)?'已自行加入前置准备，将按所选前置步骤执行。':'无强制前置准备；初始池内液量 '+(sequence.initial_cell_volume_ml ?? 0)+' mL，启动前须确认现场条件。'}</p>}
      {errors.length>0 && <p className="run-load-warning" role="status">{errors[0]} 请在实验方案中检查后重新载入。</p>}
      <details className="run-flow-preview"><summary>查看已载入的动作序列<span>只读</span></summary><ol>{blocks.map(block=>{
        if(isPreflight(block))return <li className="run-preview-preflight" key={block.id}><PreflightProgress info={preparation?.preview?.preflight || preflightInfo(sequence)} calibration={preparation?.calibration} cell={preparation?.cell_preparation}/></li>;
        const start=offset;offset+=block.steps.length;
        return <li className="run-preview-block" key={block.id}><strong>{block.id}</strong><small>{block.description}</small><ol>{block.steps.map((step,index)=><li key={step._key} className={runningIndex===start+index?'is-running':''}><span>{String(start+index+1).padStart(2,'0')}</span><div><strong>{shortNames[step.device] || step.device}</strong><small>{operationLabels[step.operation]} · {stepSummary(step,templates[step.device])}</small></div>{runningIndex===start+index && <em>执行中</em>}</li>)}</ol></li>;
      })}</ol></details>
    </>}
  </section>;
}
