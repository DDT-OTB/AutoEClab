import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { draftErrors, makeStep, moveStep, toEditor, toSequence } from './model.js';
const root = new URL('../../tests/fixtures/demo-lab/config/', import.meta.url);
const templates = Object.fromEntries(fs.readdirSync(new URL('templates/', root)).filter(x => x.endsWith('.json')).map(name => {
  const template = JSON.parse(fs.readFileSync(new URL('templates/' + name, root)));
  return [template.device_id, template];
}));
const sequence = JSON.parse(fs.readFileSync(new URL('sequences/demo_rinse_with_predrain.json', root)));

test('editing roundtrip preserves the synthetic English configuration and strips UI keys', () => {
  const editor = toEditor(sequence);
  assert.equal(editor.steps.length, 11);
  assert.deepEqual(toSequence(editor), sequence);
  assert.equal(JSON.stringify(toSequence(editor)).includes('_key'), false);
});
test('new operations inherit operation, fluid, and default valve route', () => {
  assert.deepEqual(toSequence({ ...sequence, steps: [makeStep(templates.peristaltic_1, 10)] }).steps,
    [{ device: 'peristaltic_1', operation: 'inject', volume_ml: 10, fluid: 'water' }]);
  assert.equal(makeStep(templates.valve).route, 'waste_1');
});
test('moving a step preserves its parameters without mutating the source', () => {
  const steps = toEditor(sequence).steps;
  const moved = moveStep(steps, 2, 0);
  assert.equal(moved[0], steps[2]);
  assert.equal(steps[0].device, 'valve');
  assert.equal(moveStep(steps, 0, -1), steps);
});
test('invalid amount, fluid, English metadata and missing waste route are found', () => {
  assert.deepEqual(draftErrors(toEditor(sequence), templates), []);
  const draft = toEditor(sequence);
  draft.steps[2].volume_ml = 0;
  assert.ok(draftErrors(draft, templates).length);
  draft.steps[2].volume_ml = 10;
  draft.steps[2].fluid = 'sulfuric_acid';
  assert.ok(draftErrors(draft, templates).length);
  const drain = { ...sequence, steps: [makeStep(templates.peristaltic_2)] };
  assert.ok(draftErrors(drain, templates).length);
  assert.ok(draftErrors({ ...toEditor(sequence), description: '中文配置' }, templates).length);
});
