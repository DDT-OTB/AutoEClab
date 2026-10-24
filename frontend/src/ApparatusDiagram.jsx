import React, { useId } from 'react';
import { MoveHorizontal } from 'lucide-react';
import { activeFlowDevice, confirmedWasteRoute, DEVICE_MAP } from './equipment.js';
import { ElectrolyticCellDrawing, PeristalticPumpDrawing, SelectorValveDrawing, SyringePumpDrawing, WasteBottleDrawing, WorkstationDrawing } from './DeviceIllustrations.jsx';

const COLORS={water:'#72a1bf',acid:'#bc985e',electrolyte:'#9b86ba',waste:'#78a18a'};
const NODES=[
  {id:'peristaltic_1',x:24,y:188,kind:'peristaltic'},
  {id:'peristaltic_3',x:24,y:363,kind:'peristaltic'},
  {id:'syringe_c',x:24,y:540,kind:'syringe'},
  {id:'peristaltic_2',x:501,y:294,kind:'peristaltic'},
  {id:'valve',x:501,y:458,kind:'valve'},
  {id:'electrochemical_station',x:262,y:40,kind:'electrochemical'},
];
const INLET_PIPES=[
  {id:'peristaltic_1',tone:'water',path:'M 224 249 H 242 Q 264 249 264 271 V 367 Q 264 389 286 389 H 292'},
  {id:'peristaltic_3',tone:'acid',path:'M 224 424 H 292'},
  {id:'syringe_c',tone:'electrolyte',path:'M 224 597 H 242 Q 264 597 264 575 V 481 Q 264 459 286 459 H 292'},
];

function activateOnKeyboard(event, callback) {
  if (event.key==='Enter' || event.key===' ') { event.preventDefault(); callback(); }
}

function StatusPlate({ item, x=105, y=119, width=90 }) {
  return <g className={'drawing-status state-'+item.activity.state} transform={'translate('+x+' '+y+')'} aria-hidden="true">
    <rect width={width} height="19" rx="9.5"/><circle cx="10" cy="9.5" r="2.3"/>
    <text x="18" y="13">{item.activity.label}</text>
  </g>;
}

export function InstrumentNode({ item, node, prefix, channel, selectedId, onSelect }) {
  const definition=DEVICE_MAP[node.id];
  const displayItem=item || {id:node.id,config:{kind:node.kind},activity:{state:'unknown',label:'配置待载入'}};
  const working=['executing','busy'].includes(displayItem.activity.state);
  const select=()=>onSelect(node.id);
  return <g transform={'translate('+node.x+' '+node.y+')'}
    className={'instrument-node tone-'+definition.tone+(selectedId===node.id?' instrument-selected':'')+(working?' instrument-working':'')+(item?.virtual?' instrument-reserved':'')+(!item?' instrument-unconfigured':'')}
    role={item?'button':'img'} tabIndex={item?0:undefined} aria-pressed={item?selectedId===node.id:undefined}
    aria-label={item?'查看'+item.name+'的状态详情':definition.shortName+'配置待载入'}
    onClick={item?select:undefined} onKeyDown={item?event=>activateOnKeyboard(event,select):undefined}>
    <title>{definition.name+' · '+displayItem.activity.label+(displayItem.config.rpm?' · 模板转速 '+displayItem.config.rpm+' rpm':'')}</title>
    <rect className="instrument-hit-area" x="-5" y={node.kind==='electrochemical'?-30:-6} width={node.kind==='electrochemical'?230:210} height={node.kind==='valve'?175:node.kind==='electrochemical'?160:164} rx="15"/>
    {node.kind==='peristaltic' ? <PeristalticPumpDrawing item={displayItem} prefix={prefix}/> : node.kind==='syringe' ? <SyringePumpDrawing item={displayItem} prefix={prefix}/> : node.kind==='valve' ? <SelectorValveDrawing channel={channel} prefix={prefix}/> : <WorkstationDrawing prefix={prefix}/>}
    {node.kind==='electrochemical' ? <>
      <text x="5" y="-10" className="drawing-device-name">电化学工作站</text><StatusPlate item={displayItem} x={130} y={-24} width={85}/>
    </> : node.kind==='valve' ? <>
      <text x="100" y="141" textAnchor="middle" className="drawing-device-name">六通切换阀</text><StatusPlate item={displayItem} x={55} y={149}/>
    </> : <>
      <text x="9" y="132" className="drawing-device-name">{definition.shortName}</text><StatusPlate item={displayItem}/>
      <text x="9" y="149" className="drawing-device-subtitle">{definition.subtitle}</text>
    </>}
  </g>;
}

function FluidPipe({ path, tone, working, prefix, arrow=true }) {
  return <g aria-hidden="true" className="fluid-pipe-group">
    <path d={path} className="apparatus-tube-bed"/>
    <path d={path} className={'apparatus-pipe pipe-'+tone} markerEnd={arrow?'url(#'+prefix+'-'+tone+')':undefined}/>
    {working && <path d={path} className={'apparatus-flow pipe-'+tone+' pipe-working'}/>}
  </g>;
}

