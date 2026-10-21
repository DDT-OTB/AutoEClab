import React, { useEffect, useState } from 'react';
import { Activity, ArrowRight, Check, Eye, EyeOff, KeyRound, LockKeyhole, ShieldCheck, Workflow, X } from 'lucide-react';
import App from './App.jsx';
import { appApi as api } from './api.js';
import { passwordError } from './password.js';
import './auth.css';

function PasswordField({ label, value, onChange, autoComplete = 'current-password', autoFocus = false }) {
  const [visible, setVisible] = useState(false);
  return <label className="auth-field"><span>{label}</span><div className="auth-input password-control"><KeyRound size={17} /><input type={visible ? 'text' : 'password'} required value={value} onChange={event => onChange(event.target.value)} autoComplete={autoComplete} autoFocus={autoFocus} maxLength={256} aria-label={label} /><button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? '隐藏密码' : '显示密码'}>{visible ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>;
}

function LoginForm({ onLogin, notice }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event) {
    event.preventDefault(); setBusy(true); setError('');
    try { const session = await api.login(username.trim(), password); onLogin(session, password); }
    catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  return <form className="auth-form" onSubmit={submit}>
    <span className="auth-card-icon"><LockKeyhole size={23} /></span><div className="auth-eyebrow">WELCOME TO YOUR LAB</div><h2>登录实验工作台</h2><p className="auth-subtitle">使用授权账号，访问设备状态和实验方案。</p>
    {notice && <div className="auth-notice" role="status"><Check size={16} /><span>{notice}</span></div>}
    {error && <div className="auth-error" role="alert">{error}</div>}
    <label className="auth-field"><span>用户名</span><div className="auth-input"><ShieldCheck size={17} /><input value={username} onChange={event => setUsername(event.target.value)} autoComplete="username" autoFocus required maxLength={64} spellCheck={false} aria-label="用户名" /></div></label>
    <PasswordField label="密码" value={password} onChange={setPassword} />
    <button className="auth-submit" disabled={busy} type="submit">{busy ? '正在登录…' : '登录工作台'}<ArrowRight size={17} /></button>
    <div className="auth-help"><LockKeyhole size={13} /><span>仅供授权实验人员使用</span></div>
    <p className="auth-recovery">账号由管理员分配，不开放自行注册。<br/>忘记密码请联系管理员重置。</p>
  </form>;
}

function PasswordForm({ forced, initialPassword = '', onChanged, onCancel }) {
  const [current, setCurrent] = useState(initialPassword);
  const [password, setPassword] = useState('');
  const [confirmation, setConfirmation] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event) {
    event.preventDefault();
    const invalid = passwordError(password, confirmation);
    if (invalid) { setError(invalid); return; }
    setBusy(true); setError('');
    try { await api.changePassword(current, password); onChanged(); }
    catch (failure) { setError(failure.message); }
    finally { setBusy(false); }
  }
  return <form className="auth-form" onSubmit={submit}>
    <span className="auth-card-icon"><KeyRound size={23} /></span><div className="auth-eyebrow">ACCOUNT SECURITY</div><h2>{forced ? '设置你的登录密码' : '修改登录密码'}</h2><p className="auth-subtitle">{forced ? '首次登录需要更换临时密码，完成后即可进入工作台。' : '修改后将退出所有会话，请使用新密码重新登录。'}</p>
    {error && <div className="auth-error" role="alert">{error}</div>}
    <PasswordField label={forced ? '当前临时密码' : '当前密码'} value={current} onChange={setCurrent} />
    <PasswordField label="新密码" value={password} onChange={setPassword} autoComplete="new-password" autoFocus />
    <PasswordField label="确认新密码" value={confirmation} onChange={setConfirmation} autoComplete="new-password" />
    <p className="auth-password-hint">密码须大于 8 位，并包含小写字母和数字。</p>
    <button className="auth-submit" type="submit" disabled={busy}>{busy ? '正在保存…' : '更新密码并重新登录'}<ArrowRight size={17} /></button>
    {onCancel && <button className="auth-cancel" type="button" onClick={onCancel}>取消</button>}
  </form>;
}

