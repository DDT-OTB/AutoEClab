"""Refresh traverses the real worker and PTYs using identity queries only."""
import sys
import unittest

import test_execution_linux as base
import test_cv_execution_linux as cv
from pump_console.ec110 import station_policy
from pump_console.device_status import query_device


@unittest.skipUnless(sys.platform=='linux','Requires Linux PTY serial access')
class StationStatusTests(unittest.TestCase):
    setUpAuthorization=base.LinuxExecutionTests.setUpAuthorization
    tearDownAuthorization=base.LinuxExecutionTests.tearDownAuthorization
    request=base.LinuxExecutionTests.request
    wait_for=base.LinuxExecutionTests.wait_for

    def setUp(self):
        base.LinuxExecutionTests.setUp(self)
        self.station=cv.PseudoStation();self.extra_devices.append(self.station)
        self.repo.station=station_policy(dict(enabled=True,port=self.station.port))
        self.repo.device_status.policy['enabled']=True

    def tearDown(self):base.LinuxExecutionTests.tearDown(self)

    def test_station_refresh_sends_only_identity_and_preserves_measurement_state(self):
        result=query_device('electrochemical_station',{**self.repo.station,'kind':'electrochemical'})
        self.assertEqual(result['state'],'connected');self.assertEqual(result['measurement_state'],'unknown')
        self.assertEqual(result['queries_sent'],1);self.assertEqual(result['motion_commands_sent'],0)
        self.assertEqual([command for command,payload in self.station.commands],[0x03])
        self.assertFalse(self.station.active.is_set())

    def test_api_worker_includes_all_six_devices_and_does_not_query_ports_owned_by_another_process(self):
        code,result=self.request('POST','/api/status/refresh',{})
        self.assertEqual(code,200,result);self.assertEqual(result['scan']['state'],'completed')
        self.assertEqual(len(result['status']['devices']),6)
        station=next(item for item in result['status']['devices'] if item['id']=='electrochemical_station')
        # The PTY slave is held open by this parent, while the query runs in a child.
        self.assertEqual(station['state'],'in_use');self.assertEqual(station['hardware']['reason'],'serial_port_in_use')
        self.assertEqual(self.station.commands,[])
        for device in [self.pump,*self.preflight_devices.values()]:
            self.assertFalse(device.busy);self.assertNotIn(0x47,device.commands);self.assertNotIn(0x44,device.commands)
        self.assertIsNone(self.repo.measurements.latest())


if __name__=='__main__':unittest.main()
