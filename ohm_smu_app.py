"""Ohm SMU Neo 上位机：连接、输出控制、I-V 扫描、定时监测、数据导出（中英双语）。"""

import csv
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import matplotlib

matplotlib.use("TkAgg")
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

import i18n
import pv_analysis as pva
import smu_device as sd

APP_VERSION = "1.2.1"
PV_FW_STYLE = {"color": "#D47F00", "marker": "o"}  # 正扫：橙色圆点
PV_RV_STYLE = {"color": "#2F6DB5", "marker": "s"}  # 反扫：蓝色方块
SETTINGS_PATH = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OhmSMU", "settings.json")

matplotlib.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False


def resource_path(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def fmt_current(a):
    for unit, scale in (("A", 1), ("mA", 1e-3), ("µA", 1e-6), ("nA", 1e-9)):
        if abs(a) >= scale or unit == "nA":
            return f"{a / scale:+.4f} {unit}"


class Worker(threading.Thread):
    """串行执行所有设备操作，保证同一时刻只有一个命令在串口上。"""

    def __init__(self):
        super().__init__(daemon=True)
        self.jobs = queue.Queue()

    def run(self):
        while True:
            fn = self.jobs.get()
            fn()

    def submit(self, fn):
        self.jobs.put(fn)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.settings = self._load_settings()
        self.lang = self.settings.get("lang") or i18n.detect_language()
        sd.lang = self.lang
        self.geometry("1280x800")
        self.minsize(1100, 700)
        try:
            self.iconbitmap(resource_path("ohm_smu.ico"))
        except tk.TclError:
            pass

        self.dev = None
        self.worker = Worker()
        self.worker.start()
        self.ui = queue.Queue()
        self.stop_evt = threading.Event()
        self.busy = False
        self.out_on = False

        self.sweeps = []        # [{"label", "rows": [(t, v_set, v, i)], "line", "meta"}]
        self.monitor_rows = []  # [(t, v_set, v, i)]
        self.pv_rows = []       # [(v_set, v, i)]
        self.pv_ilim = None     # 采集这组数据时的限流 mA
        self.pv_results = []    # pv_analysis.analyze_sweep 的结果
        self.pv_error = ""
        self._i18n = []         # [(widget, key, option)]，切换语言时刷新

        self._build_ui()
        self.refresh_ports()
        self.retranslate()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(50, self._drain_ui)

    # ================= 多语言 =================
    def tr(self, key, **kw):
        text = i18n.TEXT[key][0 if self.lang == "zh" else 1]
        return text.format(**kw) if kw else text

    def _t(self, widget, key, option="text"):
        """登记一个需要翻译的控件属性。"""
        self._i18n.append((widget, key, option))
        return widget

    def set_language(self, lang):
        if lang == self.lang:
            return
        self.lang = lang
        sd.lang = lang
        self.settings["lang"] = lang
        self.retranslate()
        self.refresh_ports()

    def retranslate(self):
        self.title(f"{self.tr('app_name')}  v{APP_VERSION}")
        for widget, key, option in self._i18n:
            widget.configure(**{option: self.tr(key)})
        self.nb.tab(0, text=self.tr("tab_sweep"))
        self.nb.tab(1, text=self.tr("tab_monitor"))
        self.nb.tab(2, text=self.tr("tab_pv"))
        idx = max(0, self.range_box.current())
        self.range_box.configure(values=[sd.range_name(r) for r in sd.RANGES])
        self.range_box.current(idx)
        self.conn_btn.configure(text=self.tr("disconnect") if self.dev else self.tr("connect"))
        if not self.dev:
            self.idn_var.set(self.tr("not_connected"))
        self._set_output_ui(self.out_on)
        if self.compl_var.get():
            self.compl_var.set(self.tr("compliance"))
        self.sw_ax.set_xlabel(self.tr("x_volt"))
        self.sw_ax.set_ylabel(self.tr("y_abs") if self.sw_log.get() else self.tr("y_cur"))
        self.mo_ax.set_xlabel(self.tr("x_time"))
        self.mo_ax.set_ylabel(self.tr("y_cur"))
        self.sw_canvas.draw_idle()
        self.mo_canvas.draw_idle()
        self._update_mo_stat()
        self._render_pv()

    # ================= 界面 =================
    def _build_ui(self):
        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Big.TLabel", font=("Consolas", 20, "bold"))
        style.configure("On.TLabel", foreground="#1a7f37", font=("Microsoft YaHei", 10, "bold"))
        style.configure("Off.TLabel", foreground="#888888", font=("Microsoft YaHei", 10, "bold"))

        s = self.settings
        root = ttk.Frame(self, padding=8)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        left = ttk.Frame(root, width=320)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 8))

        # --- 语言 ---
        f = ttk.Frame(left)
        f.pack(fill="x", pady=(0, 6))
        ttk.Label(f, text=i18n.TEXT["language"][0]).pack(side="left")
        self.lang_var = tk.StringVar(value=i18n.LANGS[self.lang])
        lang_box = ttk.Combobox(f, textvariable=self.lang_var, state="readonly", width=10,
                                values=list(i18n.LANGS.values()))
        lang_box.pack(side="right")
        lang_box.bind("<<ComboboxSelected>>", lambda e: self.set_language(
            next(k for k, v in i18n.LANGS.items() if v == self.lang_var.get())))

        # --- 连接 ---
        f = self._t(ttk.LabelFrame(left, padding=8), "conn")
        f.pack(fill="x", pady=(0, 8))
        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(f, textvariable=self.port_var, state="readonly", width=28)
        self.port_box.grid(row=0, column=0, columnspan=2, sticky="ew")
        self._t(ttk.Button(f, width=8, command=self.refresh_ports), "refresh").grid(
            row=0, column=2, padx=(4, 0))
        self.conn_btn = ttk.Button(f, command=self.toggle_connect)
        self.conn_btn.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.idn_var = tk.StringVar()
        ttk.Label(f, textvariable=self.idn_var, wraplength=280, foreground="#555").grid(
            row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        f.columnconfigure(0, weight=1)

        # --- 输出 ---
        f = self._t(ttk.LabelFrame(left, padding=8), "source")
        f.pack(fill="x", pady=(0, 8))
        self.v_var = tk.DoubleVar(value=s.get("v", 0.0))
        self.ilim_var = tk.DoubleVar(value=s.get("ilim", 20.0))
        self.osr_var = tk.IntVar(value=s.get("osr", sd.OSR_DEFAULT))
        self.range_box = ttk.Combobox(f, state="readonly", width=16,
                                      values=[sd.range_name(r) for r in sd.RANGES])
        self.range_box.current(0)
        rows = [
            ("voltage_v", ttk.Spinbox(f, textvariable=self.v_var, from_=sd.V_MIN, to=sd.V_MAX,
                                      increment=0.1, width=12)),
            ("ilim_ma", ttk.Spinbox(f, textvariable=self.ilim_var, from_=sd.ILIM_MIN_MA,
                                    to=sd.ILIM_MAX_MA, increment=1, width=12)),
            ("osr", ttk.Spinbox(f, textvariable=self.osr_var, from_=sd.OSR_MIN,
                                to=sd.OSR_MAX, increment=1, width=12)),
            ("range", self.range_box),
        ]
        for r, (key, w) in enumerate(rows):
            self._t(ttk.Label(f), key).grid(row=r, column=0, sticky="w", pady=2, padx=(0, 6))
            w.grid(row=r, column=1, sticky="ew", pady=2)
        f.columnconfigure(1, weight=1)
        self.apply_btn = self._t(ttk.Button(f, command=self.apply_settings), "apply")
        self.apply_btn.grid(row=4, column=0, sticky="ew", pady=(6, 0), padx=(0, 4))
        self.out_btn = ttk.Button(f, command=self.toggle_output)
        self.out_btn.grid(row=4, column=1, sticky="ew", pady=(6, 0))
        self.out_state = ttk.Label(f)
        self.out_state.grid(row=5, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.meas_btn = self._t(ttk.Button(f, command=self.single_measure), "measure")
        self.meas_btn.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        # --- 读数 ---
        f = self._t(ttk.LabelFrame(left, padding=8), "readings")
        f.pack(fill="x", pady=(0, 8))
        self.read_v = tk.StringVar(value="--- V")
        self.read_i = tk.StringVar(value="--- A")
        ttk.Label(f, textvariable=self.read_v, style="Big.TLabel").pack(anchor="e")
        ttk.Label(f, textvariable=self.read_i, style="Big.TLabel").pack(anchor="e")
        self.compl_var = tk.StringVar(value="")
        ttk.Label(f, textvariable=self.compl_var, foreground="#c62828").pack(anchor="e")

        self._t(ttk.Label(left, foreground="#777", justify="left"), "hint").pack(anchor="w")

        # --- 右侧标签页 ---
        right = ttk.Frame(root)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        self.nb = ttk.Notebook(right)
        self.nb.grid(row=0, column=0, sticky="nsew")
        self.nb.add(self._build_sweep_tab(self.nb))
        self.nb.add(self._build_monitor_tab(self.nb))
        self.nb.add(self._build_pv_tab(self.nb))

        # --- 日志 ---
        self.log_box = ScrolledText(right, height=7, font=("Consolas", 9), state="disabled")
        self.log_box.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        self._set_connected(False)

    def _param_row(self, parent, specs):
        bar = ttk.Frame(parent)
        bar.pack(fill="x", pady=(0, 4))
        for key, var, width in specs:
            self._t(ttk.Label(bar), key).pack(side="left", padx=(0, 2))
            ttk.Entry(bar, textvariable=var, width=width).pack(side="left", padx=(0, 10))
        return bar

    def _make_plot(self, parent):
        fig = Figure(figsize=(6, 4), dpi=100, layout="tight")
        ax = fig.add_subplot(111)
        ax.grid(True, alpha=0.3)
        canvas = FigureCanvasTkAgg(fig, master=parent)
        tb_frame = ttk.Frame(parent)
        tb_frame.pack(fill="x")
        NavigationToolbar2Tk(canvas, tb_frame).update()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        return fig, ax, canvas

    def _build_sweep_tab(self, nb):
        s = self.settings
        tab = ttk.Frame(nb, padding=8)
        self.sw_start = tk.DoubleVar(value=s.get("sw_start", -1.0))
        self.sw_stop = tk.DoubleVar(value=s.get("sw_stop", 1.0))
        self.sw_points = tk.IntVar(value=s.get("sw_points", 51))
        self.sw_delay = tk.IntVar(value=s.get("sw_delay", 10))
        self.sw_dual = tk.BooleanVar(value=s.get("sw_dual", False))
        self.sw_off = tk.BooleanVar(value=s.get("sw_off", True))
        self.sw_log = tk.BooleanVar(value=False)
        self._param_row(tab, [("start_v", self.sw_start, 8), ("stop_v", self.sw_stop, 8),
                              ("points", self.sw_points, 6), ("settle_ms", self.sw_delay, 6)])
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        self._t(ttk.Checkbutton(bar, variable=self.sw_dual), "dual").pack(side="left")
        self._t(ttk.Checkbutton(bar, variable=self.sw_off), "off_after").pack(side="left", padx=10)
        self._t(ttk.Checkbutton(bar, variable=self.sw_log, command=self._redraw_sweeps),
                "logy").pack(side="left")
        self.sw_btn = self._t(ttk.Button(bar, command=self.start_sweep), "start_sweep")
        self.sw_btn.pack(side="left", padx=(20, 4))
        self._t(ttk.Button(bar, command=self.stop_evt.set), "stop").pack(side="left", padx=4)
        self._t(ttk.Button(bar, command=self.export_sweeps), "export").pack(side="left", padx=4)
        self._t(ttk.Button(bar, command=self.clear_sweeps), "clear").pack(side="left", padx=4)
        self.sw_prog = ttk.Progressbar(bar, length=140)
        self.sw_prog.pack(side="right")
        self.sw_fig, self.sw_ax, self.sw_canvas = self._make_plot(tab)
        return tab

    def _build_monitor_tab(self, nb):
        s = self.settings
        tab = ttk.Frame(nb, padding=8)
        self.mo_v = tk.DoubleVar(value=s.get("mo_v", 0.5))
        self.mo_interval = tk.DoubleVar(value=s.get("mo_interval", 1.0))
        self.mo_duration = tk.DoubleVar(value=s.get("mo_duration", 0))
        self._param_row(tab, [("bias_v", self.mo_v, 8), ("interval", self.mo_interval, 6),
                              ("duration", self.mo_duration, 8)])
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        self.mo_btn = self._t(ttk.Button(bar, command=self.start_monitor), "start_mon")
        self.mo_btn.pack(side="left", padx=(0, 4))
        self._t(ttk.Button(bar, command=self.stop_evt.set), "stop").pack(side="left", padx=4)
        self._t(ttk.Button(bar, command=self.export_monitor), "export").pack(side="left", padx=4)
        self.mo_stat = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.mo_stat, foreground="#555").pack(side="right")
        self.mo_fig, self.mo_ax, self.mo_canvas = self._make_plot(tab)
        (self.mo_line,) = self.mo_ax.plot([], [], ".-", lw=1, ms=3)
        return tab

    def _build_pv_tab(self, nb):
        s = self.settings
        tab = ttk.Frame(nb, padding=8)
        self.pv_pin = tk.DoubleVar(value=s.get("pv_pin", 100.0))
        self.pv_area = tk.DoubleVar(value=s.get("pv_area", 0.1))
        self.pv_start = tk.DoubleVar(value=s.get("pv_start", -0.1))
        self.pv_stop = tk.DoubleVar(value=s.get("pv_stop", 1.2))
        self.pv_points = tk.IntVar(value=s.get("pv_points", 61))
        self.pv_delay = tk.IntVar(value=s.get("pv_delay", 20))
        self.pv_dual = tk.BooleanVar(value=s.get("pv_dual", True))
        self.pv_off = tk.BooleanVar(value=s.get("pv_off", True))
        self.pv_zoom = tk.BooleanVar(value=s.get("pv_zoom", False))
        self.pv_use_i = tk.BooleanVar(value=s.get("pv_use_i", False))
        self._param_row(tab, [("pv_pin", self.pv_pin, 7), ("pv_area", self.pv_area, 7),
                              ("start_v", self.pv_start, 7), ("stop_v", self.pv_stop, 7),
                              ("points", self.pv_points, 5), ("settle_ms", self.pv_delay, 5)])
        bar = ttk.Frame(tab)
        bar.pack(fill="x", pady=(0, 6))
        self._t(ttk.Checkbutton(bar, variable=self.pv_dual), "pv_dual").pack(side="left")
        self._t(ttk.Checkbutton(bar, variable=self.pv_off), "off_after").pack(side="left", padx=10)
        self.pv_btn = self._t(ttk.Button(bar, command=self.start_pv), "pv_start")
        self.pv_btn.pack(side="left", padx=(10, 4))
        self._t(ttk.Button(bar, command=self.stop_evt.set), "stop").pack(side="left", padx=4)
        self._t(ttk.Button(bar, command=self.pv_recalc), "pv_recalc").pack(side="left", padx=4)
        self._t(ttk.Button(bar, command=self.pv_from_sweep), "pv_from_iv").pack(side="left", padx=4)
        self._t(ttk.Button(bar, command=self.export_pv), "export").pack(side="left", padx=4)
        self.pv_prog = ttk.Progressbar(bar, length=110)
        self.pv_prog.pack(side="right")
        view = ttk.Frame(tab)
        view.pack(fill="x", pady=(0, 4))
        self._t(ttk.Checkbutton(view, variable=self.pv_zoom, command=self._render_pv), "pv_zoom").pack(side="left")
        self._t(ttk.Checkbutton(view, variable=self.pv_use_i, command=self._render_pv), "pv_use_i").pack(
            side="left", padx=10)

        self._t(ttk.Label(tab, foreground="#777", wraplength=900, justify="left"), "pv_note").pack(
            side="bottom", fill="x", pady=(6, 0))
        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True)
        side = ttk.Frame(body)
        side.pack(side="right", fill="y", padx=(8, 0))
        self.pv_tree = ttk.Treeview(side, columns=("p", "a", "b"), show="headings", height=16)
        for c, w in (("p", 150), ("a", 95), ("b", 95)):
            self.pv_tree.column(c, width=w, anchor="w" if c == "p" else "e", stretch=False)
        self.pv_tree.pack(fill="y", expand=True)
        self.pv_err_var = tk.StringVar()
        ttk.Label(side, textvariable=self.pv_err_var, foreground="#c62828", wraplength=330).pack(fill="x")
        plot = ttk.Frame(body)
        plot.pack(side="left", fill="both", expand=True)
        self.pv_fig, self.pv_ax, self.pv_canvas = self._make_plot(plot)
        return tab

    # ================= 辅助 =================
    def log(self, key, level="info", **kw):
        """日志在产生时按当前语言格式化；key 也可以是已格式化好的文本。"""
        msg = self.tr(key, **kw) if key in i18n.TEXT else key
        self.ui.put(("log", msg, level))

    def _append_log(self, msg, level):
        stamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"warn": "⚠ ", "error": "✖ "}.get(level, "")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"[{stamp}] {prefix}{msg}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _drain_ui(self):
        try:
            while True:
                item = self.ui.get_nowait()
                if item[0] == "log":
                    self._append_log(item[1], item[2])
                elif item[0] == "call":
                    item[1]()
        except queue.Empty:
            pass
        self.after(50, self._drain_ui)

    def call_ui(self, fn):
        self.ui.put(("call", fn))

    def warn(self, text):
        messagebox.showwarning(self.tr("app_name"), text)

    def info(self, text):
        messagebox.showinfo(self.tr("app_name"), text)

    def _get(self, var, key):
        try:
            return var.get()
        except tk.TclError:
            raise ValueError(self.tr("invalid_num", name=self.tr(key)))

    def _set_connected(self, ok):
        self.conn_btn.configure(text=self.tr("disconnect") if ok else self.tr("connect"))
        self.port_box.configure(state="disabled" if ok else "readonly")
        self._set_busy(self.busy)
        if not ok:
            self._set_output_ui(False)

    def _set_busy(self, busy):
        self.busy = busy
        enabled = self.dev is not None and not busy
        for b in (self.apply_btn, self.out_btn, self.meas_btn, self.sw_btn, self.mo_btn, self.pv_btn):
            b.configure(state="normal" if enabled else "disabled")
        self.conn_btn.configure(state="disabled" if busy else "normal")

    def _set_output_ui(self, on):
        self.out_on = on
        self.out_btn.configure(text=self.tr("out_off_btn") if on else self.tr("out_on_btn"))
        self.out_state.configure(text=self.tr("state_on") if on else self.tr("state_off"),
                                 style="On.TLabel" if on else "Off.TLabel")

    def _show_reading(self, v, i):
        self.read_v.set(f"{v:+.4f} V")
        self.read_i.set(fmt_current(i))
        ilim = self.dev.ilim_ma / 1000 if self.dev else None
        self.compl_var.set(self.tr("compliance") if ilim and abs(i) >= 0.95 * ilim else "")

    def run_job(self, fn, busy=False):
        """在工作线程执行设备操作，统一处理异常。"""
        if busy:
            self._set_busy(True)
            self.stop_evt.clear()

        def job():
            try:
                fn()
            except sd.SMUError as e:
                self.log(str(e), "error")
            except Exception as e:  # 防止工作线程因意外异常退出
                self.log("internal_err", "error", e=repr(e))
            finally:
                if busy:
                    self.call_ui(lambda: self._set_busy(False))

        self.worker.submit(job)

    # ================= 连接 =================
    def refresh_ports(self):
        current = self._port_map.get(self.port_var.get()) if hasattr(self, "_port_map") else None
        ports = sd.available_ports()
        self._port_map = {f"{dev} — {desc}" if desc != dev else dev: dev for dev, desc in ports}
        names = list(self._port_map)
        self.port_box.configure(values=names)
        want = current or self.settings.get("port")
        pick = next((n for n, d in self._port_map.items() if d == want), None)
        if pick is None:
            # 优先选第一个真实串口（前两项是模拟器）
            pick = names[2] if len(names) > 2 else names[0]
        self.port_var.set(pick)

    def toggle_connect(self):
        if self.dev:
            dev, self.dev = self.dev, None
            self._set_connected(False)
            self.idn_var.set(self.tr("not_connected"))
            self.run_job(lambda: (dev.close(), self.log("disconnected", port=dev.port)))
            return
        port = self._port_map.get(self.port_var.get())
        if not port:
            return
        self.conn_btn.configure(state="disabled")
        osr = self.osr_var.get()

        def job():
            dev = None
            try:
                dev = sd.OhmSMU(port)
                idn = dev.idn()
                dev.set_osr(osr)
            except sd.SMUError as e:
                if dev:
                    dev.ser.close()
                self.log("connect_failed", "error", e=e)
                self.call_ui(lambda: self.conn_btn.configure(state="normal"))
                return

            def done():
                self.dev = dev
                self.idn_var.set(f"{port}\n{idn}")
                self._set_connected(True)
                self.settings["port"] = port
            self.call_ui(done)
            self.log("connected", port=port, idn=idn)

        self.worker.submit(job)

    # ================= 输出控制 =================
    def _read_output_params(self):
        v = self._get(self.v_var, "voltage_v")
        ilim = self._get(self.ilim_var, "ilim_ma")
        osr = self._get(self.osr_var, "osr")
        rng = max(0, self.range_box.current())
        return v, ilim, osr, rng

    def apply_settings(self):
        try:
            v, ilim, osr, rng = self._read_output_params()
        except ValueError as e:
            self.warn(str(e))
            return
        dev = self.dev

        def job():
            ilim2 = dev.set_current_limit(ilim)
            osr2 = dev.set_osr(osr)
            dev.set_range(rng)
            msg = self.tr("summary", ilim=ilim2, osr=osr2, rng=sd.range_name(rng))
            if ilim2 != ilim or osr2 != osr:
                self.log("clamped_params", "warn", msg=msg)
            if dev.output_on:
                v2 = dev.set_voltage(v)
                msg = self.tr("v_prefix", v=v2) + msg
                if v2 != v:
                    self.log("v_clamped", "warn", v=v2)
            self.log("applied", msg=msg)
        self.run_job(job)

    def toggle_output(self):
        dev = self.dev
        on = not dev.output_on
        try:
            v, ilim, _, _ = self._read_output_params()
        except ValueError as e:
            self.warn(str(e))
            return

        def job():
            if on:
                dev.ilim_ma = sd.clamp(ilim, sd.ILIM_MIN_MA, sd.ILIM_MAX_MA)
                dev.set_output(True)
                v2 = dev.set_voltage(v)
                self.log("out_on_log", v=v2, ilim=dev.ilim_ma)
            else:
                dev.set_output(False)
                self.log("out_off_log")
            self.call_ui(lambda: self._set_output_ui(on))
        self.run_job(job)

    def single_measure(self):
        dev = self.dev
        try:
            v = self._get(self.v_var, "voltage_v")
        except ValueError as e:
            self.warn(str(e))
            return
        if not dev.output_on:
            self.log("open_circuit", "warn")

        def job():
            vm, im = dev.measure(v)
            self.call_ui(lambda: self._show_reading(vm, im))
            self.log("meas_log", v=v, vm=vm, im=im)
        self.run_job(job)

    def _ensure_output(self, dev, ilim):
        dev.ilim_ma = sd.clamp(ilim, sd.ILIM_MIN_MA, sd.ILIM_MAX_MA)
        if not dev.output_on:
            dev.set_output(True)
            self.call_ui(lambda: self._set_output_ui(True))
        else:
            dev.set_current_limit(ilim)

    # ================= I-V 扫描 =================
    def start_sweep(self):
        try:
            v0 = self._get(self.sw_start, "start_v")
            v1 = self._get(self.sw_stop, "stop_v")
            n = self._get(self.sw_points, "points")
            delay = self._get(self.sw_delay, "settle_ms") / 1000
            _, ilim, osr, rng = self._read_output_params()
        except ValueError as e:
            self.warn(str(e))
            return
        if not (2 <= n <= 5000):
            self.warn(self.tr("points_range"))
            return
        if not all(sd.V_MIN <= x <= sd.V_MAX for x in (v0, v1)):
            self.warn(self.tr("sweep_range", lo=sd.V_MIN, hi=sd.V_MAX))
            return
        volts = list(np.linspace(v0, v1, n))
        if self.sw_dual.get():
            volts += volts[-2::-1]
        turn_off = self.sw_off.get()
        dev = self.dev

        label = f"#{len(self.sweeps) + 1}  {datetime.now():%H:%M:%S}"
        sweep = {"label": label, "rows": [], "line": None, "meta": [
            datetime.now().isoformat(timespec="seconds"), ilim, osr, rng, delay * 1000]}
        self.sweeps.append(sweep)
        (sweep["line"],) = self.sw_ax.plot([], [], ".-", lw=1, ms=3, label=label)
        self.sw_ax.legend(loc="best", fontsize=8)
        self.sw_prog.configure(maximum=len(volts), value=0)

        def job():
            dev.set_osr(osr)
            dev.set_range(rng)
            self._ensure_output(dev, ilim)
            self.log("sweep_start", label=label, v0=v0, v1=v1, n=len(volts))
            t0 = time.monotonic()
            for k, v in enumerate(volts):
                if self.stop_evt.is_set():
                    self.log("sweep_stopped", "warn")
                    break
                if delay > 0:
                    dev.set_voltage(v)
                    self.stop_evt.wait(delay)
                vm, im = dev.measure(v)
                sweep["rows"].append((time.monotonic() - t0, v, vm, im))
                self.call_ui(lambda k=k, vm=vm, im=im: self._on_sweep_point(sweep, k, vm, im))
            else:
                self.log("sweep_done", label=label, t=time.monotonic() - t0)
            if turn_off:
                dev.set_output(False)
                self.call_ui(lambda: self._set_output_ui(False))
        self.run_job(job, busy=True)

    def _on_sweep_point(self, sweep, k, vm, im):
        self.sw_prog.configure(value=k + 1)
        self._show_reading(vm, im)
        self._update_sweep_line(sweep)
        # 每 5 点或最后一点重绘，避免高点数时界面卡顿
        if k % 5 == 0 or k + 1 >= self.sw_prog["maximum"]:
            self._rescale(self.sw_ax, self.sw_canvas)

    def _update_sweep_line(self, sweep):
        rows = sweep["rows"]
        x = [r[2] for r in rows]
        y = [abs(r[3]) if self.sw_log.get() else r[3] for r in rows]
        sweep["line"].set_data(x, y)

    def _redraw_sweeps(self):
        self.sw_ax.set_yscale("log" if self.sw_log.get() else "linear")
        self.sw_ax.set_ylabel(self.tr("y_abs") if self.sw_log.get() else self.tr("y_cur"))
        for sw in self.sweeps:
            self._update_sweep_line(sw)
        self._rescale(self.sw_ax, self.sw_canvas)

    @staticmethod
    def _rescale(ax, canvas):
        ax.relim()
        ax.autoscale_view()
        canvas.draw_idle()

    def clear_sweeps(self):
        if self.busy:
            return
        self.sweeps.clear()
        self.sw_ax.cla()
        self.sw_ax.set_xlabel(self.tr("x_volt"))
        self.sw_ax.grid(True, alpha=0.3)
        self._redraw_sweeps()

    def _csv_path(self, prefix):
        return filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile=f"{prefix}_{datetime.now():%Y%m%d_%H%M%S}.csv")

    def export_sweeps(self):
        data = [sw for sw in self.sweeps if sw["rows"]]
        if not data:
            self.info(self.tr("no_sweep"))
            return
        path = self._csv_path("IV")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([self.tr("csv_device"), self.idn_var.get().replace("\n", " ")])
            w.writerow(i18n.TEXT["csv_sweep_head"][0 if self.lang == "zh" else 1])
            for sw in data:
                start, ilim, osr, rng, settle = sw["meta"]
                meta = [start, ilim, osr, sd.range_name(rng), settle]
                for t, vs, vm, im in sw["rows"]:
                    w.writerow([sw["label"], *meta,
                                f"{t:.3f}", f"{vs:.4f}", f"{vm:.4f}", f"{im:.6e}"])
        self.log("exported_sweeps", n=len(data), path=path)

    # ================= 定时监测 =================
    def start_monitor(self):
        try:
            v = self._get(self.mo_v, "bias_v")
            interval = max(0.1, self._get(self.mo_interval, "interval"))
            duration = self._get(self.mo_duration, "duration")
            _, ilim, osr, rng = self._read_output_params()
        except ValueError as e:
            self.warn(str(e))
            return
        if not sd.V_MIN <= v <= sd.V_MAX:
            self.warn(self.tr("bias_range", lo=sd.V_MIN, hi=sd.V_MAX))
            return
        self.monitor_rows = []
        self.mo_line.set_data([], [])
        dev = self.dev

        def job():
            dev.set_osr(osr)
            dev.set_range(rng)
            self._ensure_output(dev, ilim)
            dev.set_voltage(v)
            dur = self.tr("dur_n", d=duration) if duration > 0 else self.tr("dur_inf")
            self.log("mon_start", v=v, iv=interval, dur=dur)
            t0 = time.monotonic()
            next_t = t0
            while not self.stop_evt.is_set():
                t = time.monotonic() - t0
                if duration > 0 and t > duration:
                    break
                vm, im = dev.measure(v)
                self.monitor_rows.append((t, v, vm, im))
                self.call_ui(lambda vm=vm, im=im: self._on_monitor_point(vm, im))
                next_t += interval
                self.stop_evt.wait(max(0.0, next_t - time.monotonic()))
            self.log("mon_done", n=len(self.monitor_rows))
        self.run_job(job, busy=True)

    def _on_monitor_point(self, vm, im):
        rows = self.monitor_rows
        self._show_reading(vm, im)
        self.mo_line.set_data([r[0] for r in rows], [r[3] for r in rows])
        self._update_mo_stat()
        self._rescale(self.mo_ax, self.mo_canvas)

    def _update_mo_stat(self):
        if not self.monitor_rows:
            self.mo_stat.set("")
            return
        cur = np.array([r[3] for r in self.monitor_rows])
        self.mo_stat.set(self.tr("mon_stat", n=len(cur), mean=fmt_current(cur.mean()), std=cur.std()))

    def export_monitor(self):
        if not self.monitor_rows:
            self.info(self.tr("no_mon"))
            return
        path = self._csv_path("Monitor")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([self.tr("csv_device"), self.idn_var.get().replace("\n", " ")])
            w.writerow(i18n.TEXT["csv_mon_head"][0 if self.lang == "zh" else 1])
            for t, vs, vm, im in self.monitor_rows:
                w.writerow([f"{t:.3f}", f"{vs:.4f}", f"{vm:.4f}", f"{im:.6e}"])
        self.log("exported_mon", path=path)

    # ================= 光伏测试 =================
    def start_pv(self):
        try:
            v0 = self._get(self.pv_start, "start_v")
            v1 = self._get(self.pv_stop, "stop_v")
            n = self._get(self.pv_points, "points")
            delay = self._get(self.pv_delay, "settle_ms") / 1000
            self._pv_conditions()
            _, ilim, osr, rng = self._read_output_params()
        except ValueError as e:
            self.warn(str(e))
            return
        if not (5 <= n <= 5000):
            self.warn(self.tr("points_range"))
            return
        if not all(sd.V_MIN <= x <= sd.V_MAX for x in (v0, v1)):
            self.warn(self.tr("sweep_range", lo=sd.V_MIN, hi=sd.V_MAX))
            return
        volts = list(np.linspace(v0, v1, n))
        if self.pv_dual.get():
            volts += volts[-2::-1]
        turn_off = self.pv_off.get()
        dev = self.dev
        self.pv_rows = []
        self.pv_ilim = ilim
        self.pv_results = []
        self.pv_error = ""
        self._render_pv()
        self.pv_prog.configure(maximum=len(volts), value=0)

        def job():
            dev.set_osr(osr)
            dev.set_range(rng)
            self._ensure_output(dev, ilim)
            self.log("sweep_start", label=self.tr("tab_pv").strip(), v0=v0, v1=v1, n=len(volts))
            t0 = time.monotonic()
            for k, v in enumerate(volts):
                if self.stop_evt.is_set():
                    self.log("sweep_stopped", "warn")
                    break
                if delay > 0:
                    dev.set_voltage(v)
                    self.stop_evt.wait(delay)
                vm, im = dev.measure(v)
                self.pv_rows.append((v, vm, im))
                self.call_ui(lambda k=k, vm=vm, im=im: self._on_pv_point(k, vm, im))
            else:
                self.log("sweep_done", label=self.tr("tab_pv").strip(), t=time.monotonic() - t0)
            if turn_off:
                dev.set_output(False)
                self.call_ui(lambda: self._set_output_ui(False))
            self.call_ui(self._pv_compute)
        self.run_job(job, busy=True)

    def _pv_conditions(self):
        return self._get(self.pv_pin, "pv_pin"), self._get(self.pv_area, "pv_area")

    def _on_pv_point(self, k, vm, im):
        self.pv_prog.configure(value=k + 1)
        self._show_reading(vm, im)
        if k % 5 == 0 or k + 1 >= self.pv_prog["maximum"]:
            self._render_pv()

    def pv_from_sweep(self):
        if self.busy:
            return
        data = [sw for sw in self.sweeps if sw["rows"]]
        if not data:
            self.info(self.tr("no_sweep"))
            return
        sw = data[-1]
        self.pv_rows = [(vs, vm, im) for _, vs, vm, im in sw["rows"]]
        self.pv_ilim = sw["meta"][1]
        self._pv_compute()

    def pv_recalc(self):
        if self.busy:  # 测量进行中，数据还在增加
            return
        self._pv_compute()

    def _pv_compute(self):
        if not self.pv_rows:
            self.info(self.tr("pv_no_data"))
            return
        try:
            pin, area = self._pv_conditions()
        except ValueError as e:
            self.warn(str(e))
            return
        vs, vm, im = (np.array(c, float) for c in zip(*self.pv_rows))
        self.pv_results, self.pv_error = [], ""
        try:
            self.pv_results = pva.analyze_sweep(vs, vm, im, area, pin)
        except pva.PVError as e:
            self.pv_error = e.args[0]
            self.log("pv_failed", "warn", e=self.tr(self.pv_error))
        if self.pv_ilim and np.max(np.abs(im)) >= 0.95 * self.pv_ilim / 1000:
            self.log("pv_compl", "warn", ilim=self.pv_ilim)
        for r in self.pv_results:
            name = self.tr("pv_fw" if r["forward"] else "pv_rv")
            self.log("pv_done", txt=f"{name}  PCE {r['pce'] * 100:.2f} %  FF {r['ff'] * 100:.1f} %  "
                                    f"Voc {r['voc']:.4f} V  Jsc {r['jsc']:.3f} mA/cm²")
        self._render_pv()

    @staticmethod
    def _fmt_ohm(x):
        return "∞" if not np.isfinite(x) else f"{x:.4g}"

    def _pv_table(self):
        """[(参数, [每段的值])]，导出 CSV 与界面表格共用。"""
        f = self._fmt_ohm
        spec = [
            ("Voc (V)", lambda r: f"{r['voc']:.4f}"),
            ("Jsc (mA/cm²)", lambda r: f"{r['jsc']:.3f}"),
            ("FF (%)", lambda r: f"{r['ff'] * 100:.2f}"),
            ("PCE (%)", lambda r: f"{r['pce'] * 100:.2f}"),
            ("Pmax (mW)", lambda r: f"{r['pmax'] * 1e3:.4f}"),
            ("Vmp (V)", lambda r: f"{r['vmp']:.4f}"),
            ("Jmp (mA/cm²)", lambda r: f"{r['jmp']:.3f}"),
            ("Isc (mA)", lambda r: f"{r['isc'] * 1e3:.4f}"),
            ("Rs (Ω·cm²)", lambda r: f(r["rs_area"])),
            ("Rsh (Ω·cm²)", lambda r: f(r["rsh_area"])),
            ("Rs (Ω)", lambda r: f(r["rs"])),
            ("Rsh (Ω)", lambda r: f(r["rsh"])),
            (self.tr("pv_polarity"), lambda r: self.tr("pv_pol_rev" if r["reversed"] else "pv_pol_ok")),
        ]
        rows = [(name, [fn(r) for r in self.pv_results]) for name, fn in spec]
        hi = pva.hysteresis_index(self.pv_results)
        if hi is not None:
            rows.append((self.tr("pv_hi"), [f"{hi * 100:.1f} %"]))
        return rows

    def _render_pv(self):
        if not hasattr(self, "pv_tree"):
            return
        res = self.pv_results
        heads = [self.tr("pv_fw" if r["forward"] else "pv_rv") for r in res[:2]]
        self.pv_tree.heading("p", text=self.tr("pv_param"))
        self.pv_tree.heading("a", text=heads[0] if heads else "")
        self.pv_tree.heading("b", text=heads[1] if len(heads) > 1 else "")
        self.pv_tree.delete(*self.pv_tree.get_children())
        if res:
            for name, vals in self._pv_table():
                self.pv_tree.insert("", "end", values=(name, *(vals[:2] + ["", ""])[:2]))
        self.pv_err_var.set(self.tr(self.pv_error) if self.pv_error else "")

        ax = self.pv_ax
        ax.cla()
        ax.grid(True, alpha=0.3)
        ax.axhline(0, color="#888", lw=0.8)
        ax.axvline(0, color="#888", lw=0.8)
        use_i = self.pv_use_i.get()
        ax.set_xlabel(self.tr("x_cell_v"))
        ax.set_ylabel(self.tr("y_i_ma" if use_i else "y_j"))
        if res:
            for r in res:
                st = PV_FW_STYLE if r["forward"] else PV_RV_STYLE
                y = r["i_ma"] if use_i else r["j"]
                ax.plot(r["x"], y, "-", lw=1, ms=4, color=st["color"], marker=st["marker"],
                        label=self.tr("pv_fw" if r["forward"] else "pv_rv"))
                ymp = r["imp"] * 1e3 if use_i else r["jmp"]
                ax.plot([r["vmp"]], [ymp], "*", ms=13, color=st["color"], mec="black", mew=0.8)
            if self.pv_zoom.get():  # 只看发电区：第四象限（按光电流为正）附近
                ysc = max(r["isc"] * 1e3 if use_i else r["jsc"] for r in res)
                vmax = max(r["voc"] for r in res)
                ax.set_ylim(-0.3 * ysc, 1.2 * ysc)
                ax.set_xlim(min(min(r["x"].min() for r in res), 0) - 0.02 * vmax, 1.15 * vmax)
            ax.plot([], [], "*", ms=11, color="#555", mec="black", label=self.tr("pv_mpp"))
            ax.legend(loc="lower left", fontsize=9)
        elif self.pv_rows:
            # 测量中或计算失败：按正极接 HI 画原始数据
            try:
                area = self._get(self.pv_area, "pv_area")
            except ValueError:
                area = 0
            if use_i or area > 0:
                vm = [r[1] for r in self.pv_rows]
                y = [-r[2] * 1e3 / (1 if use_i else area) for r in self.pv_rows]
                ax.plot(vm, y, ".-", lw=1, ms=3, color="#555")
        self.pv_canvas.draw_idle()

    def export_pv(self):
        if not self.pv_rows:
            self.info(self.tr("pv_no_data"))
            return
        try:
            pin, area = self._pv_conditions()
        except ValueError as e:
            self.warn(str(e))
            return
        path = self._csv_path("PV")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow([self.tr("csv_device"), self.idn_var.get().replace("\n", " ")])
            w.writerow([self.tr("csv_pv_cond"), f"{self.tr('pv_pin')} = {pin:g}",
                        f"{self.tr('pv_area')} = {area:g}", datetime.now().isoformat(timespec="seconds")])
            w.writerow([])
            if self.pv_results:
                w.writerow([self.tr("pv_param")] +
                           [self.tr("pv_fw" if r["forward"] else "pv_rv") for r in self.pv_results])
                for name, vals in self._pv_table():
                    w.writerow([name, *vals])
            elif self.pv_error:
                w.writerow([self.tr(self.pv_error)])
            w.writerow([])
            w.writerow(i18n.TEXT["csv_pv_head"][0 if self.lang == "zh" else 1])
            segs = [(self.tr("pv_fw" if r["forward"] else "pv_rv"), r["seg"]) for r in self.pv_results]
            if not segs:
                segs = [(self.tr("pv_scan"), (0, len(self.pv_rows)))]
            sign = 1 if self.pv_results and self.pv_results[0]["reversed"] else -1  # 光电流为正
            for name, (a, b) in segs:
                for vs, vm, im in self.pv_rows[a:b]:
                    w.writerow([name, f"{vs:.4f}", f"{vm:.4f}", f"{im:.6e}", f"{sign * im * 1e3 / area:.5f}"])
        self.log("exported_pv", path=path)

    # ================= 设置与退出 =================
    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        keys = {"v": self.v_var, "ilim": self.ilim_var, "osr": self.osr_var,
                "sw_start": self.sw_start, "sw_stop": self.sw_stop, "sw_points": self.sw_points,
                "sw_delay": self.sw_delay, "sw_dual": self.sw_dual, "sw_off": self.sw_off,
                "mo_v": self.mo_v, "mo_interval": self.mo_interval, "mo_duration": self.mo_duration,
                "pv_pin": self.pv_pin, "pv_area": self.pv_area, "pv_start": self.pv_start,
                "pv_stop": self.pv_stop, "pv_points": self.pv_points, "pv_delay": self.pv_delay,
                "pv_dual": self.pv_dual, "pv_off": self.pv_off, "pv_zoom": self.pv_zoom,
                "pv_use_i": self.pv_use_i}
        for k, var in keys.items():
            try:
                self.settings[k] = var.get()
            except tk.TclError:
                pass
        try:
            os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def on_close(self):
        if self.busy and not messagebox.askyesno(self.tr("app_name"), self.tr("quit_confirm")):
            return
        self.stop_evt.set()
        self._save_settings()
        dev, self.dev = self.dev, None
        if dev:
            done = threading.Event()
            self.worker.submit(lambda: (dev.close(), done.set()))
            done.wait(timeout=3)
        self.destroy()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    App().mainloop()


if __name__ == "__main__":
    main()
