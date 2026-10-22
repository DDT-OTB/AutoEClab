import React from 'react';
import { ArrowDown, ArrowRight, CircleHelp, LockKeyhole, MousePointer2, Settings2 } from 'lucide-react';
import { ActionIcon } from './FlowStep.jsx';
import { cvDuration, cvErrors, cvWaveform, isMeasurement } from './cv.js';
import { fluidLabels, formatDuration, operationLabels } from './model.js';

function NumberField({ label, value, onChange, unit, scale = 1, step = 'any', min, max }) {
  const display = typeof value === 'number' && Number.isFinite(value) ? Number((value*scale).toPrecision(12)) : '';
  return <label className="parameter-field"><span>{label}</span><div className="parameter-number"><input type="number" value={display} step={step} min={min} max={max} onChange={event => onChange(event.target.value === '' ? '' : Number(event.target.value)/scale)} aria-label={label} />{unit && <small>{unit}</small>}</div></label>;
}

function VoltageProgram({ parameters }) {
  const wave = cvWaveform(parameters);
  if (!wave) return <div className="voltage-program-empty">补全有效参数后显示电位程序</div>;
  const p = parameters, width = p.upper_potential_v-p.lower_potential_v;
  const x = time => 42 + time/wave.duration*274;
  const y = potential => 117 - (potential-p.lower_potential_v)/width*89;
  const points = wave.points.map(point => x(point.time).toFixed(2)+','+y(point.potential).toFixed(2)).join(' ');
  return <figure className="voltage-program"><div className="parameter-section-heading">设定的电位程序 <span>{wave.clipped ? '前 3 圈示意' : p.cycles + ' 圈'}</span></div>
    <svg viewBox="0 0 340 151" role="img" aria-label="循环伏安法设定电位随时间变化示意，非实测数据">
      <title>循环伏安法设定电位程序，不是实测电流曲线</title>
      <line x1="42" y1="22" x2="42" y2="124" /><line x1="42" y1="124" x2="320" y2="124" />
      {[p.lower_potential_v, (p.upper_potential_v+p.lower_potential_v)/2, p.upper_potential_v].map((potential,index) => <g key={index}><line className="plot-grid" x1="42" x2="320" y1={y(potential)} y2={y(potential)} /><text x="35" y={y(potential)+3} textAnchor="end">{potential.toFixed(2)}</text></g>)}
      <polyline className="potential-program-line" points={points} />
      <text x="8" y="15">E / V</text><text x="42" y="140">0</text><text x="316" y="140" textAnchor="end">{wave.duration.toFixed(1)} s</text>
    </svg><figcaption>电位按所选参比电极计；此图仅展示设定程序。</figcaption>
  </figure>;
}

