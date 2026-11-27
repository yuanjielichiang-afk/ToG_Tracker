r"""
========================================================================================
ToG Tracker: Standalone Normal Mode Runner (純自主追蹤執行器)
========================================================================================
本腳本專門用於「只執行 Normal Mode (自主追蹤)」並輸出標準光學滑鼠軌跡 CSV 檔案。
不依賴 Ground Truth (真值)，不執行耗時的評估與診斷比較，快速輸出自主追蹤結果。

使用方式範例：
  python run_normal_mode.py POS1-1.raw
  python run_normal_mode.py "C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw"
  python run_normal_mode.py POS1-1.raw --out ./my_output --plot

輸出檔案：
  - Track_B_NormalMode.csv (標準欄位: frame, dx, dy, x, y, squal)
  - Track_B_NormalMode_plot.png (若啟用 --plot 或預設產出軌跡圖)
========================================================================================
"""

import os
import csv
import sys
import shutil
import argparse
import json
from datetime import datetime
import numpy as np
import matplotlib.pyplot as plt

# 確保專案根目錄在 sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from step1_anchor_and_textureless_analyzer import OpniRawIO
from tracker_core import NormalModeTracker

def find_raw_file(raw_input):
    """智能搜尋 RAW 檔案"""
    if not raw_input.endswith('.raw'):
        raw_input += '.raw'

    if os.path.isabs(raw_input) and os.path.exists(raw_input):
        return os.path.abspath(raw_input)

    candidate_dirs = [
        r"C:\Data\testPattern玻璃\Circle_R0.1inch_IPS0.005_Glass",
        r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW",
        r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass",
        r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_MousePad\RAW",
        r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_MousePad",
        r"C:\Data\testPattern玻璃\20260529\Circle\Clean Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_0.1ips\Image",
        r"C:\Data\testPattern玻璃\20260529\Circle\Clean Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_1ips\Image",
        r"C:\Data\testPattern玻璃\20260529\Circle\Dirty Glass\Radius 0.5 inch\P3950_Circle_0.5inch_1rev_0.1ips\Image",
        r"C:\Data\DATA_FAMS\FAMS_GLASS",
        os.getcwd()
    ]
    for cdir in candidate_dirs:
        cand = os.path.join(cdir, os.path.basename(raw_input))
        if os.path.exists(cand):
            return os.path.abspath(cand)

    return None

def find_existing_fpn(raw_path):
    """搜尋現有的高品質 FPN 模型 (僅限同序列或同目錄)"""
    seq_name = os.path.splitext(os.path.basename(raw_path))[0]
    parent_dir = os.path.basename(os.path.dirname(os.path.abspath(raw_path)))
    if parent_dir.upper() in ['RAW', 'CSV', 'PNG', 'IMAGE']:
        parent_dir = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(raw_path))))

    candidate_fpns = [
        os.path.join(SCRIPT_DIR, "experiments", parent_dir, seq_name, "fpn_model.raw"),
        os.path.join(os.path.dirname(raw_path), "fpn_model.raw"),
        os.path.join(os.path.dirname(os.path.dirname(raw_path)), "fpn_model.raw"),
    ]
    for fp in candidate_fpns:
        if os.path.exists(fp):
            return os.path.abspath(fp)
    return None

