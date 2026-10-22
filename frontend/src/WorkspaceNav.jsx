import React from 'react';
import {Activity,FilePenLine,FolderOpen,History,Users} from 'lucide-react';
import {workspacePages} from './workspace.js';

const icons={run:Activity,plans:FolderOpen,history:History};
export default function WorkspaceNav({active,onNavigate,count,user,onAccounts,mobile=false}) {
  return <nav className={mobile?'mobile-workspace-nav':''} aria-label={mobile?'页面导航':'主导航'}>
    {Object.entries(workspacePages).map(([id,page])=>{const Icon=icons[id];return <button type="button" key={id} className={active===id?'nav-active':''} aria-current={active===id?'page':undefined} aria-label={page.label} title={page.label} onClick={()=>onNavigate(id)}><Icon size={18}/><span>{page.label}</span>{id==='plans' && <em>{count}</em>}</button>;})}
    {user?.role==='admin' && <button type="button" onClick={onAccounts} aria-label="账号管理" title="账号管理"><Users size={18}/><span>账号管理</span></button>}
  </nav>;
}

export function PlanWorkspaceTabs({view,onChange,dirty}) {
  return <div className="plan-workspace-heading"><div><span className="eyebrow">EXPERIMENT PLANS</span><h2>实验方案</h2><p>管理方案文件，在同一工作区编排流程。</p></div><div className="plan-workspace-tabs" role="tablist" aria-label="实验方案工作区">
    <button type="button" role="tab" aria-selected={view==='files'} aria-controls="plan-workspace-panel" onClick={()=>onChange('files')}><FolderOpen size={15}/>文件管理</button>
    <button type="button" role="tab" aria-selected={view==='editor'} aria-controls="plan-workspace-panel" onClick={()=>onChange('editor')}><FilePenLine size={15}/>流程编辑{dirty && <i title="有未保存的修改"/>}</button>
  </div></div>;
}