export default function ActionInspector({ step, template, index, title, onChange, onOpenTemplates }) {
  if (!step || !template) return <div className="inspector-empty"><MousePointer2 size={29} /><h3>选择一个动作</h3><p>点击流程节点，在这里编辑参数。</p></div>;
  const measurement = isMeasurement(step), config = template.device, p = step.parameters || {};
  const updateParameter = (key,value) => onChange({ parameters: { ...p, [key]: value } });
  return <div className="action-parameter-editor">
    <div className={'action-editor-heading fluid-' + (measurement ? 'measurement' : step.fluid || 'valve')}>
      <span className="action-editor-icon"><ActionIcon kind={config.kind} size={22} /></span><div><span className="action-editor-index">步骤 {String(index+1).padStart(2,'0')} · {operationLabels[step.operation]}</span><h3>{title}</h3><p>{measurement ? '循环伏安法 · CV' : step.device}</p></div>
    </div>
    {measurement ? <>
      <div className="measurement-integration-note"><LockKeyhole size={15} /><span>测量动作已预留，工作站尚未接入。可编辑并保存参数，当前不会进行实际测量。</span></div>
      <section className="parameter-group"><h4>电位窗口</h4><div className="parameter-grid">
        <NumberField label="起始电位" value={p.initial_potential_v} onChange={value => updateParameter('initial_potential_v',value)} unit="V" step="0.01" />
        <NumberField label="终止电位" value={p.final_potential_v} onChange={value => updateParameter('final_potential_v',value)} unit="V" step="0.01" />
        <NumberField label="下限电位" value={p.lower_potential_v} onChange={value => updateParameter('lower_potential_v',value)} unit="V" step="0.01" />
        <NumberField label="上限电位" value={p.upper_potential_v} onChange={value => updateParameter('upper_potential_v',value)} unit="V" step="0.01" />
      </div><label className="parameter-field"><span>起始扫描方向</span><select value={p.initial_scan_direction || ''} onChange={event => updateParameter('initial_scan_direction',event.target.value)} aria-label="起始扫描方向"><option value="positive">先向上限扫描（正向）</option><option value="negative">先向下限扫描（负向）</option></select></label></section>
      <section className="parameter-group"><h4>扫描与采样</h4><div className="parameter-grid">
        <NumberField label="扫描速率" value={p.scan_rate_v_s} onChange={value => updateParameter('scan_rate_v_s',value)} unit="mV/s" scale={1000} min="0" />
        <NumberField label="采样电位间隔" value={p.potential_step_v} onChange={value => updateParameter('potential_step_v',value)} unit="mV" scale={1000} min="0" />
        <NumberField label="扫描圈数" value={p.cycles} onChange={value => updateParameter('cycles',value)} unit="圈" min="1" max="10000" step="1" />
        <NumberField label="扫描前静置" value={p.quiet_time_s} onChange={value => updateParameter('quiet_time_s',value)} unit="s" min="0" />
      </div></section>
      <section className="parameter-group"><h4>量程与电位参照</h4><label className="parameter-field"><span>电流量程</span><select value={p.current_range_a === null ? 'auto' : 'manual'} onChange={event => updateParameter('current_range_a',event.target.value === 'auto' ? null : '')} aria-label="电流量程"><option value="auto">自动量程</option><option value="manual">指定固定量程</option></select></label>
        {p.current_range_a !== null && <NumberField label="固定电流量程" value={p.current_range_a} onChange={value => updateParameter('current_range_a',value)} unit="µA" scale={1000000} min="0" />}
        <label className="parameter-field"><span>参比电极</span><input list="cv-reference-options" value={p.reference_electrode === 'unspecified' ? '' : p.reference_electrode || ''} onChange={event => updateParameter('reference_electrode',event.target.value || 'unspecified')} placeholder="待确认，例如 RHE 或 Ag/AgCl" aria-label="参比电极" /><datalist id="cv-reference-options">{['Ag/AgCl','SCE','SHE','RHE','Hg/HgO','Hg/Hg2SO4'].map(value => <option value={value} key={value} />)}</datalist></label>
        <p className="parameter-hint">{p.reference_electrode === 'unspecified' ? '参比电极尚未指定，电位参数当前仅作编排草稿。' : '参比电极采用英文标识保存；实际参数范围待工作站接入后复核。'}</p>
      </section>
      {cvErrors(p).length > 0 && <div className="parameter-validation" role="status"><CircleHelp size={15} /><span>{cvErrors(p)[0]}</span></div>}
      <VoltageProgram parameters={p} />
      <div className="measurement-duration"><span>预计测量时长</span><strong>{formatDuration(cvDuration(p))}</strong></div>
      <p className="parameter-hint">含扫描前静置；不含工作站通讯及设备自身额外等待。默认值仅为草稿示例。</p>
    </> : <>
      <section className="parameter-group"><h4>本步骤设置</h4>
      {config.kind === 'valve' ? <>
        <label className="parameter-field"><span>目标流路</span><select value={step.route || ''} onChange={event => onChange({route:event.target.value})} aria-label="目标废液流路">{Object.entries(config.routes).map(([route,channel]) => <option key={route} value={route}>{route === 'waste_1' ? '废液 1' : route === 'waste_2' ? '废液 2' : route} · 通道 {channel}</option>)}</select></label>
        <div className="parameter-route-preview"><span>切换阀</span><ArrowRight size={17} /><strong>通道 {config.routes[step.route] ?? '?'}</strong></div>
      </> : <>
        <NumberField label={step.operation === 'withdraw' ? '抽出体积' : '注入体积'} value={step.volume_ml} onChange={value => onChange({volume_ml:value})} unit="mL" min="0.001" max={config.capacity_ml} step="0.1" />
        <div className="parameter-flow-direction"><span>{step.operation === 'withdraw' ? '电解池' : fluidLabels[step.fluid] || step.fluid}</span><ArrowRight size={18} /><strong>{step.operation === 'withdraw' ? '所选废液流路' : '电解池'}</strong></div>
        {config.kind === 'syringe' && <p className="parameter-hint">模板动作会先从储液侧吸入，再向电解池注入所填体积；容量为 {config.capacity_ml} mL。</p>}
        {template.action.type === 'drain' && <p className="parameter-hint">按模板增加 {Math.round(template.action.extra_time_fraction*100)}% 排液时间；抽液前需先选择废液流路。</p>}
      </>}
      </section>
      <section className="parameter-group template-parameter-summary"><div className="parameter-section-heading">继承的模板参数{onOpenTemplates && <button onClick={onOpenTemplates} type="button"><Settings2 size={13} />查看</button>}</div><dl><dt>操作</dt><dd>{operationLabels[step.operation]}</dd><dt>液体</dt><dd>{fluidLabels[step.fluid] || '—'}</dd><dt>默认速度</dt><dd>{config.rpm ? config.rpm+' rpm' : config.speed_steps_s ? config.speed_steps_s+' 步/秒' : '—'}</dd><dt>流量估算</dt><dd>{config.calibration?.flow_ml_min ? config.calibration.flow_ml_min+' mL/min' : '—'}</dd><dt>设备地址</dt><dd>{config.address}</dd></dl><p className="parameter-hint">速度、标定及通讯设置由对应 JSON 模板统一管理。</p></section>
    </>}
  </div>;
}
