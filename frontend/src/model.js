import { cvErrors, isMeasurement } from './cv.js';
import { cellVolumes, defaultSafetyPolicy } from './cellSafety.js';
import { flattenSequence, workflowSequence, workflowErrors, workflowToEditor, uiKey } from './workflow.js';
import {preflightErrors} from './preflight.js';
import {isDebug,modeFields} from './workflowMode.js';
import {syringeStrokes} from './syringe.js';
export { uiKey } from './workflow.js';
export const fluidLabels = { water: '超纯水', waste_liquid: '废液', sulfuric_acid: '硫酸', electrolyte: '电解液' };
export const operationLabels = { inject: '注入', withdraw: '抽出', switch_route: '切换流路', measure: '测量',initialize:'机械回零' };
export function toEditor(sequence) {
  if(sequence.schema_version===3) return workflowToEditor(sequence);
  return { ...sequence, steps: sequence.steps.map(step => ({ ...structuredClone(step), _key: uiKey() })) };
}
export function toSequence(draft) {
  if(draft.blocks) return workflowSequence(draft);
  return {
    schema_version: 2, id: draft.id, description: draft.description,...modeFields(draft),
    notes: draft.notes || ['Review this experiment before hardware execution.'],
    ...(draft.preflight?{preflight:structuredClone(draft.preflight)}:{}),
    steps: draft.steps.map(({ _key, ...step }) => structuredClone(step)),
  };
}
export function makeStep(template, volume = 1) {
  const step = { device: template.device_id, operation: template.action.operation };
  if (template.device.kind === 'electrochemical') {
    step.technique = template.action.technique;
    step.parameters = structuredClone(template.action.default_parameters);
  }
  else if (template.device.kind === 'valve') step.route = Object.keys(template.device.routes)[0];
  else { step.volume_ml = volume; step.fluid = template.action.fluid; }
  return { ...step, _key: uiKey() };
}
export function moveStep(steps, from, to) {
  if (from < 0 || to < 0 || from >= steps.length || to >= steps.length) return steps;
  const result = [...steps];
  const [item] = result.splice(from, 1);
  result.splice(to, 0, item);
  return result;
}
export function draftErrors(draft, templates, safetyPolicy=defaultSafetyPolicy) {
  const errors = [];
  if(draft.blocks) errors.push(...workflowErrors(toSequence(draft),safetyPolicy));
  else errors.push(...preflightErrors(draft,safetyPolicy));
  if (!/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(draft.id)) errors.push('方案 ID 请使用字母开头的英文、数字、下划线或短横线。');
  if (/[\u3400-\u9fff]/.test(JSON.stringify(toSequence(draft)))) errors.push('保存的 JSON 内容请使用英文。');
  const flat=flattenSequence(toSequence(draft));
  if (!flat.steps.length) errors.push('请先添加至少一个操作步骤。');
  const routes = {};
  flat.steps.forEach((step, index) => {
    const prefix = '第 ' + (index + 1) + ' 步';
    const template = templates[step.device];
    if (!template) { errors.push(prefix + '设备配置尚未就绪。'); return; }
    if(step.device==='syringe_c' && step.operation==='initialize') {
      if(!isDebug(draft))errors.push(prefix+'显式回零动作仅适用于调试模式。');
      if(Object.keys(step).some(key=>!['device','operation','id','description','_key'].includes(key)))errors.push(prefix+'回零动作不接受体积或其他参数。');
      return;
    }
    if (step.operation !== template.action.operation) errors.push(prefix + '操作与模板不一致。');
    if (isMeasurement(step)) {
      if (step.device !== 'electrochemical_station' || step.technique !== 'cyclic_voltammetry') errors.push(prefix + '仅支持电化学工作站的循环伏安法。');
      errors.push(...cvErrors(step.parameters).map(message => prefix + '：' + message));
      if(Number.isFinite(template.device.max_potential_v) && ['initial_potential_v','upper_potential_v','lower_potential_v','final_potential_v'].some(key=>Number.isFinite(step.parameters?.[key]) && Math.abs(step.parameters[key])>template.device.max_potential_v)) errors.push(prefix+'电位超出仪器允许范围 ±'+template.device.max_potential_v+' V。');
    } else if (template.device.kind === 'valve') {
      if (!(step.route in template.device.routes)) errors.push(prefix + '流路未定义。');
      routes[step.device] = step.route;
    } else {
      if (typeof step.volume_ml !== 'number' || !Number.isFinite(step.volume_ml) || step.volume_ml <= 0)
        errors.push(prefix + '体积必须大于 0。');
      if (template.action.allowed_fluids?!template.action.allowed_fluids.includes(step.fluid):step.fluid !== template.action.fluid) errors.push(prefix + '液体与模板不一致。');
      if (template.action.type === 'drain' && template.action.routing_mode!=='direct' && !routes[template.action.valve_device]) errors.push(prefix + '抽液前，请先选择废液流路。');
      if (template.action.source_valve && routes[template.action.source_valve]!==step.fluid && !(isDebug(draft) && !routes[template.action.source_valve])) errors.push(prefix+'注射泵吸液前，请先选择相符入口：水为 5，电解液为 6。');
      if(isDebug(draft) && template.action.source_valve && !routes[template.action.source_valve])routes[template.action.source_valve]=step.fluid;
      if (template.device.kind === 'syringe') {
        try{syringeStrokes(step.volume_ml,template);}catch(error){errors.push(prefix+error.message);}
      }
    }
  });
  return [...errors,...cellVolumes(toSequence(draft),safetyPolicy,templates).errors];
}
export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return '待预演';
  if (seconds < 60) return seconds.toFixed(1) + ' 秒';
  return Math.floor(seconds / 60) + ' 分 ' + Math.round(seconds % 60) + ' 秒';
}
