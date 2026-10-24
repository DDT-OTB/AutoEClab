import React, { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, ArrowUpRight, Check, CheckCircle2, ChevronRight, Code2, Copy, Download, FileJson, FileText, FolderOpen, Info, LoaderCircle, Pencil, Plus, RefreshCw, Search, SlidersHorizontal, Trash2, Upload, X } from 'lucide-react';
import { appApi as api } from './api.js';
import { experimentKind, experimentKindLabels, fileNameError, fileSize, filterExperiments, uniqueExperimentId } from './experiments.js';

const timeText=value=>value ? new Date(value).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'}) : '—';

export function ExperimentFileList({ items, selectedId, activeId, onSelect, onOpen, disabled }) {
  return <table className="experiment-file-table"><thead><tr><th scope="col">方案文件</th><th scope="col">步骤</th><th scope="col">修改时间</th><th scope="col">大小</th><th scope="col"><span className="file-sr-only">选择</span></th></tr></thead><tbody>
    {items.map(item=><tr key={item.id} className={selectedId===item.id?'file-row-selected':''} onClick={()=>!disabled && onSelect(item.id)} onDoubleClick={()=>!disabled && item.editable && onOpen(item.id)}>
      <td><button className="experiment-file-name" type="button" onClick={()=>onSelect(item.id)} disabled={disabled} aria-pressed={selectedId===item.id} aria-label={'查看 '+item.id+' 的文件详情'}><span className={'file-type-icon file-kind-'+experimentKind(item)}><FileJson size={22}/></span><span><strong>{item.filename || item.id+'.json'}{item.sequence_mode==='debug' && <i>调试</i>}{activeId===item.id && <i>已加载</i>}</strong><small>{item.description || experimentKindLabels[experimentKind(item)]}</small></span></button></td>
      <td><span className="file-steps-count">{item.step_count}<small>动作{item.block_count?' · '+item.block_count+' 块':''}</small></span></td><td><time dateTime={item.modified_at}>{timeText(item.modified_at)}</time></td><td>{fileSize(item.size_bytes)}</td><td><ChevronRight size={14}/></td>
    </tr>)}
  </tbody></table>;
}

export function ExperimentFileDetails({ item, content, loading, tab, setTab, user, activeId, dirty, busy, onOpen, onDownload, onAction,onUse }) {
  if(!item) return <aside className="experiment-file-inspector"><div className="file-details-empty"><span><FolderOpen size={32}/></span><h3>选择一份实验方案</h3><p>查看文件信息、JSON 内容，<br/>或管理已有方案。</p></div></aside>;
  const editable=user?.role==='admin';
  return <aside className="experiment-file-inspector" aria-label="实验方案文件详情">
    <div className="file-details-heading"><span className={'file-type-icon file-kind-'+experimentKind(item)}><FileJson size={25}/></span><span><h3>{item.filename}</h3><small>{item.sequence_mode==='debug'?'调试编排 · ':''}{experimentKindLabels[experimentKind(item)]}</small></span></div>
    <div className="file-detail-tabs" role="tablist" aria-label="文件详情"><button role="tab" id="file-info-tab" aria-selected={tab==='info'} aria-controls="file-info-panel" className={tab==='info'?'active':''} onClick={()=>setTab('info')}><Info size={13}/>文件信息</button><button role="tab" id="file-json-tab" aria-selected={tab==='json'} aria-controls="file-json-panel" className={tab==='json'?'active':''} onClick={()=>setTab('json')}><Code2 size={13}/>JSON 预览</button></div>
    <div className="file-details-body" role="tabpanel" id={'file-'+tab+'-panel'} aria-labelledby={'file-'+tab+'-tab'}>
      {tab==='info' ? <><p className="file-description">{item.description || '暂无方案描述。'}</p><dl className="file-info-grid"><dt>方案 ID</dt><dd>{item.id}</dd><dt>操作步骤</dt><dd>{item.step_count} 步</dd><dt>文件大小</dt><dd>{fileSize(item.size_bytes)}</dd><dt>修改时间</dt><dd>{timeText(item.modified_at)}</dd><dt>说明文档</dt><dd>{item.has_markdown?<><FileText size={12}/>同名 Markdown</>:'尚未生成'}</dd></dl>
        {item.contains_measurement && <p className="file-detail-note"><Info size={14}/><span>方案包含测量参数，工作站接入后方可执行。</span></p>}
      </> : loading ? <div className="file-preview-loading"><LoaderCircle size={17} className="refresh-spinning"/>正在读取文件…</div> : <pre className="file-json-preview" tabIndex={0} aria-label="实验方案 JSON 内容">{content ?? '文件内容读取失败，请刷新后重试。'}</pre>}
      {item.file_error && <p className="file-detail-warning"><Info size={14}/><span>{item.file_error}</span></p>}
      {activeId===item.id && <p className="file-loaded-note"><CheckCircle2 size={13}/>{dirty?'编排中有未保存的修改，文件操作会保留草稿。':'当前已加载到实验编排。'}</p>}
    </div>
    <div className="file-details-actions"><button type="button" className="primary-button" onClick={()=>onOpen(item.id)} disabled={busy || !item.editable}><ArrowUpRight size={14}/>{editable?'进入编排编辑':'查看实验流程'}</button><button type="button" className="outline-button" onClick={onDownload} disabled={busy || loading || content===null}><Download size={14}/>下载 JSON</button>
      {onUse && <button type="button" className="outline-button file-run-button" onClick={()=>onUse(item.id)} disabled={busy || !item.editable} title="只载入流程，仍需管理员确认后启动">载入实验运行<ArrowUpRight size={14}/></button>}
      {editable ? <><div className="file-secondary-actions"><button type="button" onClick={()=>onAction('rename',item)} disabled={busy || !item.editable}><Pencil size={13}/>重命名</button><button type="button" onClick={()=>onAction('duplicate',item)} disabled={busy || !item.editable}><Copy size={13}/>复制方案</button></div><button type="button" className="file-delete-button" onClick={()=>onAction('delete',item)} disabled={busy}><Trash2 size={13}/>删除方案</button></> : <p className="file-readonly-note">只读账号可查看、下载和预演方案。</p>}
    </div>
  </aside>;
}

