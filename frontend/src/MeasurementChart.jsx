import React,{useMemo,useRef,useState} from 'react';
import {Activity,Download,LineChart} from 'lucide-react';
import {chartData,chartDomain,displayTrend,downloadBlob,measurementStates,numberText} from './measurements.js';

function tick(value) {
  if(value!==0 && (Math.abs(value)<.001 || Math.abs(value)>=10000))return value.toExponential(1);
  return Number(value.toPrecision(4)).toString();
}
export function TraceChart({points=[],kind='cv',title,exportName='cv'}) {
  const svg=useRef(null),[hover,setHover]=useState(null),[view,setView]=useState('smooth');
  const data=useMemo(()=>chartData(points,kind),[points,kind]);
  const xd=useMemo(()=>chartDomain(data.map(p=>p.x)),[data]);
  const yd=useMemo(()=>chartDomain(data.map(p=>p.y)),[data]);
  const W=900,H=340,left=76,right=24,top=24,bottom=58;
  const x=value=>left+(value-xd[0])/(xd[1]-xd[0])*(W-left-right);
  const y=value=>H-bottom-(value-yd[0])/(yd[1]-yd[0])*(H-top-bottom);
  const ticks=Array.from({length:5},(_,i)=>i/4);
  const shown=useMemo(()=>view==='smooth'?displayTrend(data,kind):data,[data,kind,view]);
  const line=shown.map(point=>`${x(point.x).toFixed(2)},${y(point.y).toFixed(2)}`).join(' ');
  function locate(event) {
    if(!data.length)return;
    const bounds=event.currentTarget.getBoundingClientRect();
    const px=(event.clientX-bounds.left)/bounds.width*W,py=(event.clientY-bounds.top)/bounds.height*H;
    let best=null,distance=Infinity;
    for(const point of data){const d=(x(point.x)-px)**2+(y(point.y)-py)**2;if(d<distance){distance=d;best=point;}}
    setHover(best);
  }
  function exportSvg() {
    if(!svg.current)return;
    downloadBlob(new Blob([svg.current.outerHTML],{type:'image/svg+xml'}),exportName+'-'+kind+'-'+view+'.svg');
  }
  return <section className="measurement-chart" aria-label={title}>
    <div className="measurement-chart-heading"><h3><LineChart size={16}/>{title}</h3><div className="measurement-chart-tools"><div className="chart-view-switch" role="group" aria-label="图形显示方式">{[['line','连线'],['scatter','散点'],['smooth','显示平滑']].map(([value,label])=><button type="button" key={value} aria-pressed={view===value} onClick={()=>setView(value)}>{label}</button>)}</div><button type="button" className="subtle-button" disabled={!data.length} onClick={exportSvg}><Download size={13}/>SVG</button></div></div>
    {!data.length?<div className="measurement-chart-empty"><Activity size={27}/><strong>等待仪器返回采样数据</strong><span>收到有效 CV 数据后，曲线会自动更新。</span></div>:<div className="measurement-svg-wrap">
      <svg ref={svg} xmlns="http://www.w3.org/2000/svg" viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title} onMouseMove={locate} onMouseLeave={()=>setHover(null)}>
        <title>{title}；{view==='smooth'?'7 点显示移动平均，淡色点为未平滑抽样值；':''}电流为名义换算值，时间为服务器接收时间</title><rect width={W} height={H} fill="white"/>
        <g fontFamily="sans-serif" fontSize="16" fill="#64748b">
          {ticks.map(f=>{const xv=xd[0]+f*(xd[1]-xd[0]),yv=yd[0]+f*(yd[1]-yd[0]);return <g key={f}><line x1={x(xv)} x2={x(xv)} y1={top} y2={H-bottom} stroke="#e8edf3"/><line x1={left} x2={W-right} y1={y(yv)} y2={y(yv)} stroke="#e8edf3"/><text x={x(xv)} y={H-bottom+24} textAnchor="middle">{tick(xv)}</text><text x={left-12} y={y(yv)+4} textAnchor="end">{tick(yv)}</text></g>;})}
          <text x={W/2} y={H-9} textAnchor="middle">{kind==='cv'?'电位 / V（DAC 换算）':'接收时间 / s（相对启动应答）'}</text><text x="18" y={H/2} textAnchor="middle" transform={`rotate(-90 18 ${H/2})`}>电流 / µA（名义值）</text>
        </g>
        {view!=='line' && data.map(point=><circle key={point.index} cx={x(point.x)} cy={y(point.y)} r={view==='smooth'?1.2:1.6} fill="#3273b8" opacity={view==='smooth'?.22:.65}/>)}
        {view!=='scatter' && <polyline fill="none" stroke="#3273b8" strokeWidth="1.8" strokeLinejoin="round" points={line}/>}
        {data.length===1 && <circle cx={x(data[0].x)} cy={y(data[0].y)} r="3" fill="#3273b8"/>}
        {hover && <g pointerEvents="none"><line x1={x(hover.x)} x2={x(hover.x)} y1={top} y2={H-bottom} stroke="#94a3b8" strokeDasharray="4 4"/><circle cx={x(hover.x)} cy={y(hover.y)} r="4" fill="#0c6b76" stroke="white" strokeWidth="2"/></g>}
      </svg>
      <div className="measurement-chart-readout" aria-live="off">{hover?<><span>E {numberText(hover.potential,5)} V</span><span>I {numberText(hover.y,5)} µA</span><span>t {numberText(hover.time,3)} s</span></>:<span>移动指针查看电位、电流和接收时间</span>}</div>
      {view==='smooth' && <p className="measurement-data-note">7 点移动平均，仅用于显示；CV 在换向处分段计算。淡色点与指针读数保留未平滑的抽样值，原始数据和 CSV 不变。</p>}
      {kind==='time' && <p className="measurement-data-note">同一数据帧的多个点共享接收时刻，竖直线段不代表仪器电流在同一瞬间全部跳变。</p>}
    </div>}
  </section>;
}

