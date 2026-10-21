"""Small device drivers; serial links are supplied by the runner."""
from __future__ import annotations

import time
from .transport import DeviceError


class Device:
    def __init__(self, name, config, link, event, clock=time.monotonic, sleep=time.sleep):
        self.name, self.config, self.link, self.event = name, config, link, event
        self.clock, self.sleep = clock, sleep
        self.motion_pending = False

    def custom(self, function, value=0, allowed=(0,)):
        result = self.link.custom(function, value)
        if result.status not in allowed:
            raise DeviceError(f"{self.name}: 功能 0x{function:02X} 返回状态 0x{result.status:02X}")
        return result

    def normal(self):
        return self.custom(0x4A, allowed=(0, 4, 254)).status == 0

    def wait_normal(self, timeout):
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            if self.normal():
                return
            self.sleep(self.config["poll_interval_s"])
        raise DeviceError(f"{self.name}: 等待停止超时")

    def stop(self):
        self.custom(0x49, allowed=(0, 4, 254))
        self.wait_normal(self.config["stop_timeout_s"])
        self.motion_pending = False
        self.event("device_stopped", device=self.name)

    def preflight(self):
        self.custom(0x3F)
        if not self.normal():
            raise DeviceError(f"{self.name}: 当前设备忙，流程尚未开始")


class Peristaltic(Device):
    def run(self, step):
        seconds = step["duration_s"]
        if seconds is None or seconds <= 0 or seconds > self.config["max_run_seconds"]:
            raise DeviceError(f"{self.name}: 无有效的有限运行时间")
        self.custom(0x4B, step["rpm"])
        self.motion_pending = True
        started = self.clock()
        deadline = started + seconds
        self.custom(0x47 if step["direction"] == "CW" else 0x48, allowed=(0, 4, 254))
        while self.clock() < deadline:
            self.normal()  # Busy is allowed; communication loss or a fault aborts.
            remaining = deadline - self.clock()
            if remaining > 0:
                self.sleep(min(self.config["poll_interval_s"], remaining))
        self.stop()
        self.event("timed_transfer_finished", device=self.name,
                   target_volume_ml=step["volume_ml"],
                   timing_equivalent_ml=step["timing_equivalent_ml"],
                   planned_seconds=seconds, elapsed_seconds=self.clock() - started,
                   volume_measured=False)


class Valve(Device):
    def channel(self):
        value = self.custom(0x3E).value
        if not 1 <= value <= self.config["channels"]:
            raise DeviceError(f"{self.name}: 返回无效阀通道 {value}")
        return value

    def preflight(self):
        super().preflight()
        self.channel()

    def goto(self, channel):
        if self.channel() == channel:
            self.event("valve_already_positioned", device=self.name, channel=channel)
            return
        self.motion_pending = True
        self.custom(0x44, channel, allowed=(0, 4, 254))
        deadline = self.clock() + self.config["completion_timeout_s"]
        while self.clock() < deadline:
            if self.normal() and self.channel() == channel:
                self.motion_pending = False
                self.event("valve_position_verified", device=self.name, channel=channel)
                return
            self.sleep(self.config["poll_interval_s"])
        raise DeviceError(f"{self.name}: 阀未在规定时间到达通道 {channel}")


