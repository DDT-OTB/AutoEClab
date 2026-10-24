import React from 'react';
import { ArrowLeft, ChevronRight, CircleHelp, Radio, RefreshCw } from 'lucide-react';
import { ActionIcon } from './FlowStep.jsx';
import { formatDuration, operationLabels } from './model.js';
import { progressPercent } from './monitor.js';
import { deviceQuickInfo, equipmentStatusSource, equipmentSummary, hardwareReason } from './equipment.js';
import StationMeasurements from './StationMeasurements.jsx';

const timeText=value=>value ? new Date(value).toLocaleString('zh-CN', {
  month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',
}) : '暂无记录';
const routeName=route=>({water:'水 · 通道 5',electrolyte:'电解液 · 通道 6',waste:'废液瓶',waste_1:'废液 1',waste_2:'废液 2'}[route] || route);

export function EquipmentState({ activity }) {
  return <span className={'equipment-state state-'+activity.state}><i/>{activity.label}</span>;
}

function DeviceDetails({ item, fresh,measurement,onOpenMeasurement }) {
  const { config, device, activity }=item;
  const hardware=device?.hardware;
  const currentHardware=fresh && hardware && !hardware.stale && activity.source==='hardware';
  const programActivity=fresh && activity.source!=='hardware' && ['executing','waiting','completed','error'].includes(activity.state);
  const percent=progressPercent(activity);
  return <div className="equipment-device-details">
    <div className={'device-detail-identity tone-'+item.tone}>
      <span className="monitor-device-icon"><ActionIcon kind={config.kind} size={23}/></span>
      <div><h4>{item.name}</h4><code>{item.id}</code></div>
    </div>
    <div className="device-detail-state"><EquipmentState activity={activity}/><span>{equipmentStatusSource(item,fresh)}</span></div>
    {item.virtual ? <div className="station-reserved-note"><Radio size={21}/><strong>测量接口尚未接入</strong><p>循环伏安法（CV）的参数可在“实验方案”中设置。工作站通讯接入后，这里将显示测量状态、电位与电流。</p><span>当前暂无实时测量数据</span></div> : config.kind==='electrochemical'?<>
      <div className="equipment-current-note"><CircleHelp size={15}/><p>{!fresh?'状态连接已中断，等待重新获取数据。':hardware?.stale?'最近连接回读已过期，请刷新状态。':currentHardware && hardware.state==='connected'?'工作站身份回读成功；当前连接已确认，工作状态由执行控制器监测。':hardware?.reason?hardwareReason[hardware.reason] || '工作站状态读取失败，请检查连接。':'点击刷新可读取工作站连接和身份。'}</p></div>
      {currentHardware && hardware.product && <dl className="equipment-readings"><dt>仪器身份</dt><dd>{hardware.product}</dd><dt>固件版本</dt><dd>{hardware.firmware || '未返回'}</dd></dl>}
      <StationMeasurements measurement={measurement} fresh={fresh} active={programActivity && activity.state==='executing' && activity.action==='measurement'} onOpen={onOpenMeasurement}/>
    </>:<>
      {programActivity ? <section className="equipment-current-action" aria-label="本次程序动作">
        <div className="detail-section-title"><span>本次程序动作</span><strong>{operationLabels[activity.operation] || activity.label}</strong></div>
        <div className="current-action-target">{typeof activity.volume_ml==='number' ? <><strong>{activity.volume_ml}</strong><span>mL · 目标体积</span></> : <strong>{routeName(activity.route) || '设备动作'}</strong>}</div>
        {activity.stroke_count>1 && <p className="detail-note">第 {activity.stroke_index} / {activity.stroke_count} 次 · 本次 {activity.stroke_volume_ml ?? '—'} mL · 已完成分次标称量 {activity.completed_nominal_ml ?? 0} / {activity.volume_ml} mL</p>}
        {activity.state==='executing' && <>
          <div className="equipment-progress-label"><span>已用 {formatDuration(activity.elapsed_seconds)}</span><span>{percent===null?'正在执行':'估算进度 '+percent+'%'}</span></div>
          {percent!==null && <div className="equipment-progress" role="progressbar" aria-label="估算动作进度" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}><span style={{width:percent+'%'}}/></div>}
          <p className="detail-note">{activity.stroke_count>1?'进度按已完成分次累计；体积按活塞步数换算，非流量传感器实测。':'进度按程序计时估算，体积不是流量传感器实测值。'}</p>
        </>}
      </section> : <div className="equipment-current-note"><CircleHelp size={15}/><p>{activity.state==='busy' ? '设备最近返回工作中；当前没有关联的程序动作，暂无法确定目标体积与进度。' : !fresh ? '状态连接已中断，等待重新获取数据。' : hardware?.stale ? '最近的设备读数已过期，可点击刷新重新读取。' : '当前没有正在执行的程序动作。'}</p></div>}
      {currentHardware && ['idle','busy','not_ready'].includes(hardware.state) && <dl className="equipment-readings">
        {hardware.channel!==undefined && <><dt>设备返回通道</dt><dd>{hardware.channel}{config.routes?.water?[5,6].includes(hardware.channel)?' · '+(hardware.channel===5?'水':'电解液'):' · 未配置入口':[5,6].includes(hardware.channel)?' · 废液 '+(hardware.channel===5?'1':'2'):''}</dd></>}
        {hardware.position_steps!==undefined && <><dt>活塞位置</dt><dd>{hardware.position_steps} 步</dd></>}
        {hardware.valve_position && <><dt>注射泵阀位</dt><dd>{hardware.valve_position.toLowerCase()==='i'?'I · 吸入口':hardware.valve_position.toLowerCase()==='o'?'O · 注出口':hardware.valve_position}</dd></>}
      </dl>}
    </>}
    {!item.virtual && <>
      {hardware?.reason && config.kind!=='electrochemical' && <p className="equipment-device-hint">{!currentHardware?'上次读取提示：':''}{hardwareReason[hardware.reason] || '设备状态尚未确认，请检查连接与通信设置。'}</p>}
      {currentHardware && Number.isInteger(hardware.error_code) && hardware.error_code!==0 && <p className="equipment-device-hint">设备返回错误码 {hardware.error_code}。{item.id==='syringe_c' && [1,7].includes(hardware.error_code)?'需确认接液条件后，显式执行注射泵回零；刷新不会自动初始化。':''}</p>}
      <details className="equipment-connection-details"><summary>通信与记录 <ChevronRight size={13}/></summary><dl>
        <dt>串口节点</dt><dd>{device?.port_present===true?'存在':device?.port_present===false?'未发现':'待确认'}<small>节点存在不代表设备已通电</small></dd>
        <dt>设备接口</dt><dd><code>{device?.port || config.port || '未配置'}</code></dd>
        {config.kind!=='electrochemical' && <><dt>设备地址</dt><dd>{config.address ?? '未配置'}</dd></>}
        <dt>波特率</dt><dd>{device?.baudrate ?? config.baudrate ?? '未配置'}</dd>
        <dt>最近设备查询</dt><dd>{timeText(hardware?.queried_at)}{hardware && <small>{!fresh?'状态连接待恢复':hardware.stale?'读数已过期':'最近读数'}</small>}</dd>
        <dt>上次程序记录</dt><dd>{timeText(device?.last_observation?.at)}{device?.last_observation?.channel!==undefined && <small>历史通道 {device.last_observation.channel}</small>}</dd>
      </dl></details>
    </>}
  </div>;
}

