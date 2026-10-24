import React from 'react';

export function PeristalticPumpDrawing({ item, prefix }) {
  return <g className="machine-drawing peristaltic-drawing" aria-hidden="true">
    <ellipse className="machine-shadow" cx="100" cy="109" rx="89" ry="7"/>
    <rect x="9" y="17" width="183" height="87" rx="13" fill={'url(#'+prefix+'-housing)'} className="machine-case"/>
    <path d="M 22 17 H 179 Q 192 17 192 30 V 36 H 9 V 30 Q 9 17 22 17" className="machine-top"/>
    <rect x="18" y="101" width="26" height="7" rx="3" className="machine-foot"/><rect x="153" y="101" width="26" height="7" rx="3" className="machine-foot"/>
    <circle cx="58" cy="61" r="37" className="pump-head-rim"/><circle cx="58" cy="61" r="30" className="pump-head-window"/>
    <path d="M 0 61 H 24 V 48 C 24 5 93 5 93 48 V 61 H 99 V 78 H 184 V 61 H 200" className="machine-tube-outline"/>
    <path d="M 0 61 H 24 V 48 C 24 5 93 5 93 48 V 61 H 99 V 78 H 184 V 61 H 200" className="machine-tube"/>
    <g className={'pump-rotor'+(item.config.direction==='CCW'?' rotor-counterclockwise':'')}>
      <circle cx="58" cy="61" r="26" fill="transparent" stroke="none"/>
      {[0,120,240].map(angle=><g key={angle} transform={'rotate('+angle+' 58 61)'}><path d="M 58 61 H 78" className="pump-rotor-arm"/><circle cx="78" cy="61" r="6" className="pump-roller"/></g>)}
      <circle cx="58" cy="61" r="9" className="pump-hub"/><circle cx="58" cy="61" r="3" className="machine-screw"/>
    </g>
    <rect x="108" y="35" width="69" height="35" rx="5" className="machine-display"/>
    <text x="115" y="45" className="machine-lcd-label">SET RPM</text><text x="115" y="63" className="machine-lcd-value">{item.config.rpm ?? '—'}</text>
    <circle cx="121" cy="88" r="5" className="machine-knob"/><path d="M 121 84 V 87" className="machine-knob-notch"/>
    <circle cx="143" cy="88" r="3" className="machine-led"/>
    <path d="M 157 84 H 174 M 157 88 H 174 M 157 92 H 174" className="machine-vents"/>
    <rect x="0" y="57" width="8" height="8" rx="2" className="machine-fitting"/><rect x="192" y="57" width="8" height="8" rx="2" className="machine-fitting"/>
  </g>;
}

export function SyringePumpDrawing({ item, prefix }) {
  return <g className="machine-drawing syringe-drawing" aria-hidden="true">
    <ellipse className="machine-shadow" cx="100" cy="110" rx="90" ry="7"/>
    <rect x="9" y="19" width="183" height="86" rx="12" fill={'url(#'+prefix+'-housing)'} className="machine-case"/>
    <rect x="18" y="29" width="43" height="58" rx="7" className="syringe-motor"/>
    <path d="M 27 38 V 68 M 34 38 V 68 M 41 38 V 68 M 48 38 V 68" className="machine-vents"/>
    <path d="M 61 37 H 176 M 61 79 H 176" className="syringe-rail"/>
    <path d="M 62 58 H 111" className="syringe-drive"/>
    {[66,73,80,87,94,101].map(x=><path key={x} d={'M '+x+' 54 l 4 8'} className="syringe-drive-thread"/>)}
    <rect x="109" y="39" width="66" height="36" rx="4" className="syringe-barrel"/>
    <path d="M 117 46 H 168 V 68 H 117" className="syringe-barrel-inner"/>
    <path d="M 109 43 V 71 M 118 44 V 70" className="syringe-plunger"/>
    {[127,136,145,154,163].map((x,i)=><path key={x} d={'M '+x+' 40 v '+(i%2===0?'9':'6')} className="syringe-graduation"/>)}
    <path d="M 175 50 H 184 V 64 H 175" className="syringe-nozzle"/><path d="M 184 57 H 200" className="machine-tube"/>
    <rect x="192" y="53" width="8" height="8" rx="2" className="machine-fitting"/>
    <rect x="69" y="87" width="102" height="14" rx="3" className="machine-display"/>
    <text x="75" y="97" className="syringe-setting-text">SET {item.config.speed_steps_s ?? '—'} STEP/S</text>
    <circle cx="183" cy="94" r="3" className="machine-led"/>
    <text x="22" y="98" className="machine-label">{item.config.capacity_ml ?? '—'} mL</text>
    <rect x="21" y="103" width="25" height="6" rx="2" className="machine-foot"/><rect x="155" y="103" width="25" height="6" rx="2" className="machine-foot"/>
  </g>;
}

