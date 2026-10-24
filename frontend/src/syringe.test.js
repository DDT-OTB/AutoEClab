import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {syringeStrokes} from './syringe.js';
import {cellVolumes,defaultSafetyPolicy} from './cellSafety.js';
import {draftErrors} from './model.js';
import {defaultPreflight} from './preflight.js';
import {stepSummary} from './cv.js';

const read=id=>JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/demo-lab/config/templates/'+id+'.json',import.meta.url)));
const templates=Object.fromEntries(['syringe_c','valve','peristaltic_1'].map(id=>[id,read(id)]));
templates.valve.device.routes={water:5,electrolyte:6};
templates.syringe_c.action.source_valve='valve';templates.syringe_c.action.max_stroke_volume_ml=20;
templates.syringe_c.action.allowed_fluids=['water','electrolyte'];
const prefix=defaultPreflight();prefix.steps[2].route='waste';
const policy={...defaultSafetyPolicy,cell:{...defaultSafetyPolicy.cell,allowed_waste_routes:['waste'],drain_mode:'direct'}};
const sequence={schema_version:2,id:'total_dose',description:'Total syringe dose',preflight:prefix,steps:[
  {device:'valve',operation:'switch_route',route:'electrolyte'},
  {device:'syringe_c',operation:'inject',fluid:'electrolyte',volume_ml:30}]};

test('total thirty is accepted and previewed as twenty plus ten without changing the physical capacity',()=>{
  assert.deepEqual(syringeStrokes(30,templates.syringe_c),[20,10]);
  assert.deepEqual(syringeStrokes(25,templates.syringe_c),[20,5]);
  assert.deepEqual(syringeStrokes(40,templates.syringe_c),[20,20]);
  assert.deepEqual(draftErrors(sequence,templates,policy),[]);
  assert.equal(templates.syringe_c.device.capacity_ml,25);
  assert.match(stepSummary(sequence.steps[1],templates.syringe_c),/30 mL · 20 \+ 10 mL/);
});
test('cell capacity accounts for the total amount across every stroke',()=>{
  const value={...sequence,steps:[{device:'peristaltic_1',operation:'inject',fluid:'water',volume_ml:15},...sequence.steps]};
  assert.equal(cellVolumes(value,policy,templates).final_volume_ml,45);
  assert.match(draftErrors(value,templates,policy).join(' '),/超过 40/);
});
test('server-provided lower stroke limits are reflected in the preview',()=>{
  const template=structuredClone(templates.syringe_c);template.action.max_stroke_volume_ml=10;
  assert.deepEqual(syringeStrokes(30,template),[10,10,10]);
});
