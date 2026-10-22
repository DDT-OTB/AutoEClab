import {exportBlock,importBlock,instantiateBlock} from './workflow.js';

export const templateDraft=document=>instantiateBlock(importBlock(document),[]);
export const newTemplateDraft=()=>instantiateBlock({id:'new_block_template',description:'New reusable function block',steps:[]},[]);
export function templateDocument(draft) {
  const document=exportBlock(draft);
  importBlock(document);
  return document;
}
export function librarySaveError(draft,base,entries) {
  try {
    const document=templateDocument(draft);
    if(entries.some(entry=>entry.id===document.id && entry.id!==base?.id)) return '此模板 ID 已存在，请改名另存，或明确选择更新同名模板。';
    return '';
  } catch(error) {return error.message;}
}