const valvePorts=[
  {channel:1,x:55,y:64,labelX:43,labelY:67},
  {channel:2,x:77.5,y:25,labelX:70,labelY:16},
  {channel:3,x:122.5,y:25,labelX:130,labelY:16},
  {channel:4,x:145,y:64,labelX:157,labelY:67},
  {channel:5,x:77.5,y:103,labelX:66,labelY:119},
  {channel:6,x:122.5,y:103,labelX:134,labelY:119},
];
export function SelectorValveDrawing({ channel, prefix }) {
  return <g className="machine-drawing selector-drawing" aria-hidden="true" data-confirmed-channel={channel ?? 'unknown'}>
    <ellipse className="machine-shadow" cx="100" cy="125" rx="79" ry="6"/>
    <rect x="23" y="8" width="154" height="114" rx="15" fill={'url(#'+prefix+'-housing)'} className="machine-case"/>
    <circle cx="100" cy="64" r="46" className="valve-outer-ring"/><circle cx="100" cy="64" r="33" className="valve-face"/>
    <path d="M 100 0 V 64" className={'valve-common'+(channel?' valve-channel-selected':'')}/>
    {valvePorts.map(port=><g className={channel===port.channel?'valve-channel-selected':'valve-channel-idle'} key={port.channel}>
      <path d={'M 100 64 L '+port.x+' '+port.y} className="valve-channel-spoke"/>
      <circle cx={port.x} cy={port.y} r="5.5" className="valve-port"/>
      <text x={port.labelX} y={port.labelY} className="valve-port-number" textAnchor="middle">{port.channel}</text>
    </g>)}
    <circle cx="100" cy="64" r="16" className="valve-selector-hub"/>
    <text x="100" y="68" className="valve-selected-label" textAnchor="middle">{channel ?? '?'}</text>
    <rect x="96" y="0" width="8" height="9" rx="2" className="machine-fitting"/>
    {[[34,20],[166,20],[34,110],[166,110]].map(([x,y])=><circle key={x+'-'+y} cx={x} cy={y} r="2" className="machine-screw"/>)}
  </g>;
}

export function WorkstationDrawing({ prefix }) {
  return <g className="machine-drawing workstation-drawing" aria-hidden="true">
    <ellipse className="machine-shadow" cx="110" cy="119" rx="96" ry="7"/>
    <rect x="10" y="14" width="202" height="100" rx="13" fill={'url(#'+prefix+'-housing)'} className="machine-case"/>
    <path d="M 23 14 H 199 Q 212 14 212 27 V 30 H 10 V 27 Q 10 14 23 14" className="machine-top"/>
    <rect x="25" y="32" width="123" height="62" rx="5" className="station-screen"/>
    <text x="34" y="43" className="station-screen-label">CV · POTENTIAL PROGRAM</text>
    <path d="M 36 50 V 83 H 137" className="station-plot-axis"/>
    <path d="M 39 72 L 62 52 L 97 80 L 126 55" className="station-program-line"/>
    <text x="132" y="88" className="station-axis-text">t</text><text x="28" y="56" className="station-axis-text">E</text>
    <path d="M 164 37 H 194 M 164 42 H 194 M 164 47 H 194" className="machine-vents"/>
    <circle cx="177" cy="72" r="11" className="machine-knob"/><path d="M 177 63 V 68" className="machine-knob-notch"/>
    <text x="161" y="98" className="station-reserved-label">RESERVED</text>
    {[{x:76,name:'WE',tone:'working'},{x:105,name:'RE',tone:'reference'},{x:134,name:'CE',tone:'counter'}].map(port=><g key={port.name} className={'electrode-'+port.tone}>
      <text x={port.x} y="105" textAnchor="middle" className="station-terminal-label">{port.name}</text><circle cx={port.x} cy="115" r="5" className="station-terminal"/><path d={'M '+port.x+' 115 V 123'} className="station-terminal-wire"/>
    </g>)}
  </g>;
}

