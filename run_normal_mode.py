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
    """搜尋現有的高品質 FPN 模型"""
    seq_name = os.path.splitext(os.path.basename(raw_path))[0]
    parent_dir = os.path.basename(os.path.dirname(os.path.abspath(raw_path)))
    if parent_dir.upper() in ['RAW', 'CSV', 'PNG']:
        parent_dir = os.path.basename(os.path.dirname(os.path.dirname(os.path.abspath(raw_path))))

    candidate_fpns = [
        os.path.join(SCRIPT_DIR, "experiments", parent_dir, seq_name, "fpn_model.raw"),
        os.path.join(SCRIPT_DIR, "step1_output", "fpn_model.raw"),
        os.path.join(os.path.dirname(raw_path), "fpn_model.raw"),
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

def main():
    parser = argparse.ArgumentParser(description="ToG Tracker 純 Normal Mode 自主追蹤工具")
    parser.add_argument("raw", help="RAW 檔案名稱或完整路徑 (例如: POS1-1.raw)")
    parser.add_argument("--out", "-o", default=None, help="輸出目錄 (預設為當前目錄或 RAW 同層)")
    parser.add_argument("--fpn", default=None, help="指定 FPN 模型路徑 (.raw 檔，預設自動提取)")
    parser.add_argument("--radius", "-r", type=int, default=None, help="搜尋半徑 (預設: 36x36 為 1, >=64x64 為 2)")
    parser.add_argument("--plot", action="store_true", default=True, help="是否生成 2D 軌跡折線圖 (預設開啟)")
    args = parser.parse_args()

    raw_path = find_raw_file(args.raw)
    if not raw_path or not os.path.exists(raw_path):
        print(f"[錯誤] 找不到 RAW 檔案: {args.raw}")
        sys.exit(1)

    # 決定輸出目錄
    if args.out:
        out_dir = os.path.abspath(args.out)
    else:
        seq_name = os.path.splitext(os.path.basename(raw_path))[0]
        out_dir = os.path.join(SCRIPT_DIR, "normal_mode_output", seq_name)
    os.makedirs(out_dir, exist_ok=True)

    header = OpniRawIO.read_header(raw_path)
    w, h = header['width'], header['height']
    total_frames = header['num_frames']
    fps = header['fps']
    bit_depth = header.get('bit_depth', 8)

    print("\n" + "=" * 70)
    print("  ToG Tracker: Normal Mode 獨立追蹤器")
    print(f"  目標檔案: {raw_path}")
    print(f"  影像規格: {w}x{h}, {bit_depth}-bit, {total_frames} 幀, {fps} FPS")
    print(f"  輸出路徑: {out_dir}")
    print("=" * 70)

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

    print(f"[2/3] 初始化 NormalModeTracker (搜尋半徑: {search_radius}, 窗口: {2*search_radius+1}x{2*search_radius+1})...")
    tracker = NormalModeTracker(
        search_radius=search_radius,
        subpixel_scale=16,
        keyframe_dist=1.5,
        squal_th=5.0,
        v_alpha=0.25,
        fpn_model=fpn_model,
        use_corner_filter=use_corner_filter
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

    # 4. 儲存標準 CSV 輸出
    csv_path = os.path.join(out_dir, "Track_B_NormalMode.csv")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write("frame,dx,dy,x,y,squal\n")
        for i in range(len(records)):
            f.write(f"{int(frames_idx[i])},{dxs[i]:.4f},{dys[i]:.4f},{xs[i]:.4f},{ys[i]:.4f},{squals[i]:.2f}\n")
    print(f"\n[完成] 已成功匯出標準 CSV 軌跡: {csv_path}")

    # 5. 繪製並儲存軌跡圖 (若啟用)
    if args.plot and len(xs) > 1:
        plot_path = os.path.join(out_dir, "Track_B_NormalMode_plot.png")
        plt.figure(figsize=(8, 8))
        plt.plot(xs, ys, color='#2563eb', linewidth=1.5, label='Track B: Normal Mode')
        plt.scatter(xs[0], ys[0], color='#10b981', s=80, marker='o', edgecolors='black', label='Start (0, 0)', zorder=5)
        plt.scatter(xs[-1], ys[-1], color='#ef4444', s=80, marker='s', edgecolors='black', label=f'End ({xs[-1]:.1f}, {ys[-1]:.1f})', zorder=5)
        plt.title(f"Normal Mode Trajectory: {os.path.basename(raw_path)} ({len(xs)} frames)", fontsize=13, fontweight='bold')
        plt.xlabel("Accumulated X (pixels)", fontsize=11)
        plt.ylabel("Accumulated Y (pixels)", fontsize=11)
        plt.grid(True, linestyle='--', alpha=0.5)
        plt.axis('equal')
        plt.legend(loc='best')
        plt.tight_layout()
        plt.savefig(plot_path, dpi=150)
        plt.close()
        print(f"[完成] 已生成軌跡視覺化圖: {plot_path}")

    # 統計摘要
    speeds = np.sqrt(dxs**2 + dys**2)
    moving_mask = speeds > 0.01
    print("\n" + "=" * 70)
    print("  Normal Mode 追蹤成果統計")
    print("=" * 70)
    print(f"  總處理幀數      : {len(xs)} 幀")
    print(f"  運動活躍幀數    : {np.sum(moving_mask)} 幀 ({np.mean(moving_mask)*100:.1f}%)")
    print(f"  最終累積位移 (X): {xs[-1]:+.2f} px")
    print(f"  最終累積位移 (Y): {ys[-1]:+.2f} px")
    print(f"  X 座標極值範圍  : [{np.min(xs):.2f}, {np.max(xs):.2f}] px (跨距: {np.ptp(xs):.2f} px)")
    print(f"  Y 座標極值範圍  : [{np.min(ys):.2f}, {np.max(ys):.2f}] px (跨距: {np.ptp(ys):.2f} px)")
    print(f"  平均單幀運動速度: {np.mean(speeds[moving_mask]) if np.any(moving_mask) else 0.0:.3f} px/frame")
    print(f"  最大單幀運動速度: {np.max(speeds):.3f} px/frame")
    print(f"  平均特徵品質SQUAL: {np.mean(squals):.1f}")
    print("=" * 70 + "\n")

if __name__ == '__main__':
    main()
