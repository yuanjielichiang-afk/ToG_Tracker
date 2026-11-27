r"""
========================================================================================
ToG Tracker: Dataset-Level High-Precision Ground Truth (GTH) Reconstructor
========================================================================================
利用無硬體限制的高精度光學核 (Gain-Normalized Full-FPN + 7x7 Concentric BandPass + Adaptive-Lag
ZNCC 光學錨點 + 閉環運動學圓弧重建)，為每個測試資料集建立高精準度 Ground Truth，並直接存放
於對應資料集目錄下的 `GTH/` 子目錄，供 `run_normal_mode.py` 自動讀取與計算全域軌跡誤差均方根。

關鍵精度保障：
  1. 連續浮點亮度增益正規化 (float mean) + 時間中位數 FPN 減除。
  2. 原點閒置平台精準鎖定 + 檔尾異常壞幀濾除 + 正逆向高解析運動邊界偵測。
  3. 目標跨距鎖定 [1.7, 3.2] px (搭配搜尋半徑 r=7)，使匹配峰值避開 (0,0) 自相關鄰域，消除次像素內吸偏誤。

使用方式範例：
  py build_dataset_gth.py --all-default
  py build_dataset_gth.py "C:\Data\testPattern玻璃\20260529\Circle\Clean Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_1ips\Image\P3950_Circle_0.5inch_1rev_1ips-1.raw"
========================================================================================
"""

import os
import sys
import json
import math
import argparse
import numpy as np
import matplotlib.pyplot as plt
from scipy.interpolate import PchipInterpolator
from scipy.ndimage import gaussian_filter1d

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from step1_anchor_and_textureless_analyzer import OpniRawIO
from tracker_core import NormalModeTracker

DEFAULT_RAW_LIST = [
    # 1. R=0.1 inch, 0.005 ips
    r"C:\Data\testPattern玻璃\Circle_R0.1inch_IPS0.005_Glass\POS1-1.raw",
    r"C:\Data\testPattern玻璃\Circle_R0.1inch_IPS0.005_Glass\POS1-2.raw",
    r"C:\Data\testPattern玻璃\Circle_R0.1inch_IPS0.005_Glass\POS1-3.raw",
    # 2. R=0.2 inch, 0.02 ips
    r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw",
    # 3. R=0.5 inch, Dirty Glass, 0.1 ips
    r"C:\Data\testPattern玻璃\20260529\Circle\Dirty Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_0.1ips\Image\P3950_Circle_0.5inch_1rev_0.1ips-1.raw",
    r"C:\Data\testPattern玻璃\20260529\Circle\Dirty Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_0.1ips\Image\P3950_Circle_0.5inch_1rev_0.1ips-2.raw",
    r"C:\Data\testPattern玻璃\20260529\Circle\Dirty Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_0.1ips\Image\P3950_Circle_0.5inch_1rev_0.1ips-3.raw",
    # 4. R=0.5 inch, Clean Glass, 0.1 ips
    r"C:\Data\testPattern玻璃\20260529\Circle\Clean Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_0.1ips\Image\P3950_Circle_0.5inch_1rev_0.1ips-1.raw",
    r"C:\Data\testPattern玻璃\20260529\Circle\Clean Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_0.1ips\Image\P3950_Circle_0.5inch_1rev_0.1ips-2.raw",
    r"C:\Data\testPattern玻璃\20260529\Circle\Clean Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_0.1ips\Image\P3950_Circle_0.5inch_1rev_0.1ips-3.raw",
    # 5. R=0.5 inch, Clean Glass, 1.0 ips
    r"C:\Data\testPattern玻璃\20260529\Circle\Clean Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_1ips\Image\P3950_Circle_0.5inch_1rev_1ips-1.raw",
    r"C:\Data\testPattern玻璃\20260529\Circle\Clean Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_1ips\Image\P3950_Circle_0.5inch_1rev_1ips-2.raw",
    r"C:\Data\testPattern玻璃\20260529\Circle\Clean Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_1ips\Image\P3950_Circle_0.5inch_1rev_1ips-3.raw",
]


def resolve_dataset_gth_dir(raw_path):
    """回傳該 RAW 所屬資料集的 GTH 輸出目錄：<dataset_dir>/GTH"""
    raw_dir = os.path.dirname(os.path.abspath(raw_path))
    parent_name = os.path.basename(raw_dir).upper()
    if parent_name in ("IMAGE", "RAW", "TRACE", "CSV"):
        dataset_dir = os.path.dirname(raw_dir)
    else:
        dataset_dir = raw_dir
    return os.path.join(dataset_dir, "GTH")


