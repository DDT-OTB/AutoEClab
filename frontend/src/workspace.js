import {workflowToEditor,workflowSequence} from './workflow.js';

export const workspacePages={
  run:{label:'实验运行',title:'载入流程，监测实验',subtitle:'流程执行与设备状态'},
  plans:{label:'实验方案',title:'设计与管理实验方案',subtitle:'文件管理与功能块编排'},
  data:{label:'数据管理',title:'查看实验测量数据',subtitle:'CV 曲线、采集记录与数据下载'},
  history:{label:'运行记录',title:'查看每次实验的记录',subtitle:'执行结果与历史事件'},
};
export function workspacePage(hash='') {
  const value=hash.replace(/^#/,'').split('?')[0];
  return Object.hasOwn(workspacePages,value)?value:'run';
}
export function workspaceLocation(hash='') {
  const page=workspacePage(hash),query=new URLSearchParams(hash.replace(/^#/,'').split('?')[1] || '');
  const run=query.get('run_id'),measurement=query.get('measurement_id');
  return {page,runId:page==='data' && /^[A-Za-z0-9_-]{1,128}$/.test(run || '')?run:null,
    measurementId:page==='data' && /^cv-[a-f0-9]{32}$/.test(measurement || '')?measurement:null};
}
export function workspaceHash(page,{runId,measurementId}={}) {
  page=workspacePage(page);
  const query=new URLSearchParams();
  if(page==='data') {
    if(runId)query.set('run_id',runId);
    if(measurementId)query.set('measurement_id',measurementId);
  }
  return '#'+page+(query.size?'?'+query:'');
}
export function runSnapshot(sequence,safetyPolicy) {
  if(typeof sequence?.id!=='string' || typeof sequence?.description!=='string') throw new Error('实验方案需要文字类型的 id 和 description。');
  return workflowSequence(workflowToEditor(sequence,safetyPolicy));
}
export const emptyRunSequence={schema_version:3,id:'no_experiment_selected',description:'No experiment selected',notes:[],blocks:[]};
