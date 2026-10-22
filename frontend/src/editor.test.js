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
  stdin: { contents: "export { default as FlowStep } from './src/FlowStep.jsx'; export { default as ActionInspector } from './src/ActionInspector.jsx'; export { default as AccountManagement } from './src/AccountManagement.jsx'; export { BlockCanvas, BlockInspector, BlockPalette } from './src/WorkflowBuilder.jsx'; export { default as App } from './src/App.jsx'; export {default as BlockLibraryManager} from './src/BlockLibraryManager.jsx';",
    loader: 'jsx', resolveDir: fileURLToPath(new URL('../', import.meta.url)) },
  bundle: true, platform: 'node', format: 'cjs', write: false, external: ['react', 'lucide-react'],loader:{'.css':'empty'},
}).outputFiles[0].text;
const module = { exports: {} };
new Function('require', 'module', 'exports', compiled)(require, module, module.exports);
const { FlowStep, ActionInspector, AccountManagement } = module.exports;
const {BlockCanvas,BlockInspector,App}=module.exports;
const {BlockPalette,BlockLibraryManager}=module.exports;
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

test('the workflow canvas selects blocks while the inspector owns block and action parameter inputs',()=>{
  const step={device:'peristaltic_1',operation:'inject',volume_ml:10,fluid:'water',_key:'action-1'};
  const block={id:'rinse',description:'Water rinse',steps:[step],_key:'block-1'};
  const common={block,blockIndex:0,templates:{peristaltic_1:pump},names:{},shortNames:{},cellBudget:{steps:[],capacity_ml:40},offset:0,runningIndex:-1};
  const html=renderToStaticMarkup(React.createElement(BlockCanvas,{blocks:[block],templates:common.templates,shortNames:{},selected:block._key,runningIndex:0}));
  assert.doesNotMatch(html,/<(input|select|textarea)\b/);
  assert.match(html,/1 个动作/);
  assert.match(html,/动作 1 执行中/);
  const inspector=renderToStaticMarkup(React.createElement(BlockInspector,common));
  assert.match(inspector,/aria-label="功能块 ID"/);
  assert.match(inspector,/导出功能块 JSON/);
  assert.doesNotMatch(inspector,/aria-label="注入体积"/);
  const detail=renderToStaticMarkup(React.createElement(BlockInspector,{...common,selectedStep:step}));
  assert.match(detail,/返回 rinse 的动作列表/);
  assert.match(detail,/aria-label="注入体积"/);
});
test('the application renders an empty block editor without assuming top-level actions exist',()=>{
  const html=renderToStaticMarkup(React.createElement(App,{user:{username:'synthetic_viewer',role:'viewer'},initialPage:'plans',initialPlanView:'editor'}));
  assert.match(html,/从第一个功能块开始/);
  assert.match(html,/功能块 \/ 动作/);
  assert.doesNotMatch(html,/确认启动设备/);
});

test('only administrator views expose template save and library management controls',()=>{
  const step={device:'peristaltic_1',operation:'inject',volume_ml:10,fluid:'water',_key:'action-1'};
  const block={id:'custom_block',description:'Synthetic block',steps:[step],_key:'block-1'};
  const props={block,templates:{peristaltic_1:pump},names:{},shortNames:{},cellBudget:{steps:[],capacity_ml:40},offset:0};
  assert.match(renderToStaticMarkup(React.createElement(BlockInspector,{...props,canManage:true})),/保存到功能块库/);
  assert.doesNotMatch(renderToStaticMarkup(React.createElement(BlockInspector,{...props,canManage:false})),/保存到功能块库/);
  const palette={blocks:[{id:block.id,description:block.description,steps:[step]}],templates:{},measurementTemplates:{},mode:'blocks',shortNames:{}};
  const admin=renderToStaticMarkup(React.createElement(BlockPalette,{...palette,canManage:true}));
  assert.match(admin,/管理功能块模板/);assert.match(admin,/aria-label="编辑模板 custom_block"/);
  const viewer=renderToStaticMarkup(React.createElement(BlockPalette,{...palette,canManage:false}));
  assert.doesNotMatch(viewer,/管理功能块模板|编辑模板 custom_block/);
  assert.match(renderToStaticMarkup(React.createElement(BlockLibraryManager,{user:{role:'viewer'},templates:{}})),/需要管理员权限/);
});
test('the template editor shows the seeded action list and action buttons cannot submit its save form',()=>{
  const seed={schema_version:1,kind:'function_block',id:'test_block',description:'Synthetic template',steps:[{device:'peristaltic_1',operation:'inject',volume_ml:10,fluid:'water'}]};
  const html=renderToStaticMarkup(React.createElement(BlockLibraryManager,{user:{role:'admin'},seed,templates:{peristaltic_1:pump},names:{},shortNames:{}}));
  assert.match(html,/aria-label="模板 ID"/);assert.match(html,/value="test_block"/);assert.match(html,/保存模板/);
  assert.match(html,/编辑第 1 步参数/);assert.doesNotMatch(html,/运行已载入流程|确认启动设备/);
  assert.match(html,/<form class="library-template-form" noValidate=""/);
  const action=renderToStaticMarkup(React.createElement(FlowStep,{step:seed.steps[0],template:pump,index:0,total:1}));
  assert.ok([...action.matchAll(/<button\b[^>]*>/g)].every(([tag])=>tag.includes('type="button"')));
});
