export function automationAvailability({user, enabled, fresh, current, runtime, errors=[], measurementCount=0, busy=false}) {
  if(user?.role!=='admin') return {allowed:false,reason:'仅管理员可启动和停止自动化。'};
  if(!enabled) return {allowed:false,reason:'服务器尚未开放自动化执行。'};
  if(!fresh) return {allowed:false,reason:'等待实时状态连接恢复。'};
  if(current?.controller_alive || runtime?.controller_alive || ['starting','checking','unknown'].includes(runtime?.state)) return {allowed:false,reason:'当前控制器或设备查询正在工作。'};
  if(busy) return {allowed:false,reason:'请等待当前操作完成。'};
  if(measurementCount) return {allowed:false,reason:'电化学工作站尚未接入，包含测量的方案不能执行。'};
  if(errors.length) return {allowed:false,reason:errors[0]};
  return {allowed:true,reason:'运行前由后端校验并固定本次方案与设备参数。'};
}

export const executionLabels={starting:'正在准备控制器',running:'自动化执行中',stopping:'正在停止与清理',completed:'全部步骤完成',stopped:'流程已停止',failed:'流程执行异常'};

export function sequenceKey(sequence) {
  return JSON.stringify(sequence,(key,value)=>value && typeof value==='object' && !Array.isArray(value) ? Object.fromEntries(Object.keys(value).sort().map(name=>[name,value[name]])):value);
}

export function preparedIsCurrent(prepared, sequence) {
  return !!prepared && sequenceKey(prepared.sequence)===sequenceKey(sequence);
}
