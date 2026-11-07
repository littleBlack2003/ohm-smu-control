# Ohm SMU Neo 上位机 / Ohm SMU Neo Control

[中文](#中文) | [English](#english)

---

## 中文

适配 uSMU v10 固件命令集的 Windows 上位机，单文件 exe，无需安装 Python。界面支持中文 / English，左上角“语言 / Language”可随时切换；首次启动按系统语言自动选择。

### 使用

1. 从 [Releases](https://github.com/littleBlack2003/ohm-smu-control/releases) 下载 `OhmSMU.exe` 并运行（或自行打包，见“源码与打包”）。
2. 在“连接”中选择设备的 COM 口（“USB 串行设备”，VID:PID 0483:5740），点“连接”。没有硬件时可选“SIM”模拟器（二极管）或“SIM-PV”模拟器（光照下的太阳能电池）演示全部功能。
3. 功能：
   - **源输出**：设置电压、限流、平均次数（OSR）、电流量程（自动 / 锁定 1–4），打开/关闭输出，单点测量。
   - **I-V 扫描**：起止电压、点数、稳定延时，可往返扫描，完成后自动关闭输出；多条曲线叠加，可切换 |I| 对数坐标；导出 CSV（UTF-8 BOM，Excel 可直接打开）。
   - **定时监测**：恒定偏置下按间隔采样，显示 I-t 曲线与均值/标准差，导出 CSV。
   - **光伏测试**（v1.2.0 新增）：输入光强（mW/cm²，默认 100）和电池有效面积（cm²），扫描 J-V 曲线，计算 Voc、Jsc、FF、PCE、Pmax、最大功率点（Vmp、Jmp）、串联电阻 Rs 和并联电阻 Rsh（同时给出 Ω 和 Ω·cm²）。详见下方“光伏测试说明”。
4. 参数与语言保存在 `%APPDATA%\OhmSMU\settings.json`，下次启动自动恢复。导出 CSV 的表头使用当前界面语言。

### 光伏测试说明

- **接线**：电池正极接 HI、负极接 LO。接反也能算，结果表“接线极性”一栏会注明，参数按电池实际极性换算。
- **扫描范围**：起点要略低于 0 V（如 −0.1 V），终点要高于 Voc（如钙钛矿 1.2 V、硅电池 0.7 V）。范围不够时会提示原因，不出结果。
- **正反扫**：勾选后先正扫（Jsc → Voc）再反扫（Voc → Jsc），两组参数并列显示，并给出迟滞因子 HI = (PCE反扫 − PCE正扫) / PCE反扫。
- **计算方法**：
  - Jsc：0 ~ 0.25·Voc 段线性拟合在 0 V 处的截距；Voc：电流过零点附近 4 点二次拟合求根；
  - Pmax：0 ~ Voc 内功率最大点，再用相邻 3 点抛物线细化；FF = Pmax / (Voc·Isc)；PCE = Pmax / (光强 × 面积)；
  - Rs、Rsh 用斜率法：Rs = −dV/dI 在 Voc 处，Rsh = −dV/dI 在 0 ~ 0.25·Voc 段。斜率法的 Rs 包含二极管在 Voc 处的动态电阻，是上限估计；Rsh 很大时对电流噪声敏感，可增加点数或 OSR。
- **光强和面积**由用户输入，软件不测光强。改动后点“重新计算”即可，不必重测。
- **限流**：左侧“限流”要大于 Isc（面积 × Jsc）。读数达到限流时日志会警告 Jsc 可能被截断。源表最大 40 mA，例如 Jsc 25 mA/cm² 时面积不宜超过约 1.5 cm²。
- “分析 I-V 扫描最新曲线”可直接分析 I-V 扫描页最后一条曲线。导出 CSV 包含测试条件、参数表和原始数据。

### 安全与固件规避

uSMU v10 固件有若干缺陷（应答带 NUL 填充、不检查参数范围、上电时 DAC 处于零刻度等）。上位机侧的保护：

- 电压限制 ±5 V，限流 0.1–40 mA，OSR 1–255，超出自动限幅并在日志中警告；
- 打开输出前先写 0 V 和限流；关闭输出、断开连接、退出程序时都会回 0 V 并关闭输出；
- 读数达到限流 95% 时显示“已达限流”；
- 应答去除固件的 NUL 填充，命令间留 50 ms 间隔；
- 不提供 `WRITE`/`CAL:*` 校准写入入口，避免误操作覆盖校准数据。

### 部署到其他电脑

只需复制 `OhmSMU.exe` 一个文件。Python 运行时、VC++ 运行库、tkinter/Tcl/Tk、numpy、matplotlib、pyserial 均已打包在内，目标电脑无需安装 Python 或任何运行库。

- 系统：Windows 10 / 11 64 位（不支持 Windows 7/8 与 32 位系统）。
- 驱动：源表使用 Windows 自带的 USB 串口驱动，插上即用。
- 首次启动需解压到 `%TEMP%`，约需数秒；部分杀毒软件可能误报 PyInstaller 打包的程序，加入信任即可。
- 不需要 Keysight IO Libraries（仅核查脚本使用）。

### 故障排查

- **连接后无应答，或应答只是原样回显命令**：多半是 COM 口号冲突（两个设备被分配了同一个 COM 号）。在设备管理器 → 端口 → “USB 串行设备” → 属性 → 端口设置 → 高级，把 COM 号改成未被占用的值，或拔掉冲突的另一个 USB 串口设备。

### 源码与打包

| 文件 | 说明 |
|---|---|
| `ohm_smu_app.py` | 界面（tkinter + matplotlib） |
| `i18n.py` | 中英文案 |
| `smu_device.py` | 串口驱动与限幅逻辑 |
| `pv_analysis.py` | 光伏参数计算（无界面依赖，可单独调用） |
| `smu_sim.py` | 固件行为模拟器（二极管 / 太阳能电池 DUT 模型） |
| `build.bat` | PyInstaller 打包脚本，输出 `dist/OhmSMU.exe` |

```
python -m pip install -r requirements.txt
build.bat
```

开发时可直接 `python ohm_smu_app.py` 运行。

### 版本

| 版本 | 日期 | 内容 |
|---|---|---|
| 1.2.0 | 2026-11-07 | 新增光伏测试页和 SIM-PV 模拟器 |
| 1.1.0 | 2026-10-29 | 中英双语界面、I-V 扫描、定时监测 |

### 许可证与致谢

本软件以 [GPL-3.0](LICENSE) 许可证发布。硬件与固件来自 Joel Troughton 的开源项目 [μSMU](https://github.com/joeltroughton/uSMU)（CERN-OHL-P v2），本仓库只包含上位机软件，不包含其硬件设计或固件。

---

## English

Windows control software for the uSMU v10 firmware command set. Single-file exe; no Python installation required. The UI is available in Chinese and English — switch any time with "语言 / Language" at the top left; on first launch the system UI language is used.

### Usage

1. Download `OhmSMU.exe` from [Releases](https://github.com/littleBlack2003/ohm-smu-control/releases) and run it (or build it yourself; see "Source and build").
2. Under "Connection", choose the device's COM port ("USB Serial Device", VID:PID 0483:5740) and click "Connect". Without hardware, choose "SIM" (diode) or "SIM-PV" (illuminated solar cell) to try every feature with the built-in simulator.
3. Features:
   - **Source output**: set voltage, current limit, averaging (OSR) and current range (auto / locked 1–4); turn output on/off; single-point measurement.
   - **I-V sweep**: start/stop voltage, points, settle time, optional dual (forward + reverse) sweep, automatic output-off when done; overlay multiple curves, toggle log |I|; export CSV (UTF-8 with BOM, opens directly in Excel).
   - **Time monitor**: sample at a fixed bias and interval, plot I vs. t with mean/standard deviation, export CSV.
   - **PV test** (new in v1.2.0): enter the irradiance (mW/cm², default 100) and the cell's active area (cm²), sweep the J-V curve and get Voc, Jsc, FF, PCE, Pmax, the maximum power point (Vmp, Jmp), series resistance Rs and shunt resistance Rsh (in Ω and Ω·cm²). See "PV test notes" below.
4. Parameters and language are saved to `%APPDATA%\OhmSMU\settings.json` and restored on the next launch. CSV headers follow the current UI language.

### PV test notes

- **Wiring**: cell anode to HI, cathode to LO. Reversed wiring is detected and corrected; the "Polarity" row says so.
- **Sweep range**: start slightly below 0 V (e.g. −0.1 V) and stop above Voc (e.g. 1.2 V for perovskite, 0.7 V for silicon). If the range is not enough, the reason is shown instead of results.
- **Forward + reverse**: a forward scan (Jsc → Voc) then a reverse scan (Voc → Jsc), shown side by side, with the hysteresis index HI = (PCE_reverse − PCE_forward) / PCE_reverse.
- **Method**:
  - Jsc: intercept at 0 V of a linear fit over 0 to 0.25·Voc; Voc: root of a quadratic fit to the 4 points around the zero crossing;
  - Pmax: highest-power point between 0 and Voc, refined with a parabola through its neighbours; FF = Pmax / (Voc·Isc); PCE = Pmax / (irradiance × area);
  - Rs and Rsh use the slope method: Rs = −dV/dI at Voc, Rsh = −dV/dI over 0 to 0.25·Voc. The slope-method Rs includes the diode's dynamic resistance at Voc, so it is an upper bound; a large Rsh is sensitive to current noise — use more points or a higher OSR.
- **Irradiance and area** are user inputs; the software does not measure light. After changing them, click "Recalculate" — no need to re-measure.
- **Current limit**: set it above Isc (area × Jsc). If readings reach the limit, the log warns that Jsc may be clipped. The SMU's maximum is 40 mA, so at 25 mA/cm² keep the area below about 1.5 cm².
- "Analyse latest I-V sweep" analyses the last curve from the I-V Sweep tab. The CSV export contains the conditions, the parameter table and the raw data.

### Safety and firmware workarounds

The uSMU v10 firmware has several defects (NUL-padded replies, no range checks on parameters, the DAC at zero scale after power-up, and others). The software protects against them by:

- Clamping voltage to ±5 V, current limit to 0.1–40 mA and OSR to 1–255, with a warning in the log;
- Writing 0 V and the current limit before enabling the output; returning to 0 V and disabling the output on output-off, disconnect and exit;
- Showing "In compliance" when the reading reaches 95% of the current limit;
- Stripping the firmware's NUL padding from replies and spacing commands 50 ms apart;
- Not exposing the `WRITE` / `CAL:*` calibration-write commands, so calibration data cannot be overwritten by mistake.

### Deploying to another PC

Copy `OhmSMU.exe` alone. The Python runtime, VC++ runtime, tkinter/Tcl/Tk, numpy, matplotlib and pyserial are bundled, so the target PC needs no Python or runtime installation.

- OS: Windows 10 / 11, 64-bit (Windows 7/8 and 32-bit systems are not supported).
- Driver: the SMU uses Windows' built-in USB serial driver; plug and play.
- Each launch unpacks to `%TEMP%`, which takes a few seconds; some antivirus tools flag PyInstaller-built programs by mistake — add it to the allow list.
- Keysight IO Libraries is not required (only the verification scripts use it).

### Troubleshooting

- **No reply after connecting, or the reply is just the command echoed back**: most likely a COM number conflict (two devices assigned the same COM number). In Device Manager → Ports → "USB Serial Device" → Properties → Port Settings → Advanced, change the COM number to an unused one, or unplug the other conflicting USB serial device.

### Source and build

| File | Purpose |
|---|---|
| `ohm_smu_app.py` | UI (tkinter + matplotlib) |
| `i18n.py` | Chinese / English strings |
| `smu_device.py` | Serial driver and clamping |
| `pv_analysis.py` | PV parameter calculation (no UI dependency; usable on its own) |
| `smu_sim.py` | Firmware simulator (diode / solar-cell DUT models) |
| `build.bat` | PyInstaller build script, outputs `dist/OhmSMU.exe` |

```
python -m pip install -r requirements.txt
build.bat
```

During development, run `python ohm_smu_app.py` directly.

### Versions

| Version | Date | Changes |
|---|---|---|
| 1.2.0 | 2026-11-07 | PV test tab and SIM-PV simulator |
| 1.1.0 | 2026-10-29 | Bilingual UI, I-V sweep, time monitor |

### License and acknowledgements

This software is released under the [GPL-3.0](LICENSE) licence. The hardware and firmware come from Joel Troughton's open-source [μSMU](https://github.com/joeltroughton/uSMU) project (CERN-OHL-P v2); this repository contains only the host software, not its hardware design or firmware.