def match_zncc_subpixel(fa, fb, bp_tracker, radius=7, min_std=1.35):
    """在兩張 7x7 帶通影像間進行寬窗 ZNCC 匹配與嚴格 2D 拋物面次像素峰值擬合"""
    if float(np.std(fa)) < min_std or float(np.std(fb)) < min_std:
        return None
    h, w = fa.shape
    m = radius + 1
    if h - 2 * m < 8 or w - 2 * m < 8:
        return None
    ra = fa[m:h - m, m:w - m]
    ra_z = ra - np.mean(ra)
    nra = float(np.linalg.norm(ra_z))
    if nra < 1e-3:
        return None

    # 向量化一次構造所有 (2*r+1)^2 候選位移 patch 以大幅加速寬搜尋窗計算
    roi_h, roi_w = h - 2 * m, w - 2 * m
    side = 2 * radius + 1
    patches = np.lib.stride_tricks.sliding_window_view(fb[1:h - 1, 1:w - 1], (roi_h, roi_w))
    # patches shape: (2*radius+1, 2*radius+1, roi_h, roi_w)
    if patches.shape[0] != side or patches.shape[1] != side:
        return None
    p_flat = patches.reshape(side * side, roi_h * roi_w)
    p_z = p_flat - np.mean(p_flat, axis=1, keepdims=True)
    norms = np.linalg.norm(p_z, axis=1)
    dots = p_z @ ra_z.ravel()
    denom = nra * norms
    scores = np.where(denom > 1e-3, dots / np.maximum(denom, 1e-6), -1.0)
    surf = scores.reshape(side, side).astype(np.float32)

    vy, vx = np.unravel_index(np.argmax(surf), surf.shape)
    best_sc = float(surf[vy, vx])
    if vy <= 0 or vy >= 2 * radius or vx <= 0 or vx >= 2 * radius:
        return None

    patch = surf[vy - 1:vy + 2, vx - 1:vx + 2]
    dx_sub, dy_sub = bp_tracker.parabolic_2d(patch)
    dx_tot = float(vx - radius) + dx_sub
    dy_tot = float(vy - radius) + dy_sub
    return dx_tot, dy_tot, best_sc


