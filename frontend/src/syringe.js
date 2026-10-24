import policy from '../../config/syringe_policy.json' with {type:'json'};

export function syringeStrokes(volume,template) {
  const device=template?.device;
  if(device?.kind!=='syringe' || typeof volume!=='number' || !Number.isFinite(volume) || volume<=0)return [];
  const limit=Math.min(template.action?.max_stroke_volume_ml ?? policy.max_stroke_volume_ml,device.capacity_ml);
  const maxSteps=Math.floor(limit*device.stroke_steps/device.capacity_ml);
  if(maxSteps<1)throw new Error('单次注射上限小于注射泵一步。');
  const maximum=maxSteps*device.capacity_ml/device.stroke_steps;
  const count=Math.ceil(volume/maximum);
  if(count>(template.action?.max_strokes_per_action ?? policy.max_strokes_per_action))throw new Error('注射分次数超过服务器上限。');
  return Array.from({length:count},(_,index)=>Number(Math.min(maximum,volume-index*maximum).toPrecision(12)));
}
export function syringeAchievableVolume(volume,template) {
  const device=template.device;
  return syringeStrokes(volume,template).reduce((sum,amount)=>sum+Math.round(amount/device.capacity_ml*device.stroke_steps)*device.capacity_ml/device.stroke_steps,0);
}
