export const isDebug=sequence=>sequence?.mode==='debug';
export const hasPreparation=sequence=>!!sequence?.preflight || !!sequence?.blocks?.some(block=>block.type==='preflight');
export const preparationRequired=sequence=>!isDebug(sequence) || hasPreparation(sequence);
export function validateMode(sequence) {
  if(!['experiment','debug'].includes(sequence.mode ?? 'experiment')) throw new Error('方案 mode 必须为 experiment 或 debug。');
  if('initial_cell_volume_ml' in sequence && (!isDebug(sequence) || typeof sequence.initial_cell_volume_ml!=='number' || !Number.isFinite(sequence.initial_cell_volume_ml) || sequence.initial_cell_volume_ml<0)) throw new Error('仅调试方案可填写非负的初始池内液量。');
}
export function modeFields(sequence) {
  return {...(sequence.mode?{mode:sequence.mode}:{}),...('initial_cell_volume_ml' in sequence?{initial_cell_volume_ml:sequence.initial_cell_volume_ml}:{})};
}
