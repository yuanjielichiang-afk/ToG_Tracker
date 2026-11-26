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
import sys
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

def get_smart_output_dir(raw_path, base_out=None, tag=None):
    """
    智能階層輸出目錄生成器 (Smart Hierarchical Output Directory Resolver)
    
    徹底解決 Clean Glass 與 Dirty Glass (或不同測試條件) 同檔名導致的覆蓋衝突。
    自動擷取路徑中的關鍵鑑別目錄（如 Clean Glass / Dirty Glass、半徑、速度等），
    並過濾掉無鑑別度的中介容器層（如 Image, RAW, Trace, CSV）。
    
    範例：
      輸入: C:\\Data\\testPattern玻璃\\20260529\\Circle\\Clean Glass\\Radius 0.5 inch\\P3950_Circle_0.5inch_1rev_0.1ips\\Image\\P3950_Circle_0.5inch_1rev_0.1ips-1.raw
      輸出: normal_mode_output/20260529/Circle/Clean_Glass/Radius_0.5_inch/P3950_Circle_0.5inch_1rev_0.1ips/P3950_Circle_0.5inch_1rev_0.1ips-1/
    """
    if base_out:
        return os.path.abspath(base_out)

    abs_path = os.path.abspath(raw_path)
    seq_name = os.path.splitext(os.path.basename(abs_path))[0]
    
    # 搜尋已知的資料集根目錄標記
    known_roots = ["testPattern玻璃", "DATA_FAMS", "ToG_Tracker", "Data"]
    parts = os.path.normpath(abs_path).split(os.sep)
    lower_parts = [p.lower() for p in parts]
    
    matched_parts = None
    for r in known_roots:
        if r.lower() in lower_parts:
            idx = lower_parts.index(r.lower())
            matched_parts = parts[idx + 1:-1] # 排除根與檔名
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

    folder_name = seq_name
    if tag:
        folder_name += f"_{tag}"

    out_dir = os.path.join(SCRIPT_DIR, "normal_mode_output", subpath, folder_name)
    return os.path.abspath(out_dir)

