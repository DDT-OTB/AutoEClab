import { deviceActivity } from './monitor.js';
import { fluidLabels } from './model.js';

export const DEVICE_MAP = {
  peristaltic_1: { name:'泵 1 · 超纯水', shortName:'超纯水泵', subtitle:'泵 1 · 水 A', group:'注入侧', position:'water', tone:'water' },
  peristaltic_3: { name:'泵 3 · 硫酸', shortName:'硫酸泵', subtitle:'泵 3 · 硫酸 B', group:'注入侧', position:'acid', tone:'acid' },
  syringe_c: { name:'注射泵 · 电解液', shortName:'注射泵', subtitle:'电解液 C', group:'注入侧', position:'syringe', tone:'electrolyte' },
  peristaltic_2: { name:'泵 2 · 排液', shortName:'排液泵', subtitle:'泵 2 · 废液', group:'排液侧', position:'drain', tone:'waste' },
  valve: { name:'切换阀', shortName:'切换阀', subtitle:'废液流路选择', group:'排液侧', position:'valve', tone:'waste' },
  electrochemical_station: { name:'电化学工作站', shortName:'电化学工作站', subtitle:'循环伏安法 · 预留', group:'测量侧', position:'station', tone:'measurement' },
};
const ORDER = ['peristaltic_1','peristaltic_3','syringe_c','peristaltic_2','valve'];

export function equipmentDevices(templates, measurementTemplates, status, fresh) {
  const keys = Object.keys(templates).sort((a,b) => {
    const ai = ORDER.indexOf(a), bi = ORDER.indexOf(b);
    return (ai<0 ? 100 : ai)-(bi<0 ? 100 : bi);
  });
  const items = keys.map(id => {
    const template = templates[id];
    const device = status.devices?.find(item=>item.id===id);
    return { id, template, config:template.device, device, virtual:false,
      ...({ name:template.device.label,shortName:template.device.label,subtitle:fluidLabels[template.action.fluid] || '仪器',
        group:'其他设备',tone:'other',position:null }), ...DEVICE_MAP[id],
      activity:deviceActivity(device,status.runtime,fresh),
    };
  });
  const template = measurementTemplates?.electrochemical_station;
  const integrated=template?.device?.execution_supported===true;
  const device=status.devices?.find(item=>item.id==='electrochemical_station');
  items.push({ id:'electrochemical_station', ...DEVICE_MAP.electrochemical_station, virtual:!integrated,
    subtitle:integrated?'循环伏安法 · EC110':DEVICE_MAP.electrochemical_station.subtitle, device,
    template, config:template?.device || {kind:'electrochemical'},
    activity:integrated?deviceActivity(device,status.runtime,fresh):{state:'not_integrated',label:'尚未接入'},
  });
  return items;
}

export function equipmentSummary(items) {
  const physical=items.filter(item=>!item.virtual);
  return {
    total:physical.length, reserved:items.length-physical.length,
    working:physical.filter(item=>['executing','busy'].includes(item.activity.state)).length,
    idle:physical.filter(item=>['idle','waiting','completed'].includes(item.activity.state)).length,
    attention:physical.filter(item=>['error','no_response','port_missing','in_use','not_ready','unsupported'].includes(item.activity.state)).length,
    unknown:physical.filter(item=>!['executing','busy','idle','waiting','completed','error','no_response','port_missing','in_use','not_ready','unsupported'].includes(item.activity.state)).length,
  };
}

export function activeFlowDevice(items, runtime, fresh) {
  const step=runtime?.active_step;
  if (!fresh || runtime?.state!=='running' || step?.state!=='executing' || step.action==='initialize_syringe') return null;
  const device=items.find(item=>item.id===step.device && !item.virtual);
  return device?.activity.state==='executing' ? device.id : null;
}

export function confirmedWasteRoute(items, runtime, fresh) {
  if (!fresh) return null;
  const valve=items.find(item=>item.id==='valve')?.device;
  const hardware=valve?.hardware;
  if (hardware && !hardware.stale && ['idle','busy','not_ready'].includes(hardware.state) && runtime?.controller_alive!==true && [5,6].includes(hardware.channel))
    return {channel:hardware.channel,route:hardware.channel===5?'waste_1':'waste_2',source:'hardware',at:hardware.queried_at};
  const observation=valve?.last_observation;
  if (runtime?.state==='running' && valve?.activity?.state==='completed' && [5,6].includes(observation?.channel))
    return {channel:observation.channel,route:observation.channel===5?'waste_1':'waste_2',source:'controller',at:observation.at};
  return null;
}

export function equipmentStatusSource(item, fresh) {
  if (item.virtual) return '测量接口预留';
  if (!fresh) return '状态连接待恢复';
  if (item.activity.source==='hardware') return '最近设备查询';
  if (item.device?.activity && ['executing','waiting','completed','error'].includes(item.activity.state)) return '控制程序事件';
  if (item.device?.hardware?.stale) return '设备读数已过期';
  return item.device?.port_present ? '串口节点存在，状态待确认' : '设备状态尚未确认';
}

export function deviceQuickInfo(item) {
  if (item.virtual) return 'CV 参数已可编排';
  if(item.id==='electrochemical_station')return 'CV 测量 · 自动归档';
  if (item.activity.state==='executing' && typeof item.activity.volume_ml==='number') return '目标 '+item.activity.volume_ml+' mL';
  if (item.config.rpm) return '模板 '+item.config.rpm+' rpm';
  if (item.config.speed_steps_s) return '模板 '+item.config.speed_steps_s+' 步/秒';
  return item.config.channels ? item.config.channels+' 个通道' : item.group;
}

export const hardwareReason = {
  response_timeout:'本次查询无应答，请确认设备电源、连接与通信设置。',
  serial_node_missing:'未发现配置的串口节点。',
  serial_port_in_use:'串口被其他程序占用，本次未读取。',
  serial_permission_denied:'服务没有访问该串口的权限。',
  identity_mismatch:'读取的设备标识与配置不一致。',
  invalid_valve_position:'切换阀返回的通道信息无效。',
  status_query_failed:'设备状态读取失败，可检查连接后再次刷新。',
  serial_close_failed:'串口关闭异常，需要检查通信连接。',
  unsupported_instrument:'尚未提供此类仪器的状态适配。',
  invalid_serial_node:'配置的设备接口不是有效串口。',
};
