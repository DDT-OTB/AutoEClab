export async function refreshAllStatus(api, readHardware) {
  if (readHardware) return api.refreshStatus();
  const [health, status, recent, items] = await Promise.all([api.health(), api.status(), api.runs(), api.templates()]);
  return { health, status, templates: items.templates, measurement_templates: items.measurement_templates || {}, safety_policy:items.safety_policy, runs: recent.runs, scan: { state: 'display_only' } };
}

export function refreshMessage(scan, devices) {
  if (scan.state === 'skipped') return '实验正在运行或串口锁被占用，已更新程序状态，未发送硬件查询。';
  if (scan.state === 'failed') return '硬件状态读取未完成，请检查连接后重试。';
  if (scan.state === 'cached') return '设备状态刚刚已读取，当前显示最近结果。';
  if (scan.state === 'display_only') return '已更新状态显示；只读账户不会发起硬件查询。';
  const replied = devices.filter(device => device.hardware && !device.hardware.stale && (['idle','busy','connected','not_ready'].includes(device.hardware.state) || Number.isInteger(device.hardware.error_code))).length;
  return '已检查 ' + devices.length + ' 台设备：' + replied + ' 台收到应答，' + (devices.length - replied) + ' 台未连接或读取失败。';
}