def compute_temporal_median_fpn(raw_path, header, n_samples=300):
    """
    透過全序列時間中位數 (Temporal Median) 提取純淨感測器 FPN 底噪。
    中位數可完美剔除移動中的微塵與表面特徵，防止微塵被誤算進 FPN 導致特徵消失。
    """
    total_frames = header['num_frames']
    sample_step = max(1, total_frames // n_samples)
    sampled = []
    with open(raw_path, 'rb') as f:
        for idx in range(0, total_frames, sample_step):
            frm = OpniRawIO.load_frame(f, header, idx)
            if frm is not None:
                sampled.append(frm.astype(np.float32))
    if not sampled:
        return None
    return np.median(sampled, axis=0)

def _extract_subpath_and_seq(raw_path):
    """擷取資料集相對階層路徑 (dataset_subpath) 與序列名稱 (seq_name)"""
    abs_path = os.path.abspath(raw_path)
    seq_name = os.path.splitext(os.path.basename(abs_path))[0]

    known_roots = ["testPattern玻璃", "DATA_FAMS", "ToG_Tracker", "Data"]
    parts = os.path.normpath(abs_path).split(os.sep)
    lower_parts = [p.lower() for p in parts]

    matched_parts = None
    for r in known_roots:
        if r.lower() in lower_parts:
            idx = lower_parts.index(r.lower())
            matched_parts = parts[idx + 1:-1]
            break

    if matched_parts and len(matched_parts) > 0:
        filtered = [p.replace(' ', '_') for p in matched_parts if p.upper() not in ['IMAGE', 'RAW', 'TRACE', 'CSV', 'DATA']]
        subpath = os.path.join(*filtered) if filtered else ''
    else:
        p_dir = os.path.dirname(abs_path)
        p_name = os.path.basename(p_dir)
        if p_name.upper() in ['IMAGE', 'RAW', 'TRACE', 'CSV']:
            p_name = os.path.basename(os.path.dirname(p_dir))
        subpath = p_name.replace(' ', '_')
    return subpath, seq_name


def resolve_output_paths(raw_path, base_out=None, tag=None, filter_type='bandpass3x7',
                         fpn_mode='csbc', gain_norm='none', no_timestamp=False):
    """
    版本化輸出路徑解析器：
      - 若指定 --out，直接寫入該指定路徑 (latest_dir / index_csv 為 None)。
      - 否則建立：normal_mode_output/<dataset_subpath>/<seq_name>/
          ├── runs/<YYYYMMDD_HHMMSS>_<flt>_<fpn>[_gn<mode>][_<tag>]/
          ├── latest/           (永遠同步最新一次輸出的 CSV / JSON / PNG)
          └── runs_index.csv    (所有歷史執行的索引總表)
    """
    if base_out:
        return os.path.abspath(base_out), None, None, os.path.basename(os.path.abspath(base_out))

    subpath, seq_name = _extract_subpath_and_seq(raw_path)
    seq_root = os.path.abspath(os.path.join(SCRIPT_DIR, "normal_mode_output", subpath, seq_name))

    filter_short_map = {
        'bandpass3x7': 'bp3x7',
        'bandpass3x7_r0': 'bp3x7r0',
        'bandpass5x7': 'bp5x7',
        'bandpass7': 'bp7',
        'bandpass5': 'bp5',
        'bandpass5_sep': 'bp5sep',
        'optnavi_ctrl': 'optnavi',
        'none': 'none',
    }
    flt_short = filter_short_map.get(filter_type.lower(), filter_type.lower())
    prefix = "latest" if no_timestamp else datetime.now().strftime("%Y%m%d_%H%M%S")
    parts = [prefix, flt_short, fpn_mode.lower()]
    if gain_norm and gain_norm.lower() != 'none':
        parts.append(f"gn{gain_norm.lower()}")
    if tag:
        parts.append(tag.strip().replace(' ', '_'))
    run_id = "_".join(parts)

    runs_dir = os.path.join(seq_root, "runs")
    os.makedirs(runs_dir, exist_ok=True)
    out_dir = os.path.join(runs_dir, run_id)
    if not no_timestamp and os.path.exists(out_dir):
        suffix_idx = 1
        while os.path.exists(f"{out_dir}_{suffix_idx:02d}"):
            suffix_idx += 1
        run_id = f"{run_id}_{suffix_idx:02d}"
        out_dir = os.path.join(runs_dir, run_id)

    latest_dir = os.path.join(seq_root, "latest")
    index_csv = os.path.join(seq_root, "runs_index.csv")
    return os.path.abspath(out_dir), os.path.abspath(latest_dir), os.path.abspath(index_csv), run_id


def get_smart_output_dir(raw_path, base_out=None, tag=None):
    """相容舊介面的智能階層輸出目錄解析 (預設版本化)"""
    out_dir, _, _, _ = resolve_output_paths(raw_path, base_out=base_out, tag=tag)
    return out_dir

def fit_kasa_circle(xs, ys):
    """Kasa 最小平方法正圓擬合，回傳 (xc, yc, r_fit, radial_rmse_px, aspect_ratio_xy)"""
    if len(xs) < 10 or (np.ptp(xs) < 1.0 and np.ptp(ys) < 1.0):
        return 0.0, 0.0, 0.0, 0.0, 1.0
    A_mat = np.column_stack([xs, ys, np.ones_like(xs)])
    b_vec = xs**2 + ys**2
    sol, _, _, _ = np.linalg.lstsq(A_mat, b_vec, rcond=None)
    xc = float(sol[0] / 2.0)
    yc = float(sol[1] / 2.0)
    r_fit = float(np.sqrt(max(0.0, sol[2] + xc**2 + yc**2)))
    radii = np.hypot(xs - xc, ys - yc)
    radial_rmse = float(np.sqrt(np.mean((radii - r_fit)**2)))
    aspect = float(np.ptp(xs) / (np.ptp(ys) + 1e-6))
    return xc, yc, r_fit, radial_rmse, aspect

def load_gth_xy_safe(csv_path, expected_len):
    """安全讀取含 UTF-8 BOM 與 # Metadata 註解標頭的 GTH CSV，避免 np.genfromtxt 解析異常"""
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        data_lines = [line for line in f if line.strip() and not line.lstrip().startswith("#")]
    if len(data_lines) < 2:
        return None
    reader = csv.DictReader(data_lines)
    if not reader.fieldnames:
        return None
    fields = [c.strip() for c in reader.fieldnames]
    x_col = "x_px" if "x_px" in fields else ("x" if "x" in fields else None)
    y_col = "y_px" if "y_px" in fields else ("y" if "y" in fields else None)
    if not x_col or not y_col:
        return None
    gx, gy = [], []
    for row in reader:
        try:
            gx.append(float(row[x_col]))
            gy.append(float(row[y_col]))
        except (TypeError, ValueError, KeyError):
            continue
    if len(gx) != expected_len:
        return None
    return np.asarray(gx, dtype=np.float64), np.asarray(gy, dtype=np.float64)


def find_matched_gth_xy(raw_path, expected_len):
    """尋找與序列長度相符之 Dataset GTH / experiments GTH 座標 (gx, gy, ref_label)"""
    seq_name = os.path.splitext(os.path.basename(raw_path))[0]
    raw_dir = os.path.dirname(os.path.abspath(raw_path))
    parent_name = os.path.basename(raw_dir)
    if parent_name.upper() in ['IMAGE', 'RAW', 'TRACE', 'CSV']:
        parent_name = os.path.basename(os.path.dirname(raw_dir))

    gth_candidates = [
        (os.path.join(raw_dir, "GTH", f"{seq_name}_ground_truth.csv"), f"Dataset_GTH ({seq_name}_ground_truth.csv)"),
        (os.path.join(os.path.dirname(raw_dir), "GTH", f"{seq_name}_ground_truth.csv"), f"Dataset_GTH ({seq_name}_ground_truth.csv)"),
        (os.path.join(raw_dir, f"{seq_name}_ground_truth.csv"), f"Dataset_GTH ({seq_name}_ground_truth.csv)"),
        (os.path.join(SCRIPT_DIR, "experiments", parent_name, seq_name, f"{seq_name}_ground_truth.csv"), f"GTH ({seq_name}_ground_truth.csv)"),
        (os.path.join(SCRIPT_DIR, "step2_output", f"{seq_name}_ground_truth.csv"), f"GTH ({seq_name}_ground_truth.csv)"),
    ]
    for gp, ref_label in gth_candidates:
        if os.path.exists(gp):
            try:
                loaded = load_gth_xy_safe(gp, expected_len)
                if loaded is not None:
                    return loaded[0], loaded[1], ref_label
            except Exception:
                pass
    return None


def compute_circle_and_global_rmse(xs, ys, raw_path):
    """
    計算圓度誤差與全域軌跡誤差均方根 (Global Trajectory RMSE)：
      1. 優先尋找該測試資料集目錄下的 `GTH/<seq>_ground_truth.csv`，次之搜尋既有 experiments/step2_output GTH。
      2. 若無 GTH，尋找同目錄或 `../Trace/` 的 P3950 參考 CSV，依運動弧長線性重採樣並做 2D Procrustes 對齊後計算 RMSE。
      3. 若無外部真值 CSV，則以 Kasa 最佳擬合正圓計算軌跡均方根誤差 (Ideal_Circle_Fit)。
    """
    xc, yc, r_fit, radial_rmse, aspect = fit_kasa_circle(xs, ys)
    N = len(xs)
    seq_name = os.path.splitext(os.path.basename(raw_path))[0]
    raw_dir = os.path.dirname(os.path.abspath(raw_path))

    # 1. 最優先搜尋 Dataset 目錄下的 GTH/，再搜尋既有專案 experiments / step2_output
    matched = find_matched_gth_xy(raw_path, N)
    if matched is not None:
        gx, gy, ref_label = matched
        rmse = float(np.sqrt(np.mean((xs - gx)**2 + (ys - gy)**2)))
        return xc, yc, r_fit, radial_rmse, aspect, rmse, ref_label

    # 2. 尋找原相 P3950 參考 CSV (同目錄、../Trace/ 或 ../CSV/)
    p3950_candidates = [
        os.path.join(raw_dir, f"{seq_name}.csv"),
        os.path.join(os.path.dirname(raw_dir), "Trace", f"{seq_name}.csv"),
        os.path.join(os.path.dirname(raw_dir), "CSV", f"{seq_name}.csv"),
    ]
    for cp in p3950_candidates:
        if os.path.exists(cp):
            try:
                arr = np.loadtxt(cp, delimiter=',')
                if arr.ndim == 2 and arr.shape[1] >= 2 and len(arr) >= 50:
                    px_raw = np.cumsum(arr[:, 0])
                    py_raw = np.cumsum(arr[:, 1])
                    # 依累積弧長重採樣活躍區段以對齊時序差
                    t_arc_p = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(px_raw), np.diff(py_raw)))])
                    t_arc_p /= (t_arc_p[-1] + 1e-9)
                    t_arc_x = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(xs), np.diff(ys)))])
                    t_arc_x /= (t_arc_x[-1] + 1e-9)
                    px_resamp = np.interp(t_arc_x, t_arc_p, px_raw)
                    py_resamp = np.interp(t_arc_x, t_arc_p, py_raw)

                    # 2D 相似變換對齊 (尺度 + 旋轉 + 平移，不含鏡射)
                    P = np.column_stack([px_resamp - np.mean(px_resamp), py_resamp - np.mean(py_resamp)])
                    Q = np.column_stack([xs - np.mean(xs), ys - np.mean(ys)])
                    U, _, Vt = np.linalg.svd(P.T @ Q)
                    R = U @ Vt
                    if np.linalg.det(R) < 0:
                        Vt[-1, :] *= -1
                        R = U @ Vt
                    scale = float(np.sum((P @ R) * Q) / (np.sum(P**2) + 1e-9))
                    Q_ref = scale * (P @ R) + np.array([np.mean(xs), np.mean(ys)])
                    rmse = float(np.sqrt(np.mean(np.sum((np.column_stack([xs, ys]) - Q_ref)**2, axis=1))))
                    return xc, yc, r_fit, radial_rmse, aspect, rmse, f"P3950_Aligned ({os.path.basename(cp)})"
            except Exception:
                pass

    # 3. 退回：相對於 Kasa 最佳理想正圓之軌跡均方根誤差
    return xc, yc, r_fit, radial_rmse, aspect, radial_rmse, "Ideal_Circle_Fit"

