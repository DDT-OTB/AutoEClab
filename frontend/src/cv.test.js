import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { cvDuration, cvErrors, cvWaveform, stepSummary } from './cv.js';
import { draftErrors, makeStep, moveStep, toEditor, toSequence } from './model.js';
const template = JSON.parse(fs.readFileSync(new URL('../../config/action_templates/cyclic_voltammetry.json', import.meta.url)));
const parameters = template.action.default_parameters;
const measurementTemplates = { [template.device_id]: template };

test('measurement defaults come from the JSON template and remain independent when copied', () => {
  const a = makeStep(template), b = makeStep(template);
  assert.equal(a.operation, 'measure');
  assert.equal(a.technique, 'cyclic_voltammetry');
  a.parameters.cycles = 3;
  assert.equal(b.parameters.cycles, 1);
  assert.equal(parameters.cycles, 1);
  assert.equal(a.volume_ml, undefined);
});
test('measurement editing roundtrip preserves English parameters and removes UI-only state', () => {
  const source = { schema_version: 2, id: 'cv_test', description: 'CV planning test', notes: [],
    steps: [{ device: template.device_id, operation: 'measure', technique: 'cyclic_voltammetry', parameters: structuredClone(parameters) }] };
  const draft = toEditor(source);
  draft.steps[0].parameters.scan_rate_v_s = .05;
  draft.steps[0].parameters.reference_electrode = 'RHE';
  const saved = toSequence(draft);
  assert.equal(saved.steps[0].parameters.scan_rate_v_s, .05);
  assert.equal(source.steps[0].parameters.scan_rate_v_s, .1);
  assert.equal(JSON.stringify(saved).includes('_key'), false);
  assert.equal(/[\u3400-\u9fff]/.test(JSON.stringify(saved)), false);
  assert.deepEqual(draftErrors(draft, measurementTemplates), []);
});
test('CV estimates include the hold once and complete scan cycles in either direction', () => {
  assert.equal(cvDuration(parameters), 25);
  assert.equal(cvDuration({ ...parameters, cycles: 3 }), 65);
  assert.equal(cvDuration({ ...parameters, initial_scan_direction: 'negative' }), 25);
  const wave = cvWaveform(parameters);
  assert.equal(wave.points.at(-1).potential, parameters.final_potential_v);
  assert.equal(wave.duration, 25);
  assert.equal(cvWaveform({ ...parameters, cycles: 100 }).shownCycles, 3);
});
test('invalid potentials, rates, counts and ranges are rejected before saving', () => {
  for (const patch of [{ lower_potential_v: 1 }, { initial_potential_v: 2 }, { scan_rate_v_s: 0 },
    { potential_step_v: 2 }, { cycles: 1.5 }, { current_range_a: 0 }, { quiet_time_s: -1 },
    { reference_electrode: '中文' }]) assert.ok(cvErrors({ ...parameters, ...patch }).length);
  assert.equal(cvDuration({ ...parameters, scan_rate_v_s: '' }), null);
  assert.equal(cvWaveform({ ...parameters, scan_rate_v_s: '' }), null);
});
test('reordering measurement actions preserves their nested parameter values', () => {
  const steps = [makeStep(template), makeStep(template)];
  steps[1].parameters.cycles = 5;
  const reordered = moveStep(steps, 1, 0);
  assert.equal(reordered[0].parameters.cycles, 5);
  assert.match(stepSummary(reordered[0], template), /5 圈/);
  assert.equal(steps[0].parameters.cycles, 1);
});
