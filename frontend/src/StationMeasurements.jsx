import React,{useState} from 'react';
import {ArrowUpRight} from 'lucide-react';
import {TraceChart} from './MeasurementChart.jsx';
import {measurementStates,numberText} from './measurements.js';

export default function StationMeasurements({measurement,fresh,onOpen}) {
  const [view,setView]=useState('cv');
  const data=measurement?.metadata,points=measurement?.points || [],last=points.at(-1);
  return <div className="station-measurements" aria-label="电化学工作站测量数据">
    <div className="station-measurement-heading"><strong>循环伏安法 · CV</strong>{data && <span className={'measurement-state state-'+data.state}>{measurementStates[data.state] || data.state}</span>}</div>
    {data?<>
      <div className="station-measurement-source"><span>{data.sequence_id}</span><small>{data.step_id}</small></div>
      <div className="station-measurement-values"><div><span>电位</span><strong>{last?numberText(last[2],4):'—'}<small> V</small></strong></div><div><span>电流 · 名义值</span><strong>{last?numberText(last[3]*1e6,3):'—'}<small> µA</small></strong></div><div><span>均值数据点</span><strong>{numberText(data.point_count,0)}</strong></div></div>
      {!fresh && ['starting','running'].includes(data.state) && <p className="measurement-warning">连接待恢复，保留最后接收数据。</p>}
      {data.message && data.state==='failed' && <p className="measurement-warning">{data.message}</p>}
      <div className="station-chart-tabs" role="tablist" aria-label="工作站测量图形"><button type="button" role="tab" aria-selected={view==='cv'} onClick={()=>setView('cv')}>CV 曲线</button><button type="button" role="tab" aria-selected={view==='time'} onClick={()=>setView('time')}>时间—电流</button></div>
      <TraceChart points={points} kind={view} title={view==='cv'?'电位—电流':'时间—电流'} exportName={data.id}/>
      <p className="measurement-data-note">电位由 DAC 换算，电流为名义值；时间使用服务器接收时刻。每点为 100 个样本均值，图形抽样保留端点和峰值。</p>
      <div className="station-data-footer"><span>{data.stop_confirmed?'停止回读已确认':['starting','running'].includes(data.state)?'数据自动保存中':'停止回读待确认'}</span>{onOpen && <button type="button" className="subtle-button" onClick={()=>onOpen(data.id)}>数据管理<ArrowUpRight size={13}/></button>}</div>
    </>:<div className="station-data-empty"><strong>等待测量数据</strong><p>在实验方案中设置 CV 参数，运行后会在此显示曲线和采集状态。</p></div>}
  </div>;
}