def detect_motion_bounds_and_closure(norm_frames, clean_frames, fps):
    """
    精確偵測有效運動區間 [t_start, t_end] 與是否完整閉環 (is_closed)：
      - 排除檔尾寫入異常壞幀 (如 0.1in POS1-2 最後 2 幀突變)
      - 利用前段原點基準找出真正啟動幀 t_start (確保 f_origin 不混淆移動幀)
      - 利用逐幀向後掃描精確找出停轉平台起點 t_end_move
      - 以純正原點微塵圖 f_origin 比對尾段確認是否閉環回到原點
    """
    nf = len(norm_frames)
    # 1. 排除檔尾異常突變壞幀 (diff > 25 counts)
    valid_end = nf - 1
    for i in range(nf - 1, max(nf - 10, 1), -1):
        d_prev = float(np.std(norm_frames[i] - norm_frames[i - 1]))
        if d_prev > 20.0:
            valid_end = i - 1
        else:
            break

    # 2. 讀取雜訊基準 (使用 clean_frames 消除整體亮度微漂後更準確)
    step_n = max(1, valid_end // 300)
    adj_diffs = [float(np.std(clean_frames[i + 1] - clean_frames[i])) for i in range(0, valid_end - 1, step_n)]
    noise_floor = float(np.clip(np.percentile(adj_diffs, 20), 0.7, 2.0)) if adj_diffs else 1.0
    motion_th = max(1.75, 1.85 * noise_floor)

    def _same_pos(f_test, ref_raw, ref_z, ref_norm, use_corr):
        if float(np.std(f_test - ref_raw)) > motion_th:
            return False
        if use_corr:
            tz = f_test - np.mean(f_test)
            c = float(np.sum(ref_z * tz) / (ref_norm * (float(np.linalg.norm(tz)) + 1e-6)))
            if c < 0.68:
                return False
        return True

    # 3. 尋找起點靜止平台結束幀 t_start (同時考量 std 差異與相關係數驟降)
    c0_ref = clean_frames[0]
    c0_z = c0_ref - np.mean(c0_ref)
    c0_n = float(np.linalg.norm(c0_z)) + 1e-6
    early_diffs = [float(np.std(clean_frames[i] - c0_ref)) for i in range(1, min(15, valid_end))]
    early_corrs = [float(np.sum(c0_z * (clean_frames[i] - np.mean(clean_frames[i]))) / (c0_n * (float(np.linalg.norm(clean_frames[i] - np.mean(clean_frames[i]))) + 1e-6))) for i in range(1, min(8, valid_end))]
    has_head_idle = bool(early_diffs and np.median(early_diffs[:6]) <= motion_th)
    head_use_corr = bool(early_corrs and np.median(early_corrs) >= 0.72)

    t_start = 0
    if has_head_idle:
        scan_lim = min(valid_end - 20, max(600, int(valid_end * 0.35)))
        for fi in range(1, scan_lim, 5):
            if not _same_pos(clean_frames[fi], c0_ref, c0_z, c0_n, head_use_corr):
                nxt = min(valid_end, fi + max(6, int(fps * 0.08)))
                nxt2 = min(valid_end, fi + max(12, int(fps * 0.15)))
                if (not _same_pos(clean_frames[nxt], c0_ref, c0_z, c0_n, head_use_corr)) and (not _same_pos(clean_frames[nxt2], c0_ref, c0_z, c0_n, head_use_corr)):
                    for sub in range(fi, max(0, fi - 15), -1):
                        if _same_pos(clean_frames[sub], c0_ref, c0_z, c0_n, head_use_corr):
                            t_start = sub
                            break
                    break
        if t_start > 5:
            head_med = np.median(clean_frames[:t_start], axis=0)
            hm_z = head_med - np.mean(head_med)
            hm_n = float(np.linalg.norm(hm_z)) + 1e-6
            for sub in range(t_start, min(scan_lim, t_start + 30)):
                if not _same_pos(clean_frames[sub], head_med, hm_z, hm_n, head_use_corr):
                    t_start = max(0, sub - 1)
                    break

    # 4. 建立無模糊純正起點微塵模板 f_origin (只平均 t_start 之前的純靜止幀！)
    if t_start >= 5:
        head_end = max(1, t_start - 1)
        head_start = max(0, head_end - 20)
        f_origin = np.mean(clean_frames[head_start:head_end + 1], axis=0)
    else:
        f_origin = clean_frames[0]

    fo_z = f_origin - np.mean(f_origin)
    no = float(np.linalg.norm(fo_z)) + 1e-6

    # 5. 逐幀逆向偵測尾段停轉靜止平台起點 t_end_move
    tail_ref = np.median(clean_frames[max(0, valid_end - 12):valid_end + 1], axis=0)
    tr_z = tail_ref - np.mean(tail_ref)
    tr_n = float(np.linalg.norm(tr_z)) + 1e-6
    tail_idle_check = [float(np.std(clean_frames[i] - tail_ref)) for i in range(max(0, valid_end - 15), valid_end + 1)]
    tail_corrs = [float(np.sum(tr_z * (clean_frames[i] - np.mean(clean_frames[i]))) / (tr_n * (float(np.linalg.norm(clean_frames[i] - np.mean(clean_frames[i]))) + 1e-6))) for i in range(max(0, valid_end - 12), valid_end + 1)]
    has_tail_plateau = bool(np.median(tail_idle_check) <= motion_th)
    tail_use_corr = bool(tail_corrs and np.median(tail_corrs) >= 0.72)

    t_end_move = valid_end
    if has_tail_plateau:
        rev_lim = max(t_start + 50, int(valid_end * 0.50))
        consec_dev = 0
        for fi in range(valid_end - 12, rev_lim, -1):
            if not _same_pos(clean_frames[fi], tail_ref, tr_z, tr_n, tail_use_corr):
                consec_dev += 1
                if consec_dev >= 6:
                    t_end_move = fi + consec_dev
                    break
            else:
                consec_dev = 0

    # 6. 閉環檢驗：在尾段 [0.65*valid_end .. valid_end] 與靜止平台計算與純正 f_origin 的相關性
    search_lo = max(t_start + 50, int(valid_end * 0.65))
    scan_step = max(1, (valid_end - search_lo) // 350)
    best_corr = -1.0
    best_idx = t_end_move
    for fi in range(search_lo, valid_end + 1, scan_step):
        fc = clean_frames[fi]
        fc_z = fc - np.mean(fc)
        corr = float(np.sum(fo_z * fc_z) / (no * (float(np.linalg.norm(fc_z)) + 1e-6)))
        if corr > best_corr:
            best_corr = corr
            best_idx = fi

    for fi in range(max(search_lo, best_idx - scan_step - 5), min(valid_end + 1, best_idx + scan_step + 6)):
        fc = clean_frames[fi]
        fc_z = fc - np.mean(fc)
        corr = float(np.sum(fo_z * fc_z) / (no * (float(np.linalg.norm(fc_z)) + 1e-6)))
        if corr > best_corr:
            best_corr = corr
            best_idx = fi

    plateau_corr = -1.0
    if has_tail_plateau and t_end_move < valid_end:
        p_end = min(valid_end + 1, t_end_move + 25)
        ft_clean = np.mean(clean_frames[t_end_move:p_end], axis=0)
        ft_z = ft_clean - np.mean(ft_clean)
        plateau_corr = float(np.sum(fo_z * ft_z) / (no * (float(np.linalg.norm(ft_z)) + 1e-6)))
        if plateau_corr > best_corr:
            best_corr = plateau_corr
            best_idx = t_end_move

    is_closed = bool(best_corr >= 0.65)
    if is_closed:
        if has_tail_plateau and plateau_corr >= 0.65 and best_idx >= t_end_move - 25:
            t_end = t_end_move
        else:
            t_end = best_idx
    else:
        t_end = t_end_move if has_tail_plateau else valid_end

    if t_end <= t_start + 30:
        t_start = 0
        t_end = valid_end

    return t_start, t_end, valid_end, is_closed, best_corr, noise_floor


def extract_adaptive_bp7_anchors(bp7_frames, t_start, t_end):
    """
    目標跨距鎖定 [1.7, 3.2] px (搭配 r=7) 抽取 7x7 帶通高信賴度光學錨點：
    避免 lag=1 時位移僅 ~1.0-1.3 px 落入 (0,0) 靜態殘影鄰域造成次像素內縮偏誤！
    """
    active_len = t_end - t_start
    bp7_helper = NormalModeTracker(fpn_model=None, use_bandpass_filter=True, bandpass_type='bandpass7')
    radius = 7

    cand_lags = [l for l in [1, 2, 3, 4, 6, 8, 12, 16, 24, 32] if l < active_len // 10]
    if not cand_lags:
        cand_lags = [1]

    test_indices = np.linspace(t_start + max(5, active_len // 12), t_end - max(35, active_len // 12), 28, dtype=int)
    lag_scores = {}
    for lag in cand_lags:
        disps = []
        for idx in test_indices:
            if idx + lag > t_end:
                continue
            m = match_zncc_subpixel(bp7_frames[idx], bp7_frames[idx + lag], bp7_helper, radius=radius, min_std=1.4)
            if m is not None:
                dx, dy, sc = m
                if sc >= 0.68:
                    disps.append(math.hypot(dx, dy))
        if len(disps) >= 3:
            med_d = float(np.median(disps))
            p90_d = float(np.percentile(disps, 90))
            # 確保 90 百分位位移仍在搜尋窗 (radius-0.8) 內，且中位位移達 >= 1.6 px 以避開 (0,0) 鄰域
            if p90_d <= radius - 0.8 and 1.65 <= med_d <= 3.3:
                score = abs(med_d - 2.35) - 0.04 * min(len(disps), 22)
            elif p90_d <= radius - 0.5 and 1.2 <= med_d <= 4.0:
                score = 2.0 + abs(med_d - 2.35)
            else:
                score = 15.0 + abs(med_d - 2.35)
            lag_scores[lag] = (score, med_d, len(disps))

    best_lag = min(lag_scores.keys(), key=lambda l: lag_scores[l][0]) if lag_scores else 1

    sample_step = max(1, (t_end - t_start - best_lag) // 950)
    raw_anchors = []
    for idx in range(t_start, t_end - best_lag + 1, sample_step):
        m = match_zncc_subpixel(bp7_frames[idx], bp7_frames[idx + best_lag], bp7_helper, radius=radius, min_std=1.35)
        if m is None:
            continue
        dx, dy, sc = m
        spd = math.hypot(dx, dy) / best_lag
        if sc >= 0.72 and spd > 0.015:
            ang = math.atan2(dy, dx)
            raw_anchors.append((idx + 0.5 * best_lag, dx / best_lag, dy / best_lag, spd, ang, sc))

    # 若選用大 lag 在極速爆衝段超出 radius (貼邊被拒)，改以 lag=1 補強該區域空缺，使速度峰不致缺失
    if best_lag >= 2 and len(raw_anchors) > 10:
        covered_t = np.array([a[0] for a in raw_anchors])
        for idx in range(t_start, t_end - 1, sample_step):
            if np.min(np.abs(covered_t - (idx + 0.5))) > max(8, 3 * sample_step):
                m = match_zncc_subpixel(bp7_frames[idx], bp7_frames[idx + 1], bp7_helper, radius=radius, min_std=1.35)
                if m is not None:
                    dx, dy, sc = m
                    spd = math.hypot(dx, dy)
                    if sc >= 0.72 and spd > 1.4:
                        raw_anchors.append((idx + 0.5, dx, dy, spd, math.atan2(dy, dx), sc))
        raw_anchors.sort(key=lambda a: a[0])

    if len(raw_anchors) < 40:
        raw_anchors = []
        for idx in range(t_start, t_end - best_lag + 1, max(1, sample_step // 2)):
            m = match_zncc_subpixel(bp7_frames[idx], bp7_frames[idx + best_lag], bp7_helper, radius=radius, min_std=1.15)
            if m is None:
                continue
            dx, dy, sc = m
            spd = math.hypot(dx, dy) / best_lag
            if sc >= 0.62 and spd > 0.012:
                raw_anchors.append((idx + 0.5 * best_lag, dx / best_lag, dy / best_lag, spd, math.atan2(dy, dx), sc))

    anchors = np.array(raw_anchors, dtype=np.float64)
    if len(anchors) < 12:
        return anchors, best_lag

    # 局部中位數速度與角度一致性離群過濾
    spds = anchors[:, 3]
    ang_u = np.unwrap(anchors[:, 4])
    keep = np.ones(len(anchors), dtype=bool)
    for k in range(len(anchors)):
        lo = max(0, k - 7)
        hi = min(len(anchors), k + 8)
        med_s = float(np.median(spds[lo:hi]))
        med_a = float(np.median(ang_u[lo:hi]))
        if abs(spds[k] - med_s) > max(0.35, 0.38 * med_s):
            keep[k] = False
        if abs(ang_u[k] - med_a) > math.radians(40.0):
            keep[k] = False

    filtered = anchors[keep]
    if len(filtered) >= 12:
        anchors = filtered
    return anchors, best_lag


def reconstruct_kinematic_gth(raw_path, anchors, best_lag, nf, t_start, t_end, valid_end, is_closed, loop_corr):
    """由高信賴度 bandpass7 光學錨點重建連續運動學圓弧 GTH"""
    tk = anchors[:, 0]
    vxk = anchors[:, 1]
    vyk = anchors[:, 2]
    spdk = anchors[:, 3]
    angk = anchors[:, 4]

    ang_u = np.unwrap(angk)
    slope, _ = np.polyfit(tk, ang_u, 1)
    sign_dir = 1.0 if slope >= 0.0 else -1.0

    t_act = np.arange(t_start, t_end + 1, dtype=np.float64)
    pchip = PchipInterpolator(tk, spdk, extrapolate=False)
    v_act = pchip(t_act)
    left_mask = t_act < tk[0]
    right_mask = t_act > tk[-1]
    v_left = float(np.median(spdk[:min(5, len(spdk))]))
    v_right = float(np.median(spdk[max(0, len(spdk) - 5):]))
    if np.any(left_mask):
        v_act[left_mask] = v_left
    if np.any(right_mask):
        v_act[right_mask] = v_right

    v_act = np.clip(v_act, 0.0, None)
    sigma = max(2.5, (t_end - t_start) / 150.0)
    v_smooth = gaussian_filter1d(v_act, sigma=sigma, mode='nearest')
    v_smooth = np.clip(v_smooth, 0.0, None)

    v_full = np.zeros(nf, dtype=np.float64)
    v_full[t_start + 1:t_end + 1] = v_smooth[1:]
    s_full = np.cumsum(v_full)

    s_end = float(s_full[t_end])
    if is_closed:
        R_px = s_end / (2.0 * np.pi)
    else:
        s_at_tk = np.interp(tk, np.arange(nf, dtype=np.float64), s_full)
        R_fit, _ = np.polyfit(sign_dir * (ang_u - ang_u[0]), s_at_tk - s_at_tk[0], 1)
        R_px = abs(float(R_fit))
        if R_px < 10.0:
            R_px = (s_at_tk[-1] - s_at_tk[0]) / max(0.2, abs(ang_u[-1] - ang_u[0]))

    phi_rel_full = sign_dir * (s_full / R_px)
    phi_rel_tk = np.interp(tk, np.arange(nf, dtype=np.float64), phi_rel_full)
    diff_ang = angk - phi_rel_tk
    phi0 = math.atan2(float(np.mean(np.sin(diff_ang))), float(np.mean(np.cos(diff_ang))))

    phi_full = phi0 + phi_rel_full
    dx_gth = np.zeros(nf, dtype=np.float64)
    dy_gth = np.zeros(nf, dtype=np.float64)
    phi_mid = np.copy(phi_full)
    phi_mid[1:] = 0.5 * (phi_full[:-1] + phi_full[1:])
    dx_gth[t_start + 1:t_end + 1] = v_full[t_start + 1:t_end + 1] * np.cos(phi_mid[t_start + 1:t_end + 1])
    dy_gth[t_start + 1:t_end + 1] = v_full[t_start + 1:t_end + 1] * np.sin(phi_mid[t_start + 1:t_end + 1])

    if is_closed:
        w_act = v_full[t_start + 1:t_end + 1]
        w_sum = float(np.sum(w_act)) + 1e-9
        res_x = float(np.sum(dx_gth))
        res_y = float(np.sum(dy_gth))
        dx_gth[t_start + 1:t_end + 1] -= res_x * (w_act / w_sum)
        dy_gth[t_start + 1:t_end + 1] -= res_y * (w_act / w_sum)

    x_gth = np.cumsum(dx_gth)
    y_gth = np.cumsum(dy_gth)

    idx_k = np.clip(np.round(tk).astype(int), 0, nf - 1)
    vel_errs = np.hypot(vxk - dx_gth[idx_k], vyk - dy_gth[idx_k])
    vel_rmse = float(np.sqrt(np.mean(vel_errs ** 2)))

    phi_at_tk = np.interp(tk, np.arange(nf, dtype=np.float64), phi_full)
    ang_errs_deg = np.rad2deg((angk - phi_at_tk + np.pi) % (2.0 * np.pi) - np.pi)
    ang_rmse_deg = float(np.sqrt(np.mean(ang_errs_deg ** 2)))

    closure_err = float(math.hypot(x_gth[-1] - x_gth[0], y_gth[-1] - y_gth[0]))
    sweep_deg = float(np.rad2deg(abs(phi_full[t_end] - phi_full[t_start])))

    return {
        'x_px': x_gth,
        'y_px': y_gth,
        'dx_px': dx_gth,
        'dy_px': dy_gth,
        'phi_rad': phi_full,
        'v_smooth': v_full,
        'radius_px': float(R_px),
        'phi0_deg': float(np.rad2deg(phi0)),
        'direction': 'CCW' if sign_dir > 0 else 'CW',
        'sweep_deg': sweep_deg,
        'closure_error_px': closure_err,
        'anchor_vel_rmse_px_per_frame': vel_rmse,
        'anchor_angle_rmse_deg': ang_rmse_deg,
        'v_med_px_per_frame': float(np.median(v_full[t_start + 1:t_end + 1])) if t_end > t_start else 0.0,
    }


def build_gth_for_raw(raw_path):
    """為單一 RAW 檔案重建 GTH 並存於該 Dataset 目錄下的 GTH/ 資料夾"""
    raw_path = os.path.abspath(raw_path)
    seq_name = os.path.splitext(os.path.basename(raw_path))[0]
    gth_dir = resolve_dataset_gth_dir(raw_path)
    os.makedirs(gth_dir, exist_ok=True)

    hdr = OpniRawIO.read_header(raw_path)
    nf, fps, w, h = hdr['num_frames'], hdr['fps'], hdr['width'], hdr['height']

    with open(raw_path, 'rb') as f:
        frames = [OpniRawIO.load_frame(f, hdr, i).astype(np.float32) for i in range(nf)]

    sample_step = max(1, nf // 300)
    sampled = frames[::sample_step]
    means = np.array([float(np.mean(fr)) for fr in sampled], dtype=np.float32)
    m_ref = float(np.mean(means))
    norm_sampled = [fr * (m_ref / max(1.0, float(m))) for fr, m in zip(sampled, means)]
    fpn_model = np.median(norm_sampled, axis=0).astype(np.float32)

    norm_frames = [fr * (m_ref / max(1.0, float(np.mean(fr)))) for fr in frames]
    clean_frames = [nfrm - fpn_model for nfrm in norm_frames]

    bp7_trk = NormalModeTracker(fpn_model=None, use_bandpass_filter=True, bandpass_type='bandpass7')
    bp7_frames = [bp7_trk.preprocess_frame(c) for c in clean_frames]

    t_start, t_end, valid_end, is_closed, loop_corr, noise_floor = detect_motion_bounds_and_closure(
        norm_frames, clean_frames, fps
    )

    anchors, best_lag = extract_adaptive_bp7_anchors(bp7_frames, t_start, t_end)
    if len(anchors) < 12:
        raise RuntimeError(f"[{seq_name}] 無法抽取足夠的高信賴度光學錨點 (僅 {len(anchors)} 個)")

    res = reconstruct_kinematic_gth(
        raw_path, anchors, best_lag, nf, t_start, t_end, valid_end, is_closed, loop_corr
    )

    csv_out = os.path.join(gth_dir, f"{seq_name}_ground_truth.csv")
    with open(csv_out, "w", encoding="utf-8-sig") as f:
        f.write(f"# SOURCE_RAW: {raw_path}\n")
        f.write(f"# GTH_METHOD: Float-Mean Gain-Normalized Full-FPN + BandPass7 (ZNCC>=0.72) Adaptive-Lag Kinematic Circle Reconstruction\n")
        f.write(f"# SPECS: {w}x{h}, {nf} frames, {fps} fps | valid_end={valid_end} | active=[{t_start}, {t_end}]\n")
        f.write(f"# METRICS: radius_px={res['radius_px']:.2f}, is_closed={is_closed}, loop_corr={loop_corr:.3f}, sweep_deg={res['sweep_deg']:.1f}, anchors={len(anchors)}, lag={best_lag}, anchor_vel_rmse={res['anchor_vel_rmse_px_per_frame']:.4f} px/f, closure_err={res['closure_error_px']:.4f} px\n")
        f.write("frame,x_px,y_px,dx_px,dy_px,angle_deg\n")
        x_px, y_px, dx_px, dy_px = res['x_px'], res['y_px'], res['dx_px'], res['dy_px']
        phi_deg = np.rad2deg(res['phi_rad']) % 360.0
        for i in range(nf):
            f.write(f"{i},{x_px[i]:.4f},{y_px[i]:.4f},{dx_px[i]:.4f},{dy_px[i]:.4f},{phi_deg[i]:.2f}\n")

    report = {
        "source_raw": raw_path,
        "gth_csv": csv_out,
        "width": int(w),
        "height": int(h),
        "num_frames": int(nf),
        "fps": int(fps),
        "valid_end": int(valid_end),
        "t_start": int(t_start),
        "t_end": int(t_end),
        "active_frames": int(t_end - t_start),
        "is_closed": bool(is_closed),
        "loop_closure_corr": round(float(loop_corr), 4),
        "noise_floor_std": round(float(noise_floor), 3),
        "best_lag_frames": int(best_lag),
        "num_high_conf_anchors": int(len(anchors)),
        "radius_px": round(float(res['radius_px']), 3),
        "direction": res['direction'],
        "phi0_deg": round(float(res['phi0_deg']), 2),
        "sweep_deg": round(float(res['sweep_deg']), 2),
        "v_med_px_per_frame": round(float(res['v_med_px_per_frame']), 4),
        "closure_error_px": round(float(res['closure_error_px']), 4),
        "anchor_vel_rmse_px_per_frame": round(float(res['anchor_vel_rmse_px_per_frame']), 4),
        "anchor_angle_rmse_deg": round(float(res['anchor_angle_rmse_deg']), 2),
    }
    json_out = os.path.join(gth_dir, f"{seq_name}_gth_report.json")
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    png_out = os.path.join(gth_dir, f"{seq_name}_gth_verify.png")
    fig = plt.figure(figsize=(14, 7))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.15, 1.0])

    ax1 = fig.add_subplot(gs[:, 0])
    ax1.plot(res['x_px'], res['y_px'], color='#2563eb', linewidth=1.8, label='Reconstructed GTH Trajectory')
    ax1.scatter(res['x_px'][0], res['y_px'][0], color='#10b981', s=75, zorder=5, edgecolors='k', label='Start (0, 0)')
    ax1.scatter(res['x_px'][-1], res['y_px'][-1], color='#ef4444', s=75, marker='s', zorder=5, edgecolors='k',
                label=f"End ({res['x_px'][-1]:.1f}, {res['y_px'][-1]:.1f})")
    step_q = max(1, len(anchors) // 24)
    idx_q = np.clip(np.round(anchors[::step_q, 0]).astype(int), 0, nf - 1)
    ax1.quiver(res['x_px'][idx_q], res['y_px'][idx_q], anchors[::step_q, 1], anchors[::step_q, 2],
               color='#f59e0b', scale_units='xy', angles='xy', width=0.004, headwidth=3.5,
               label='Optical Anchors Tangent (bp7)', zorder=6)
    ax1.set_title(
        f"GTH Reconstruction: {seq_name}\n"
        f"R={res['radius_px']:.2f}px | Closed={is_closed} (corr={loop_corr:.3f}, sweep={res['sweep_deg']:.1f}deg) | E_close={res['closure_error_px']:.3f}px",
        fontsize=10.5, fontweight='bold'
    )
    ax1.set_xlabel("X (pixels)"); ax1.set_ylabel("Y (pixels)")
    ax1.axis('equal'); ax1.grid(True, linestyle='--', alpha=0.45); ax1.legend(loc='best', fontsize=8.5)

    ax2 = fig.add_subplot(gs[0, 1])
    t_arr = np.arange(nf)
    ax2.scatter(anchors[:, 0], anchors[:, 3], s=8, alpha=0.55, color='#f59e0b', label=f'bp7 Optical Anchors (n={len(anchors)}, lag={best_lag})')
    ax2.plot(t_arr, res['v_smooth'], color='#1d4ed8', linewidth=1.6, label='Smoothed GTH Speed v(t)')
    ax2.axvline(t_start, color='#10b981', linestyle=':', linewidth=1.1, label=f't_start={t_start}')
    ax2.axvline(t_end, color='#ef4444', linestyle=':', linewidth=1.1, label=f't_end={t_end}')
    ax2.set_title(f"Speed Profile | Vel RMSE = {res['anchor_vel_rmse_px_per_frame']:.4f} px/frame", fontsize=10, fontweight='bold')
    ax2.set_ylabel("Speed (px/frame)"); ax2.grid(True, linestyle='--', alpha=0.45); ax2.legend(loc='best', fontsize=8)

    ax3 = fig.add_subplot(gs[1, 1])
    ang_u_deg = np.rad2deg(np.unwrap(anchors[:, 4]))
    phi_u_deg = np.rad2deg(res['phi_rad'])
    off_deg = round(float(np.median(ang_u_deg - np.interp(anchors[:, 0], t_arr, phi_u_deg))) / 360.0) * 360.0
    ax3.scatter(anchors[:, 0], ang_u_deg - off_deg, s=8, alpha=0.55, color='#10b981', label='Optical Anchor Angles (unwrapped)')
    ax3.plot(t_arr, phi_u_deg, color='#dc2626', linewidth=1.4, label='GTH Phase Angle phi(t)')
    ax3.set_title(f"Phase Angle Progression | Dir={res['direction']} | Ang RMSE = {res['anchor_angle_rmse_deg']:.2f} deg", fontsize=10, fontweight='bold')
    ax3.set_xlabel("Frame Index"); ax3.set_ylabel("Angle (deg)"); ax3.grid(True, linestyle='--', alpha=0.45); ax3.legend(loc='best', fontsize=8)

    plt.tight_layout()
    plt.savefig(png_out, dpi=140)
    plt.close()

    print(
        f"[GTH 完成] {seq_name:34s} | R={res['radius_px']:6.2f}px | Closed={str(is_closed):5s} (corr={loop_corr:.3f}) "
        f"| active=[{t_start:4d},{t_end:5d}] | anchors={len(anchors):3d}(lag={best_lag:2d}) "
        f"| velRMSE={res['anchor_vel_rmse_px_per_frame']:.4f} -> {csv_out}"
    )
    return report


def collect_raws_from_target(target):
    target = os.path.abspath(target)
    if os.path.isfile(target) and target.lower().endswith('.raw'):
        return [target]
    raws = []
    for root, dirs, files in os.walk(target):
        if os.path.basename(root).upper() == 'GTH':
            continue
        for fn in sorted(files):
            if fn.lower().endswith('.raw'):
                raws.append(os.path.join(root, fn))
    return raws


def main():
    parser = argparse.ArgumentParser(description="建立 Dataset 目錄下 GTH/ 高精度 Ground Truth 工具")
    parser.add_argument("target", nargs="?", default=None, help="RAW 檔案或資料集目錄路徑")
    parser.add_argument("--all-default", action="store_true", help="一鍵為全部 5 大標準測試集建置 GTH/")
    args = parser.parse_args()

    if args.all_default:
        raw_list = [p for p in DEFAULT_RAW_LIST if os.path.exists(p)]
    elif args.target:
        raw_list = collect_raws_from_target(args.target)
    else:
        parser.print_help()
        sys.exit(1)

    if not raw_list:
        print("[錯誤] 找不到可處理的 .raw 檔案。")
        sys.exit(1)

    print(f"準備為 {len(raw_list)} 個 RAW 序列重建 Dataset GTH (GainNorm Full-FPN + BandPass7)...")
    summaries = []
    for rp in raw_list:
        rep = build_gth_for_raw(rp)
        summaries.append(rep)

    print("\n=== GTH 建置總結 ===")
    for s in summaries:
        print(
            f"  {os.path.basename(s['source_raw']):38s} | R={s['radius_px']:6.2f}px | "
            f"Closed={str(s['is_closed']):5s} | Sweep={s['sweep_deg']:5.1f}deg | "
            f"Anchors={s['num_high_conf_anchors']:3d} | velRMSE={s['anchor_vel_rmse_px_per_frame']:.4f}"
        )


if __name__ == "__main__":
    main()
