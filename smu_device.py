"""Ohm SMU Neo 串口驱动（兼容 uSMU v10 固件命令集）。

uSMU v10 固件存在若干缺陷，本驱动做了以下规避：
- 应答去除 NUL 填充，每次查询前清空接收缓冲
- 电压、限流、OSR 在上位机侧限幅（固件不检查范围）
- 开启输出前先写 0 V 与限流（固件上电时 DAC 处于零刻度，约 −6 V）
- 命令之间留间隔，避免固件单缓冲区被覆盖
"""

import threading
import time

import serial
from serial.tools import list_ports

V_MIN, V_MAX = -5.0, 5.0
ILIM_MIN_MA, ILIM_MAX_MA = 0.1, 40.0
OSR_MIN, OSR_MAX = 1, 255
OSR_DEFAULT = 25
CMD_GAP_S = 0.05
SIM_PORT = "SIM"
SIM_PV_PORT = "SIM-PV"

# 量程编号 -> (中文, English)；满量程按 4.096 V / 49.9 Ω / 增益估算
RANGES = {
    0: ("自动", "Auto"),
    1: ("量程1 ±60 mA", "Range 1 ±60 mA"),
    2: ("量程2 ±10 mA", "Range 2 ±10 mA"),
    3: ("量程3 ±1.3 mA", "Range 3 ±1.3 mA"),
    4: ("量程4 ±0.47 mA", "Range 4 ±0.47 mA"),
}

_MESSAGES = {
    "sim_desc": ("内置模拟器（无需硬件）", "Built-in simulator (no hardware)"),
    "sim_pv_desc": ("内置模拟器：光照太阳能电池", "Built-in simulator: illuminated solar cell"),
    "open_fail": ("无法打开 {port}：{e}", "Cannot open {port}: {e}"),
    "write_fail": ("串口写入失败：{e}", "Serial write failed: {e}"),
    "comm_fail": ("串口通信失败：{e}", "Serial communication failed: {e}"),
    "timeout": ("命令 {cmd!r} 无应答（超时 {t:.1f} s）", "No reply to {cmd!r} (timeout {t:.1f} s)"),
    "bad_range": ("无效量程 {r}", "Invalid range {r}"),
    "parse_fail": ("无法解析测量应答：{line!r}", "Cannot parse measurement reply: {line!r}"),
}
lang = "zh"  # 由界面设置："zh" 或 "en"


def msg(key, **kw):
    return _MESSAGES[key][0 if lang == "zh" else 1].format(**kw)


def range_name(r):
    return RANGES[r][0 if lang == "zh" else 1]


class SMUError(Exception):
    pass


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def available_ports():
    """返回 [(device, 描述)]，两个模拟器固定排在最前。"""
    ports = [(SIM_PORT, msg("sim_desc")), (SIM_PV_PORT, msg("sim_pv_desc"))]
    for p in sorted(list_ports.comports(), key=lambda p: p.device):
        ports.append((p.device, p.description or p.device))
    return ports


class OhmSMU:
    def __init__(self, port):
        if port in (SIM_PORT, SIM_PV_PORT):
            from smu_sim import SimSerial
            self.ser = SimSerial("pv" if port == SIM_PV_PORT else "diode")
        else:
            try:
                # USB CDC 虚拟串口，波特率无实际意义
                self.ser = serial.Serial(port, 115200, timeout=0.05, write_timeout=1.0)
            except serial.SerialException as e:
                raise SMUError(msg("open_fail", port=port, e=e)) from e
        self.port = port
        self.lock = threading.RLock()
        self.osr = OSR_DEFAULT
        self.ilim_ma = 20.0
        self.output_on = False
        time.sleep(0.1)
        self.ser.reset_input_buffer()

    # ---------- 底层 ----------
    def close(self):
        with self.lock:
            try:
                if self.output_on:
                    self.set_output(False)
            except Exception:
                pass
            self.ser.close()

    def _write(self, cmd):
        with self.lock:
            try:
                self.ser.write((cmd + "\n").encode("ascii"))
                self.ser.flush()
            except serial.SerialException as e:
                raise SMUError(msg("write_fail", e=e)) from e
            time.sleep(CMD_GAP_S)

    def _query(self, cmd, timeout=1.0):
        with self.lock:
            try:
                self.ser.reset_input_buffer()
                self.ser.write((cmd + "\n").encode("ascii"))
                self.ser.flush()
                buf = b""
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    chunk = self.ser.read(self.ser.in_waiting or 1)
                    if not chunk:
                        continue
                    buf += chunk
                    text = buf.replace(b"\x00", b"")
                    if b"\n" in text:
                        line = text.split(b"\n", 1)[0].decode("ascii", "replace").strip()
                        time.sleep(0.01)
                        self.ser.reset_input_buffer()  # 丢弃剩余 NUL 填充
                        return line
            except serial.SerialException as e:
                raise SMUError(msg("comm_fail", e=e)) from e
            raise SMUError(msg("timeout", cmd=cmd, t=timeout))

    # ---------- 命令 ----------
    def idn(self):
        return self._query("*IDN?", timeout=1.0)

    def set_voltage(self, volts):
        """返回实际下发的（限幅后）电压。"""
        v = clamp(float(volts), V_MIN, V_MAX)
        self._write(f"CH1:VOL {v:.4f}")
        return v

    def set_current_limit(self, ma):
        ma = clamp(float(ma), ILIM_MIN_MA, ILIM_MAX_MA)
        self._write(f"CH1:CUR {ma:.3f}")
        self.ilim_ma = ma
        return ma

    def set_osr(self, n):
        n = int(clamp(int(n), OSR_MIN, OSR_MAX))
        self._write(f"CH1:OSR {n}")
        self.osr = n
        return n

    def set_range(self, r):
        r = int(r)
        if r not in RANGES:
            raise SMUError(msg("bad_range", r=r))
        self._write(f"CH1:RANGE {r}")  # 必须带空格，否则固件静默切到自动量程
        return r

    def set_output(self, on):
        with self.lock:
            if on:
                # 固件上电时 DAC 处于零刻度（≈ −6 V）且限流未启动，先写安全值
                self.set_voltage(0.0)
                self.set_current_limit(self.ilim_ma)
                self._write("CH1:ENA")
            else:
                self.set_voltage(0.0)
                self._write("CH1:DIS")
            self.output_on = on

    def measure(self, volts):
        """设定电压并测量，返回 (实测电压 V, 实测电流 A)。"""
        v = clamp(float(volts), V_MIN, V_MAX)
        # 固件每个样本两次转换，128 SPS 下约 16 ms
        timeout = 0.5 + self.osr * 0.04
        line = self._query(f"CH1:MEA:VOL {v:.4f}", timeout=timeout)
        try:
            vs, is_ = line.split(",")
            return float(vs), float(is_)
        except ValueError as e:
            raise SMUError(msg("parse_fail", line=line)) from e
