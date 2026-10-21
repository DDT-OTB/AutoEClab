import React, { useEffect, useState } from 'react';
import { Check, KeyRound, Plus, RefreshCw, ShieldCheck, Users } from 'lucide-react';
import { appApi as api } from './api.js';
import { passwordError } from './password.js';

export default function AccountManagement({ user }) {
  const [users,setUsers]=useState([]);
  const [loading,setLoading]=useState(true);
  const [target,setTarget]=useState(null);
  const [username,setUsername]=useState('');
  const [role,setRole]=useState('viewer');
  const [password,setPassword]=useState('');
  const [confirmation,setConfirmation]=useState('');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  async function load() {
    setLoading(true);
    try { const result=await api.users(); setUsers(result.users); }
    catch(failure) { setError(failure.message); }
    finally { setLoading(false); }
  }
  useEffect(()=>{if(user?.role==='admin') load();},[user?.role]);
  if(user?.role!=='admin') return <p className="account-admin-denied">账号管理需要管理员权限。</p>;
  function selectReset(account) {
    setTarget(account.username); setUsername(account.username); setPassword(''); setConfirmation(''); setError(''); setNotice('');
  }
  function newAccount() {
    setTarget(null); setUsername(''); setRole('viewer'); setPassword(''); setConfirmation(''); setError('');
  }
  async function submit(event) {
    event.preventDefault();
    if(busy) return;
    const invalid=passwordError(password,confirmation);
    if(invalid) {setError(invalid);return;}
    setBusy(true);setError('');setNotice('');
    try {
      if(target) await api.resetUserPassword(target,password);
      else await api.createUser(username.trim(),password,role);
      setNotice(target?'临时密码已重置，该账号原有登录已失效。':'账号已创建，请将临时密码单独交给使用者。');
      newAccount();await load();
    } catch(failure) {setError(failure.message);}
    finally {setBusy(false);}
  }
  return <div className="account-management">
    <p className="account-admin-intro"><ShieldCheck size={17}/><span>账号由管理员分配，不开放自行注册。新账号和重置密码后的账号，首次登录必须修改临时密码。</span></p>
    {error && <div className="auth-error" role="alert">{error}</div>}
    {notice && <div className="auth-notice" role="status"><Check size={15}/><span>{notice}</span></div>}
    <div className="account-admin-grid">
      <section className="account-user-list" aria-label="授权账号列表"><header><span><Users size={16}/>授权账号 <small>{users.length}</small></span><button type="button" onClick={load} disabled={loading || busy} aria-label="刷新账号列表"><RefreshCw size={14}/></button></header>
        {loading && <p className="account-list-loading">正在读取账号…</p>}
        {users.map(account=><article key={account.username}><div><strong>{account.username}{account.username===user.username && <small>当前账号</small>}</strong><span>{account.role==='admin'?'管理员':'观测员'} · {account.must_change_password?'待首次改密':'已设置密码'}</span></div>
          {account.username!==user.username && <button type="button" disabled={busy} onClick={()=>selectReset(account)}><KeyRound size={13}/>重置密码</button>}
        </article>)}
      </section>
      <form className="account-provision-form" onSubmit={submit} aria-label={target?'重置账号临时密码':'创建授权账号'}>
        <div className="account-provision-title"><strong>{target?'重置临时密码':'创建授权账号'}</strong>{target && <button type="button" onClick={newAccount} disabled={busy}><Plus size={13}/>新建账号</button>}</div>
        <label><span>用户名</span><input required value={username} onChange={event=>setUsername(event.target.value)} disabled={!!target || busy} autoComplete="off" maxLength={64} pattern="[A-Za-z][A-Za-z0-9_-]{0,63}" placeholder="例如 researcher01" aria-label="新账号用户名" spellCheck={false}/></label>
        {!target && <label><span>账号权限</span><select value={role} onChange={event=>setRole(event.target.value)} disabled={busy} aria-label="新账号权限"><option value="viewer">观测员 · 查看状态与预演</option><option value="admin">管理员 · 配置与账号管理</option></select></label>}
        <label><span>临时密码</span><input type="password" required value={password} onChange={event=>setPassword(event.target.value)} disabled={busy} autoComplete="new-password" maxLength={128} aria-label="账号临时密码"/></label>
        <label><span>确认临时密码</span><input type="password" required value={confirmation} onChange={event=>setConfirmation(event.target.value)} disabled={busy} autoComplete="new-password" maxLength={128} aria-label="确认账号临时密码"/></label>
        <p>密码须大于 8 位，并包含小写字母和数字。临时密码在 24 小时内有效，使用者首次登录后须更换。</p>
        <button className="primary-button" type="submit" disabled={busy}><ShieldCheck size={14}/>{busy?'正在保存…':target?'设置临时密码':'创建账号'}</button>
      </form>
    </div>
  </div>;
}
