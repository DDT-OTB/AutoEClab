export function deviceActivity(device, runtime, fresh) {
  const activity = device?.activity;
  if (!fresh) return { state: 'unknown', label: '状态待确认' };
  if (!['running', 'error'].includes(runtime?.state) || !activity || runtime?.controller_alive===false) {
    if (runtime?.controller_alive === true) return { state: 'unknown', label: '程序状态待确认' };
    const hardware = device?.hardware;
    if (!hardware) return { state: 'unknown', label: '状态待确认' };
    if (hardware.stale) return { state: 'unknown', label: '状态待刷新' };
    const labels = { idle: '空闲', busy: '工作中', not_ready: '未就绪', no_response: '无应答',
      port_missing: '串口缺失', in_use: '串口被占用', error: '设备异常', unsupported: '暂不支持',connected:'已连接' };
    return { ...hardware, source: 'hardware', label: labels[hardware.state] || '状态待确认' };
  }
  if (activity.state === 'error') return { ...activity, label: '执行异常' };
  if (activity.state === 'executing') {
    const labels = { dose: '正在注入', drain: '正在抽出', drain_direct:'正在抽出', syringe_transfer: '吸入 / 注入中', switch_valve: '正在切阀', wait: '等待中', initialize_syringe: '正在机械回零校验', measurement:'正在采集 CV' };
    return { ...activity, label: labels[activity.action] || '正在执行' };
  }
  return { ...activity, label: activity.state === 'completed' ? '本次动作完成' : '程序待命' };
}

export function liveStepIndex(draft, runtime, dirty, fresh) {
  if (!fresh || dirty || runtime?.state !== 'running' || runtime.sequence_id !== draft.id) return -1;
  const live = runtime.active_step;
  if (!live || live.state !== 'executing') return -1;
  const index = live.index - 1, step = draft.steps[index];
  if (!step) return -1;
  return ['device', 'operation', 'volume_ml', 'fluid', 'route'].every(key => step[key] === live[key]) ? index : -1;
}

export function progressPercent(activity) {
  return Number.isFinite(activity?.progress_fraction) ? Math.max(0, Math.min(99, Math.round(activity.progress_fraction * 100))) : null;
}