export default function ApparatusDiagram({ items, runtime, fresh, selectedId, onSelect }) {
  const prefix='apparatus-'+useId().replace(/[^A-Za-z0-9_-]/g,'');
  const active=activeFlowDevice(items,runtime,fresh), route=confirmedWasteRoute(items,runtime,fresh);
  const byId=Object.fromEntries(items.map(item=>[item.id,item]));
  const activeItem=items.find(item=>item.id===active);
  const phase=active==='electrochemical_station'?'正在测量':active==='peristaltic_2'?'正在排液':active==='valve'?'正在切换流路':active?'正在注液':'实验单元';
  const reset=()=>onSelect(null);
  return <section className="apparatus-panel" aria-label="实验装置工作流图">
    <header className="equipment-panel-heading"><div><span className="equipment-eyebrow">APPARATUS FLOW</span><h3>装置流程图</h3></div><span className="equipment-panel-hint">点击设备查看状态</span></header>
    <div className="apparatus-diagram-caption"><span><i/>装置连接示意</span><small>注液 · 反应 · 排液 · 测量</small></div>
    <div className="apparatus-viewport">
      <svg className="apparatus-scene" viewBox="0 0 720 720" role="group" aria-label="电解池居中，注液设备在左，排液设备在右，测量仪器在上方">
        <defs>
          <linearGradient id={prefix+'-housing'} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#ffffff"/><stop offset="1" stopColor="#edf2ed"/></linearGradient>
          <linearGradient id={prefix+'-glass'} x1="0" x2="1"><stop offset="0" stopColor="#f3f8f5" stopOpacity=".88"/><stop offset=".25" stopColor="#ffffff" stopOpacity=".72"/><stop offset=".7" stopColor="#e6f0e8" stopOpacity=".48"/><stop offset="1" stopColor="#dfeae2" stopOpacity=".9"/></linearGradient>
          {Object.entries(COLORS).map(([tone,color])=><marker key={tone} id={prefix+'-'+tone} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M 0 0 L 7 3.5 L 0 7 Z" fill={color}/></marker>)}
        </defs>
        <g className="apparatus-zone-labels" aria-hidden="true"><text x="33" y="164">01 / 液体注入</text><text x="511" y="270">03 / 废液排出</text><text x="328" y="280">02 / 电解池</text></g>
        <rect x="482" y="282" width="232" height="426" rx="22" className="drain-zone-boundary" aria-hidden="true"/>
        {INLET_PIPES.map(pipe=><FluidPipe key={pipe.id} {...pipe} working={active===pipe.id} prefix={prefix}/>)}
        <FluidPipe path="M 456 451 H 474 Q 486 451 486 439 V 366 Q 486 355 497 355 H 501" tone="waste" working={active==='peristaltic_2'} prefix={prefix}/>
        <FluidPipe path="M 701 355 H 702 Q 712 355 712 365 V 422 Q 712 434 700 434 H 613 Q 601 434 601 446 V 458" tone="waste" working={active==='peristaltic_2'} prefix={prefix}/>
        <FluidPipe path="M 578.5 561 H 558 Q 548 561 548 571 V 635" tone="waste" working={active==='peristaltic_2' && route?.channel===5} prefix={prefix}/>
        <FluidPipe path="M 623.5 561 H 644 Q 654 561 654 571 V 635" tone="waste" working={active==='peristaltic_2' && route?.channel===6} prefix={prefix}/>
        <g className={'measurement-cables'+(active==='electrochemical_station'?' is-measuring':'')} aria-label={byId.electrochemical_station?.virtual?'预留的三电极测量连接':'三电极测量连接'}>
          <path d="M 338 163 V 210 Q 338 221 341 231 V 321" className="electrode-working"/>
          <path d="M 367 163 V 225 Q 367 237 374 245 V 321" className="electrode-reference"/>
          <path d="M 396 163 V 210 Q 396 222 407 231 V 321" className="electrode-counter"/>
        </g>
        <g className="measurement-cable-caption" aria-hidden="true"><text x="433" y="214">三电极测量</text><text x="433" y="231" className="measurement-cable-reserved">{byId.electrochemical_station?.virtual?'预留连接 · 尚未接入':active==='electrochemical_station'?'EC110 · 正在采集':'EC110 · CV 测量'}</text></g>
        {NODES.map(node=><InstrumentNode key={node.id} item={byId[node.id]} node={node} prefix={prefix} channel={route?.channel} selectedId={selectedId} onSelect={onSelect}/>)}
        <g className={'reactor-node'+(active?' reactor-working':'')} transform="translate(292 321)" role="button" tabIndex={0} aria-label="返回全部设备状态总览" onClick={reset} onKeyDown={event=>activateOnKeyboard(event,reset)}>
          <title>电解池结构与液位示意；点击返回全部设备状态</title>
          <rect x="-10" y="-11" width="184" height="269" rx="18" className="reactor-hit-area"/>
          <ElectrolyticCellDrawing prefix={prefix}/>
          <text x="82" y="228" textAnchor="middle" className="reactor-name">电解池</text>
          <text x="82" y="246" textAnchor="middle" className="reactor-phase">{activeItem?phase+' · '+activeItem.shortName:'进液 · 测量 · 排液'}</text>
          {selectedId && <text x="82" y="269" textAnchor="middle" className="reactor-return">← 返回设备总览</text>}
        </g>
        <g className="waste-bottle-group" aria-label="废液收集路径">
          {[{channel:5,name:'废液 1',x:522},{channel:6,name:'废液 2',x:628}].map(destination=><g key={destination.channel} transform={'translate('+destination.x+' 635)'}><WasteBottleDrawing {...destination} confirmed={route?.channel===destination.channel} prefix={prefix}/></g>)}
        </g>
        <text x="601" y="642" textAnchor="middle" className="drawing-route-confirmation">{route?'已确认 · CH '+route.channel:'阀位待确认'}</text>
      </svg>
    </div>
    <div className="apparatus-pan-hint"><MoveHorizontal size={13}/>左右滑动查看完整装置</div>
    <footer className="apparatus-legend"><div><span><i className="legend-water"/>超纯水</span><span><i className="legend-acid"/>硫酸</span><span><i className="legend-electrolyte"/>电解液</span><span><i className="legend-waste"/>废液</span><span><i className="legend-signal"/>预留测量线</span></div><small>结构与液位为示意，管线按功能连接；实际顺序以现场接线为准。</small></footer>
  </section>;
}
