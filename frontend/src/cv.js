export const MEASUREMENT_DEVICE = 'electrochemical_station';
export const CV_FIELDS = ['initial_potential_v', 'upper_potential_v', 'lower_potential_v', 'final_potential_v',
  'initial_scan_direction', 'scan_rate_v_s', 'potential_step_v', 'cycles', 'quiet_time_s', 'current_range_a', 'reference_electrode'];
export const isMeasurement = step => step?.device === MEASUREMENT_DEVICE || step?.operation === 'measure';
const finite = value => typeof value === 'number' && Number.isFinite(value);

export function cvErrors(parameters) {
  const p = parameters, errors = [];
  if (!p || typeof p !== 'object' || Array.isArray(p) || Object.keys(p).length !== CV_FIELDS.length ||
      CV_FIELDS.some(key => !(key in p))) return ['CV 参数字段缺失或包含未支持的字段。'];
  const potentialKeys = ['initial_potential_v', 'upper_potential_v', 'lower_potential_v', 'final_potential_v'];
  if (potentialKeys.some(key => !finite(p[key]))) errors.push('电位必须填写有限数值。');
  else {
    if (!(p.lower_potential_v < p.upper_potential_v)) errors.push('下限电位必须小于上限电位。');
    for (const key of ['initial_potential_v', 'final_potential_v']) {
      if (p[key] < p.lower_potential_v || p[key] > p.upper_potential_v) {
        errors.push('起始和终止电位必须位于电位上下限之间。'); break;
      }
    }
  }
  if (!['positive', 'negative'].includes(p.initial_scan_direction)) errors.push('请选择起始扫描方向。');
  if (!finite(p.scan_rate_v_s) || p.scan_rate_v_s <= 0) errors.push('扫描速率必须大于 0。');
  if (!finite(p.potential_step_v) || p.potential_step_v <= 0) errors.push('采样电位间隔必须大于 0。');
  else if (p.potential_step_v > p.upper_potential_v - p.lower_potential_v) errors.push('采样电位间隔不能大于整个电位窗口。');
  if (!Number.isInteger(p.cycles) || p.cycles < 1 || p.cycles > 10000) errors.push('扫描圈数必须是 1 至 10000 的整数。');
  if (!finite(p.quiet_time_s) || p.quiet_time_s < 0) errors.push('静置时间不能小于 0。');
  if (p.current_range_a !== null && (!finite(p.current_range_a) || p.current_range_a <= 0)) errors.push('固定电流量程必须大于 0。');
  if (typeof p.reference_electrode !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9 /()+.,_-]{0,79}$/.test(p.reference_electrode))
    errors.push('参比电极请使用英文标识，例如 RHE 或 Ag/AgCl。');
  if (!errors.length && (!Number.isFinite(cvDurationRaw(p)) || cvDurationRaw(p) <= 0)) errors.push('参数产生的预计测量时长无效。');
  return errors;
}
function cvDurationRaw(p) {
  const first = p.initial_scan_direction === 'positive' ? p.upper_potential_v : p.lower_potential_v;
  const second = p.initial_scan_direction === 'positive' ? p.lower_potential_v : p.upper_potential_v;
  const distance = Math.abs(first-p.initial_potential_v) + (2*p.cycles-1)*Math.abs(second-first) + Math.abs(p.final_potential_v-second);
  return p.quiet_time_s + distance/p.scan_rate_v_s;
}
export function cvDuration(p) { return cvErrors(p).length ? null : cvDurationRaw(p); }

export function cvWaveform(p, maxCycles = 3) {
  if (cvErrors(p).length) return null;
  const shownCycles = Math.min(p.cycles, maxCycles);
  const first = p.initial_scan_direction === 'positive' ? p.upper_potential_v : p.lower_potential_v;
  const second = p.initial_scan_direction === 'positive' ? p.lower_potential_v : p.upper_potential_v;
  const points = [{ time: 0, potential: p.initial_potential_v }];
  let time = p.quiet_time_s, potential = p.initial_potential_v;
  if (time) points.push({ time, potential });
  for (let cycle = 0; cycle < shownCycles; cycle++) {
    for (const target of [first, second]) {
      time += Math.abs(target-potential)/p.scan_rate_v_s; potential = target;
      points.push({ time, potential });
    }
  }
  time += Math.abs(p.final_potential_v-potential)/p.scan_rate_v_s;
  points.push({ time, potential: p.final_potential_v });
  return { points, duration: time, shownCycles, clipped: p.cycles > shownCycles };
}

export function stepSummary(step, template) {
  if (isMeasurement(step)) {
    const p = step.parameters || {};
    return ['CV', Number.isFinite(p.scan_rate_v_s) ? (p.scan_rate_v_s*1000).toLocaleString() + ' mV/s' : '速率待设置',
      Number.isInteger(p.cycles) ? p.cycles + ' 圈' : '圈数待设置'].join(' · ');
  }
  if (template?.device.kind === 'valve')
    return ({water:'水',electrolyte:'电解液',waste:'废液瓶',waste_1:'废液 1',waste_2:'废液 2'}[step.route] || step.route || '流路待选择') +
      ' · 通道 ' + (template.device.routes[step.route] ?? '?');
  return (step.volume_ml === '' || step.volume_ml === undefined ? '体积待填写' : step.volume_ml + ' mL') +
    (template?.device.rpm ? ' · ' + template.device.rpm + ' rpm' :
      template?.device.speed_steps_s ? ' · ' + template.device.speed_steps_s + ' 步/秒' : '');
}