export default function MeasurementCharts({metadata,points=[],live=false,fresh=true,onOpen}) {
  if(!metadata)return null;
  const state=metadata.state;
  return <section className="measurement-results" aria-label="CV 测量结果">
    <header className="measurement-results-heading"><div><span className="eyebrow">CYCLIC VOLTAMMETRY</span><h2>{live?'实验测量':'测量数据'} <span className={'measurement-state state-'+state}>{measurementStates[state] || state}</span></h2><p>{metadata.sequence_id} · {metadata.step_id}</p></div>{onOpen && <button type="button" className="outline-button" onClick={()=>onOpen(metadata.id)}>在数据管理中查看</button>}</header>
    {!fresh && live && <p className="measurement-warning" role="status">实时连接待恢复，当前显示最后接收的数据。</p>}
    <div className="measurement-metrics"><div><span>有效数据点</span><strong>{numberText(metadata.point_count,0)}</strong><small>每点为 100 个原始样本的均值</small></div><div><span>原始采样数</span><strong>{numberText(metadata.sample_count,0)}</strong><small>原始帧单独保存</small></div><div><span>电位范围</span><strong>{numberText(metadata.statistics?.potential_v_min,4)} ~ {numberText(metadata.statistics?.potential_v_max,4)}<small> V</small></strong><small>从返回 DAC CODE 换算</small></div><div><span>停止回读</span><strong>{['starting','running'].includes(state)?'采集中':metadata.stop_confirmed?'已确认':'待确认'}</strong><small>{metadata.instrument?.product || 'EC110'} {metadata.instrument?.firmware || ''}</small></div></div>
    {metadata.message && <p className="measurement-warning" role="status">{metadata.message}</p>}
    <div className="measurement-chart-grid"><TraceChart points={points} title="CV · 电位—电流" kind="cv" exportName={metadata.id}/><TraceChart points={points} title="时间—电流" kind="time" exportName={metadata.id}/></div>
    <p className="measurement-data-note">图形为保留端点与峰值的抽样显示，完整均值数据可下载 CSV。电流为名义换算值；时间为服务器接收时间，受分包与缓冲影响。</p>
  </section>;
}
