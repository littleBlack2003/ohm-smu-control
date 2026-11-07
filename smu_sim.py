"""模拟 uSMU v10 固件的串口行为，用于无硬件演示与测试。

DUT 模型（单二极管模型，串联电阻 Rs、并联电阻 Rsh）：
- "diode"：二极管 Is=1e-12 A、n=1.8，Rs 50 Ω，Rsh 1 MΩ。
- "pv"：光照下的太阳能电池（按 0.1 cm²、钙钛矿量级）：光电流 2.2 mA（22 mA/cm²），Is=1e-15 A、n=1.5，
  Rs 50 Ω（5 Ω·cm²），Rsh 50 kΩ（5 kΩ·cm²）。正极接 HI，发电时 V > 0、I < 0。
应答格式与 NUL 填充长度与固件一致（测量 32 字节、IDN 64 字节、未知命令 1024 字节）。
"""

import math
import random
import time

VT = 0.02585
MODELS = {  # Is, n, Rs, Rsh, 光电流
    "diode": (1e-12, 1.8, 50.0, 1e6, 0.0),
    "pv": (1e-15, 1.5, 50.0, 5e4, 2.2e-3),
}


def _dut_current(v_dut, model="diode"):
    """端电压 v_dut 下流入 DUT 的电流（求解串联电阻的隐式方程）。"""
    is_, n, rs, rsh, iph = MODELS[model]
    lo, hi = -1.0, 1.0
    for _ in range(80):
        i = (lo + hi) / 2
        vd = v_dut - i * rs
        f = is_ * (math.exp(min(vd / (n * VT), 80)) - 1) + vd / rsh - iph - i
        if f > 0:
            lo = i
        else:
            hi = i
    return (lo + hi) / 2


def _dut_voltage(i_target, model="diode"):
    """电流为 i_target 时的端电压（限流时使用）。"""
    lo, hi = -10.0, 10.0
    for _ in range(80):
        v = (lo + hi) / 2
        if _dut_current(v, model) < i_target:
            lo = v
        else:
            hi = v
    return (lo + hi) / 2


class SimSerial:
    def __init__(self, model="diode"):
        self.model = model
        self._rx = bytearray()
        self._ready_at = 0.0
        self._line = b""
        self.v_set = 0.0
        self.enabled = False
        self.ilim_ma = 40.0
        self.osr = 25
        self.is_open = True

    # ---- pyserial 兼容接口 ----
    @property
    def in_waiting(self):
        return len(self._rx) if time.monotonic() >= self._ready_at else 0

    def read(self, n=1):
        if time.monotonic() < self._ready_at or not self._rx:
            time.sleep(0.01)
            return b""
        out = bytes(self._rx[:n])
        del self._rx[:n]
        return out

    def reset_input_buffer(self):
        self._rx.clear()

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    def write(self, data):
        self._line += data
        while b"\n" in self._line:
            line, self._line = self._line.split(b"\n", 1)
            self._handle(line.decode("ascii", "replace").strip())
        return len(data)

    # ---- 固件逻辑 ----
    def _reply(self, text, size, delay=0.0):
        payload = text.encode("ascii")
        self._rx += payload + b"\x00" * max(0, size - len(payload))
        self._ready_at = time.monotonic() + delay

    def _arg(self, line, idx=1):
        parts = line.split(" ")
        return parts[idx] if len(parts) > idx else "0"

    def _handle(self, line):
        if "CH1:ENA" in line:
            self.enabled = True
        elif "CH1:DIS" in line:
            self.enabled = False
        elif "CH1:MEA:VOL" in line:
            self.v_set = float(self._arg(line))
            v, i = self._measure()
            self._reply(f"{v:4.3f},{i:e}\n", 32, delay=self.osr * 0.016)
        elif "CH1:VOL" in line:
            self.v_set = float(self._arg(line))
        elif "CH1:CUR" in line:
            self.ilim_ma = float(self._arg(line))
        elif "CH1:OSR" in line:
            self.osr = max(1, int(self._arg(line)))
        elif "CH1:RANGE" in line or "CH1:VCAL" in line:
            pass
        elif "*IDN?" in line:
            self._reply("uSMU version 1.0 ID:SIMULATOR\n", 64)
        else:
            self._reply("Test string not found\n", 1024)

    def _measure(self):
        if not self.enabled:
            return random.gauss(0, 1e-3), random.gauss(0, 2e-8)
        ilim = self.ilim_ma / 1000
        i = _dut_current(self.v_set, self.model)
        v = self.v_set
        if abs(i) > ilim:
            i = math.copysign(ilim, i)
            v = _dut_voltage(i, self.model)
        v += random.gauss(0, 3e-4)
        i += random.gauss(0, 5e-8 + abs(i) * 1e-3)
        return v, i
