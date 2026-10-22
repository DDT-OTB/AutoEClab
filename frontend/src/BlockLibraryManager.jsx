import React,{useEffect,useRef,useState} from 'react';
import {ArrowLeft,Boxes,Check,LoaderCircle,Plus,RefreshCw,Save,Search,Trash2,X} from 'lucide-react';
import {appApi as api} from './api.js';
import {makeStep,moveStep,uiKey} from './model.js';
import {newTemplateDraft,templateDraft,templateDocument,librarySaveError} from './blockLibrary.js';
import FlowStep from './FlowStep.jsx';
import ActionInspector from './ActionInspector.jsx';

const sourceLabels={builtin:'内置模板',modified:'已修改内置模板',custom:'自定义模板'};

export default function BlockLibraryManager({user,seed,initialId,templates,names,shortNames,defaultVolume=1,onChanged,onClose}) {
  const [entries,setEntries]=useState([]),[draft,setDraft]=useState(()=>seed?templateDraft(seed):newTemplateDraft());
  const [base,setBase]=useState(null),[dirty,setDirty]=useState(!!seed),[selected,setSelected]=useState(null);
  const [loading,setLoading]=useState(true),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [query,setQuery]=useState(''),[device,setDevice]=useState(Object.keys(templates)[0] || '');
  const pending=useRef(false),editorBody=useRef(null);
  const locked=loading || !!busy;
  const saveError=librarySaveError(draft,base,entries);
  const sameName=entries.find(entry=>entry.id===draft.id);
  const action=draft.steps.find(step=>step._key===selected);

  useEffect(()=>{
    if(user?.role!=='admin') return;
    let disposed=false;
    api.functionBlocks().then(result=>{
      if(disposed)return;
      setEntries(result.entries);
      const chosen=result.entries.find(entry=>entry.id===initialId);
      if(chosen && !seed) {setDraft(templateDraft(chosen.block));setBase({id:chosen.id,version:chosen.version});setDirty(false);}
      else if(initialId && !seed) setError('此模板已不存在，请刷新后选择其他模板。');
    }).catch(failure=>{if(!disposed)setError(failure.message);}).finally(()=>{if(!disposed)setLoading(false);});
    return()=>{disposed=true;};
  },[]);
  useEffect(()=>{if(editorBody.current)editorBody.current.scrollTop=0;},[selected]);
  useEffect(()=>{if(error && editorBody.current)editorBody.current.scrollTop=0;},[error]);
  function discard() {return !dirty || window.confirm('离开会放弃尚未保存的模板修改，是否继续？');}
  function close() {if(!busy && discard())onClose();}
  useEffect(()=>{
    const handler=event=>{if(event.key==='Escape'){event.preventDefault();close();}};
    window.addEventListener('keydown',handler);return()=>window.removeEventListener('keydown',handler);
  },[dirty,locked]);
  function edit(update) {setDraft(current=>typeof update==='function'?update(current):{...current,...update});setDirty(true);setNotice('');setError('');}
  function choose(entry) {
    if(locked || !discard())return;
    setDraft(templateDraft(entry.block));setBase({id:entry.id,version:entry.version});setDirty(false);setSelected(null);setError('');setNotice('');
  }
  function create() {if(locked || !discard())return;setDraft(newTemplateDraft());setBase(null);setSelected(null);setDirty(false);setError('');setNotice('');}
  async function refresh() {
    if(pending.current || locked)return;
    pending.current=true;setLoading(true);setError('');
    try {
      const result=await api.functionBlocks();setEntries(result.entries);onChanged(result);
      setNotice('模板库已刷新。当前编辑内容保留，重新选择模板可载入其最新版本。');
    } catch(failure){setError(failure.message);}
    finally{pending.current=false;setLoading(false);}
  }
  function useExisting() {
    if(!sameName || locked)return;
    setBase({id:sameName.id,version:sameName.version});setNotice('保存时将更新同名模板；已有实验中的副本保持原样。');
  }
  function addAction() {
    if(!templates[device] || locked)return;
    const step=makeStep(templates[device],defaultVolume);edit(current=>({...current,steps:[...current.steps,step]}));setSelected(step._key);
  }
  function updateAction(values){edit(current=>({...current,steps:current.steps.map(step=>step._key===selected?{...step,...values}:step)}));}
  function copyAction(key){
    const source=draft.steps.find(step=>step._key===key),copy={...structuredClone(source),_key:uiKey()};
    if(copy.id)copy.id='copy_'+copy._key.replaceAll('-','_');
    edit(current=>({...current,steps:current.steps.flatMap(step=>step._key===key?[step,copy]:[step])}));setSelected(copy._key);
  }
  function deleteAction(key){edit(current=>({...current,steps:current.steps.filter(step=>step._key!==key)}));if(selected===key)setSelected(null);}
  async function save(event) {
    event.preventDefault();if(pending.current || locked || saveError || user?.role!=='admin')return;
    pending.current=true;setBusy('save');setError('');
    try {
      const document=templateDocument(draft);
      const result=base?await api.updateFunctionBlock(base.id,document,base.version):await api.createFunctionBlock(document);
      setEntries(result.library.entries);onChanged(result.library);
      setBase({id:result.id,version:result.version});setDirty(false);setNotice('模板已保存；左侧功能块库已更新，刷新网页后仍可使用。');
    }catch(failure){setError(failure.message);}
    finally{pending.current=false;setBusy('');}
  }
  async function remove() {
    if(!base || pending.current || locked || user?.role!=='admin')return;
    if(!window.confirm('从功能块库中删除 '+base.id+'？已有实验中的副本会保留；当前未保存的模板修改会被放弃。'))return;
    pending.current=true;setBusy('delete');setError('');
    try {
      const result=await api.deleteFunctionBlock(base.id,base.version);setEntries(result.library.entries);onChanged(result.library);
      setDraft(newTemplateDraft());setBase(null);setSelected(null);setDirty(false);setNotice('模板已从功能块库删除。');
    }catch(failure){setError(failure.message);}
    finally{pending.current=false;setBusy('');}
  }
  if(user?.role!=='admin')return <div role="alert">功能块模板管理需要管理员权限。</div>;
  return <div className="modal-backdrop" onMouseDown={close}><section className="modal block-library-dialog" role="dialog" aria-modal="true" aria-label="功能块模板管理" onMouseDown={event=>event.stopPropagation()}>
    <div className="modal-header"><h2><Boxes size={19}/>功能块模板库</h2><button className="icon-button" disabled={!!busy} onClick={close} aria-label="关闭功能块模板管理"><X size={19}/></button></div>
    <p className="library-intro">保存后可直接从左侧添加到任意实验。模板修改仅影响以后添加的副本，已保存与运行中的实验保持原样。</p>
    <div className="library-workspace">
      <aside className="library-list-panel"><div className="library-list-toolbar"><button onClick={create} disabled={locked}><Plus size={14}/>新建模板</button><button onClick={refresh} disabled={locked} aria-label="刷新功能块库"><RefreshCw size={14}/></button></div>
        <label className="library-search"><Search size={14}/><input value={query} onChange={event=>setQuery(event.target.value)} placeholder="搜索模板…" aria-label="搜索功能块模板"/></label>
        <div className="library-entry-list">{loading?<p>正在读取模板库…</p>:entries.filter(entry=>(entry.id+' '+entry.block.description).toLowerCase().includes(query.trim().toLowerCase())).map(entry=><button key={entry.id} onClick={()=>choose(entry)} disabled={locked} aria-pressed={base?.id===entry.id} className={base?.id===entry.id?'selected':''}><strong>{entry.id}</strong><span>{entry.block.description}</span><small>{entry.step_count} 个动作 · {sourceLabels[entry.source] || '模板'}</small></button>)}{!loading && !entries.length && <p>还没有保存的功能块模板。</p>}</div>
      </aside>
      <form className="library-template-form" noValidate onSubmit={save}>
        <div className="library-editor-heading"><strong>{base?'编辑模板 · '+base.id:'新建可复用模板'}</strong><span>{dirty?'未保存修改':'模板参数'}</span></div>
        <fieldset disabled={locked} className="library-editor-body" ref={editorBody}>
          {error && <p className="library-error" role="alert">{error}</p>}{notice && <p className="library-notice" role="status"><Check size={14}/>{notice}</p>}
          {!base && sameName && <div className="library-collision"><p>同名模板已存在。可修改 ID 另存，或使用当前参数更新同名模板。</p><button type="button" onClick={useExisting}>更新此同名模板</button></div>}
          {action?<><button type="button" className="block-back" onClick={()=>setSelected(null)}><ArrowLeft size={14}/>返回模板动作列表</button><ActionInspector step={action} template={templates[action.device]} index={draft.steps.findIndex(step=>step._key===selected)} title={names[action.device] || action.device} onChange={updateAction}/></>:<div className="library-template-fields">
            <label className="parameter-field"><span>模板 ID · 英文，模板库内唯一</span><input value={draft.id} maxLength={80} aria-label="模板 ID" onChange={event=>edit({id:event.target.value})} spellCheck={false}/></label>
            <label className="parameter-field"><span>模板说明 · 英文</span><input value={draft.description} aria-label="模板说明" onChange={event=>edit({description:event.target.value})}/></label>
            <div className="library-add-action"><select value={device} onChange={event=>setDevice(event.target.value)} aria-label="模板动作设备">{Object.keys(templates).map(id=><option key={id} value={id}>{shortNames[id] || id}</option>)}</select><button type="button" onClick={addAction} disabled={!templates[device]}><Plus size={14}/>添加动作</button></div>
            <div className="block-action-list library-action-list">{draft.steps.map((step,index)=><FlowStep key={step._key} step={step} template={templates[step.device]} title={shortNames[step.device] || step.device} index={index} total={draft.steps.length} onSelect={()=>setSelected(step._key)} onMove={(from,to)=>edit(current=>({...current,steps:moveStep(current.steps,from,to)}))} onCopy={()=>copyAction(step._key)} onDelete={()=>deleteAction(step._key)}/>)}</div>
            {!draft.steps.length && <p className="block-empty-note">添加动作并设置参数，再保存为可复用模板。</p>}
          </div>}
        </fieldset>
        <footer className="library-editor-footer"><p>{saveError || '完整实验仍需校验整体液量、流路与启动前准备。'}</p><div>{base && <button type="button" className="library-delete" onClick={remove} disabled={locked}><Trash2 size={14}/>删除模板</button>}<button type="submit" className="primary-button" disabled={locked || !!saveError}>{busy==='save'?<LoaderCircle size={14} className="refresh-spinning"/>:<Save size={14}/>} {base && base.id!==draft.id?'重命名并保存':'保存模板'}</button></div></footer>
      </form>
    </div>
  </section></div>;
}
