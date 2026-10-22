import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowDown, ArrowDownToLine, ArrowUp, ArrowUpRight, Check, CheckCheck,
  ChevronDown, ChevronRight, CircleHelp, Clipboard, Code2, Copy, Download,
  Droplet, FileJson, FlaskConical, FolderOpen, GripVertical, History,
  LayoutDashboard, Link2, LockKeyhole, Plus, RefreshCw, Save, Settings2,
  ShieldCheck, SlidersHorizontal, Trash2, Unplug, Users, Workflow, X,
} from 'lucide-react';
import { appApi as api } from './api.js';
import { draftErrors, fluidLabels, formatDuration, makeStep, moveStep, operationLabels, toEditor, toSequence, uiKey } from './model.js';
import { LiveRun, useLiveStatus } from './live.jsx';
import { liveStepIndex } from './monitor.js';
import { refreshAllStatus, refreshMessage } from './refresh.js';
import ActionInspector from './ActionInspector.jsx';
import FlowStep, { ActionIcon } from './FlowStep.jsx';
import { isMeasurement } from './cv.js';
import EquipmentOverview from './EquipmentOverview.jsx';
import AccountManagement from './AccountManagement.jsx';
import ExperimentManager from './ExperimentManager.jsx';
import AutomationControl from './AutomationControl.jsx';
import { sequenceKey } from './automation.js';
import { cellVolumes } from './cellSafety.js';
import { editorAfterFileChange, uniqueExperimentId } from './experiments.js';

const emptyDraft = () => ({
  schema_version: 2, id: 'new_experiment', description: 'New pump experiment',
  notes: ['Review this experiment before hardware execution.'], steps: [],
});
const names = {
  peristaltic_1: '泵 1 · 超纯水', peristaltic_2: '泵 2 · 排液', peristaltic_3: '泵 3 · 硫酸',
  syringe_c: '注射泵 · 电解液', valve: '切换阀',
  electrochemical_station: '电化学工作站',
};
const shortNames = {
  peristaltic_1: '超纯水泵', peristaltic_2: '废液泵', peristaltic_3: '硫酸泵', syringe_c: '注射泵', valve: '切换阀',
  electrochemical_station: '循环伏安法',
};
const timeText = value => value ? new Date(value).toLocaleString('zh-CN', {
  month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
}) : '暂无记录';

function DeviceIcon({ kind, size = 20 }) {
  return <ActionIcon kind={kind} size={size} />;
}

