import {workflowToEditor,workflowSequence} from './workflow.js';

export const workspacePages={
  run:{label:'实验运行',title:'载入流程，监测实验',subtitle:'流程执行与设备状态'},
  plans:{label:'实验方案',title:'设计与管理实验方案',subtitle:'文件管理与功能块编排'},
  history:{label:'运行记录',title:'查看每次实验的记录',subtitle:'执行结果与历史事件'},
};
export function workspacePage(hash='') {
  const value=hash.replace(/^#/,'');
  return Object.hasOwn(workspacePages,value)?value:'run';
}
export function runSnapshot(sequence) {
  if(typeof sequence?.id!=='string' || typeof sequence?.description!=='string') throw new Error('实验方案需要文字类型的 id 和 description。');
  return workflowSequence(workflowToEditor(sequence));
}
export const emptyRunSequence={schema_version:3,id:'no_experiment_selected',description:'No experiment selected',notes:[],blocks:[]};
