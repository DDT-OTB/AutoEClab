export const measurementStates={starting:'准备采集',running:'正在采集',completed:'已完成',stopped:'已停止',failed:'采集异常',imported:'已导入'};
export function canDeleteMeasurement(user,record) {
  return user?.role==='admin' && !!record?.version && ['completed','stopped','failed','imported'].includes(record.state);
}
export function chartData(points=[],kind='cv') {
  if(!Array.isArray(points))return [];
  return points.filter(point=>Array.isArray(point) && point.length>=4 && point.slice(0,4).every(Number.isFinite))
    .map(point=>({index:point[0],x:kind==='cv'?point[2]:point[1],y:point[3]*1e6,time:point[1],potential:point[2]}));
}
export function chartDomain(values) {
  values=values.filter(Number.isFinite);
  if(!values.length)return [0,1];
  let minimum=Infinity,maximum=-Infinity;
  for(const value of values){minimum=Math.min(minimum,value);maximum=Math.max(maximum,value);}
  const pad=maximum===minimum?Math.max(Math.abs(minimum)*.05,.001):(maximum-minimum)*.06;
  return [minimum-pad,maximum+pad];
}
export function numberText(value,digits=3) {
  if(!Number.isFinite(value))return '—';
  return new Intl.NumberFormat('zh-CN',{maximumFractionDigits:digits}).format(value);
}
export function downloadBlob(blob,filename) {
  const url=URL.createObjectURL(blob),link=document.createElement('a');
  link.href=url;link.download=filename;link.click();
  setTimeout(()=>URL.revokeObjectURL(url),1000);
}