def main():
    parser = argparse.ArgumentParser(description="ToG Tracker 純 Normal Mode 自主追蹤工具 (支援第三階段 IC 極致輕量化無影像 FPN 架構)")
    parser.add_argument("raw", help="RAW 檔案名稱或完整路徑 (例如: POS1-1.raw)")
    parser.add_argument("--out", "-o", default=None, help="輸出目錄 (預設為智能階層目錄)")
    parser.add_argument("--tag", default=None, help="輸出目錄自訂標籤後綴 (例如: csbc, nofpn, bp3x7)")
    parser.add_argument("--fpn", default=None, help="指定 FPN 模型路徑 (.raw 檔；若指定則自動切換為 full 模式)")
    parser.add_argument("--fpn-mode", default="csbc", choices=["csbc", "none", "full"],
                        help="背景雜訊抑制模式: csbc=無影像FPN互相關純量扣除(預設,僅82B省93.7%%面積), none=極致0-Byte純濾波, full=傳統整張FPN影像(1296B)")
    parser.add_argument("--radius", "-r", type=int, default=None, help="搜尋半徑 (預設: 36x36 為 1, >=64x64 為 2)")
    parser.add_argument("--plot", action="store_true", default=True, help="是否生成 2D 軌跡折線圖 (預設開啟)")
    parser.add_argument("--filter", default="bandpass3x7", 
                        choices=["bandpass3x7", "bandpass3x7_r0", "bandpass5x7", "bandpass7", "bandpass5", "bandpass5_sep", "optnavi_ctrl", "none"],
                        help="單級前處理濾波器 (預設: bandpass3x7 扁長方形核，IC 僅需 2 條 Line Buffer；optnavi_ctrl 為 OptNavi box2+saddle3+corner 對照組)")
    parser.add_argument("--bandpass", action="store_true", default=True, help="啟用帶通濾波器前處理 (相容舊參數)")
    parser.add_argument("--no-bandpass", dest="bandpass", action="store_false", help="關閉帶通濾波器")
    parser.add_argument("--gain-norm", default="none", choices=["none", "median", "mean"],
                        help="全幀曝光亮度增益正規化模式 (0 額外 LineBuf: none=關閉, median=中位數比例, mean=連續浮點均值比例)")
    parser.add_argument("--zncc-coast-th", type=float, default=None,
                        help="ZNCC 無紋理慣性滑行門檻 (預設 None；Clean Glass 對照測試可設如 0.55)")
    parser.add_argument("--predictor", default="shift_iir", choices=["shift_iir", "shift_fir4", "legacy_iir"],
                        help="Stage A 速度預測器 (預設 shift_iir: IC零乘法移位IIR; shift_fir4: OptNavi 4幀平均; legacy_iir: 浮點對照)")
    parser.add_argument("--max-phys-v", type=float, default=4.0,
                        help="速度上限 (與 search_radius 解耦，預設 4.0 px/frame 使 r=1 可追蹤 1+ ips)")
    parser.add_argument("--max-accel", type=float, default=4.0,
                        help="預測器單幀創新量突波鉗位閾值 (px/frame, 預設 4.0)")
    parser.add_argument("--coast-decay", type=int, default=0,
                        help="無紋理連續滑行每 K 幀衰減 1/8 之間隔 (0=關閉, 預設 0)")
    parser.add_argument("--no-auto-surface", action="store_true", default=False,
                        help="停用滑鼠墊自動切換至 3x3 4-Corner，允許直接測試 bandpass3x7 等濾波器")
    parser.add_argument("--numeric-mode", default="float", choices=["float"],
                        help="數值模式 (Stage A 保留 float32 Golden Reference；Stage B 將擴充 fixed)")
    parser.add_argument("--no-timestamp", action="store_true", default=False,
                        help="停用時間戳記子目錄，固定覆寫寫入 runs/latest_<config>/")
    args = parser.parse_args()

    raw_path = find_raw_file(args.raw)
    if not raw_path or not os.path.exists(raw_path):
        print(f"[錯誤] 找不到 RAW 檔案: {args.raw}")
        sys.exit(1)

    # 決定濾波器與單級多工模式
    is_mousepad = ('mousepad' in raw_path.lower()) and (not args.no_auto_surface)
    filter_type = args.filter.lower()
    if not args.bandpass:
        filter_type = 'none'

    # 滑鼠墊預設採用單級 3x3 Corner (共用 2 條 Line Buffer，不串聯前端帶通，避免多級 Line Buffer 浪費！)
    # 若明確指定 optnavi_ctrl (box2+saddle3+corner 對照組)，則交由 apply_bandpass_filter 內部完整執行，不重複疊加
    use_corner_filter = is_mousepad and (filter_type != 'optnavi_ctrl')
    if is_mousepad and args.filter == "bandpass3x7":
        filter_type = 'none'

    fpn_mode = 'full' if args.fpn else args.fpn_mode.lower()
    gain_norm_mode = args.gain_norm.lower()
    if is_mousepad and fpn_mode == 'csbc' and filter_type != 'optnavi_ctrl' and args.fpn_mode == 'csbc':
        # 布質滑鼠墊紋理極強，單級 3x3 Corner 在 0-Byte 無 FPN 下免背景儲存即可追蹤
        fpn_mode = 'none'

    # 決定版本化輸出目錄 (runs/<timestamp>_<config>/ + latest/ + runs_index.csv)
    out_dir, latest_dir, index_csv, run_id = resolve_output_paths(
        raw_path,
        base_out=args.out,
        tag=args.tag,
        filter_type=filter_type,
        fpn_mode=fpn_mode,
        gain_norm=gain_norm_mode,
        no_timestamp=args.no_timestamp
    )
    os.makedirs(out_dir, exist_ok=True)

    header = OpniRawIO.read_header(raw_path)
    w, h = header['width'], header['height']
    total_frames = header['num_frames']
    fps = header['fps']
    bit_depth = header.get('bit_depth', 8)
    file_size_bytes = os.path.getsize(raw_path)
    file_mtime = datetime.fromtimestamp(os.path.getmtime(raw_path)).isoformat()
    now_iso = datetime.now().isoformat()

    # 硬體開銷與算力評估 (嚴格以 RTL Raster-Scan 所需 H_k - 1 條 Line Buffer 計算，對標原廠 7x7 的 6 條 Line Buffer)
    hw_specs = {
        'bandpass3x7':    {'win_rows': 3, 'line_buffers': 2, 'macs_per_pix': 21, 'sram_savings_pct': 66.7, 'mac_savings_pct': 57.1, 'desc': '3x7 扁長方形零均值核 (僅2條LineBuf, 對稱僅6乘法, valid內縮)'},
        'bandpass3x7_r0': {'win_rows': 3, 'line_buffers': 2, 'macs_per_pix': 21, 'sram_savings_pct': 66.7, 'mac_savings_pct': 57.1, 'desc': '3x7 逐列零和雙消失矩核 (僅2條LineBuf, 100%消滅Row-FPN與二次光暈)'},
        'bandpass5x7':    {'win_rows': 5, 'line_buffers': 4, 'macs_per_pix': 35, 'sram_savings_pct': 33.3, 'mac_savings_pct': 28.6, 'desc': '5x7 扁長方形零均值核 (4條LineBuf, 總和=0.0)'},
        'bandpass7':      {'win_rows': 7, 'line_buffers': 6, 'macs_per_pix': 49, 'sram_savings_pct': 0.0,  'mac_savings_pct': 0.0,  'desc': '7x7 同心零均值帶通核 (原廠基準 6條LineBuf, 總和=0.0)'},
        'bandpass5':      {'win_rows': 5, 'line_buffers': 4, 'macs_per_pix': 25, 'sram_savings_pct': 33.3, 'mac_savings_pct': 49.0, 'desc': '5x5 同心零均值帶通核 (4條LineBuf, 總和=0.0)'},
        'bandpass5_sep':  {'win_rows': 5, 'line_buffers': 4, 'macs_per_pix': 10, 'sram_savings_pct': 33.3, 'mac_savings_pct': 79.6, 'desc': '5x5 可分離 1D+1D 卷積 (4條LineBuf, 極簡低功耗)'},
        'optnavi_ctrl':   {'win_rows': 6, 'line_buffers': 5, 'macs_per_pix': 28, 'sram_savings_pct': 16.7, 'mac_savings_pct': 42.9, 'desc': 'OptNavi 對照組: box2 + saddle3 + 4-Corner (雙級串聯需 5 條LineBuf)'},
        'none':           {'win_rows': (3 if use_corner_filter else 1), 'line_buffers': (2 if use_corner_filter else 0), 'macs_per_pix': (12 if use_corner_filter else 0), 'sram_savings_pct': (66.7 if use_corner_filter else 100.0), 'mac_savings_pct': (75.5 if use_corner_filter else 100.0), 'desc': ('單級 3x3 4-Corner 抗鋸齒核 (共用2條LineBuf, 免串聯)' if use_corner_filter else '無濾波器')}
    }[filter_type]

    full_fpn_bytes = w * h
    fpn_mem_bytes = {'csbc': 82, 'none': 0, 'full': full_fpn_bytes}[fpn_mode]
    fpn_mem_saving_pct = (1.0 - fpn_mem_bytes / max(1, full_fpn_bytes)) * 100.0
    fpn_desc_map = {
        'csbc': f"CSBC 互相關域靜態純量扣除 (0 Bytes 影像FPN! 僅 41 個對稱純量={fpn_mem_bytes}B, 省 {fpn_mem_saving_pct:.1f}% 面積)",
        'none': "NONE 純濾波無背景儲存 (0 Bytes FPN SRAM, 省 100.0% 面積!)",
        'full': f"FULL 傳統整張時間中位數 FPN 影像 ({w}x{h}={full_fpn_bytes} Bytes SRAM)"
    }

    print("\n" + "=" * 78)
    print("  ToG Tracker: Normal Mode 獨立追蹤器 (Stage-A IC r=1 解耦架構)")
    print(f"  原始 RAW 檔案: {raw_path}")
    print(f"  影像規格      : {w}x{h}, {bit_depth}-bit, {total_frames} 幀, {fps} FPS | 數值={args.numeric_mode.upper()}")
    print(f"  單級特徵濾波  : {filter_type.upper()} ({hw_specs['desc']})")
    print(f"  FPN 抑制架構  : {fpn_desc_map[fpn_mode]} | 增益正規化={gain_norm_mode.upper()}")
    print(f"  IC 硬體資源   : Line Buffer={hw_specs['line_buffers']} 條 (減省 {hw_specs['sram_savings_pct']:.1f}%), FPN記憶體={fpn_mem_bytes}B (減省 {fpn_mem_saving_pct:.1f}%), MAC/Pix={hw_specs['macs_per_pix']}")
    print(f"  版本化輸出目錄: {out_dir}")
    print("=" * 78)

    # 1. 確定追蹤參數與準備 FPN / CSBC 模型 (Stage A: 36x36 全速域統一使用 r=1 3x3 搜尋，靠準確 PP 覆蓋 1+ ips！)
    if args.radius is not None:
        search_radius = args.radius
    else:
        search_radius = 2 if (w >= 64) else 1

    max_phys_v = float(args.max_phys_v)
    search_candidates = (2 * search_radius + 1) ** 2
    eff_w = (w - 6) if '3x7' in filter_type else (w - 2 * (search_radius + 2))
    eff_h = (h - 2) if '3x7' in filter_type else (h - 2 * (search_radius + 2))
    corr_macs_per_frame = search_candidates * max(64, eff_w * eff_h)
    use_bandpass = (filter_type != 'none')
    fpn_model = None
    static_bias_table = None
    ref_bg_level = None

    if fpn_mode == 'full':
        if args.fpn and os.path.exists(args.fpn):
            print(f"[1/3] 載入自訂整張 FPN 模型: {args.fpn}")
            fpn_hdr = OpniRawIO.read_header(args.fpn)
            with open(args.fpn, 'rb') as f:
                fpn_model = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)
        else:
            existing_fpn = find_existing_fpn(raw_path)
            if existing_fpn and gain_norm_mode == 'none':
                print(f"[1/3] 載入已校準整張 FPN 模型: {existing_fpn}")
                fpn_hdr = OpniRawIO.read_header(existing_fpn)
                with open(existing_fpn, 'rb') as f:
                    fpn_model = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)
            else:
                print("[1/3] 使用全序列時間中位數提取整張 FPN 模型 (1296 Bytes)...")
                if gain_norm_mode in ('median', 'mean'):
                    helper_gn = NormalModeTracker(gain_norm_mode=gain_norm_mode)
                    sample_step = max(1, total_frames // 300)
                    sampled = []
                    with open(raw_path, 'rb') as f:
                        for idx in range(0, total_frames, sample_step):
                            frm = OpniRawIO.load_frame(f, header, idx)
                            if frm is not None:
                                sampled.append(helper_gn.normalize_frame_gain(frm))
                    fpn_model = np.median(sampled, axis=0).astype(np.float32) if sampled else None
                    ref_bg_level = helper_gn.ref_bg_level
                else:
                    fpn_model = compute_temporal_median_fpn(raw_path, header)
    elif fpn_mode == 'csbc':
        print("[1/3] 執行 CSBC 跨幀線上自校準 (不儲存任何 FPN 影像，僅提取 41 個對稱相關純量 = 82 Bytes)...")
        proto = NormalModeTracker(
            search_radius=search_radius,
            fpn_model=None,
            use_corner_filter=use_corner_filter,
            use_bandpass_filter=use_bandpass,
            bandpass_type=filter_type,
            gain_norm_mode=gain_norm_mode
        )
        max_lag_csbc = max(4, search_radius + 3)
        with open(raw_path, 'rb') as f_cal:
            static_bias_table = NormalModeTracker.calibrate_csbc_table(
                raw_loader_fn=lambda idx: OpniRawIO.load_frame(f_cal, header, idx),
                total_frames=total_frames,
                preprocess_fn=proto.preprocess_frame,
                max_lag=max_lag_csbc
            )
        ref_bg_level = proto.ref_bg_level
    else:
        print("[1/3] 極致 0-Byte 無 FPN 模式：完全不載入任何背景模型，純靠單級零均值濾波器抑制光暈！")

    if use_corner_filter:
        print("  [提示] 偵測到滑鼠墊紋理，啟用單級 3x3 4-Corner 抗鋸齒濾波器 (僅用 2 條 Line Buffer，免串聯)")

    print(f"[2/3] 初始化 NormalModeTracker (搜尋半徑: {search_radius} [{search_candidates}點], predictor={args.predictor}, max_phys_v={max_phys_v}, 次像素: 解析解 2D 橢圓拋物面)...")
    tracker = NormalModeTracker(
        search_radius=search_radius,
        subpixel_scale=32,
        keyframe_dist=2.5,
        squal_th=5.0,
        v_alpha=0.25,
        fpn_model=fpn_model,
        use_corner_filter=use_corner_filter,
        use_bandpass_filter=use_bandpass,
        bandpass_type=filter_type,
        max_phys_v=max_phys_v,
        static_bias_table=static_bias_table,
        gain_norm_mode=gain_norm_mode,
        ref_bg_level=ref_bg_level,
        zncc_coast_th=args.zncc_coast_th,
        predictor_mode=args.predictor,
        max_accel=args.max_accel,
        coast_decay_interval=args.coast_decay
    )

    # 3. 逐幀執行追蹤
    print(f"[3/3] 開始自主追蹤 (共 {total_frames} 幀)...")
    records = []
    print_step = max(500, total_frames // 10)

    with open(raw_path, 'rb') as f:
        for fi in range(total_frames):
            frame = OpniRawIO.load_frame(f, header, fi)
            if frame is None:
                break
            res = tracker.step(frame, fi)
            records.append((
                fi,
                res['est_dx'],
                res['est_dy'],
                res['est_x'],
                res['est_y'],
                res['squal']
            ))

            if fi % print_step == 0 or fi == total_frames - 1:
                print(f"  Frame {fi:5d}/{total_frames}: "
                      f"當前位移=(dx:{res['est_dx']:+.3f}, dy:{res['est_dy']:+.3f}), "
                      f"累計座標=(X:{res['est_x']:+.2f}, Y:{res['est_y']:+.2f}), "
                      f"SQUAL={res['squal']:.1f}")

    records = np.array(records)
    frames_idx = records[:, 0]
    dxs = records[:, 1]
    dys = records[:, 2]
    xs = records[:, 3]
    ys = records[:, 4]
    squals = records[:, 5]

    # 指標統計 (含全域軌跡誤差均方根與圓度指標 + Stage A 診斷指標)
    speeds = np.sqrt(dxs**2 + dys**2)
    moving_mask = speeds > 0.01
    closure_error_px = float(np.sqrt((xs[-1] - xs[0])**2 + (ys[-1] - ys[0])**2))
    xc, yc, r_est, radial_rmse_px, aspect_ratio_xy, global_traj_rmse_px, rmse_ref_type = compute_circle_and_global_rmse(xs, ys, raw_path)
    matched_gth = find_matched_gth_xy(raw_path, len(xs))
    diag = tracker.get_diagnostics()

    # 4. 儲存標準 CSV 輸出 (含 Metadata Header 與 Feature Generation 完整配置，使用 utf-8-sig 防止 Excel 亂碼)
    csv_path = os.path.join(out_dir, "Track_B_NormalMode.csv")
    csv_target = csv_path
    try:
        f_csv = open(csv_target, "w", encoding="utf-8-sig")
    except PermissionError:
        csv_target = os.path.join(out_dir, "Track_B_NormalMode_latest.csv")
        print(f"\n  [提示] 檔案 {os.path.basename(csv_path)} 正被 Excel 或其他軟體開啟鎖定，已自動另存為: {os.path.basename(csv_target)}")
        f_csv = open(csv_target, "w", encoding="utf-8-sig")

    with f_csv as f:
        f.write(f"# RAW_SOURCE: {raw_path}\n")
        f.write(f"# OUTPUT_RUN_ID: {run_id}\n")
        f.write(f"# PROCESSED_AT: {now_iso}\n")
        f.write(f"# SPECS: {w}x{h}, {total_frames} frames, {fps} fps, {bit_depth}-bit\n")
        f.write(f"# FEATURE_GENERATION_PIPELINE:\n")
        f.write(f"#   0. Gain_Normalization: {gain_norm_mode.upper()}\n")
        f.write(f"#   1. FPN_Suppression_Mode: {fpn_mode.upper()} ({fpn_desc_map[fpn_mode]})\n")
        f.write(f"#   2. Single_Stage_Filter: {filter_type.upper()} ({hw_specs['desc']})\n")
        f.write(f"#   3. Corner_Filter: {'Enabled (OptNavi 4-Corner 0/45/90/135 deg)' if (use_corner_filter or filter_type == 'optnavi_ctrl') else 'Disabled (單級直出無串聯)'}\n")
        f.write(f"#   4. SQUAL_&_Textureless_Gate: {'CSBC 純淨微塵能量檢測 + 無紋理慣性滑行 (Coasting)' if fpn_mode == 'csbc' else f'3x3 Laplacian Variance (閾值={tracker.squal_th}, ZNCC_Coast={args.zncc_coast_th})'}\n")
        f.write(f"#   5. Correlation_Surface: {'CSBC 去靜態偏置協方差曲面' if fpn_mode == 'csbc' else 'ZNCC 面積補償相關曲面'} (搜尋半徑={search_radius}, {search_candidates} 點, CorrMAC~={corr_macs_per_frame}/frame)\n")
        f.write(f"#   6. Trajectory_Predictor: {args.predictor.upper()} (max_phys_v={max_phys_v}, max_accel={args.max_accel}, coast_decay={args.coast_decay}, numeric={args.numeric_mode})\n")
        f.write(f"#   7. Subpixel_Interpolation: 解析解 2D 橢圓拋物面峰值擬合 (+ 安全1D退避, 1/32 px)\n")
        f.write(f"# HARDWARE_RESOURCE: Line_Buffer={hw_specs['line_buffers']} 條 (視窗高={hw_specs['win_rows']}列, 節省 {hw_specs['sram_savings_pct']:.1f}%), FPN_Memory={fpn_mem_bytes} Bytes (節省 {fpn_mem_saving_pct:.1f}%), Filter={hw_specs['macs_per_pix']} MAC/pix, Corr={corr_macs_per_frame} MAC/frame\n")
        f.write(f"# TRACKING_METRICS: closure_error={closure_error_px:.2f}px, radius={r_est:.2f}px, radial_rmse={radial_rmse_px:.2f}px, global_traj_rmse={global_traj_rmse_px:.2f}px ({rmse_ref_type}), aspect_xy={aspect_ratio_xy:.3f}, mean_squal={np.mean(squals):.1f}\n")
        f.write(f"# STAGE_A_DIAGNOSTICS: pp_center_hit={diag['pp_center_hit_pct']:.1f}%, border_hit={diag['border_hit_pct']:.1f}%, coast_ratio={diag['coast_ratio_pct']:.1f}%, recenter={diag['recenter_frames']}, kf_updates={diag['keyframe_updates']}, spike_clamped={diag['spike_clamped_frames']}\n")
        f.write("frame,dx,dy,x,y,squal\n")
        for i in range(len(records)):
            f.write(f"{int(frames_idx[i])},{dxs[i]:.4f},{dys[i]:.4f},{xs[i]:.4f},{ys[i]:.4f},{squals[i]:.2f}\n")
    print(f"\n[完成] 已成功匯出標準 CSV 軌跡 (含完整 Feature Generation 標頭與 UTF-8 BOM): {csv_target}")

    # 5. 儲存完整結構化元數據文件 (run_meta.json)
    meta_path = os.path.join(out_dir, "run_meta.json")
    meta_data = {
        "raw_file_info": {
            "absolute_path": raw_path,
            "filename": os.path.basename(raw_path),
            "size_bytes": file_size_bytes,
            "modified_time": file_mtime
        },
        "execution_info": {
            "timestamp": now_iso,
            "run_id": run_id,
            "output_directory": out_dir,
            "command_args": sys.argv[1:]
        },
        "image_specifications": {
            "width": w,
            "height": h,
            "bit_depth": bit_depth,
            "fps": fps,
            "total_frames": total_frames
        },
        "hardware_and_algorithm_config": {
            "gain_norm_mode": gain_norm_mode,
            "fpn_mode": fpn_mode,
            "fpn_memory_bytes": fpn_mem_bytes,
            "fpn_memory_savings_pct": round(fpn_mem_saving_pct, 1),
            "filter_type": filter_type,
            "window_rows": hw_specs['win_rows'],
            "line_buffer_lines": hw_specs['line_buffers'],
            "macs_per_pixel": hw_specs['macs_per_pix'],
            "line_buffer_savings_pct": hw_specs['sram_savings_pct'],
            "mac_savings_pct": hw_specs['mac_savings_pct'],
            "use_corner_filter": bool(use_corner_filter or filter_type == 'optnavi_ctrl'),
            "search_radius": search_radius,
            "search_candidates": search_candidates,
            "corr_macs_per_frame": corr_macs_per_frame,
            "numeric_mode": args.numeric_mode,
            "predictor_mode": args.predictor,
            "max_phys_v": float(tracker.max_phys_v),
            "max_accel": float(args.max_accel),
            "coast_decay_interval": int(args.coast_decay),
            "zncc_coast_th": args.zncc_coast_th,
            "subpixel_method": "analytical_2d_paraboloid",
            "subpixel_scale": 32,
            "keyframe_dist": 2.5,
            "squal_threshold": 5.0,
            "v_alpha": 0.25
        },
        "tracking_metrics": {
            "total_frames_processed": len(xs),
            "active_moving_frames": int(np.sum(moving_mask)),
            "active_moving_ratio": float(np.mean(moving_mask)),
            "closure_error_px": round(closure_error_px, 2),
            "estimated_circle_radius_px": round(r_est, 2),
            "radial_rmse_px": round(radial_rmse_px, 2),
            "global_traj_rmse_px": round(global_traj_rmse_px, 2),
            "rmse_ref_type": rmse_ref_type,
            "aspect_ratio_xy": round(aspect_ratio_xy, 3),
            "circle_center_px": [round(xc, 2), round(yc, 2)],
            "final_position_px": [round(float(xs[-1]), 2), round(float(ys[-1]), 2)],
            "mean_speed_px_per_frame": round(float(np.mean(speeds[moving_mask])) if np.any(moving_mask) else 0.0, 3),
            "max_speed_px_per_frame": round(float(np.max(speeds)), 3),
            "mean_squal": round(float(np.mean(squals)), 1)
        },
        "stage_a_diagnostics": diag
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_data, f, indent=2, ensure_ascii=False)
    print(f"[完成] 已成功記錄結構化元數據日誌: {meta_path}")

    # 6. 繪製並儲存軌跡圖 (含最佳擬合理想正圓參考虛線)
    plot_path = None
    if args.plot and len(xs) > 1:
        plot_path = os.path.join(out_dir, "Track_B_NormalMode_plot.png")
        plt.figure(figsize=(8, 8))
        if matched_gth is not None:
            gx, gy, _ = matched_gth
            plt.plot(gx, gy, color='#10b981', linestyle=':', linewidth=1.6, alpha=0.85, label='Dataset GTH')
        if r_est > 1.0:
            th = np.linspace(0, 2 * np.pi, 360)
            plt.plot(xc + r_est * np.cos(th), yc + r_est * np.sin(th), color='#94a3b8', linestyle='--', linewidth=1.2, label=f'Fitted Circle (R={r_est:.1f}px)')
        gn_tag = f", gn={gain_norm_mode}" if gain_norm_mode != 'none' else ""
        plt.plot(xs, ys, color='#2563eb', linewidth=1.6, label=f'Normal Mode ({filter_type}, fpn={fpn_mode}, r={search_radius}, {args.predictor}{gn_tag})')
        plt.scatter(xs[0], ys[0], color='#10b981', s=80, marker='o', edgecolors='black', label='Start (0, 0)', zorder=5)
        plt.scatter(xs[-1], ys[-1], color='#ef4444', s=80, marker='s', edgecolors='black', label=f'End ({xs[-1]:.1f}, {ys[-1]:.1f})', zorder=5)
        plt.title(f"ToG Normal Mode: {os.path.basename(raw_path)}\nFilter: {filter_type.upper()} (LineBuf={hw_specs['line_buffers']}, FPN={fpn_mode.upper()}:{fpn_mem_bytes}B, r={search_radius} [{search_candidates}pt], {args.predictor}{gn_tag})\nE_close={closure_error_px:.2f}px | Global_RMSE={global_traj_rmse_px:.2f}px | RadRMSE={radial_rmse_px:.2f}px | X/Y={aspect_ratio_xy:.3f}", fontsize=10, fontweight='bold')
        plt.xlabel("Accumulated X (pixels)", fontsize=11)
        plt.ylabel("Accumulated Y (pixels)", fontsize=11)
        plt.grid(True, linestyle='--', alpha=0.5)
        plt.axis('equal')
        plt.legend(loc='best')
        plt.tight_layout()
        plt.savefig(plot_path, dpi=150)
        plt.close()
        print(f"[完成] 已生成軌跡視覺化圖: {plot_path}")

    # 7. 同步 latest/ 目錄並追加版本索引至 runs_index.csv (不覆蓋任何歷史 run)
    if latest_dir:
        os.makedirs(latest_dir, exist_ok=True)
        for src_p, dst_name in [(csv_target, "Track_B_NormalMode.csv"), (meta_path, "run_meta.json"), (plot_path, "Track_B_NormalMode_plot.png")]:
            if src_p and os.path.exists(src_p):
                try:
                    shutil.copy2(src_p, os.path.join(latest_dir, dst_name))
                except PermissionError:
                    pass
    if index_csv:
        new_file = not os.path.exists(index_csv) or os.path.getsize(index_csv) == 0
        try:
            with open(index_csv, "a", encoding="utf-8-sig", newline="") as f_idx:
                writer = csv.writer(f_idx)
                if new_file:
                    writer.writerow([
                        "timestamp", "run_id", "filter", "fpn_mode", "gain_norm",
                        "search_radius", "zncc_coast_th", "closure_error_px", "radius_px",
                        "radial_rmse_px", "global_traj_rmse_px", "rmse_ref_type", "aspect_xy", "output_dir"
                    ])
                writer.writerow([
                    now_iso, run_id, filter_type, fpn_mode, gain_norm_mode,
                    search_radius, "" if args.zncc_coast_th is None else args.zncc_coast_th,
                    f"{closure_error_px:.2f}", f"{r_est:.2f}", f"{radial_rmse_px:.2f}",
                    f"{global_traj_rmse_px:.2f}", rmse_ref_type, f"{aspect_ratio_xy:.3f}", out_dir
                ])
        except PermissionError:
            pass

    # 終端機統計摘要
    print("\n" + "=" * 75)
    print("  Normal Mode 追蹤成果統計")
    print("=" * 75)
    print(f"  總處理幀數        : {len(xs)} 幀")
    print(f"  運動活躍幀數      : {np.sum(moving_mask)} 幀 ({np.mean(moving_mask)*100:.1f}%)")
    print(f"  回原點閉環誤差    : {closure_error_px:.2f} px")
    print(f"  全域軌跡誤差均方根: {global_traj_rmse_px:.2f} px (基準: {rmse_ref_type})")
    print(f"  圓度誤差均方根    : {radial_rmse_px:.2f} px (長寬比 X/Y={aspect_ratio_xy:.3f})")
    print(f"  擬合圓半徑        : {r_est:.2f} px (中心: [{xc:.1f}, {yc:.1f}])")
    print(f"  最終累積位移 (X)  : {xs[-1]:+.2f} px")
    print(f"  最終累積位移 (Y)  : {ys[-1]:+.2f} px")
    print(f"  X 座標極值範圍    : [{np.min(xs):.2f}, {np.max(xs):.2f}] px (跨距: {np.ptp(xs):.2f} px)")
    print(f"  Y 座標極值範圍    : [{np.min(ys):.2f}, {np.max(ys):.2f}] px (跨距: {np.ptp(ys):.2f} px)")
    print(f"  平均單幀運動速度  : {np.mean(speeds[moving_mask]) if np.any(moving_mask) else 0.0:.3f} px/frame")
    print(f"  最大單幀運動速度  : {np.max(speeds):.3f} px/frame")
    print(f"  平均特徵品質 SQUAL: {np.mean(squals):.1f}")
    print(f"  Stage A 預測統計  : PP中心命中={diag['pp_center_hit_pct']:.1f}%, 觸邊={diag['border_hit_pct']:.1f}% (重取樣={diag['recenter_frames']}), TL滑行={diag['coast_ratio_pct']:.1f}%, Ref換幀={diag['keyframe_updates']}, 突波鉗位={diag['spike_clamped_frames']}")
    print(f"  相關搜尋估算算力  : r={search_radius} ({search_candidates} 點), CorrMAC ~ {corr_macs_per_frame}/frame")
    print("=" * 75 + "\n")

if __name__ == '__main__':
    main()
