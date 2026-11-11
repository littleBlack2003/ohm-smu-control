"""光伏 J-V 参数计算：Voc、Jsc、FF、PCE、Pmax、串联电阻 Rs、并联电阻 Rsh。

约定：
- 输入是源表读数：实测电压 V（HI 相对 LO）和实测电流 I（流入 HI 为正）。
- 电池正极接 HI 时，发电象限是 V > 0、I < 0；反接时是 V < 0、I > 0。按 0 V 处电流的符号自动判断，
  统一换算成“电池电压 x、光电流 y 为正”的形式再计算。
- Rs、Rsh 用斜率法：Rs = −dV/dI 在 Voc 处，Rsh = −dV/dI 在短路（0 V）处。
  斜率法的 Rs 含二极管在 Voc 处的动态电阻，是 Rs 的上限估计。
"""

import numpy as np


class PVError(ValueError):
    """数据不足以计算光伏参数；args[0] 是 i18n 键。"""


def split_segments(v_set):
    """按设定电压的单调段切分（往返扫描切成两段，转折点两段共用）。返回 [(start, stop)]，stop 不含。"""
    v = np.asarray(v_set, float)
    if len(v) < 3:
        return [(0, len(v))]
    d = np.sign(np.diff(v))
    segs, start = [], 0
    for k in range(1, len(d)):
        if d[k] != 0 and d[k - 1] != 0 and d[k] != d[k - 1]:
            segs.append((start, k + 1))
            start = k
    segs.append((start, len(v)))
    return segs


def _line_fit(x, y):
    """y = a·x + b，返回 (a, b)。"""
    a, b = np.polyfit(x, y, 1)
    return a, b


def analyze(v, i, area_cm2, pin_mw_cm2):
    """计算一段单调扫描的光伏参数。

    v, i：实测电压 (V)、实测电流 (A)；area_cm2：有效面积 (cm²)；pin_mw_cm2：入射光强 (mW/cm²)。
    返回 dict，电流单位 A，功率 W，电阻 Ω。
    """
    if area_cm2 <= 0 or pin_mw_cm2 <= 0:
        raise PVError("pv_err_input")
    v = np.asarray(v, float)
    i = np.asarray(i, float)
    if len(v) < 5:
        raise PVError("pv_err_points")
    order = np.argsort(v)
    v, i = v[order], i[order]
    if not v[0] <= 0 <= v[-1]:
        raise PVError("pv_err_no_zero")

    # 极性：0 V 处的电流应为光电流方向
    i0 = np.interp(0.0, v, i)
    reversed_ = i0 > 0
    if reversed_:
        x, y = -v[::-1], i[::-1]
    else:
        x, y = v, -i
    if np.interp(0.0, x, y) <= 0:
        raise PVError("pv_err_no_light")

    # 开路点：y 由正变负的位置，两侧各取 2 点做二次拟合，求根并取该处斜率
    cross = np.where((y[:-1] > 0) & (y[1:] <= 0) & (x[1:] > 0))[0]
    if len(cross) == 0:
        raise PVError("pv_err_no_voc")
    k = cross[0]
    voc = x[k] + y[k] * (x[k + 1] - x[k]) / (y[k] - y[k + 1])  # 两点插值，作为初值和退路
    a_oc = (y[k + 1] - y[k]) / (x[k + 1] - x[k])
    sel = np.arange(max(0, k - 1), min(len(x), k + 3))
    if len(sel) >= 4:
        x0 = voc
        c2, c1, c0 = np.polyfit(x[sel] - x0, y[sel], 2)  # y = c2·u² + c1·u + c0，u = x − x0
        disc = c1 * c1 - 4 * c2 * c0
        if c2 != 0 and disc >= 0:
            us = (-c1 + np.array([1.0, -1.0]) * np.sqrt(disc)) / (2 * c2)
            u = us[np.argmin(np.abs(us))]
            if x[k] <= x0 + u <= x[k + 1]:
                voc, a_oc = x0 + u, 2 * c2 * u + c1
    if a_oc >= 0:
        raise PVError("pv_err_no_voc")

    # 短路点：0 ~ 0.25·Voc 内线性拟合（至少 3 点），截距为 Isc，斜率给 Rsh
    near0 = np.where(x <= 0.25 * voc)[0]
    if len(near0) < 3:
        near0 = np.argsort(np.abs(x))[:3]
    a0, isc = _line_fit(x[near0], y[near0])
    if isc <= 0:
        raise PVError("pv_err_no_light")

    # 最大功率点：0..Voc 区间取最大 P，再用相邻 3 点抛物线细化
    m = np.where((x >= 0) & (x <= voc))[0]
    if len(m) == 0:
        raise PVError("pv_err_points")
    p = x * y
    j = m[np.argmax(p[m])]
    vmp, pmax = x[j], p[j]
    if 0 < j < len(x) - 1:
        c2, c1, c0 = np.polyfit(x[j - 1:j + 2], p[j - 1:j + 2], 2)
        if c2 < 0:
            xv = -c1 / (2 * c2)
            if x[j - 1] <= xv <= x[j + 1]:
                vmp, pmax = xv, c0 + c1 * xv + c2 * xv * xv
    imp = pmax / vmp if vmp > 0 else 0.0
    if pmax <= 0:
        raise PVError("pv_err_no_voc")

    pin_w = pin_mw_cm2 * 1e-3 * area_cm2
    rs = -1.0 / a_oc
    rsh = -1.0 / a0 if a0 < 0 else float("inf")
    return {
        "reversed": bool(reversed_),
        "voc": voc,
        "isc": isc,
        "jsc": isc * 1e3 / area_cm2,           # mA/cm²
        "vmp": vmp,
        "imp": imp,
        "jmp": imp * 1e3 / area_cm2,           # mA/cm²
        "pmax": pmax,                          # W
        "ff": pmax / (voc * isc),
        "pce": pmax / pin_w,
        "rs": rs,
        "rsh": rsh,
        "rs_area": rs * area_cm2,              # Ω·cm²
        "rsh_area": rsh * area_cm2,
        "x": x,                                # 电池电压 V（按极性换算后）
        "j": y * 1e3 / area_cm2,               # 光电流密度 mA/cm²（发电为正）
        "i_ma": y * 1e3,                       # 光电流 mA（发电为正）
        "forward": None,                       # 以下两项由 analyze_sweep 填写：是否正扫、原始数据的下标区间
        "seg": None,
    }


def analyze_sweep(v_set, v_meas, i_meas, area_cm2, pin_mw_cm2):
    """切分往返扫描并逐段计算。返回 [result]，每个 result["forward"] 表示电池电压是否递增（正扫）。"""
    out = []
    for a, b in split_segments(v_set):
        if b - a < 5:
            continue
        r = analyze(v_meas[a:b], i_meas[a:b], area_cm2, pin_mw_cm2)
        rising = v_set[b - 1] > v_set[a]
        r["forward"] = rising != r["reversed"]
        r["seg"] = (a, b)
        out.append(r)
    if not out:
        raise PVError("pv_err_points")
    return out


def hysteresis_index(results):
    """迟滞因子 HI = (PCE_反扫 − PCE_正扫) / PCE_反扫；没有成对的正、反扫时返回 None。"""
    fw = [r for r in results if r["forward"]]
    rv = [r for r in results if not r["forward"]]
    if not fw or not rv or rv[0]["pce"] <= 0:
        return None
    return (rv[0]["pce"] - fw[0]["pce"]) / rv[0]["pce"]
