"""One explicit ZR operation, using the installed controller's mechanical homing."""


def initialize(devices, plan, event):
    from lab_automation.devices import Syringe
    from lab_automation.transport import SerialLink, DeviceError
    config = devices['syringe_c']
    link = SerialLink('syringe_c', config, event)
    pump = Syringe('syringe_c', config, link, event)
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
        event('sequence_started', sequence=plan['sequence_id'])
        event('step_started', step=plan['steps'][0])
        event('syringe_initialization_started', device='syringe_c', command='ZR', previous_buffer_state=previous_buffer)
        # SY11 section 4.3.1: a complete new string replaces the pending string.
        # Never send a bare R or use software zeroing z as recovery.
        result = pump.initialize_manual()
        state = pump.ascii('Q')
        if (result['position_steps'] != 0 or result['buffer_state'] != '0' or
                result['valve'].strip().lower() not in ('i', 'o') or not state.ready or state.error):
            raise DeviceError('回零后的零位、阀位、缓冲或就绪核验未通过。')
        event('step_completed', id=plan['steps'][0]['id'])
        event('sequence_completed', sequence=plan['sequence_id'])
        return {'position_steps': 0, 'valve_position': result['valve'].strip().lower(),
                'buffer_state': '0', 'serial_number': identity, 'ready': True, 'error_code': 0}
    except BaseException as error:
        event('sequence_aborted', exception=type(error).__name__, message=str(error))
        if pump.motion_pending:
            try:
                pump.stop()
            except Exception as stop_error:
                event('stop_failed', device='syringe_c', message=str(stop_error))
        raise
    finally:
        link.close()