export default function ExperimentManager({ items, user, activeId, dirty, appBusy, onReload, onOpen, onNew, onImport, onBack, onChanged,onUse,embedded=false }) {
  const [query,setQuery]=useState(''),[kind,setKind]=useState('all'),[sort,setSort]=useState('modified');
  const [selectedId,setSelectedId]=useState(activeId),[detail,setDetail]=useState(null),[detailLoading,setDetailLoading]=useState(false),[tab,setTab]=useState('info');
  const [loading,setLoading]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [action,setAction]=useState(null),[newId,setNewId]=useState('');
  const visible=useMemo(()=>filterExperiments(items,{query,kind,sort}),[items,query,kind,sort]);
  const selected=items.find(item=>item.id===selectedId) || null;
  const current=detail?.id===selectedId && selected ? {...selected,...detail}:selected;
  const content=detail?.id===selectedId ? detail.content:null;
  const disabled=busy || !!appBusy;
  async function refresh() {
    setLoading(true);setError('');
    try { await onReload(); }
    catch(failure) { setError('读取方案列表失败：'+failure.message); }
    finally {setLoading(false);}
  }
  useEffect(()=>{refresh();},[]);
  useEffect(()=>{
    if(selectedId && !items.some(item=>item.id===selectedId)) setSelectedId(null);
  },[items,selectedId]);
  useEffect(()=>{
    let disposed=false;
    setDetail(null);
    if(!selectedId) {setDetailLoading(false);return;}
    setDetailLoading(true);
    api.experimentContent(selectedId).then(value=>{if(!disposed) setDetail(value);})
      .catch(failure=>{if(!disposed) setError('读取文件失败：'+failure.message);})
      .finally(()=>{if(!disposed) setDetailLoading(false);});
    return ()=>{disposed=true;};
  },[selectedId,selected?.version]);
  function select(id) {setSelectedId(id);setAction(null);setError('');}
  function beginAction(type,item) {
    setSelectedId(item.id);setAction({type,item:{...item}});setError('');setNotice('');
    setNewId(type==='duplicate'?uniqueExperimentId(item.id+'_copy',items):item.id);
  }
  async function submitAction(event) {
    event.preventDefault();
    if(!action || disabled) return;
    const {type,item}=action;
    if(type!=='delete') {
      const invalid=fileNameError(newId,item.id,items);
      if(invalid) {setError(invalid);return;}
    }
    setBusy(true);setError('');setNotice('');
    try {
      const result=type==='rename' ? await api.renameExperiment(item.id,newId,item.version) : type==='duplicate' ? await api.duplicateExperiment(item.id,newId,item.version) : await api.deleteExperiment(item.id,item.version);
      onChanged(type,result);
      setAction(null);setDetail(null);
      const message=type==='rename'?'文件已重命名，说明文档同步更新。':type==='duplicate'?'已创建独立副本，原文件已保留。':activeId===item.id?'文件已删除并备份，编排内容保留为未保存草稿。':'方案和同名说明已删除，删除前已自动备份。';
      setNotice(message);
      try { await onReload();setSelectedId(type==='delete'?null:result.sequence.id); } catch { setSelectedId(null);setError('文件操作已完成，列表刷新失败，请点击刷新重新读取。'); }
    } catch(failure) {
      setError(failure.message);
      if(failure.status===409 || failure.status===404) {
        setAction(null);setDetail(null);
        try {await onReload();} catch {}
      }
    } finally {setBusy(false);}
  }
  function download() {
    if(!current || content===null) return;
    const url=URL.createObjectURL(new Blob([content],{type:'application/json'}));
    const link=document.createElement('a');link.href=url;link.download=current.filename;link.click();URL.revokeObjectURL(url);
  }
  return <section className="experiment-manager" aria-label="实验方案文件管理">
    {!embedded && <div className="section-heading file-page-heading"><div><span className="eyebrow">EXPERIMENT FILES</span><h2>实验方案 <span className="count">{items.length} 个文件</span></h2><p>查找与管理方案文件，选中后可查看详情或进入流程编排。</p></div><button className="outline-button" type="button" onClick={onBack}><ArrowLeft size={14}/>返回编排</button></div>}
    <div className="file-workspace">
      <header className="file-workspace-header"><div className="file-folder-breadcrumb"><span><FolderOpen size={18}/></span><strong>实验方案</strong><ChevronRight size={12}/><small>全部文件</small></div><div className="file-workspace-tools"><button type="button" className="subtle-button" onClick={onImport} disabled={disabled || user?.role!=='admin'}><Upload size={14}/>导入 JSON</button><button type="button" className="primary-button" onClick={onNew} disabled={disabled || user?.role!=='admin'}><Plus size={15}/>新建方案</button></div></header>
      <div className="file-filter-bar"><label className="file-search"><Search size={16}/><input aria-label="搜索实验方案" placeholder="搜索文件名或方案描述…" value={query} onChange={event=>setQuery(event.target.value)}/>{query && <button type="button" aria-label="清空搜索" onClick={()=>setQuery('')}><X size={13}/></button>}</label><label className="file-kind-filter"><SlidersHorizontal size={14}/><select aria-label="筛选方案类型" value={kind} onChange={event=>setKind(event.target.value)}><option value="all">全部类型</option><option value="fluidics">泵阀流程</option><option value="measurement">包含测量</option><option value="draft">空白草稿</option><option value="review">待检查</option></select></label><label className="file-sort"><select aria-label="排序实验方案" value={sort} onChange={event=>setSort(event.target.value)}><option value="modified">最近修改优先</option><option value="name">文件名 A–Z</option><option value="steps">步骤数量</option></select></label><button className="file-refresh" type="button" onClick={refresh} disabled={loading || disabled} aria-label="刷新实验方案列表"><RefreshCw size={15} className={loading?'refresh-spinning':''}/></button></div>
      {error && <div className="file-message file-message-error" role="alert"><Info size={15}/><span>{error}</span><button type="button" onClick={()=>setError('')} aria-label="关闭文件错误"><X size={13}/></button></div>}
      {notice && <div className="file-message file-message-success" role="status"><Check size={15}/><span>{notice}</span></div>}
      <div className="file-browser-grid"><div className="file-browser-main">
        <div className="file-list-caption"><span>{loading?'正在刷新…':'显示 '+visible.length+' / '+items.length+' 个方案'}</span><small>{user?.role==='admin'?'管理员可管理文件':'只读访问'}</small></div>
        {visible.length ? <div className="file-table-scroll"><ExperimentFileList items={visible} selectedId={selectedId} activeId={activeId} onSelect={select} onOpen={onOpen} disabled={disabled}/></div> : <div className="file-list-empty"><span>{query || kind!=='all'?<Search size={31}/>:<FolderOpen size={34}/>}</span><h3>{query || kind!=='all'?'没有匹配的方案':'还没有实验方案'}</h3><p>{query || kind!=='all'?'尝试其他文件名、描述或筛选条件。':'新建方案，或导入一份英文 JSON 开始编排。'}</p>{(query || kind!=='all') && <button type="button" className="outline-button" onClick={()=>{setQuery('');setKind('all');}}>清除筛选</button>}</div>}
        <footer className="file-list-footer"><FileText size={13}/><span>双击文件进入编排 · 同名说明文档随方案一起管理</span></footer>
      </div><div className="file-details-column"><ExperimentFileDetails item={current} content={content} loading={detailLoading} tab={tab} setTab={setTab} user={user} activeId={activeId} dirty={dirty} busy={disabled} onOpen={onOpen} onUse={onUse} onDownload={download} onAction={beginAction}/>
        {action && <form className={'file-operation-form operation-'+action.type} onSubmit={submitAction} aria-label={action.type==='delete'?'确认删除实验方案':action.type==='rename'?'重命名实验方案':'复制实验方案'}><div><strong>{action.type==='delete'?'删除方案':action.type==='rename'?'重命名方案':'复制为新方案'}</strong><button type="button" onClick={()=>setAction(null)} disabled={busy} aria-label="取消文件操作"><X size={14}/></button></div><p>{action.item.filename}</p>{action.type==='delete' ? <p className="file-delete-confirmation">将删除此 JSON 和同名说明文档。删除前会自动备份，其他方案不受影响。</p> : <label><span>新方案 ID</span><input required autoFocus value={newId} onChange={event=>setNewId(event.target.value)} maxLength={80} pattern="[A-Za-z][A-Za-z0-9_-]{0,79}" aria-label="新方案文件名" disabled={busy} spellCheck={false}/><small>文件名为 {newId || 'experiment'}.json</small></label>}<div className="file-operation-buttons"><button type="button" className="outline-button" disabled={busy} onClick={()=>setAction(null)}>取消</button><button type="submit" className={action.type==='delete'?'file-danger-submit':'primary-button'} disabled={disabled || (action.type!=='delete' && !!fileNameError(newId,action.item.id,items))}>{busy?'正在保存…':action.type==='delete'?'确认删除':'确认保存'}</button></div></form>}
      </div></div>
    </div>
  </section>;
}