class Syringe(Device):
    def ascii(self, command):
        result = self.link.ascii(command)
        if result.error != 0:
            raise DeviceError(f"{self.name}: {command} 返回错误码 {result.error}，不自动复位")
        return result

    def ready(self):
        return self.ascii("Q").ready

    def position(self):
        try:
            position = int(self.ascii("?").payload)
        except ValueError as error:
            raise DeviceError(f"{self.name}: 活塞位置不是整数") from error
        if not 0 <= position <= self.config["stroke_steps"]:
            raise DeviceError(f"{self.name}: 活塞位置超出行程")
        return position

    def preflight(self, transfers=()):
        if not self.ready():
            raise DeviceError(f"{self.name}: 注射泵当前忙")
        # IR/OR/A...R must not execute an old queued program.
        if self.ascii("?10").payload.strip() != "0":
            raise DeviceError(f"{self.name}: 存在未执行的缓冲指令，流程尚未开始")
        expected = self.config["expected_serial_number"]
        if expected and self.ascii("?202").payload.strip() != expected:
            raise DeviceError(f"{self.name}: 序列号与配置不符")
        start = self.position()
        for step in transfers:
            if start + step["delta_steps"] > self.config["stroke_steps"]:
                raise DeviceError(f"{self.name}: 当前余量不足以先吸入 {step['volume_ml']} mL，不截短体积")

    def wait_ready(self, timeout):
        deadline = self.clock() + min(timeout, self.config["completion_timeout_s"])
        while self.clock() < deadline:
            if self.ready():
                return
            self.sleep(self.config["poll_interval_s"])
        raise DeviceError(f"{self.name}: 等待注射泵完成超时")

    def set_valve(self, direction):
        self.motion_pending = True
        self.ascii("IR" if direction == "in" else "OR")
        self.wait_ready(self.config["valve_timeout_s"])
        expected = "i" if direction == "in" else "o"
        if self.ascii("?6").payload.strip().lower() != expected:
            raise DeviceError(f"{self.name}: 注射泵内置阀未到达 {expected}")
        self.motion_pending = False

    def move(self, target, speed):
        if (type(target) is not int or not 0 <= target <= self.config["stroke_steps"]
                or type(speed) is not int or not 1 <= speed <= self.config["max_speed_steps_s"]):
            raise DeviceError(f"{self.name}: 活塞目标或步速超出配置范围")
        start = self.position()
        self.motion_pending = True
        self.ascii(f"v{speed}V{speed}A{target}R")
        self.wait_ready(abs(target - start) / speed * self.config["move_timeout_factor"]
                        + self.config["move_timeout_margin_s"])
        actual = self.position()
        if actual != target:
            raise DeviceError(f"{self.name}: 实际位置 {actual} 与目标 {target} 不一致")
        self.motion_pending = False

    def transfer(self, step):
        self.preflight([step])
        start, speed = self.position(), step["speed_steps_s"]
        target = start + step["delta_steps"]
        self.set_valve("in")
        self.move(target, speed)
        self.set_valve("out")
        self.move(start, speed)
        self.event("syringe_transfer_finished", device=self.name,
                   requested_ml=step["volume_ml"],
                   volume_by_steps_ml=step["achievable_volume_ml"],
                   start_steps=start, end_steps=self.position(), volume_measured=False)

    def initialize_manual(self):
        """Explicit mechanical homing; never called automatically by transfer()."""
        self.motion_pending = True
        reply = self.link.ascii("ZR")
        if reply.error not in (0, 1):
            raise DeviceError(f"{self.name}: homing command returned error {reply.error}")
        deadline = self.clock() + self.config["completion_timeout_s"]
        while self.clock() < deadline:
            reply = self.link.ascii("Q")
            if reply.error not in (0, 1):
                raise DeviceError(f"{self.name}: homing returned error {reply.error}")
            if reply.ready and reply.error == 0:
                position = self.position()
                if position != 0:
                    raise DeviceError(f"{self.name}: homing finished at {position}, expected 0")
                valve = self.ascii("?6").payload.strip()
                buffer = self.ascii("?10").payload.strip()
                self.motion_pending = False
                result = {"position_steps": position, "valve": valve,
                          "buffer_state": buffer, "ready": True, "error": 0}
                self.event("syringe_homed", device=self.name, result=result)
                return result
            if reply.ready and reply.error:
                raise DeviceError(f"{self.name}: homing failed with error {reply.error}")
            self.sleep(self.config["poll_interval_s"])
        raise DeviceError(f"{self.name}: mechanical homing timed out")

    def stop(self):
        self.ascii("T")
        self.wait_ready(self.config["stop_timeout_s"])
        self.motion_pending = False
        self.event("device_stopped", device=self.name)


DEVICE_CLASSES = {"peristaltic": Peristaltic, "valve": Valve, "syringe": Syringe}
