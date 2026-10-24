import template from '../../config/preflight_block.json' with {type:'json'};
import policy from '../../config/safety_policy.json' with {type:'json'};
import {preparedIsCurrent} from './automation.js';
import {preparationRequired} from './workflowMode.js';
export const isPreflight=block=>block?.type==='preflight';
export function defaultPreflight() {
  const {schema_version,kind,...block}=structuredClone(template);return block;
}
export function validatePreflight(block,safetyPolicy) {
  const steps=block.steps,route=steps?.[1]?.route;
  const allowed=safetyPolicy?.cell.allowed_waste_routes || ['waste_1','waste_2','waste'];
  if(!Array.isArray(steps) || steps.length!==3 || !allowed.includes(route))throw new Error('前置准备必须包含固定的回零、排空和确认步骤，并选择有效的废液流路。');
  const expected=defaultPreflight().steps;expected[1].route=route;
  const canonical=value=>JSON.stringify(value,(key,item)=>key==='_key'?undefined:item && typeof item==='object' && !Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(name=>[name,item[name]])):item);
  if(canonical(steps)!==canonical(expected))throw new Error('前置准备步骤及顺序不可修改：按需回零 → 排空 → 人工空池确认。');
}
export function preflightErrors(sequence,safetyPolicy) {
  try {
    if(!preparationRequired(sequence))return [];
    let block;
    if(sequence.schema_version===2) block=sequence.preflight;
    else {
      const blocks=sequence.blocks || [],required=blocks.filter(isPreflight);
      if(required.length!==1 || !isPreflight(blocks[0]))throw new Error('实验必须且只能以前置准备功能块开始。');
      block=required[0];
    }
    if(!isPreflight(block))throw new Error('实验缺少前置准备功能块。');
    validatePreflight(block,safetyPolicy);return [];
  }catch(error){return [error.message];}
}
export function preflightInfo(sequence) {
  const block=sequence.blocks?.find(isPreflight) || sequence.preflight;
  const steps=(sequence.blocks || []).filter(block=>!isPreflight(block)).flatMap(block=>block.steps);
  return {block_id:block?.id,requires_syringe:(sequence.steps || steps).some(step=>step.device==='syringe_c'),route:block?.steps[1]?.route,
    predrain_volume_ml:policy.cell.predrain_volume_ml,drain_mode:block?.steps[1]?.route==='waste'?'direct':'selector'};
}
export function alignPreflight(sequence,safetyPolicy) {
  if(safetyPolicy?.cell.drain_mode!=='direct')return sequence;
  const copy=structuredClone(sequence);
  const block=copy.blocks?.find(isPreflight) || copy.preflight;
  if(block && ['waste_1','waste_2'].includes(block.steps?.[1]?.route))block.steps[1].route='waste';
  return copy;
}
export function canAutoAdvance(prepared,sequence,fresh,enabled) {
  return !!enabled && fresh && prepared?.workflow?.needs_advance===true && prepared?.workflow?.authorized===true &&
    ['ready_to_home','ready_to_drain'].includes(prepared.workflow.phase) &&
    preparedIsCurrent(prepared,sequence);
}
