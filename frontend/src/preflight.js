import template from '../../config/preflight_block.json' with {type:'json'};
import policy from '../../config/safety_policy.json' with {type:'json'};
import {preparedIsCurrent} from './automation.js';
import {preparationRequired} from './workflowMode.js';
export const isPreflight=block=>block?.type==='preflight';
export function defaultPreflight() {
  const {schema_version,kind,...block}=structuredClone(template);return block;
}
const canonical=value=>JSON.stringify(value,(key,item)=>key==='_key'?undefined:item && typeof item==='object' && !Array.isArray(item)?Object.fromEntries(Object.keys(item).sort().map(name=>[name,item[name]])):item);
export function upgradePreflightBlock(block) {
  const value=structuredClone(block);
  if(!isPreflight(value) || value.steps?.length!==3)return value;
  const route=value.steps[1]?.route;
  const expected=[{device:'syringe_c',operation:'home_if_used'},{device:'cell',operation:'drain_to_waste',route},{device:'cell',operation:'confirm_empty'}];
  if(['waste','waste_1','waste_2'].includes(route) && canonical(value.steps)===canonical(expected))value.steps=[{device:'valve',operation:'select_syringe_water_if_used',route:'water'},...value.steps];
  return value;
}
export function upgradePreflightSequence(sequence) {
  const value=structuredClone(sequence);
  if(value.preflight)value.preflight=upgradePreflightBlock(value.preflight);
  if(Array.isArray(value.blocks))value.blocks=value.blocks.map(upgradePreflightBlock);
  return value;
}
export function validatePreflight(block,safetyPolicy) {
  const steps=block.steps,route=steps?.[2]?.route;
  const allowed=safetyPolicy?.cell.allowed_waste_routes || ['waste_1','waste_2','waste'];
  if(!Array.isArray(steps) || steps.length!==4 || !allowed.includes(route))throw new Error('前置准备必须包含固定的切水回读、回零、排空和确认四个步骤，请重新载入方案。');
  const expected=defaultPreflight().steps;expected[2].route=route;
  if(canonical(steps)!==canonical(expected))throw new Error('前置准备步骤及顺序不可修改：按需切水并回读 → 回零 → 排空 → 人工空池确认。');
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
  const route=block?.steps.find(step=>step.operation==='drain_to_waste')?.route;
  return {block_id:block?.id,requires_syringe:(sequence.steps || steps).some(step=>step.device==='syringe_c'),route,
    homing_source:{device:'valve',route:'water',channel:5},
    predrain_volume_ml:policy.cell.predrain_volume_ml,drain_mode:route==='waste'?'direct':'selector'};
}
export function alignPreflight(sequence,safetyPolicy) {
  const copy=upgradePreflightSequence(sequence);
  if(safetyPolicy?.cell.drain_mode!=='direct')return copy;
  const block=copy.blocks?.find(isPreflight) || copy.preflight;
  const drain=block?.steps.find(step=>step.operation==='drain_to_waste');
  if(drain && ['waste_1','waste_2'].includes(drain.route))drain.route='waste';
  return copy;
}
export function canAutoAdvance(prepared,sequence,fresh,enabled) {
  return !!enabled && fresh && prepared?.workflow?.needs_advance===true && prepared?.workflow?.authorized===true &&
    ['ready_to_home','ready_to_drain'].includes(prepared.workflow.phase) &&
    preparedIsCurrent(prepared,sequence);
}