export function ElectrolyticCellDrawing({ prefix }) {
  return <g className="machine-drawing reactor-drawing" aria-hidden="true">
    <ellipse className="machine-shadow" cx="82" cy="204" rx="77" ry="8"/>
    <path d="M 18 45 V 170 C 18 206 146 206 146 170 V 45" fill={'url(#'+prefix+'-glass)'} className="reactor-glass"/>
    <path d="M 22 110 Q 82 131 142 110 V 168 C 142 199 22 199 22 168 Z" className="reactor-liquid"/>
    <ellipse cx="82" cy="110" rx="60" ry="13" className="reactor-liquid-surface"/>
    <path d="M 29 65 V 165 Q 29 178 42 183" className="reactor-reflection"/>
    <path d="M 133 70 V 157" className="reactor-reflection subtle-reflection"/>
    <path d="M 14 42 C 14 25 150 25 150 42 V 50 C 150 66 14 66 14 50 Z" className="reactor-lid"/>
    <ellipse cx="82" cy="42" rx="68" ry="16" className="reactor-lid-top"/>
    {[{x:49,name:'WE',tone:'working',bottom:154},{x:82,name:'RE',tone:'reference',bottom:163},{x:115,name:'CE',tone:'counter',bottom:148}].map(electrode=><g className={'electrode-'+electrode.tone} key={electrode.name}>
      <path d={'M '+electrode.x+' 0 V 33'} className="reactor-electrode-wire"/>
      <rect x={electrode.x-6} y="24" width="12" height="17" rx="3" className="reactor-electrode-seal"/>
      <path d={'M '+electrode.x+' 41 V '+electrode.bottom} className="reactor-electrode"/>
      <text x={electrode.x} y="18" className="reactor-electrode-label" textAnchor="middle">{electrode.name}</text>
    </g>)}
    {[{y:68,tone:'water'},{y:103,tone:'acid'},{y:138,tone:'electrolyte'}].map(port=><g className={'reactor-inlet tone-'+port.tone} key={port.tone}><path d={'M 0 '+port.y+' H 19'} className="reactor-port-tube"/><rect x="8" y={port.y-5} width="15" height="10" rx="3" className="reactor-port-fitting"/></g>)}
    <path d="M 145 130 H 164" className="reactor-outlet-tube"/><rect x="141" y="125" width="15" height="10" rx="3" className="reactor-port-fitting"/>
    <path d="M 151 84 H 158 M 151 99 H 158 M 151 114 H 158" className="reactor-scale"/>
    <text x="82" y="186" textAnchor="middle" className="reactor-level-note">液位示意</text>
  </g>;
}

export function WasteBottleDrawing({ name, channel, confirmed, prefix }) {
  return <g className={'waste-bottle-drawing'+(confirmed?' waste-bottle-confirmed':'')} aria-hidden="true">
    <ellipse className="machine-shadow" cx="26" cy="60" rx="24" ry="4"/>
    <path d="M 18 8 V 17 L 7 28 V 52 Q 7 59 14 59 H 38 Q 45 59 45 52 V 28 L 34 17 V 8 Z" fill={'url(#'+prefix+'-glass)'} className="waste-bottle-body"/>
    <rect x="16" y="3" width="20" height="9" rx="3" className="waste-bottle-cap"/>
    <path d="M 26 0 V 7" className="waste-bottle-inlet"/>
    <rect x="12" y="31" width="28" height="19" rx="3" className="waste-bottle-label"/>
    <text x="26" y="44" textAnchor="middle" className="waste-bottle-code">{channel===5?'W1':channel===6?'W2':'W'}</text>
    <text x="26" y="76" textAnchor="middle" className="waste-bottle-name">{name}{channel?' · CH'+channel:''}</text>
  </g>;
}
