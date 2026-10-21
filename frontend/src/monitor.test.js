import test from 'node:test';
import assert from 'node:assert/strict';
import { deviceActivity, liveStepIndex, progressPercent } from './monitor.js';
const step = { device: 'peristaltic_1', operation: 'inject', volume_ml: 10, fluid: 'water' };
const live = { ...step, state: 'executing', action: 'dose', index: 1, progress_fraction: .5 };
const runtime = { state: 'running', sequence_id: 'test', active_step: live };
test('active pump shows its operation and estimated progress', () => {
  assert.equal(deviceActivity({ activity: live }, runtime, true).label, '正在注入');
  assert.equal(progressPercent(live), 50);
  assert.equal(progressPercent({ progress_fraction: 1.5 }), 99);
  assert.equal(progressPercent({ progress_fraction: null }), null);
});
test('stale, disconnected or idle controller never shows the pump as running', () => {
  assert.equal(deviceActivity({ activity: live }, runtime, false).state, 'unknown');
  for (const state of ['idle', 'unresponsive', 'aborted', 'unknown']) {
    assert.equal(deviceActivity({ activity: live }, { ...runtime, state }, true).state, 'unknown');
  }
});
test('flow highlighting requires an unmodified matching experiment and step', () => {
  const draft = { id: 'test', steps: [step] };
  assert.equal(liveStepIndex(draft, runtime, false, true), 0);
  assert.equal(liveStepIndex(draft, runtime, true, true), -1);
  assert.equal(liveStepIndex({ ...draft, id: 'other' }, runtime, false, true), -1);
  assert.equal(liveStepIndex({ ...draft, steps: [{ ...step, volume_ml: 15 }] }, runtime, false, true), -1);
  assert.equal(liveStepIndex(draft, runtime, false, false), -1);
});
