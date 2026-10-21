export function experimentKind(item) {
  if (!item.editable) return 'review';
  if (!item.step_count) return 'draft';
  return item.contains_measurement ? 'measurement' : 'fluidics';
}

export const experimentKindLabels={review:'待检查',draft:'空白草稿',measurement:'包含测量',fluidics:'泵阀流程'};

export function filterExperiments(items, {query='',kind='all',sort='modified'}={}) {
  const value=query.trim().toLocaleLowerCase();
  const filtered=items.filter(item=>(!value || [item.id,item.filename,item.description].some(text=>String(text || '').toLocaleLowerCase().includes(value))) && (kind==='all' || experimentKind(item)===kind));
  return [...filtered].sort((a,b)=>{
    const name=a.id.localeCompare(b.id,undefined,{numeric:true});
    if(sort==='name') return name;
    if(sort==='steps') return b.step_count-a.step_count || name;
    return (Date.parse(b.modified_at) || 0)-(Date.parse(a.modified_at) || 0) || name;
  });
}

export function fileSize(bytes) {
  if(!Number.isFinite(bytes)) return '—';
  return bytes<1024 ? bytes+' B' : bytes<1024*1024 ? (bytes/1024).toFixed(1)+' KB' : (bytes/(1024*1024)).toFixed(1)+' MB';
}

export function fileNameError(id, target, items) {
  if(!/^[A-Za-z][A-Za-z0-9_-]{0,79}$/.test(id)) return '使用字母开头的英文、数字、下划线或短横线，最多 80 个字符。';
  if(id===target) return '请填写不同的新方案 ID。';
  if(items.some(item=>item.id===id)) return '该文件名已存在。';
  return '';
}

export function uniqueExperimentId(stem, items) {
  const existing=new Set(items.map(item=>item.id));
  const base=stem.slice(0,70);
  if(!existing.has(base)) return base;
  for(let index=2;index<100000;index++) {
    const id=base+'_'+index;
    if(!existing.has(id)) return id;
  }
  return base+'_'+Date.now().toString(36);
}

export function editorAfterFileChange({draft,base,dirty}, kind, result) {
  if(kind==='rename' && base.id===result.previous_id) {
    return {draft:draft.id===result.previous_id ? {...draft,id:result.sequence.id}:draft,
      base:{id:result.sequence.id,version:result.version},dirty};
  }
  if(kind==='delete' && base.id===result.id) return {draft,base:{id:null,version:null},dirty:true};
  return {draft,base,dirty};
}
