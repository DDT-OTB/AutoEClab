import React, { useId } from 'react';
import { MoveHorizontal } from 'lucide-react';
import { activeFlowDevice, confirmedWasteRoute,confirmedSourceRoute, DEVICE_MAP } from './equipment.js';
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

function LegacyApparatusDiagram({ items, runtime, fresh, selectedId, onSelect }) {
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

function CompactPump({item,x,y,width=172,onSelect,selectedId}) {
  if(!item)return null;
  const select=()=>onSelect(item.id),working=['executing','busy'].includes(item.activity.state);
  return <g transform={`translate(${x} ${y})`} className={'instrument-node compact-fluid-device tone-'+item.tone+(working?' instrument-working':'')+(selectedId===item.id?' instrument-selected':'')} role="button" tabIndex={0} aria-label={'查看'+item.name+'的状态详情'} aria-pressed={selectedId===item.id} onClick={select} onKeyDown={event=>activateOnKeyboard(event,select)}>
    <rect className="instrument-hit-area" width={width} height="68" rx="12"/>
    <rect className="compact-pump-body" x="2" y="2" width={width-4} height="64" rx="11"/>
    <circle cx="27" cy="32" r="17" className="compact-pump-face"/><circle cx="27" cy="32" r="6" className="compact-pump-hub"/>
    {[0,120,240].map(angle=><circle key={angle} cx="27" cy="20" r="3" transform={`rotate(${angle} 27 32)`} className="compact-pump-roller"/>)}
    <text x="52" y="27" className="drawing-device-name">{item.id==='peristaltic_1'?'纯水 · 泵 1':item.id==='peristaltic_3'?'硫酸 · 泵 3':'排液泵 2'}</text><text x="52" y="45" className="drawing-device-subtitle">{item.activity.label}</text>
  </g>;
}

function SupplyBottle({x,y,label,channel,tone,selected=false}) {
  return <g transform={`translate(${x} ${y})`} className={'supply-bottle tone-'+tone+(selected?' source-selected':'')}>
    <path d="M 20 8 V 20 L 6 32 V 63 Q 6 72 15 72 H 63 Q 72 72 72 63 V 32 L 58 20 V 8 Z" className="source-bottle-glass"/>
    <path d="M 11 45 H 67 V 63 Q 67 67 62 67 H 16 Q 11 67 11 63 Z" className="source-bottle-liquid"/>
    <rect x="17" y="3" width="44" height="10" rx="3" className="source-bottle-cap"/>
    <text x="39" y="42" textAnchor="middle" className="source-channel-label">CH {channel}</text>
    <text x="39" y="91" textAnchor="middle" className="source-bottle-label">{label}</text>
  </g>;
}

function InletApparatusDiagram({items,runtime,fresh,selectedId,onSelect}) {
  const prefix='inlet-apparatus-'+useId().replace(/[^A-Za-z0-9_-]/g,'');
  const byId=Object.fromEntries(items.map(item=>[item.id,item]));
  const active=activeFlowDevice(items,runtime,fresh),source=confirmedSourceRoute(items,runtime,fresh);
  const valve=byId.valve,syringe=byId.syringe_c;
  const select=id=>()=>onSelect(id);
  return <section className="apparatus-panel inlet-apparatus" aria-label="实验装置工作流图">
    <header className="equipment-panel-heading"><div><span className="equipment-eyebrow">APPARATUS FLOW</span><h3>装置流程图</h3></div><span className="equipment-panel-hint">点击设备查看状态</span></header>
    <div className="apparatus-diagram-caption"><span><i/>入口选液 · 注液 · 测量 · 直排</span></div>
    <div className="apparatus-viewport"><svg className="apparatus-scene" viewBox="0 0 720 655" role="group" aria-label="六通阀位于注射泵吸液侧：5 号水，6 号电解液；排液泵直接连接一个废液瓶">
      <defs><linearGradient id={prefix+'-housing'} x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#fff"/><stop offset="1" stopColor="#edf2ed"/></linearGradient><linearGradient id={prefix+'-glass'}><stop stopColor="#fff" stopOpacity=".8"/><stop offset="1" stopColor="#dfeae2" stopOpacity=".6"/></linearGradient>{Object.entries(COLORS).map(([tone,color])=><marker key={tone} id={prefix+'-'+tone} markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto" markerUnits="userSpaceOnUse"><path d="M 0 0 L 7 3.5 L 0 7 Z" fill={color}/></marker>)}</defs>
      <text x="25" y="169" className="source-zone-label">直接注液支路</text><text x="25" y="364" className="source-zone-label">注射泵吸液侧</text>
      <FluidPipe path="M 196 226 H 330 Q 350 226 350 246 V 333 Q 350 353 370 353 H 400" tone="water" working={active==='peristaltic_1'} prefix={prefix}/>
      <FluidPipe path="M 196 312 H 306 Q 326 312 326 332 V 368 Q 326 388 346 388 H 400" tone="acid" working={active==='peristaltic_3'} prefix={prefix}/>
      <FluidPipe path="M 92 426 H 115 Q 128 426 128 439 V 506 Q 128 520 142 520 H 182 Q 196 520 196 506 V 487" tone="water" working={active==='syringe_c' && source?.channel===5} prefix={prefix}/>
      <FluidPipe path="M 92 562 H 210 Q 224 562 224 548 V 487" tone="electrolyte" working={active==='syringe_c' && source?.channel===6} prefix={prefix}/>
      <FluidPipe path="M 210 423 H 270" tone={source?.channel===5?'water':'electrolyte'} working={active==='syringe_c'} prefix={prefix}/>
      <FluidPipe path="M 390 423 H 400" tone={source?.channel===5?'water':'electrolyte'} working={active==='syringe_c'} prefix={prefix}/>
      <FluidPipe path="M 564 415 H 570" tone="waste" working={active==='peristaltic_2'} prefix={prefix}/>
      <FluidPipe path="M 696 415 H 701 V 500 Q 701 518 683 518 H 639 Q 621 518 621 536 V 538" tone="waste" working={active==='peristaltic_2'} prefix={prefix}/>
      <g className="measurement-cables" aria-label="三电极测量连接"><path d="M 448 153 V 285" className="electrode-working"/><path d="M 477 153 V 230 Q 477 245 482 252 V 285" className="electrode-reference"/><path d="M 506 153 V 220 Q 506 236 515 245 V 285" className="electrode-counter"/></g>
      <InstrumentNode item={byId.electrochemical_station} node={{id:'electrochemical_station',x:372,y:30,kind:'electrochemical'}} prefix={prefix} selectedId={selectedId} onSelect={onSelect}/>
      <CompactPump item={byId.peristaltic_1} x={24} y={192} onSelect={onSelect} selectedId={selectedId}/><CompactPump item={byId.peristaltic_3} x={24} y={278} onSelect={onSelect} selectedId={selectedId}/>
      <SupplyBottle x={20} y={384} label="水" channel={5} tone="water" selected={source?.channel===5}/><SupplyBottle x={20} y={520} label="电解液" channel={6} tone="electrolyte" selected={source?.channel===6}/>
      <g className={'instrument-node tone-electrolyte'+(selectedId==='valve'?' instrument-selected':'')} transform="translate(148 423)" role="button" tabIndex={0} aria-label="查看六通入口选液阀的状态详情" aria-pressed={selectedId==='valve'} onClick={select('valve')} onKeyDown={event=>activateOnKeyboard(event,select('valve'))}><rect className="instrument-hit-area" x="0" y="-28" width="124" height="115" rx="12"/><text x="62" y="-13" textAnchor="middle" className="drawing-device-name">六通入口阀</text><g transform="scale(.62)"><SelectorValveDrawing channel={source?.channel} prefix={prefix}/></g></g>
      <g className={'instrument-node tone-electrolyte'+(selectedId==='syringe_c'?' instrument-selected':'')+(active==='syringe_c'?' instrument-working':'')} transform="translate(270 389)" role="button" tabIndex={0} aria-label="查看注射泵的状态详情" aria-pressed={selectedId==='syringe_c'} onClick={select('syringe_c')} onKeyDown={event=>activateOnKeyboard(event,select('syringe_c'))}><rect className="instrument-hit-area" x="-2" y="-5" width="126" height="102" rx="12"/><g transform="scale(.6)"><SyringePumpDrawing item={syringe || {config:{}}} prefix={prefix}/></g><text x="60" y="86" textAnchor="middle" className="drawing-device-name">注射泵</text></g>
      <text x="228" y="556" className="drawing-route-confirmation">{source?'已确认：'+(source.channel===5?'水 · 5':'电解液 · 6'):'入口阀位待确认'}</text>
      <g transform="translate(400 285)" className={'reactor-node'+(active?' reactor-working':'')} role="button" tabIndex={0} aria-label="返回全部设备状态总览" onClick={()=>onSelect(null)} onKeyDown={event=>activateOnKeyboard(event,()=>onSelect(null))}><rect className="reactor-hit-area" x="-8" y="-8" width="180" height="250" rx="18"/><ElectrolyticCellDrawing prefix={prefix}/><text x="82" y="232" textAnchor="middle" className="reactor-name">电解池</text></g>
      <CompactPump item={byId.peristaltic_2} x={570} y={380} width={126} onSelect={onSelect} selectedId={selectedId}/>
      <g transform="translate(587 538) scale(1.3)" aria-label="单一废液瓶，排液泵直接排入"><WasteBottleDrawing name="废液瓶" channel={null} confirmed={active==='peristaltic_2'} prefix={prefix}/></g>
    </svg></div><footer className="apparatus-legend"><div><span><i className="legend-water"/>5 · 水</span><span><i className="legend-electrolyte"/>6 · 电解液</span><span><i className="legend-waste"/>排液直达废液瓶</span></div><small>功能液路示意。六通阀只选择注射泵吸液来源，不参与排液；液位并非实时测量。</small></footer>
  </section>;
}

export default function ApparatusDiagram(props) {
  return props.items.some(item=>item.id==='valve' && item.template?.action.role==='syringe_inlet_selector')?<InletApparatusDiagram {...props}/>:<LegacyApparatusDiagram {...props}/>;
}
