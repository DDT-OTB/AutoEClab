import React, { useCallback, useEffect, useState } from 'react';
import { Activity, Radio, Timer } from 'lucide-react';
import { deviceActivity, progressPercent } from './monitor.js';
import './live.css';

export function useLiveStatus(api) {
  const [status, setStatus] = useState({ devices: [] });
  const [streaming, setStreaming] = useState(false);
  const [receivedAt, setReceivedAt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const accept = useCallback(value => {
    setStatus(previous => previous.sampled_at && previous.sampled_at > value.sampled_at ? previous : value);
    setReceivedAt(Date.now());
  }, []);
  useEffect(() => {
    let closed = false, fallback = null, pending = false;
    const poll = async () => {
      if (pending || closed) return;
      pending = true;
      try { const value = await api.status(); if (!closed) accept(value); }
      catch { if (!closed) setReceivedAt(0); }
      finally { pending = false; }
    };
    const startPolling = () => { if (!fallback) fallback = setInterval(poll, 1000); };
    const clock = setInterval(() => setNow(Date.now()), 1000);
    let source;
    try {
      source = new EventSource('/api/events');
      source.addEventListener('session_expired', () => {
        if (closed) return;
        setStreaming(false); setReceivedAt(0); source.close();
        window.dispatchEvent(new CustomEvent('pump-console-auth-required'));
      });
      source.addEventListener('status', event => {
        if (closed) return;
        try {
          const value = JSON.parse(event.data);
          if (!Array.isArray(value.devices) || !value.runtime) throw new Error('Invalid status frame');
          accept(value); setStreaming(true);
          if (fallback) { clearInterval(fallback); fallback = null; }
        } catch { setStreaming(false); startPolling(); }
      });
      source.onerror = () => { if (!closed) { setStreaming(false); setReceivedAt(0); startPolling(); } };
    } catch { startPolling(); }
    return () => { closed = true; source?.close(); clearInterval(clock); clearInterval(fallback); };
  }, [api, accept]);
  return { status, accept, streaming, fresh: receivedAt > 0 && now - receivedAt <= 3500 };
}

export function LiveRun({ runtime, fresh, streaming, names }) {
  const state = fresh ? runtime?.state : 'disconnected';
  const active = runtime?.active_step;
  const running = state === 'running';
  const activity = deviceActivity({ activity: active }, runtime, fresh);
  const progress = running ? progressPercent(active) : null;
  const titles = { running: '控制程序正在运行', idle: '当前未检测到运行中的控制程序', checking: '正在读取仪器状态', starting: '控制程序准备中',
    unresponsive: '运行日志未及时更新，状态待确认', disconnected: '实时连接待恢复', unknown: '运行状态待确认',
    error: '控制程序执行异常', completed: '本次程序已完成', aborted: '本次程序已中止' };
  return <section className={'runtime-panel ' + (running ? 'is-running' : ['error', 'aborted', 'unresponsive'].includes(state) ? 'is-warning' : '')} aria-label="实时运行状态">
    <span className={'runtime-icon ' + (running ? 'pulse-icon' : '')}><Activity size={23} /></span>
    <div className="runtime-body"><div className="runtime-heading"><strong>{titles[state] || '运行状态待确认'}</strong><span><Radio size={12} />{fresh ? streaming ? '实时推送 · 1 秒' : '自动轮询 · 1 秒' : '连接待确认'}</span></div>
      {running && active ? <><div className="runtime-operation"><b>{names[active.device] || active.device}</b><span>{activity.label}</span>{active.volume_ml !== undefined && <b>{active.volume_ml} mL</b>}{active.route && <b>{active.route}</b>}<small>第 {active.index} / {runtime.step_count || '?'} 步</small></div>
        <div className="runtime-progress"><div role="progressbar" aria-label={active.action==='measurement'?'已完成电位段进度':'当前步骤估算进度'} aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress ?? undefined} className={progress === null ? 'indeterminate' : ''}><i style={{ width: progress === null ? '35%' : progress + '%' }} /></div><span>{progress === null ? '等待动作反馈' : active.progress_is_estimate===false?'已完成电位段 '+progress+'%':'估算 ' + progress + '%'}</span></div>
        <div className="runtime-caption"><span>{runtime.sequence_id}</span><span><Timer size={12} />已执行 {active.elapsed_seconds.toFixed(1)} 秒{active.estimated_seconds ? ' / 预计 ' + active.estimated_seconds.toFixed(1) + ' 秒' : ''}</span></div></> :
        <p className="runtime-caption">{running ? '正在检查设备或衔接下一步。' : state === 'unresponsive' ? '已停止显示运行动画，等待新的程序事件。' : ['completed','aborted'].includes(state) ? runtime.sequence_id+' · 已完成 '+runtime.completed_steps+' / '+runtime.step_count+' 步。'+(state==='completed'?'控制程序已报告本次方案完成。':'请结合运行结果与停止回读检查设备。') : '通过网页启动或现有 Python 控制程序执行实验时，装置图、设备列表与当前步骤会自动更新。'}</p>}
    </div>
  </section>;
}
