import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
const require = createRequire(import.meta.url);
const { buildSync } = require('esbuild');
const compiled = buildSync({
  stdin: { contents: "export { default as FlowStep } from './src/FlowStep.jsx'; export { default as ActionInspector } from './src/ActionInspector.jsx'; export { default as AccountManagement } from './src/AccountManagement.jsx';",
    loader: 'jsx', resolveDir: fileURLToPath(new URL('../', import.meta.url)) },
  bundle: true, platform: 'node', format: 'cjs', write: false, external: ['react', 'lucide-react'],
}).outputFiles[0].text;
const module = { exports: {} };
new Function('require', 'module', 'exports', compiled)(require, module, module.exports);
const { FlowStep, ActionInspector, AccountManagement } = module.exports;
const cv = JSON.parse(fs.readFileSync(new URL('../../config/action_templates/cyclic_voltammetry.json', import.meta.url)));
const pump = JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/demo-lab/config/templates/peristaltic_1.json', import.meta.url)));
const valve = JSON.parse(fs.readFileSync(new URL('../../tests/fixtures/demo-lab/config/templates/valve.json', import.meta.url)));
const measurement = { device: cv.device_id, operation: 'measure', technique: 'cyclic_voltammetry', parameters: cv.action.default_parameters };

test('canvas nodes show summaries and selection controls without parameter inputs', () => {
  for (const [step, template] of [[{ device: 'peristaltic_1', operation: 'inject', volume_ml: 10, fluid: 'water' }, pump],
    [{ device: 'valve', operation: 'switch_route', route: 'waste_1' }, valve], [measurement, cv]]) {
    const html = renderToStaticMarkup(React.createElement(FlowStep, { step, template, title: 'Action', index: 0, total: 1 }));
    assert.doesNotMatch(html, /<(input|select|textarea)\b/);
    assert.match(html, /编辑第 1 步参数/);
  }
});
test('the selected pump and valve parameters are edited in the inspector', () => {
  const html = renderToStaticMarkup(React.createElement(ActionInspector, {
    step: { device: 'peristaltic_1', operation: 'inject', volume_ml: 10, fluid: 'water' },
    template: pump, index: 0, title: 'Water pump', onChange: () => {},
  }));
  assert.match(html, /aria-label="注入体积"/);
  assert.match(html, /value="10"/);
  const valveHtml = renderToStaticMarkup(React.createElement(ActionInspector, {
    step: { device: 'valve', operation: 'switch_route', route: 'waste_1' }, template: valve, index: 1, title: 'Valve', onChange: () => {},
  }));
  assert.match(valveHtml, /aria-label="目标废液流路"/);
});
test('CV inspector renders the full parameter form with correct display units and an honest waveform', () => {
  const html = renderToStaticMarkup(React.createElement(ActionInspector, { step: measurement, template: cv, index: 0, title: 'Workstation', onChange: () => {} }));
  const inputs = [...html.matchAll(/<input\b[^>]*>/g)].map(match => match[0]);
  assert.match(inputs.find(input => input.includes('aria-label="扫描速率"')), /value="100"/);
  assert.match(inputs.find(input => input.includes('aria-label="采样电位间隔"')), /value="1"/);
  assert.match(html, /工作站尚未接入/);
  assert.match(html, /不是实测电流曲线/);
  assert.match(html, /参比电极/);
});
test('account provisioning form is reserved for administrators and requires a temporary password',()=>{
  const viewer=renderToStaticMarkup(React.createElement(AccountManagement,{user:{username:'observer',role:'viewer'}}));
  assert.match(viewer,/需要管理员权限/);
  assert.doesNotMatch(viewer,/<input|创建账号/);
  const admin=renderToStaticMarkup(React.createElement(AccountManagement,{user:{username:'lab',role:'admin'}}));
  assert.match(admin,/不开放自行注册/);
  assert.match(admin,/首次登录必须修改临时密码/);
  assert.equal([...admin.matchAll(/type="password"/g)].length,2);
});
