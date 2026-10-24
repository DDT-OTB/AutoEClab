"""One explicit ZR operation, using the installed controller's mechanical homing."""


def initialize(devices, plan, event, existing_pump=None, manage_sequence=True, water_valve=None):
    from lab_automation.devices import Syringe
    from lab_automation.transport import SerialLink, DeviceError
    config = devices['syringe_c']
    link = existing_pump.link if existing_pump is not None else SerialLink('syringe_c', config, event)
    pump = existing_pump if existing_pump is not None else Syringe('syringe_c', config, link, event)
    try:
        state = link.ascii('Q')
        if not state.ready or state.error not in (0, 1, 7):
            raise DeviceError('注射泵忙或存在其他故障，未发送初始化指令。')
        identity = pump.ascii('?202').payload.strip()
        if not config['expected_serial_number'] or identity != config['expected_serial_number']:
            raise DeviceError('注射泵身份核验未通过，未发送初始化指令。')
        previous_buffer = pump.ascii('?10').payload.strip()
        if previous_buffer not in ('0', '1'):
            raise DeviceError('指令缓冲状态无效，未发送初始化指令。')
        event('preflight_passed', device='syringe_c')
        if manage_sequence:
            event('sequence_started', sequence=plan['sequence_id'])
        if water_valve is not None:
            event('step_started',step=plan['steps'][0])
            water_valve.goto(5)
            if not water_valve.normal() or water_valve.channel()!=5:
                raise DeviceError('入口阀未确认处于水路通道 5，禁止注射泵回零。')
            event('syringe_water_source_verified',device='valve',route='water',channel=5)
            event('step_completed',id=plan['steps'][0]['id'])
        if manage_sequence:
            event('step_started', step=plan['steps'][-1])
        if water_valve is not None and (not water_valve.normal() or water_valve.channel()!=5):
            raise DeviceError('回零前水路回读已变化，禁止注射泵回零。')
        event('syringe_initialization_started', device='syringe_c', command='ZR', previous_buffer_state=previous_buffer)
        # SY11 section 4.3.1: a complete new string replaces the pending string.
        # Never send a bare R or use software zeroing z as recovery.
        result = pump.initialize_manual()
        state = pump.ascii('Q')
        if (result['position_steps'] != 0 or result['buffer_state'] != '0' or
                result['valve'].strip().lower() not in ('i', 'o') or not state.ready or state.error):
            raise DeviceError('回零后的零位、阀位、缓冲或就绪核验未通过。')
        if manage_sequence:
            event('step_completed', id=plan['steps'][-1]['id'])
            event('sequence_completed', sequence=plan['sequence_id'])
        return {'position_steps': 0, 'valve_position': result['valve'].strip().lower(),
                'buffer_state': '0', 'serial_number': identity, 'ready': True, 'error_code': 0,
                **({'water_source_verified':True,'water_route':'water','water_channel':5} if water_valve is not None else {})}
    except BaseException as error:
        if manage_sequence:
            event('sequence_aborted', exception=type(error).__name__, message=str(error))
        if pump.motion_pending:
            try:
                pump.stop()
            except Exception as stop_error:
                event('stop_failed', device='syringe_c', message=str(stop_error))
        raise
    finally:
        if existing_pump is None:
            link.close()


def initialize_with_water(devices, plan, event):
    """The calibration worker holds the shared lock across water selection and ZR."""
    from lab_automation.devices import Valve
    from lab_automation.transport import SerialLink
    from .execution import water_calibration_plan
    expected=water_calibration_plan({'devices':devices})
    if plan!=expected:
        raise ValueError('Water-guarded homing plan does not match the fixed calibration template')
    link=SerialLink('valve',devices['valve'],event)
    valve=Valve('valve',devices['valve'],link,event)
    try:
        valve.preflight()
        event('preflight_passed',device='valve')
        return initialize(devices,plan,event,water_valve=valve)
    except BaseException:
        if valve.motion_pending:
            try:valve.stop()
            except Exception as error:event('stop_failed',device='valve',message=str(error))
        raise
    finally:link.close()