export default function App({ user, onLogout, onChangePassword, loggingOut }) {
  const [templates, setTemplates] = useState({});
  const [measurementTemplates, setMeasurementTemplates] = useState({});
  const actionTemplates = useMemo(() => ({ ...templates, ...measurementTemplates }), [templates, measurementTemplates]);
  const { status, accept: acceptStatus, streaming, fresh } = useLiveStatus(api);
  const [health, setHealth] = useState(null);
  const [catalog, setCatalog] = useState([]);
  const catalogRequest = useRef(0);
  const [runs, setRuns] = useState([]);
  const [draft, setDraft] = useState(emptyDraft);
  const [base, setBase] = useState({ id: null, version: null });
  const [dirty, setDirty] = useState(false);
  const [plan, setPlan] = useState(null);
  const [submittedSequence,setSubmittedSequence]=useState(null);
  const [connected, setConnected] = useState(false);
  const [latency, setLatency] = useState(null);
  const [refreshing, setRefreshing] = useState(false);
  const refreshLock = useRef(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [selected, setSelected] = useState(null);
  const [rightTab, setRightTab] = useState('parameters');
  const [modal, setModal] = useState(null);
  const [importText, setImportText] = useState('');
  const [dragIndex, setDragIndex] = useState(null);
  const [activeNav, setActiveNav] = useState('editor');
  const previousNav = useRef('editor');
  const historyRef = useRef(null);
  const editorRef = useRef(null);
  const inspectorRef = useRef(null);
  const editorSnapshot = useRef({draft,base,dirty});
  editorSnapshot.current={draft,base,dirty};
  const sequence = useMemo(() => toSequence(draft), [draft]);
  const cellBudget=useMemo(()=>cellVolumes(sequence,undefined,actionTemplates),[sequence,actionTemplates]);
  const errors = useMemo(() => draftErrors(draft, actionTemplates), [draft, actionTemplates]);
  const jsonText = useMemo(() => JSON.stringify(sequence, null, 2), [sequence]);
  const latestJson = useRef(jsonText);
  latestJson.current = jsonText;
  const selectedStep = draft.steps.find(step => step._key === selected);
  const selectedTemplate = selectedStep ? actionTemplates[selectedStep.device] : null;
  const measurementCount = draft.steps.filter(isMeasurement).length;
  const runningIndex = liveStepIndex(draft, status.runtime, dirty && submittedSequence!==sequenceKey(sequence), fresh);
  const totals = useMemo(() => {
    const result = { inject: 0, withdraw: 0 };
    draft.steps.forEach(step => {
      if (typeof step.volume_ml === 'number' && step.operation in result) result[step.operation] += step.volume_ml;
    });
    return result;
  }, [draft.steps]);

  async function refresh() {
    const started = performance.now();
    try {
      const [connection, devices, recent] = await Promise.all([api.health(), api.status(), api.runs()]);
      setHealth(connection); acceptStatus(devices); setRuns(recent.runs);
      setConnected(true); setLatency(Math.round(performance.now() - started));
      setError(current => current.startsWith('后端连接失败：') ? '' : current);
    } catch (failure) {
      setConnected(false); setError('后端连接失败：' + failure.message);
    }
  }
  async function refreshAll() {
    if (refreshLock.current || status.device_scan?.in_progress) return;
    refreshLock.current = true; setRefreshing(true);
    const started = performance.now();
    try {
      const data = await refreshAllStatus(api, user?.role === 'admin');
      setHealth(data.health); acceptStatus(data.status); setRuns(data.runs);
      if (JSON.stringify(data.templates) !== JSON.stringify(templates) || JSON.stringify(data.measurement_templates) !== JSON.stringify(measurementTemplates)) setPlan(null);
      setTemplates(data.templates);
      setMeasurementTemplates(data.measurement_templates || {});
      setConnected(true); setLatency(Math.round(performance.now() - started));
      setError(current => current.startsWith('状态刷新失败：') || current.startsWith('后端连接失败：') ? '' : current);
      if (data.scan.state === 'failed') setError('状态刷新失败：硬件读取未完成，请重试。');
      else setNotice(refreshMessage(data.scan, data.status.devices));
    } catch (failure) { setError('状态刷新失败：' + failure.message); }
    finally { refreshLock.current = false; setRefreshing(false); }
  }
  async function loadExperiment(id, force = false) {
    if (!force && dirty && !window.confirm('切换方案会放弃尚未保存的修改，是否继续？')) return false;
    setBusy('load'); setError('');
    try {
      const data = await api.experiment(id);
      const value = toEditor(data.sequence);
      setDraft(value); setBase({ id, version: data.version }); setDirty(false);
      setSelected(value.steps[0]?._key || null); setPlan(null);
      setRightTab('parameters');
      return true;
    } catch (failure) { setError(failure.message); return false; }
    finally { setBusy(''); }
  }
  async function reloadCatalog() {
    const request=++catalogRequest.current;
    const result=await api.experiments();
    if(request===catalogRequest.current) setCatalog(result.experiments);
    return result.experiments;
  }
  async function openManagedExperiment(id) {
    if(await loadExperiment(id)) setActiveNav('editor');
  }
  function fileChanged(kind,result) {
    const before=editorSnapshot.current;
    const next=editorAfterFileChange(before,kind,result);
    if(next.draft!==before.draft) setDraft(next.draft);
    if(next.base!==before.base) setBase(next.base);
    if(next.dirty!==before.dirty) setDirty(next.dirty);
    if(next.base!==before.base) setPlan(null);
  }
  useEffect(() => {
    (async () => {
      try {
        const [items, list] = await Promise.all([api.templates(), reloadCatalog()]);
        setTemplates(items.templates); setMeasurementTemplates(items.measurement_templates || {});
        const editable=list.filter(item=>item.editable!==false);
        const id = editable.some(item => item.id === 'demo_rinse_with_predrain')
          ? 'demo_rinse_with_predrain' : editable[0]?.id;
        if (id) await loadExperiment(id, true);
        await refresh();
      } catch (failure) { setError(failure.message); }
    })();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, []);
  useEffect(()=>{
    if(previousNav.current===activeNav) return;
    previousNav.current=activeNav;
    if(activeNav==='experiments') window.scrollTo({top:0,behavior:'smooth'});
    if(activeNav==='editor' || activeNav==='history') {
      const frame=requestAnimationFrame(()=>{(activeNav==='history'?historyRef:editorRef).current?.scrollIntoView({behavior:'smooth',block:'start'});});
      return ()=>cancelAnimationFrame(frame);
    }
  },[activeNav]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(''), 4500);
    return () => clearTimeout(timer);
  }, [notice]);
  useEffect(() => {
    const close = event => { if (event.key === 'Escape') setModal(null); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, []);
  function change(update) {
    setDraft(current => typeof update === 'function' ? update(current) : { ...current, ...update });
    setDirty(true); setPlan(null); setError('');
  }
  function addStep(id) {
    const step = makeStep(actionTemplates[id], health?.ui.default_volume_ml ?? 1);
    change(current => ({ ...current, steps: [...current.steps, step] }));
    selectAction(step._key);
  }
  function selectAction(key) {
    setSelected(key); setRightTab('parameters');
    if (window.matchMedia('(max-width: 980px)').matches) inspectorRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }
  function copyAction(key) {
    const original = draft.steps.find(step => step._key === key);
    const copy = { ...structuredClone(original), _key: uiKey() };
    if (copy.id) copy.id = 'copy_' + copy._key.replaceAll('-', '_');
    change(current => ({ ...current, steps: current.steps.flatMap(step => step._key === key ? [step, copy] : [step]) }));
    selectAction(copy._key);
  }
  function deleteAction(key) {
    const index = draft.steps.findIndex(step => step._key === key);
    const remaining = draft.steps.filter(step => step._key !== key);
    change(current => ({ ...current, steps: remaining }));
    if (selected === key) setSelected(remaining[Math.min(index, remaining.length-1)]?._key || null);
  }
  function updateStep(key, values) {
    change(current => ({ ...current, steps: current.steps.map(step => step._key === key ? { ...step, ...values } : step) }));
  }
  function reorder(from, to) { change(current => ({ ...current, steps: moveStep(current.steps, from, to) })); }
  async function preview() {
    if (errors.length) { setError(errors[0]); return; }
    setBusy('preview'); setError('');
    const submittedJson = jsonText;
    try {
      const result = await api.preview(sequence);
      if (latestJson.current !== submittedJson) { setNotice('流程参数已变化，请重新校验当前版本。'); return; }
      setPlan(result); setRightTab('summary');
      setNotice(measurementCount ? '参数预演完成；测量步骤尚未接入工作站，未执行设备动作。' : '离线预演完成，未发送硬件指令。');
    } catch (failure) { setError(failure.message); }
    finally { setBusy(''); }
  }
  async function save() {
    if (errors.length) { setError(errors[0]); return; }
    setBusy('save'); setError('');
    const submittedJson = jsonText;
    try {
      const data = await api.save(sequence, base.id === sequence.id ? base.version : undefined);
      setBase({ id: sequence.id, version: data.version }); setDirty(latestJson.current !== submittedJson);
      await reloadCatalog();
      setNotice(latestJson.current !== submittedJson ? '已保存提交的版本；后续参数修改仍未保存。' : '已保存 ' + data.filename + ' 和同名说明文档。');
    } catch (failure) { setError(failure.message); }
    finally { setBusy(''); }
  }
  function download() {
    const url = URL.createObjectURL(new Blob([jsonText + '\n'], { type: 'application/json' }));
    const link = document.createElement('a');
    link.href = url; link.download = (draft.id || 'experiment') + '.json'; link.click();
    URL.revokeObjectURL(url);
  }
  function createNew() {
    if (dirty && !window.confirm('新建方案会放弃尚未保存的修改，是否继续？')) return false;
    setDraft({...emptyDraft(),id:uniqueExperimentId('new_experiment',catalog)}); setBase({ id: null, version: null }); setDirty(true);
    setPlan(null); setSelected(null); setError('');
    setRightTab('parameters');setActiveNav('editor');setModal(null);return true;
  }
  async function importFile(event) {
    const file=event.target.files?.[0];
    if(!file) return;
    if(file.size>262144) {setError('导入文件不能超过 256 KB。');event.target.value='';return;}
    try {setImportText(await file.text());}
    catch(failure) {setError('读取文件失败：'+failure.message);}
    event.target.value='';
  }
  function importSequence() {
    try {
      const value = JSON.parse(importText);
      if (value.schema_version !== 2 || !Array.isArray(value.steps)) throw new Error('需要 schema_version=2 的实验序列。');
      if(dirty && !window.confirm('导入方案会替换尚未保存的编排内容，是否继续？')) return;
      const editor = toEditor(value);
      setDraft(editor); setBase({ id: null, version: null }); setDirty(true);
      setPlan(null); setSelected(editor.steps[0]?._key); setModal(null); setError('');
      setRightTab('parameters');setActiveNav('editor');
    } catch (failure) { setError('导入失败：' + failure.message); }
  }

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="/" aria-label="Pump Console 首页"><span className="brand-mark"><Workflow size={23} /></span><span>Pump Console<small>LABORATORY WORKSPACE</small></span></a>
      <div className="workspace-label">WORKSPACE</div>
      <nav aria-label="主导航">
        <button className={activeNav === 'editor' ? 'nav-active' : ''} onClick={() => {if(activeNav==='editor') editorRef.current?.scrollIntoView({behavior:'smooth'});else setActiveNav('editor');}}><LayoutDashboard size={18} /><span>实验控制台</span></button>
        <button className={activeNav === 'experiments' ? 'nav-active' : ''} onClick={() => { setActiveNav('experiments'); setModal(null); }}><FolderOpen size={18} /><span>实验方案</span><em>{catalog.length}</em></button>
        <button onClick={() => setModal('templates')}><Settings2 size={18} /><span>设备模板</span></button>
        {user?.role==='admin' && <button onClick={()=>setModal('accounts')}><Users size={18}/><span>账号管理</span></button>}
        <button onClick={() => {if(activeNav==='history') historyRef.current?.scrollIntoView({behavior:'smooth'});else setActiveNav('history');}}><History size={18} /><span>运行记录</span></button>
      </nav>
      <div className="sidebar-note"><ShieldCheck size={19} /><strong>{health?.hardware_execution_enabled?'自动化工作模式':'配置工作模式'}</strong><p>编辑、校验与保存方案<br />{health?.hardware_execution_enabled?'管理员可启动实验':'硬件执行尚未启用'}</p></div>
      <button className="connection-tile" onClick={() => setModal('connection')}><span className={'status-dot ' + (connected ? 'green' : 'red')} /><span>{connected ? '后端已连接' : '等待后端连接'}<small>Lab server</small></span><ChevronRight size={15} /></button>
      <div className="sidebar-footer"><span className="avatar">L</span><span>Lab workspace<small>本地实验环境</small></span><CircleHelp size={16} /></div>
    </aside>
    <main className="main-area">
      <header className="topbar">
        <div><div className="breadcrumb">Workspace <ChevronRight size={12} /><span>{activeNav==='experiments'?'实验方案':'实验控制台'}</span></div><h1>{activeNav==='experiments'?'每一份方案，有序管理':'让实验流程，一目了然'}<span>{activeNav==='experiments'?'实验方案文件管理':'配置与监测工作台'}</span></h1></div>
        <div className="top-actions">{user?.role==="admin" && <button className="outline-button campus-account-shortcut" onClick={()=>setModal("accounts")}><Users size={14}/>账号管理</button>}<span className="account-label"><ShieldCheck size={14} />{user?.username}<small>{user?.role === 'viewer' ? '只读' : '管理员'}</small></span><button className="subtle-button account-password-button" onClick={onChangePassword}>修改密码</button><button className="outline-button logout-button" onClick={onLogout} disabled={loggingOut}>{loggingOut ? '正在退出…' : '退出登录'}</button><span className="mode-badge"><ShieldCheck size={13} />{health?.hardware_execution_enabled?'管理员执行模式':'硬件执行已锁定'}</span><button className="outline-button refresh-all-button" onClick={refreshAll} disabled={refreshing || status.device_scan?.in_progress} aria-busy={refreshing || !!status.device_scan?.in_progress}><RefreshCw size={16} className={refreshing || status.device_scan?.in_progress ? 'refresh-spinning' : ''} />{refreshing || status.device_scan?.in_progress ? '正在刷新…' : user?.role === 'viewer' ? '更新状态显示' : '刷新全部状态'}</button><button className="subtle-button" onClick={() => setModal('connection')}><Link2 size={15} />连接详情</button></div>
      </header>
      <div className="content">
        {error && <div className="error-banner" role="alert"><Unplug size={17} /><span>{error}</span><button onClick={() => setError('')} aria-label="关闭错误"><X size={16} /></button></div>}
        <AutomationControl templates={actionTemplates} user={user} sequence={sequence} status={status} health={health} fresh={fresh} errors={errors} measurementCount={measurementCount} busy={busy} names={names} onPlan={setPlan} onRefresh={refresh} onStarted={value=>setSubmittedSequence(sequenceKey(value))}/>
        <LiveRun runtime={status.runtime} fresh={fresh} streaming={streaming} names={names} />
        {activeNav==='experiments' ? <ExperimentManager items={catalog} user={user} activeId={base.id} dirty={dirty} appBusy={busy} onReload={reloadCatalog} onOpen={openManagedExperiment} onNew={createNew} onImport={()=>{setImportText('');setModal('import');}} onBack={()=>setActiveNav('editor')} onChanged={fileChanged}/> : <>
        <EquipmentOverview templates={templates} measurementTemplates={measurementTemplates} status={status} fresh={fresh} streaming={streaming} onRefresh={refreshAll} refreshing={refreshing} role={user?.role}/>

        <section className="experiment-section" ref={editorRef}>
          <div className="section-heading editor-heading"><div><span className="eyebrow">EXPERIMENT BUILDER</span><h2>编排实验流程 <span className="count">{draft.steps.length} 步</span></h2></div><div className="builder-actions"><button className="outline-button" onClick={()=>setActiveNav('experiments')}><FolderOpen size={15}/>管理方案</button><button className="subtle-button" onClick={() => { setImportText(''); setModal('import'); }}><ArrowDown size={15} />导入 JSON</button><button className="outline-button" onClick={createNew}><Plus size={15} />新建方案</button></div></div>
          <div className="experiment-form">
            <label className="scheme-select"><span>加载已有方案</span><div className="select-wrap"><select value={base.id || ''} onChange={event => loadExperiment(event.target.value)} aria-label="加载已有方案"><option value="" disabled>选择实验方案</option>{catalog.map(item => <option key={item.id} value={item.id}>{item.id}</option>)}</select><ChevronDown size={15} /></div></label>
            <label><span>方案 ID <em>英文文件名</em></span><input value={draft.id} onChange={event => change({ id: event.target.value })} aria-label="方案 ID" spellCheck={false} /></label>
            <label className="description-field"><span>方案描述 <em>保存为英文 JSON</em></span><input value={draft.description} onChange={event => change({ description: event.target.value })} aria-label="方案描述" /></label>
          </div>
          <div className="builder-grid">
            <aside className="palette"><div className="panel-heading"><span>添加操作</span><span className="tiny-label">DEVICES</span></div><p className="panel-intro">选择动作，在右侧编辑参数。<br />其余设置继承对应模板。</p>
              {Object.entries(templates).map(([id, template]) => <button className={'palette-item fluid-' + (template.action.fluid || 'valve')} key={id} onClick={() => addStep(id)}><span className="palette-icon"><DeviceIcon kind={template.device.kind} size={17} /></span><span><strong>{shortNames[id] || id}</strong><small>{operationLabels[template.action.operation]} · {fluidLabels[template.action.fluid] || '废液流路'}</small></span><Plus size={16} /></button>)}
              <div className="measurement-palette-heading">仪器测量 <span>参数草稿</span></div>
              {Object.entries(measurementTemplates).map(([id, template]) => <button className="palette-item fluid-measurement" key={id} onClick={() => addStep(id)}><span className="palette-icon"><ActionIcon kind="electrochemical" size={17} /></span><span><strong>循环伏安法</strong><small>CV · 工作站待接入</small></span><Plus size={16} /></button>)}
              <div className="palette-tip"><GripVertical size={17} /><span>拖动步骤可调整顺序<br />也可使用上下移动按钮</span></div><div className="template-link"><Settings2 size={14} /><button onClick={() => setModal('templates')}>查看设备默认参数</button></div>
            </aside>
            <div className="flow-canvas"><div className="canvas-caption"><span><Workflow size={15} />顺序执行</span><span>点击节点，在右侧编辑参数</span></div>
              {!draft.steps.length && <div className="empty-canvas"><span><Workflow size={30} /></span><h3>从第一个操作开始</h3><p>点击左侧设备，将操作加入实验流程。</p></div>}
              <div className="flow-start"><span />实验开始</div>
              {draft.steps.map((step, index) => <div className="step-wrap" key={step._key}
                onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); if (dragIndex !== null) reorder(dragIndex,index); setDragIndex(null); }}>
                <div className="timeline-number">{String(index+1).padStart(2,'0')}</div>
                <FlowStep step={step} index={index} template={actionTemplates[step.device]} title={shortNames[step.device] || step.device}
                  cellVolume={cellBudget.steps[index]} cellCapacity={cellBudget.capacity_ml}
                  selected={selected===step._key} running={runningIndex===index} total={draft.steps.length}
                  onSelect={() => selectAction(step._key)} onMove={reorder} onCopy={() => copyAction(step._key)} onDelete={() => deleteAction(step._key)}
                  onDragStart={event => { event.dataTransfer.setData('text/plain',String(index)); setDragIndex(index); }}
                  onDragEnd={() => setDragIndex(null)} />
              </div>)}
              <button className="append-step" onClick={() => addStep(Object.keys(templates)[0])} disabled={!Object.keys(templates).length}><Plus size={16} />添加一个操作</button><div className="flow-end"><CheckCheck size={13} />实验结束</div>
            </div>
            <aside className="inspector" ref={inspectorRef}>
              <div className="inspector-tabs" role="tablist" aria-label="流程编辑侧栏">
                <button id="parameters-tab" role="tab" aria-controls="parameters-panel" aria-selected={rightTab==='parameters'} className={rightTab==='parameters' ? 'active' : ''} onClick={() => setRightTab('parameters')}>动作参数</button>
                <button id="summary-tab" role="tab" aria-controls="summary-panel" aria-selected={rightTab==='summary'} className={rightTab==='summary' ? 'active' : ''} onClick={() => setRightTab('summary')}>流程摘要</button>
                <button id="json-tab" role="tab" aria-controls="json-panel" aria-selected={rightTab==='json'} className={rightTab==='json' ? 'active' : ''} onClick={() => setRightTab('json')}><Code2 size={13} />JSON</button>
              </div>
              <div className="inspector-scroll" id={rightTab+'-panel'} role="tabpanel" aria-labelledby={rightTab+'-tab'}>
                {rightTab==='parameters' ? <ActionInspector key={selectedStep?._key || 'empty'} step={selectedStep} template={selectedTemplate}
                  index={draft.steps.findIndex(step => step._key===selected)} title={names[selectedStep?.device] || selectedStep?.device}
                  onChange={values => updateStep(selectedStep._key,values)} onOpenTemplates={() => setModal('templates')} /> :
                 rightTab==='json' ? <div className="json-panel"><div className="json-caption"><span>schema_version: 2</span><button onClick={async () => { try { await navigator.clipboard.writeText(jsonText); setNotice('JSON 已复制。'); } catch { setError('复制失败，请使用下载 JSON。'); } }} aria-label="复制 JSON"><Clipboard size={14} /></button></div><pre>{jsonText}</pre></div> : <div className="workflow-summary">
                  <div className="summary-head"><span className="summary-icon"><FlaskConical size={22} /></span><h3>当前实验方案</h3><p>{draft.steps.length} 个顺序步骤{measurementCount ? ' · '+measurementCount+' 个测量动作' : ''}</p></div>
                  <div className="summary-metrics"><div><span>目标注入</span><strong>{totals.inject.toLocaleString()}<small>mL</small></strong></div><div><span>标称抽出</span><strong>{totals.withdraw.toLocaleString()}<small>mL</small></strong></div></div>
                  <div className="estimate-row"><History size={17} /><div><span>计时动作合计</span><strong>{formatDuration(plan?.estimated_timed_seconds)}</strong></div></div><p className="estimate-note">含已设置测量的理论时长；不含切阀、通讯与额外等待。</p>
                  <div className={'validation-state '+(errors.length ? 'needs-review' : 'is-ready')}><span>{errors.length ? <CircleHelp size={17} /> : <Check size={17} />}</span><div><strong>{errors.length ? '还需检查' : plan ? '后端参数预演通过' : '本地参数检查通过'}</strong><p>{errors[0] || (plan ? '配置已校验，未执行设备动作。' : '点击“校验与预演”检查后端模板。')}</p></div></div>
                  {measurementCount > 0 && <div className="measurement-summary-note"><LockKeyhole size={16} /><div><strong>测量动作尚不能执行</strong><p>CV 参数会随方案保存。电化学工作站未接入，不会跳过测量去运行其余动作。</p></div></div>}
                  {plan?.warnings?.map((message,index) => <p className="summary-warning" key={index}>{message}</p>)}
                </div>}
              </div>
              <div className="inspector-footer"><LockKeyhole size={13} /><span>{health?.hardware_execution_enabled?'运行使用已确认快照；编辑不会改变当前任务':'当前为配置编排，设备动作未启用'}</span></div>
            </aside>
          </div>
          <footer className="builder-footer"><span><span className={'status-dot ' + (dirty ? 'amber' : 'green')} />{dirty ? '有未保存的修改' : '方案已加载'}<span className="footer-path">config/sequences/</span></span><div><button className="subtle-button" onClick={download}><Download size={15} />下载 JSON</button><button className="outline-button" onClick={preview} disabled={!!busy || !connected || !draft.steps.length}><Workflow size={15} />{busy === 'preview' ? '预演中…' : '校验与预演'}</button><button className="primary-button" onClick={save} disabled={!!busy || !connected || !draft.steps.length || user?.role === 'viewer'} title={user?.role === 'viewer' ? '当前账户为只读权限' : undefined}><Save size={15} />{busy === 'save' ? '保存中…' : '保存方案'}</button></div></footer>
        </section>
        <section className="history-section" ref={historyRef}><div className="section-heading"><div><span className="eyebrow">ACTIVITY</span><h2>最近运行记录</h2></div><span className="history-note">读取原有实验日志 · 不会启动设备</span></div><div className="history-table"><div className="history-row history-table-head"><span>实验方案</span><span>开始时间</span><span>完成步骤</span><span>结果</span></div>{runs.slice(0, 5).map(run => <button className="history-row" key={run.id} onClick={() => setModal({ run })}><span><FileJson size={16} />{run.sequence_id}</span><span>{timeText(run.started_at)}</span><span>{run.completed_steps} 步</span><span className={'run-tag ' + run.state}>{run.state === 'completed' ? '已完成' : run.state === 'aborted' ? '已中止' : '待确认'}<ChevronRight size={13} /></span></button>)}{!runs.length && <div className="empty-history">暂无可读取的历史运行记录</div>}</div></section>
        </>}
        <footer className="page-footer"><span>Pump Console <span>·</span> 配置工作台</span><span>连接{connected ? '正常' : '待恢复'} {latency !== null && ('· ' + latency + ' ms')} <span>·</span> JSON 使用英文</span></footer>
      </div>
    </main>
    {notice && <div className="toast" role="status"><Check size={17} />{notice}</div>}
    {modal && <div className="modal-backdrop" onMouseDown={() => setModal(null)}><section className="modal" role="dialog" aria-modal="true" aria-label="详情面板" onMouseDown={event => event.stopPropagation()}><div className="modal-header"><h2>{modal === 'accounts' ? '账号管理' : modal === 'import' ? '导入实验 JSON' : modal === 'connection' ? '前后端连接' : modal === 'templates' ? '设备模板' : '运行记录详情'}</h2><button className="icon-button" onClick={() => setModal(null)} aria-label="关闭面板"><X size={19} /></button></div>
      {modal === 'import' ? <><p className="modal-description">选择本机 JSON 文件，或粘贴英文配置，载入后可继续编排。</p><label className="file-import-input"><FileJson size={16}/><span>选择方案文件</span><input type="file" accept=".json,application/json" onChange={importFile} aria-label="选择实验方案 JSON 文件"/></label><textarea className="import-area" autoFocus value={importText} onChange={event => setImportText(event.target.value)} placeholder='{"schema_version":2,"id":"my_experiment","description":"...","steps":[]}' aria-label="导入 JSON 文本" /><button className="primary-button" onClick={importSequence}>载入编辑器<ArrowUpRight size={15} /></button></> :
        modal === 'connection' ? <><div className="connection-summary"><span className={'status-dot ' + (connected ? 'green' : 'red')} /><strong>{connected ? 'React 前端已连接 Python 后端' : '后端连接待恢复'}</strong></div><dl className="connection-details"><dt>服务</dt><dd>{health?.service || '—'}</dd><dt>工作模式</dt><dd>{health?.hardware_execution_enabled?'管理员自动化执行 / 离线预览':'配置 / 离线预览'}</dd><dt>当前来源</dt><dd>{window.location.origin}</dd><dt>实验数据</dt><dd>{health?.lab_root || '—'}</dd><dt>状态采样</dt><dd>控制程序事件，每秒推送；断线自动重连与轮询</dd><dt>设备状态读取</dt><dd>管理员手动触发，只发送白名单查询指令</dd><dt>硬件执行</dt><dd>{health?.hardware_execution_enabled?'仅管理员可确认启动方案、请求停止；不开放任意串口指令':'启动、停止、初始化及移动均禁用'}</dd></dl><button className="outline-button" onClick={refreshAll} disabled={refreshing || status.device_scan?.in_progress}><RefreshCw size={15} />{refreshing ? '正在刷新…' : '刷新全部状态'}</button></> :
        modal === 'accounts' ? <AccountManagement user={user}/> :
        modal === 'templates' ? <div className="template-grid">{Object.entries(actionTemplates).map(([id, template]) => <article key={id}><h3>{names[id] || id}</h3><small>{id==='electrochemical_station' ? 'action_templates/cyclic_voltammetry.json' : id+'.json'}</small><pre>{JSON.stringify(template, null, 2)}</pre></article>)}</div> :
        <><div className="modal-description">{modal.run?.sequence_id} · {modal.run?.completed_steps} 步完成</div><div className="run-events">{modal.run?.events.filter(item => ['step_started', 'step_completed', 'sequence_aborted', 'sequence_completed'].includes(item.event)).map((item, index) => <div key={index}><span className="status-dot gray" /><span>{timeText(item.time)}</span><strong>{item.event === 'step_started' ? item.step.description : item.event === 'step_completed' ? item.id + ' 已完成' : item.message || item.event}</strong></div>)}</div></>}
    </section></div>}
  </div>;
}
