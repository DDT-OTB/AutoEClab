export async function refreshAllStatus(api, readHardware) {
  if (readHardware) return api.refreshStatus();
  const [health, status, recent, items] = await Promise.all([api.health(), api.status(), api.runs(), api.templates()]);
  return { health, status, templates: items.templates, measurement_templates: items.measurement_templates || {}, runs: recent.runs, scan: { state: 'display_only' } };
}

export function refreshMessage(scan, devices) {
  if (scan.state === 'skipped') return '实验正在运行或串口锁被占用，已更新程序状态，未发送硬件查询。';
  if (scan.state === 'failed') return '硬件状态读取未完成，请检查连接后重试。';
  if (scan.state === 'cached') return '设备状态刚刚已读取，当前显示最近结果。';
  if (scan.state === 'display_only') return '已更新状态显示；只读账户不会发起硬件查询。';
  const replied = devices.filter(device => device.hardware && ['idle', 'busy'].includes(device.hardware.state)).length;
  return '已检查 ' + devices.length + ' 台设备：' + replied + ' 台返回正常状态，' + (devices.length - replied) + ' 台需检查。';
}
