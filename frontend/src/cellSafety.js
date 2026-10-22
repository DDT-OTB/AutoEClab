import policy from '../../config/safety_policy.json' with {type:'json'};
export const defaultSafetyPolicy=policy;
export const cellPreparationLabels={required:'未排空准备',running:'排空中',awaiting_confirmation:'待现场确认',passed:'已确认空池',failed:'排空失败',invalidated:'需要重新排空',consumed:'本次已使用',not_required:'无需准备',unknown:'状态待确认'};
export function cellAllowsStart(state) {return state?.required===false || state?.state==='passed';}
export function cellVolumes(sequence,safetyPolicy=policy,templates={}) {
  const capacity=safetyPolicy.cell.capacity_ml;
  let current=0,peak=0;
  const errors=[];
  const steps=sequence.steps.map((step,index)=>{
    const before=current;
    if(['inject','withdraw'].includes(step.operation) && Number.isFinite(step.volume_ml) && step.volume_ml>0) {
      let amount=step.volume_ml;
      const config=templates[step.device]?.device;
      if(step.operation==='inject' && config?.kind==='syringe' && config.capacity_ml>0 && config.stroke_steps>0) {
        const achievable=Math.round(amount/config.capacity_ml*config.stroke_steps)*config.capacity_ml/config.stroke_steps;
        amount=Math.max(amount,achievable);
      }
      current=step.operation==='inject'?current+amount:Math.max(0,current-amount);
      current=Math.round(current*1e9)/1e9;
    }
    peak=Math.max(peak,current);
    const over=current>capacity;
    if(over) errors.push('第 '+(index+1)+' 步后预计池内液量 '+current+' mL，超过 '+capacity+' mL 容量上限。');
    return {index:index+1,before_ml:before,after_ml:current,over_capacity:over};
  });
  return {capacity_ml:capacity,peak_volume_ml:peak,final_volume_ml:current,steps,errors,volume_measured:false};
}