function LoginLayout({ children }) {
  return <main className="login-page"><section className="login-story"><a href="/" className="login-brand"><span><Workflow size={25} /></span>Pump Console</a><div className="login-story-body"><span className="login-kicker">LABORATORY WORKSPACE</span><h1>专注实验。<br /><em>从安全访问开始。</em></h1><p>连接设备、编排流程、记录每一步。<br />让实验工作有序开展。</p><div className="login-flow-art" aria-hidden="true"><div className="login-art-head"><Activity size={16} /><span>LAB WORKSPACE</span><i /></div><div className="login-art-line"><span>01</span><div><b>Water</b><small>Peristaltic pump</small></div><i>10 mL</i></div><div className="login-art-line"><span>02</span><div><b>Electrolyte</b><small>Syringe pump</small></div><i>15 mL</i></div><div className="login-art-line"><span>03</span><div><b>Waste collection</b><small>Valve & drain pump</small></div><ShieldCheck size={18} /></div></div></div><div className="login-story-footer"><ShieldCheck size={16} /><span>设备监测 · 流程编排 · 配置管理</span></div></section><section className="login-form-pane"><div className="login-form-container">{children}<div className="login-footer">Pump Console <span>·</span> 实验室工作空间</div></div></section></main>;
}

export default function AuthGate() {
  const [user, setUser] = useState(null);
  const [checking, setChecking] = useState(true);
  const [notice, setNotice] = useState('');
  const [initialPassword, setInitialPassword] = useState('');
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [accountError, setAccountError] = useState('');
  const [loggingOut, setLoggingOut] = useState(false);
  useEffect(() => {
    let disposed = false;
    const check = async () => {
      try { const session = await api.session(); if (!disposed) setUser(session.user); }
      catch (failure) {
        if (!disposed) { setUser(null); if (failure.status !== 401) setNotice('暂时无法连接登录服务，请稍后重试。'); }
      } finally { if (!disposed) setChecking(false); }
    };
    check();
    const expired = () => {
      setUser(null);
      setNotice('登录状态已失效，请重新登录。'); setInitialPassword(''); setPasswordOpen(false);
      check();
    };
    window.addEventListener('pump-console-auth-required', expired);
    return () => { disposed = true; window.removeEventListener('pump-console-auth-required', expired); };
  }, []);
  useEffect(() => {
    if (!user || user.must_change_password) return;
    let lastTouch = Date.now();
    if (document.visibilityState === 'visible') api.touch().catch(() => {});
    const activity = () => {
      if (document.visibilityState !== 'visible' || Date.now() - lastTouch < 60000) return;
      lastTouch = Date.now();
      api.touch().catch(() => {});
    };
    document.addEventListener('pointerdown', activity);
    document.addEventListener('keydown', activity);
    document.addEventListener('wheel', activity, { passive: true });
    return () => { document.removeEventListener('pointerdown', activity); document.removeEventListener('keydown', activity); document.removeEventListener('wheel', activity); };
  }, [user]);
  function passwordChanged() {
    setUser(null); setPasswordOpen(false); setInitialPassword(''); setNotice('密码已更新，请使用新密码重新登录。');
  }
  async function logout() {
    setLoggingOut(true); setAccountError('');
    try { await api.logout(); setUser(null); setInitialPassword(''); setNotice('已安全退出。'); }
    catch (failure) { setAccountError('退出未完成：' + failure.message); }
    finally { setLoggingOut(false); }
  }
  if (checking) return <LoginLayout><div className="auth-checking"><ShieldCheck size={29} /><p>正在检查登录状态…</p></div></LoginLayout>;
  if (!user) return <LoginLayout><LoginForm notice={notice} onLogin={(session, password) => { setInitialPassword(session.user.must_change_password ? password : ''); setUser(session.user); setNotice(''); }} /></LoginLayout>;
  if (user.must_change_password) return <LoginLayout><PasswordForm forced initialPassword={initialPassword} onChanged={passwordChanged} /><button className="auth-cancel" onClick={logout} disabled={loggingOut}>退出登录</button></LoginLayout>;
  return <><App user={user} onLogout={logout} onChangePassword={() => setPasswordOpen(true)} loggingOut={loggingOut} />
    {accountError && <div className="auth-account-error" role="alert">{accountError}<button onClick={() => setAccountError('')} aria-label="关闭错误"><X size={15} /></button></div>}
    {passwordOpen && <div className="modal-backdrop"><section className="auth-password-modal" role="dialog" aria-modal="true" aria-label="修改登录密码"><button className="auth-modal-close" onClick={() => setPasswordOpen(false)} aria-label="关闭修改密码"><X size={19} /></button><PasswordForm onChanged={passwordChanged} onCancel={() => setPasswordOpen(false)} /></section></div>}
  </>;
}
