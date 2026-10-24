export const MAX_BLOCKS=128;
export const MAX_ACTIONS=1024;
const identifier=/^[A-Za-z][A-Za-z0-9_-]{0,79}$/;
let nextKey=1;
export const uiKey=()=> 'workflow-ui-'+nextKey++;
const object=value=>!!value && typeof value==='object' && !Array.isArray(value);
const fields=(value,required,optional=[])=>object(value) && required.every(key=>Object.hasOwn(value,key)) && Object.keys(value).every(key=>[...required,...optional].includes(key));

export function validateBlock(block,{allowEmpty=false}={}) {
  if(!fields(block,['id','description','steps'],['type']) || typeof block.id!=='string' || !identifier.test(block.id) || typeof block.description!=='string') throw new Error('功能块需要有效的英文 id、description 和 steps。');
  if('type' in block){if(!isPreflight(block))throw new Error('不支持的功能块类型。');validatePreflight(block);return block;}
  if(!Array.isArray(block.steps) || block.steps.length>MAX_ACTIONS || (!allowEmpty && !block.steps.length)) throw new Error('每个功能块须包含 1 至 1024 个动作。');
  const ids=new Set();
  block.steps.forEach(step=>{
    if(!object(step) || typeof step.device!=='string' || typeof step.operation!=='string' || 'blocks' in step || 'steps' in step) throw new Error('功能块内部只能包含动作，每个动作需要 device 和 operation。');
    if('id' in step) {
      if(typeof step.id!=='string' || !identifier.test(step.id) || ids.has(step.id)) throw new Error('同一功能块内的动作 ID 须为有效且不重复的英文标识。');
      ids.add(step.id);
    }
  });
  return block;
}

export function importBlock(document) {
  if(!fields(document,['schema_version','kind','id','description','steps'],['type']) || document.schema_version!==1 || document.kind!=='function_block') throw new Error('功能块文件需要 schema_version=1、kind=function_block、id、description 和 steps。');
  if(/[\u3400-\u9fff]/.test(JSON.stringify(document))) throw new Error('功能块 JSON 请使用英文。');
  return structuredClone(validateBlock({id:document.id,description:document.description,steps:document.steps,...(document.type?{type:document.type}:{})}));
}

export function exportBlock(block) {
  return {schema_version:1,kind:'function_block',id:block.id,description:block.description,
    ...(block.type?{type:block.type}:{}),
    steps:block.steps.map(({_key,...step})=>structuredClone(step))};
}

function workflowBlocks(sequence) {
  if(!object(sequence) || ![2,3].includes(sequence.schema_version)) throw new Error('实验方案需要 schema_version=2 或 3。');
  let blocks;
  if(sequence.schema_version===2) {
    if(!fields(sequence,['schema_version','id','description','steps'],['notes','preflight']) || !Array.isArray(sequence.steps) || sequence.steps.some(step=>!object(step))) throw new Error('旧版实验方案需要 steps 动作数组，不能同时包含顶层 blocks。');
    blocks=[...(sequence.preflight?[validateBlock(sequence.preflight)]:[]),...(sequence.steps.length?[{id:'imported_actions',description:'Imported action sequence',steps:sequence.steps}]:[])];
  } else {
    if(!fields(sequence,['schema_version','id','description','blocks'],['notes']) || !Array.isArray(sequence.blocks)) throw new Error('新版实验方案需要 blocks 功能块数组，不能同时包含顶层 steps。');
    const ids=new Set();
    sequence.blocks.forEach(block=>{
      validateBlock(block,{allowEmpty:true});
      if(ids.has(block.id)) throw new Error('同一实验中的功能块 ID 不能重复。');
      ids.add(block.id);
    });
    blocks=sequence.blocks;
  }
  if(blocks.length>MAX_BLOCKS || blocks.filter(block=>!isPreflight(block)).reduce((sum,block)=>sum+block.steps.length,0)>MAX_ACTIONS) throw new Error('实验最多包含 128 个功能块和 1024 个实验动作。');
  if('notes' in sequence && (!Array.isArray(sequence.notes) || sequence.notes.some(note=>typeof note!=='string'))) throw new Error('实验 notes 须为文字数组。');
  return blocks;
}

export function workflowToEditor(sequence,safetyPolicy) {
  let blocks=workflowBlocks(sequence);
  if(!blocks.some(isPreflight)) {const block=defaultPreflight();block.id=uniqueBlockId(block.id,blocks);blocks=[block,...blocks];}
  if(blocks.length>MAX_BLOCKS)throw new Error('包含前置准备后，功能块总数超过 128。');
  if(safetyPolicy)blocks=alignPreflight({blocks},safetyPolicy).blocks;
  const required=preflightErrors({schema_version:3,blocks},safetyPolicy);if(required.length)throw new Error(required[0]);
  return {schema_version:3,id:sequence.id,description:sequence.description,notes:sequence.notes || ['Review this experiment before hardware execution.'],
    blocks:blocks.map(block=>({...structuredClone(block),_key:uiKey(),steps:block.steps.map(step=>({...structuredClone(step),_key:uiKey()}))}))};
}

export function workflowSequence(draft) {
  return {schema_version:3,id:draft.id,description:draft.description,notes:structuredClone(draft.notes || []),
    blocks:draft.blocks.map(block=>({id:block.id,description:block.description,...(block.type?{type:block.type}:{}),steps:block.steps.map(({_key,...step})=>structuredClone(step))}))};
}

export function flattenSequence(sequence) {
  if(sequence.schema_version!==3 && !sequence.blocks) {const {preflight,...flat}=sequence;return flat;}
  return {schema_version:2,id:sequence.id,description:sequence.description,notes:sequence.notes || [],
    steps:sequence.blocks.flatMap((block,bi)=>isPreflight(block)?[]:block.steps.map((step,si)=>({...step,id:`block_${String(bi+1).padStart(3,'0')}_step_${String(si+1).padStart(3,'0')}`})))};
}

export function uniqueBlockId(stem,blocks) {
  const base=stem.slice(0,70),ids=new Set(blocks.map(block=>block.id));
  if(!ids.has(base)) return base;
  for(let n=2;;n++) if(!ids.has(base+'_'+n)) return base+'_'+n;
}

export function instantiateBlock(block,blocks) {
  return {...structuredClone(block),id:uniqueBlockId(block.id,blocks),_key:uiKey(),
    steps:block.steps.map(step=>({...structuredClone(step),_key:uiKey()}))};
}

export function workflowErrors(sequence,safetyPolicy) {
  try {
    workflowBlocks(sequence);
    const required=preflightErrors(sequence,safetyPolicy);if(required.length)return required;
    if(!sequence.blocks.some(block=>!isPreflight(block))) return ['请在前置准备之后添加至少一个实验功能块。'];
    sequence.blocks.forEach(block=>validateBlock(block));
    return [];
  } catch(error) {return [error.message];}
}
import {alignPreflight,defaultPreflight,isPreflight,validatePreflight,preflightErrors} from './preflight.js';