export default function EquipmentMonitor({ items, selectedItem, fresh, onSelect, onRefresh, refreshing, role,measurement,onOpenMeasurement }) {
  const summary=equipmentSummary(items);
  return <section className="equipment-monitor-panel" aria-label="实时设备状态监测">
    <header className="equipment-panel-heading"><div><span className="equipment-eyebrow">LIVE MONITOR</span><h3>{selectedItem?'设备状态详情':'设备状态监测'}</h3></div>
      <button className="monitor-refresh-button" type="button" onClick={onRefresh} disabled={refreshing} aria-label={role==='viewer'?'更新设备状态显示':'刷新全部设备状态'} aria-busy={refreshing}><RefreshCw size={13} className={refreshing?'refresh-spinning':''}/>{refreshing?'刷新中':role==='viewer'?'更新显示':'刷新状态'}</button>
    </header>
    {selectedItem ? <>
      <button className="equipment-back-button" type="button" onClick={()=>onSelect(null)}><ArrowLeft size={14}/>全部设备<span>{summary.total} 台设备 · {summary.reserved} 个预留</span></button>
      <DeviceDetails item={selectedItem} fresh={fresh} measurement={measurement} onOpenMeasurement={onOpenMeasurement}/>
    </> : <>
      <div className="equipment-monitor-summary" aria-label="设备状态汇总">
        {[['working','工作中'],['idle','空闲 / 完成'],['connected','已连接'],['attention','需关注'],['unknown','待确认']].map(([key,label])=><div className={'monitor-count count-'+key} key={key}><strong>{summary[key]}</strong><span>{label}</span></div>)}
      </div>
      <div className="equipment-list-heading"><span>{summary.total} 台设备{summary.reserved>0 && <span className="monitor-reserved-count">+ {summary.reserved} 个测量预留</span>}</span><span>点击查看详情</span></div>
      <div className="equipment-monitor-list" aria-label="设备列表">{items.map(item=><button className={'equipment-monitor-row tone-'+item.tone} type="button" key={item.id} onClick={()=>onSelect(item.id)} aria-label={'查看'+item.name+'的状态详情'}>
        <span className="monitor-device-icon"><ActionIcon kind={item.config.kind} size={18}/></span>
        <span className="monitor-row-identity"><strong>{item.name}</strong><small>{item.group} · {deviceQuickInfo(item)}</small></span>
        <EquipmentState activity={item.activity}/><ChevronRight className="monitor-row-chevron" size={13}/>
      </button>)}</div>
      <p className="equipment-monitor-note"><CircleHelp size={14}/><span>程序动作自动更新。{role==='viewer'?'设备读数由管理员手动刷新。':'点击刷新可读取设备状态。'}串口读数超过 30 秒会标记为待刷新。</span></p>
    </>}
  </section>;
}
