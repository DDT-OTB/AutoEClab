import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {createApi} from './api.js';
import {templateDraft,templateDocument,newTemplateDraft,librarySaveError} from './blockLibrary.js';

const document=JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/legacy_function_blocks/water_rinse.json',import.meta.url)));
const entry={id:document.id,block:document,version:'synthetic-version',source:'builtin'};

test('saving an experiment block as a reusable template snapshots parameters and removes UI state',()=>{
  const draft=templateDraft(document);
  assert.deepEqual(templateDocument(draft),document);
  draft.steps[0].volume_ml=8;
  assert.equal(document.steps[0].volume_ml,10);
  assert.equal(templateDocument(draft).steps[0].volume_ml,8);
  assert.doesNotMatch(JSON.stringify(templateDocument(draft)),/_key/);
});
test('new saves never silently overwrite an existing template and renamed templates check collisions',()=>{
  const draft=templateDraft(document);
  assert.match(librarySaveError(draft,null,[entry]),/已存在/);
  assert.equal(librarySaveError(draft,{id:entry.id,version:entry.version},[entry]),'');
  draft.id='another_template';
  assert.equal(librarySaveError(draft,null,[entry]),'');
  assert.match(librarySaveError(draft,{id:entry.id},[entry,{id:draft.id}]),/已存在/);
});
test('incomplete templates and non-English configuration cannot be saved to the library',()=>{
  assert.match(librarySaveError(newTemplateDraft(),null,[]),/1 至 1024/);
  const draft=templateDraft(document);draft.description='自定义';
  assert.match(librarySaveError(draft,null,[]),/英文/);
});
test('library mutations carry the observed version, authentication and CSRF through fixed API paths',async()=>{
  const calls=[];
  const api=createApi('',{fetcher:async(path,options)=>{calls.push({path,options});return {ok:true,json:async()=>path==='/api/auth/session'?{csrf_token:'synthetic-csrf'}:{}};}});
  await api.session();
  await api.createFunctionBlock(document);
  await api.updateFunctionBlock('original id',document,'observed-version');
  await api.deleteFunctionBlock('original id','observed-version');
  const [create,update,remove]=calls.slice(1);
  assert.deepEqual(JSON.parse(create.options.body),{block:document});
  assert.equal(update.path,'/api/function-blocks/original%20id');
  assert.deepEqual(JSON.parse(update.options.body),{block:document,expected_version:'observed-version'});
  assert.equal(remove.options.method,'DELETE');
  assert.deepEqual(JSON.parse(remove.options.body),{expected_version:'observed-version'});
  for(const {options} of calls.slice(1)) {
    assert.equal(options.credentials,'same-origin');assert.equal(options.headers['X-CSRF-Token'],'synthetic-csrf');assert.equal(options.headers['X-Pump-Console'],'1');
  }
});