def main():
    parser = argparse.ArgumentParser(description="ToG Tracker 純 Normal Mode 自主追蹤工具")
    parser.add_argument("raw", help="RAW 檔案名稱或完整路徑 (例如: POS1-1.raw)")
    parser.add_argument("--out", "-o", default=None, help="輸出目錄 (預設為智能階層目錄)")
    parser.add_argument("--tag", default=None, help="輸出目錄自訂標籤後綴 (例如: v1, bandpass5)")
    parser.add_argument("--fpn", default=None, help="指定 FPN 模型路徑 (.raw 檔，預設自動提取)")
    parser.add_argument("--radius", "-r", type=int, default=None, help="搜尋半徑 (預設: 36x36 為 1, >=64x64 為 2)")
    parser.add_argument("--plot", action="store_true", default=True, help="是否生成 2D 軌跡折線圖 (預設開啟)")
    parser.add_argument("--filter", default="bandpass7", 
                        choices=["bandpass7", "bandpass5", "bandpass5_sep", "bandpass9", "none"],
                        help="前處理濾波器類型 (預設: bandpass7 原廠 7x7 基準，或選 bandpass5 晶片化簡版)")
    parser.add_argument("--bandpass", action="store_true", default=True, help="啟用帶通濾波器前處理 (相容舊參數)")
    parser.add_argument("--no-bandpass", dest="bandpass", action="store_false", help="關閉帶通濾波器")
    args = parser.parse_args()

    raw_path = find_raw_file(args.raw)
    if not raw_path or not os.path.exists(raw_path):
        print(f"[錯誤] 找不到 RAW 檔案: {args.raw}")
        sys.exit(1)

    # 決定濾波器類型
    filter_type = args.filter.lower()
    if not args.bandpass:
        filter_type = 'none'

    # 決定輸出目錄 (智慧階層解析，永不衝突)
    out_dir = get_smart_output_dir(raw_path, args.out, args.tag)
    os.makedirs(out_dir, exist_ok=True)

    header = OpniRawIO.read_header(raw_path)
    w, h = header['width'], header['height']
    total_frames = header['num_frames']
    fps = header['fps']
    bit_depth = header.get('bit_depth', 8)
    file_size_bytes = os.path.getsize(raw_path)
    file_mtime = datetime.fromtimestamp(os.path.getmtime(raw_path)).isoformat()
    now_iso = datetime.now().isoformat()

    # 硬體開銷與算力評估 (以原廠 7x7 基準計算節約幅度)
    hw_specs = {
        'bandpass7': {'line_buffers': 7, 'macs_per_pix': 49, 'sram_savings_pct': 0.0, 'mac_savings_pct': 0.0, 'desc': '7x7 同心零均值帶通核 (原廠基準, 總和=0.0)'},
        'bandpass5': {'line_buffers': 5, 'macs_per_pix': 25, 'sram_savings_pct': 28.6, 'mac_savings_pct': 49.0, 'desc': '5x5 同心零均值帶通核 (化簡版, 總和=0.0)'},
        'bandpass5_sep': {'line_buffers': 5, 'macs_per_pix': 10, 'sram_savings_pct': 28.6, 'mac_savings_pct': 79.6, 'desc': '5x5 可分離 1D+1D 卷積 (極簡低功耗)'},
        'bandpass9': {'line_buffers': 9, 'macs_per_pix': 81, 'sram_savings_pct': -28.6, 'mac_savings_pct': -65.3, 'desc': '舊版 9x9 擴展帶通核'},
        'none': {'line_buffers': 0, 'macs_per_pix': 0, 'sram_savings_pct': 100.0, 'mac_savings_pct': 100.0, 'desc': '無濾波器'}
    }[filter_type]

    print("\n" + "=" * 75)
    print("  ToG Tracker: Normal Mode 獨立追蹤器")
    print(f"  原始 RAW 檔案: {raw_path}")
    print(f"  影像規格      : {w}x{h}, {bit_depth}-bit, {total_frames} 幀, {fps} FPS")
    print(f"  前處理濾波器  : {filter_type.upper()} ({hw_specs['desc']})")
    print(f"  硬體資源消耗  : Line Buffer={hw_specs['line_buffers']} 條 (SRAM減省 {hw_specs['sram_savings_pct']:.1f}%), MAC/Pix={hw_specs['macs_per_pix']} (算力減省 {hw_specs['mac_savings_pct']:.1f}%)")
    print(f"  智能輸出目錄  : {out_dir}")
    print("=" * 75)

    # 1. 準備 FPN 模型
    if args.fpn and os.path.exists(args.fpn):
        print(f"[1/3] 載入自訂 FPN 模型: {args.fpn}")
        fpn_hdr = OpniRawIO.read_header(args.fpn)
        with open(args.fpn, 'rb') as f:
            fpn_model = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)
    else:
        existing_fpn = find_existing_fpn(raw_path)
        if existing_fpn:
            print(f"[1/3] 載入已校準的 FPN 模型: {existing_fpn}")
            fpn_hdr = OpniRawIO.read_header(existing_fpn)
            with open(existing_fpn, 'rb') as f:
                fpn_model = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)
        else:
            print("[1/3] 使用全序列時間中位數 (Temporal Median) 自適應提取純淨 FPN 底噪...")
            fpn_model = compute_temporal_median_fpn(raw_path, header)

    # 2. 確定追蹤參數
    if args.radius is not None:
        search_radius = args.radius
    else:
        search_radius = 2 if (w >= 64 or '4ips' in raw_path.lower()) else 1

    use_corner_filter = 'mousepad' in raw_path.lower()
    if use_corner_filter:
        print("  [提示] 偵測到滑鼠墊紋理，啟用 OptNavi 4-Corner 抗鋸齒濾波器")

    use_bandpass = (filter_type != 'none')
    print(f"[2/3] 初始化 NormalModeTracker (搜尋半徑: {search_radius}, 次像素解算: 解析解 2D 橢圓拋物面)...")
    tracker = NormalModeTracker(
        search_radius=search_radius,
        subpixel_scale=32,
        keyframe_dist=2.5,
        squal_th=5.0,
        v_alpha=0.25,
        fpn_model=fpn_model,
        use_corner_filter=use_corner_filter,
        use_bandpass_filter=use_bandpass,
        bandpass_type=filter_type
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

    # 指標統計
    speeds = np.sqrt(dxs**2 + dys**2)
    moving_mask = speeds > 0.01
    closure_error_px = float(np.sqrt((xs[-1] - xs[0])**2 + (ys[-1] - ys[0])**2))
    xc, yc = float(np.mean(xs)), float(np.mean(ys))
    r_est = float(np.mean(np.sqrt((xs - xc)**2 + (ys - yc)**2)))

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
        f.write(f"# PROCESSED_AT: {now_iso}\n")
        f.write(f"# SPECS: {w}x{h}, {total_frames} frames, {fps} fps, {bit_depth}-bit\n")
        f.write(f"# FEATURE_GENERATION_PIPELINE:\n")
        f.write(f"#   1. FPN_Subtraction: Temporal Median (全序列時間中位數自適應底噪消除)\n")
        f.write(f"#   2. Bandpass_Filter: {filter_type.upper()} ({hw_specs['desc']})\n")
        f.write(f"#   3. Corner_Filter: {'Enabled (OptNavi 4-Corner 0/45/90/135 deg)' if use_corner_filter else 'Disabled'}\n")
        f.write(f"#   4. SQUAL_Evaluation: 3x3 Laplacian Kernel (閾值={tracker.squal_th})\n")
        f.write(f"#   5. Correlation_Surface: ZNCC 面積補償相關曲面比對 (搜尋半徑={search_radius})\n")
        f.write(f"#   6. Subpixel_Interpolation: 解析解 2D 橢圓拋物面峰值擬合 (解析度: 1/32 px)\n")
        f.write(f"# HARDWARE_RESOURCE: Line_Buffer={hw_specs['line_buffers']} 條 (SRAM節約 {hw_specs['sram_savings_pct']:.1f}%), 運算量={hw_specs['macs_per_pix']} MAC/pixel (算力節約 {hw_specs['mac_savings_pct']:.1f}%)\n")
        f.write(f"# TRACKING_METRICS: closure_error={closure_error_px:.2f}px, radius={r_est:.2f}px, mean_squal={np.mean(squals):.1f}\n")
        f.write("frame,dx,dy,x,y,squal\n")
        for i in range(len(records)):
            f.write(f"{int(frames_idx[i])},{dxs[i]:.4f},{dys[i]:.4f},{xs[i]:.4f},{ys[i]:.4f},{squals[i]:.2f}\n")
    print(f"\n[完成] 已成功匯出標準 CSV 軌跡 (含元數據標頭與 UTF-8 BOM): {csv_target}")
    print(f"\n[完成] 已成功匯出標準 CSV 軌跡 (含元數據標頭): {csv_path}")

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
            "filter_type": filter_type,
            "line_buffer_lines": hw_specs['line_buffers'],
            "macs_per_pixel": hw_specs['macs_per_pix'],
            "sram_savings_pct": hw_specs['sram_savings_pct'],
            "mac_savings_pct": hw_specs['mac_savings_pct'],
            "search_radius": search_radius,
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
            "circle_center_px": [round(xc, 2), round(yc, 2)],
            "final_position_px": [round(float(xs[-1]), 2), round(float(ys[-1]), 2)],
            "mean_speed_px_per_frame": round(float(np.mean(speeds[moving_mask])) if np.any(moving_mask) else 0.0, 3),
            "max_speed_px_per_frame": round(float(np.max(speeds)), 3),
            "mean_squal": round(float(np.mean(squals)), 1)
        }
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta_data, f, indent=2, ensure_ascii=False)
    print(f"[完成] 已成功記錄結構化元數據日誌: {meta_path}")

    # 6. 繪製並儲存軌跡圖 (若啟用)
    if args.plot and len(xs) > 1:
        plot_path = os.path.join(out_dir, "Track_B_NormalMode_plot.png")
        plt.figure(figsize=(8, 8))
        plt.plot(xs, ys, color='#2563eb', linewidth=1.5, label=f'Normal Mode ({filter_type})')
        plt.scatter(xs[0], ys[0], color='#10b981', s=80, marker='o', edgecolors='black', label='Start (0, 0)', zorder=5)
        plt.scatter(xs[-1], ys[-1], color='#ef4444', s=80, marker='s', edgecolors='black', label=f'End ({xs[-1]:.1f}, {ys[-1]:.1f})', zorder=5)
        plt.title(f"ToG Normal Mode: {os.path.basename(raw_path)}\nFilter: {filter_type.upper()} (LineBuf={hw_specs['line_buffers']}, MAC={hw_specs['macs_per_pix']}) | E_close={closure_error_px:.2f}px | R={r_est:.1f}px", fontsize=11, fontweight='bold')
        plt.xlabel("Accumulated X (pixels)", fontsize=11)
        plt.ylabel("Accumulated Y (pixels)", fontsize=11)
        plt.grid(True, linestyle='--', alpha=0.5)
        plt.axis('equal')
        plt.legend(loc='best')
        plt.tight_layout()
        plt.savefig(plot_path, dpi=150)
        plt.close()
        print(f"[完成] 已生成軌跡視覺化圖: {plot_path}")

    # 終端機統計摘要
    print("\n" + "=" * 75)
    print("  Normal Mode 追蹤成果統計")
    print("=" * 75)
    print(f"  總處理幀數      : {len(xs)} 幀")
    print(f"  運動活躍幀數    : {np.sum(moving_mask)} 幀 ({np.mean(moving_mask)*100:.1f}%)")
    print(f"  回原點閉環誤差  : {closure_error_px:.2f} px")
    print(f"  擬合圓半徑      : {r_est:.2f} px (中心: [{xc:.1f}, {yc:.1f}])")
    print(f"  最終累積位移 (X): {xs[-1]:+.2f} px")
    print(f"  最終累積位移 (Y): {ys[-1]:+.2f} px")
    print(f"  X 座標極值範圍  : [{np.min(xs):.2f}, {np.max(xs):.2f}] px (跨距: {np.ptp(xs):.2f} px)")
    print(f"  Y 座標極值範圍  : [{np.min(ys):.2f}, {np.max(ys):.2f}] px (跨距: {np.ptp(ys):.2f} px)")
    print(f"  平均單幀運動速度: {np.mean(speeds[moving_mask]) if np.any(moving_mask) else 0.0:.3f} px/frame")
    print(f"  最大單幀運動速度: {np.max(speeds):.3f} px/frame")
    print(f"  平均特徵品質SQUAL: {np.mean(squals):.1f}")
    print("=" * 75 + "\n")

if __name__ == '__main__':
    main()
