import React,{useEffect,useRef,useState} from 'react';
import {ArrowLeft,Database,Download,RefreshCw,Search} from 'lucide-react';
import {appApi as api} from './api.js';
import MeasurementCharts from './MeasurementChart.jsx';
import {downloadBlob,measurementStates,numberText} from './measurements.js';

export default function DataManagement({selectedId,onSelect,liveMeasurement}) {
  const [query,setQuery]=useState(''),[state,setState]=useState(''),[items,setItems]=useState([]),[total,setTotal]=useState(0);
  const [offset,setOffset]=useState(0),[next,setNext]=useState(null),[detail,setDetail]=useState(null),[error,setError]=useState(''),[loading,setLoading]=useState(false),[downloading,setDownloading]=useState('');
  const request=useRef(0),mounted=useRef(true);
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  async function reload() {
    const id=++request.current;setLoading(true);
    try {const result=await api.measurements({query,state,limit:30,offset});if(id===request.current && mounted.current){setItems(result.measurements);setTotal(result.total);setNext(result.next_offset);setError('');}}
    catch(failure){if(id===request.current && mounted.current)setError(failure.message);}
    finally{if(id===request.current && mounted.current)setLoading(false);}
  }
  useEffect(()=>{const timer=setTimeout(reload,250);return()=>clearTimeout(timer);},[query,state,offset,liveMeasurement?.metadata?.id,liveMeasurement?.metadata?.state]);
  useEffect(()=>{
    if(!selectedId){setDetail(null);return;}
    let closed=false,pending=false,timer=null;
    setDetail(null);setError('');
    const read=async()=>{if(pending)return;pending=true;try{const result=await api.measurement(selectedId);if(!closed){setDetail(result);if(!['starting','running'].includes(result.measurement.state))clearInterval(timer);}}catch(failure){if(!closed)setError(failure.message);}finally{pending=false;}};
    read();timer=setInterval(read,2000);
    return()=>{closed=true;clearInterval(timer);};
  },[selectedId]);
  async function download(kind) {
    if(downloading)return;setDownloading(kind);setError('');
    try{const result=await api.downloadMeasurement(selectedId,kind);downloadBlob(result.blob,result.filename);}
    catch(failure){setError(failure.message);}finally{setDownloading('');}
  }
  const current=detail?.measurement;
  const live=liveMeasurement?.metadata?.id===selectedId && ['starting','running'].includes(liveMeasurement?.metadata?.state)?liveMeasurement:null;
  return <section className="data-management" aria-label="实验数据管理">
    <div className="section-heading"><div><span className="eyebrow">EXPERIMENT DATA</span><h2><Database size={22}/>数据管理</h2><p>查看实测曲线、参数快照与采集状态，下载完整数据。</p></div><button type="button" className="outline-button" onClick={reload} disabled={loading}><RefreshCw size={14} className={loading?'refresh-spinning':''}/>刷新</button></div>
    {error && <p className="measurement-warning" role="alert">{error}</p>}
    {selectedId?<>
      <div className="dataset-toolbar"><button className="subtle-button" onClick={()=>onSelect(null)}><ArrowLeft size={15}/>全部数据</button><code>{selectedId}</code><div>{['csv','json','raw'].map(kind=><button key={kind} type="button" className="outline-button" onClick={()=>download(kind)} disabled={!!downloading || !current || (kind!=='json' && ['starting','running'].includes(current?.state))}><Download size={14}/>{downloading===kind?'下载中…':kind==='csv'?'均值 CSV':kind==='json'?'参数与记录':'原始帧'}</button>)}</div></div>
      {current?<><MeasurementCharts metadata={live?.metadata || current} points={live?.points || detail.chart.points}/>
        <details className="dataset-parameters"><summary>测量参数与下发记录</summary><div className="dataset-parameter-grid"><div><h3>实验参数</h3><pre>{JSON.stringify(current.parameters,null,2)}</pre></div><div><h3>EC110 下发参数</h3><pre>{JSON.stringify(current.encoded_parameters,null,2)}</pre></div></div>{current.encoded_parameters?.timing_basis?.independently_verified===false && <p className="measurement-warning">当前扫描速率为协议换算目标值，仪器计时尚未独立校准。</p>}</details></>:<div className="measurement-chart-empty">{error?'数据加载失败，请刷新。':'正在读取测量数据…'}</div>}
    </>:<>
      <div className="dataset-filters"><label className="dataset-search"><Search size={16}/><input aria-label="搜索测量数据" value={query} placeholder="搜索方案、步骤或数据编号" onChange={event=>{setQuery(event.target.value);setOffset(0);}}/></label><label><span>采集状态</span><select aria-label="筛选采集状态" value={state} onChange={event=>{setState(event.target.value);setOffset(0);}}><option value="">全部状态</option>{Object.entries(measurementStates).map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label><span>共 {total} 条测量记录</span></div>
      <div className="dataset-table"><div className="dataset-row dataset-table-head"><span>实验 / 测量步骤</span><span>采集时间</span><span>数据点 / 原始样本</span><span>状态</span></div>{items.map(item=><button type="button" key={item.id} className="dataset-row" onClick={()=>onSelect(item.id)}><span><strong>{item.sequence_id}</strong><small>{item.step_id} · CV</small></span><span>{new Date(item.created_at).toLocaleString('zh-CN')}</span><span>{numberText(item.point_count,0)}<small>{numberText(item.sample_count,0)} 个样本</small></span><span className={'measurement-state state-'+item.state}>{measurementStates[item.state] || item.state}</span></button>)}{!items.length && <div className="measurement-chart-empty"><Database size={26}/><strong>{loading?'正在读取数据…':'暂无匹配的测量数据'}</strong><span>实验运行中的 CV 数据会自动保存到这里。</span></div>}</div>
      <div className="dataset-pagination"><button className="outline-button" disabled={!offset || loading} onClick={()=>setOffset(Math.max(0,offset-30))}>上一页</button><span>{Math.floor(offset/30)+1} / {Math.max(1,Math.ceil(total/30))}</span><button className="outline-button" disabled={next===null || loading} onClick={()=>setOffset(next)}>下一页</button></div>
    </>}
  </section>;
}
