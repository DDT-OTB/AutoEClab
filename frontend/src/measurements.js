export const measurementStates={starting:'准备采集',running:'正在采集',finalizing:'正在保存',completed:'已完成',stopped:'已停止',failed:'采集异常',interrupted:'异常中断',imported:'已导入'};
export const activeMeasurementStates=['starting','running','finalizing'];
export function storageText(metadata) {
  if(!metadata)return '';
  if(metadata.storage_state==='error')return '保存异常 · 等待恢复已提交数据';
  if(metadata.state==='interrupted')return metadata.csv_ready?'异常中断 · 已恢复已提交数据':'异常中断 · 保存状态待核对';
  if(metadata.state==='finalizing')return '正在保存 · 生成完整 CSV 并校验';
  if(metadata.csv_ready===true)return '数据已保存 · '+numberText(metadata.point_count,0)+' 个均值点';
  if(metadata.storage_state==='recording')return '已提交 '+numberText(metadata.point_count,0)+' 个均值点 · 结束后生成 CSV';
  return '历史数据记录';
}
export function canDeleteMeasurement(user,record) {
  return user?.role==='admin' && !!record?.version && ['completed','stopped','failed','imported','interrupted'].includes(record.state);
}
export function chartData(points=[],kind='cv') {
  if(!Array.isArray(points))return [];
  return points.filter(point=>Array.isArray(point) && point.length>=4 && point.slice(0,4).every(Number.isFinite))
    .map(point=>({index:point[0],x:kind==='cv'?point[2]:point[1],y:point[3]*1e6,time:point[1],potential:point[2]}));
}
export function displayTrend(data=[],kind='cv',window=7) {
  if(!data.length)return [];
  const groups=[];let group=[data[0]],direction=0;
  for(let index=1;index<data.length;index++) {
    const step=Math.sign(data[index].potential-data[index-1].potential);
    if(kind==='cv' && step && direction && step!==direction) {
      groups.push(group);group=[data[index-1]];
    }
    group.push(data[index]);if(step)direction=step;
  }
  groups.push(group);
  const radius=Math.floor(window/2);
  return groups.flatMap(points=>points.map((point,index)=>{
    if(index===0 || index===points.length-1)return {...point};
    const selected=points.slice(Math.max(0,index-radius),Math.min(points.length,index+radius+1));
    return {...point,y:selected.reduce((sum,value)=>sum+value.y,0)/selected.length};
  }));
}
export function smoothPath(points=[]) {
  if(!points.length)return '';
  const position=point=>`${point.x.toFixed(3)},${point.y.toFixed(3)}`;
  const path=[`M${position(points[0])}`];
  function appendRun(run) {
    if(run.length<2)return;
    const h=run.slice(1).map((point,i)=>Math.abs(point.x-run[i].x));
    const slope=run.slice(1).map((point,i)=>(point.y-run[i].y)/h[i]);
    const tangent=Array(run.length).fill(0);
    const endpoint=(a,b,ha,hb)=>{
      const value=((2*ha+hb)*a-ha*b)/(ha+hb);
      return Math.sign(value)!==Math.sign(a)?0:Math.sign(a)*Math.min(Math.abs(value),3*Math.abs(a));
    };
    if(run.length===2)tangent[0]=tangent[1]=slope[0];
    else {
      tangent[0]=endpoint(slope[0],slope[1],h[0],h[1]);
      tangent[run.length-1]=endpoint(slope.at(-1),slope.at(-2),h.at(-1),h.at(-2));
      for(let i=1;i<run.length-1;i++) {
        const a=slope[i-1],b=slope[i];
        if(a*b>0) {
          const w1=2*h[i]+h[i-1],w2=h[i]+2*h[i-1];
          tangent[i]=(w1+w2)/(w1/a+w2/b);
        }
      }
    }
    for(let i=0;i<run.length-1;i++) {
      const a=run[i],b=run[i+1],dx=(b.x-a.x)/3;
      const lo=Math.min(a.y,b.y),hi=Math.max(a.y,b.y);
      const clamp=value=>Math.max(lo,Math.min(hi,value));
      // Bounded Bezier controls prevent interpolation from adding new peaks.
      const c1={x:a.x+dx,y:clamp(a.y+tangent[i]*h[i]/3)};
      const c2={x:b.x-dx,y:clamp(b.y-tangent[i+1]*h[i]/3)};
      path.push(`C${position(c1)} ${position(c2)} ${position(b)}`);
    }
  }
  let run=[points[0]],direction=0;
  for(let i=1;i<points.length;i++) {
    const previous=points[i-1],point=points[i],step=Math.sign(point.x-previous.x);
    if(!step || (direction && step!==direction)) {
      appendRun(run);
      if(!step)path.push(`L${position(point)}`);
      run=step?[previous,point]:[point];
    }else run.push(point);
    direction=step || 0;
  }
  appendRun(run);
  return path.join(' ');
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
