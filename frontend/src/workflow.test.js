import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {flattenSequence,workflowToEditor,workflowSequence,instantiateBlock,importBlock,exportBlock} from './workflow.js';
import {draftErrors,makeStep,moveStep} from './model.js';
import {liveStepIndex} from './monitor.js';
import {preparedIsCurrent} from './automation.js';
import {cellVolumes} from './cellSafety.js';

const read=path=>JSON.parse(fs.readFileSync(new URL('../../'+path,import.meta.url)));
const experiment=read('config/examples/demo_blocks.json');
const legacy=read('tests/fixtures/demo-lab/config/sequences/demo_rinse_with_predrain.json');
const template=read('config/function_blocks/water_rinse.json');
const deviceTemplates=Object.fromEntries(['peristaltic_1','peristaltic_2','peristaltic_3','syringe_c','valve'].map(id=>[id,read('tests/fixtures/demo-lab/config/templates/'+id+'.json')]));

test('two-level roundtrip retains every block and action while stripping editor identities',()=>{
  const draft=workflowToEditor(experiment);
  assert.equal(draft.blocks.length,5);
  assert.deepEqual(workflowSequence(draft),experiment);
  assert.equal(new Set(draft.blocks.flatMap(block=>[block._key,...block.steps.map(step=>step._key)])).size,17);
  assert.doesNotMatch(JSON.stringify(workflowSequence(draft)),/_key/);
  assert.deepEqual(draftErrors(draft,deviceTemplates),[]);
});
test('legacy files are wrapped without changing their ordered action parameters or original source',()=>{
  const before=structuredClone(legacy),draft=workflowToEditor(legacy);
  assert.equal(draft.blocks.length,2);
  assert.deepEqual(workflowSequence(draft).blocks[1].steps,legacy.steps);
  assert.deepEqual(legacy,before);
});
test('import, copy, export and reorder keep block instances independent with unique instance IDs',()=>{
  const imported=importBlock(template),first=instantiateBlock(imported,[]),second=instantiateBlock(imported,[first]);
  assert.equal(second.id,'water_rinse_2');
  second.steps[0].volume_ml=22;
  assert.equal(first.steps[0].volume_ml,10);
  assert.equal(template.steps[0].volume_ml,10);
  assert.deepEqual(exportBlock(first),template);
  const moved=moveStep([first,second],1,0);
  assert.equal(moved[0].steps[0].volume_ml,22);
  assert.equal(first.steps[0].volume_ml,10);
});
test('ambiguous schemas, nested blocks, empty imported blocks, duplicate IDs and unsupported fields are rejected',()=>{
  for(const patch of [{schema_version:2},{kind:'experiment'},{steps:[]},{description:'水洗'},
    {steps:[{device:'valve',operation:'switch_route',steps:[]}]},{reference:'remote.json'}]) {
    assert.throws(()=>importBlock({...template,...patch}));
  }
  assert.throws(()=>workflowToEditor({...experiment,steps:[]}));
  assert.throws(()=>workflowToEditor({...experiment,blocks:[experiment.blocks[0],experiment.blocks[0]]}));
  assert.throws(()=>workflowToEditor({...experiment,notes:'Invalid note type'}));
  const draft=workflowToEditor(experiment);
  draft.blocks[1].id=draft.blocks[0].id;
  assert.match(draftErrors(draft,deviceTemplates).join(' '),/不能重复/);
});
test('cell budgets and valve routes carry across block boundaries and reordering checks the entire flow',()=>{
  const inject=(device,volume,fluid)=>({id:device,description:'Inject liquid',steps:[{device,operation:'inject',volume_ml:volume,fluid}]});
  const value={...experiment,blocks:[inject('peristaltic_1',25,'water'),inject('peristaltic_3',16,'sulfuric_acid')]};
  assert.equal(cellVolumes(value).peak_volume_ml,41);
  assert.match(draftErrors(workflowToEditor(value),deviceTemplates).join(' '),/超过 40/);
  const route={id:'choose_route',description:'Route selection',steps:[{device:'valve',operation:'switch_route',route:'waste_1'}]};
  const drain={id:'drain',description:'Drain liquid',steps:[{device:'peristaltic_2',operation:'withdraw',volume_ml:30,fluid:'waste_liquid'}]};
  assert.deepEqual(draftErrors(workflowToEditor({...experiment,blocks:[route,drain]}),deviceTemplates),[]);
  assert.match(draftErrors(workflowToEditor({...experiment,blocks:[drain,route]}),deviceTemplates).join(' '),/选择废液流路/);
  assert.equal(cellVolumes({...value,blocks:[value.blocks[0],route,drain,value.blocks[1]]}).peak_volume_ml,25);
});
test('flattened identities and live indices map the same physical step across block boundaries',()=>{
  const flat=flattenSequence(experiment);
  assert.equal(flat.steps.length,9);
  assert.equal(flat.steps[3].device,'syringe_c');
  assert.equal(flat.steps[3].id,'block_003_step_001');
  assert.equal(new Set(flat.steps.map(step=>step.id)).size,9);
  const runtime={state:'running',sequence_id:experiment.id,active_step:{...flat.steps[3],index:4,state:'executing'}};
  assert.equal(liveStepIndex(flat,runtime,false,true),3);
  assert.equal(liveStepIndex(flat,runtime,true,true),-1);
  const prepared={sequence:structuredClone(experiment)};
  const edited=structuredClone(experiment);edited.blocks[1].steps[0].volume_ml=11;
  assert.equal(preparedIsCurrent(prepared,edited),false);
});
test('measurement parameters inside a block roundtrip independently and are still validated',()=>{
  const cv=read('config/action_templates/cyclic_voltammetry.json');
  const block={id:'measurement',description:'CV measurement draft',steps:[makeStep(cv)]};
  delete block.steps[0]._key;
  const draft=workflowToEditor({...experiment,blocks:[block]});
  const copy=instantiateBlock(block,draft.blocks);
  copy.steps[0].parameters.cycles=3;
  assert.equal(draft.blocks[1].steps[0].parameters.cycles,1);
  draft.blocks[1].steps[0].parameters.scan_rate_v_s=0;
  assert.match(draftErrors(draft,{...deviceTemplates,electrochemical_station:cv}).join(' '),/扫描速率/);
});
