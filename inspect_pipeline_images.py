r"""
========================================================================================
ToG Tracker: Multi-Stage Pipeline Image Inspector & CSBC Visualizer
========================================================================================
本工具用於檢查光學滑鼠演算法在每一個處理階段的真實影像樣貌，以及呈現 CSBC 如何於互相關域扣除靜態背景偏置：
  1. 01_original.raw                      : 原始感測器 RAW 影像
  2. 02_after_fpn.raw                     : 扣除時間中位數 FPN 模型後的純淨微塵 RAW (線性映射至 uint8, 零點=128)
  3. 03_after_fpn_and_bp.raw              : 先減 FPN 再經 Band-Pass 濾波的 RAW (如 3x7 / 5x5 / 7x7)
  4. 04_nofpn_bp_for_csbc.raw             : 無 FPN 直接過 Band-Pass (CSBC 送入相關器的影像！仍保有靜態紋理)
  5. 05_after_optnavi_box2_sad3_corner.raw: 經過對照組 OptNavi (box2 + saddle3 + 4-Corner) 的特徵圖 RAW
  6. pipeline_stages_gallery.png          : 4 個時間點 x 5 大處理階段高清視覺化對照圖
  7. csbc_correlation_visualization.png   : 原始互相關 P_raw vs 82B 靜態偏置表 B_S vs 扣除後 P_clean 熱力圖與剖面

使用範例：
  python inspect_pipeline_images.py "C:\Data\testPattern玻璃\Circle_R0.1inch_IPS0.005_Glass\POS1-1.raw" --export-raw
========================================================================================
"""

import os
import sys
import json
import argparse
import numpy as np
import matplotlib.pyplot as plt

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from step1_anchor_and_textureless_analyzer import OpniRawIO
from run_normal_mode import find_raw_file, compute_temporal_median_fpn
from tracker_core import NormalModeTracker


def map_float_to_uint8_frames(float_frames, clip_sigma=4.5):
    """使用統一的固定增益將浮點特徵序列線性映射為 uint8 (128 代表 0.0)，保留真實幀間強度比例"""
    arr = np.asarray(float_frames, dtype=np.float32)
    std_val = float(np.std(arr))
    max_abs = max(1e-3, min(float(np.max(np.abs(arr))), clip_sigma * std_val + 1e-3))
    gain = 120.0 / max_abs
    mapped = np.clip(np.round(128.0 + arr * gain), 0, 255).astype(np.uint8)
    return mapped, gain


def main():
    parser = argparse.ArgumentParser(description="ToG 多階段影像檢視與 CSBC 互相關原理視覺化工具")
    parser.add_argument("raw", help="RAW 檔案名稱或完整路徑")
    parser.add_argument("--frames", nargs="+", type=int, default=None, help="Gallery 展示的幀號列表 (預設自動挑選 4 幀)")
    parser.add_argument("--filter", default="bandpass3x7",
                        choices=["bandpass3x7", "bandpass3x7_r0", "bandpass5x7", "bandpass7", "bandpass5", "bandpass5_sep"],
                        help="Band-Pass 濾波器類型 (預設: bandpass3x7)")
    parser.add_argument("--export-raw", action="store_true", default=True, help="是否匯出 5 階段標準 OPNI .raw 檔 (預設開啟)")
    parser.add_argument("--no-export-raw", dest="export_raw", action="store_false", help="僅產生 PNG 圖，不匯出 .raw")
    parser.add_argument("--max-raw-frames", type=int, default=300, help="匯出 .raw 的最大幀數 (預設: 300)")
    parser.add_argument("--out", "-o", default=None, help="輸出目錄 (預設為 pipeline_inspect_output/<seq_name>)")
    args = parser.parse_args()

    raw_path = find_raw_file(args.raw)
    if not raw_path or not os.path.exists(raw_path):
        print(f"[錯誤] 找不到 RAW 檔案: {args.raw}")
        sys.exit(1)

    seq_name = os.path.splitext(os.path.basename(raw_path))[0]
    out_dir = os.path.abspath(args.out) if args.out else os.path.join(SCRIPT_DIR, "pipeline_inspect_output", seq_name)
    os.makedirs(out_dir, exist_ok=True)

    header = OpniRawIO.read_header(raw_path)
    w, h, total_frames, fps = header['width'], header['height'], header['num_frames'], header['fps']
    print(f"[Inspect] 目標 RAW: {raw_path} ({w}x{h}, {total_frames} 幀, {fps} FPS)")
    print(f"[Inspect] 輸出目錄: {out_dir}")

    # 1. 提取 FPN 參考圖 (僅供比較用)
    fpn_model = compute_temporal_median_fpn(raw_path, header, n_samples=200)

    # 2. 準備四種濾波組態的 NormalModeTracker 前處理管線
    trk_fpn_bp = NormalModeTracker(fpn_model=fpn_model, use_bandpass_filter=True, bandpass_type=args.filter)
    trk_nofpn_bp = NormalModeTracker(fpn_model=None, use_bandpass_filter=True, bandpass_type=args.filter)
    trk_optnavi = NormalModeTracker(fpn_model=None, use_bandpass_filter=True, bandpass_type='optnavi_ctrl')

    # 3. 匯出 5 階段 OPNI .raw 檔
    export_scale_info = {
        "source_raw": raw_path,
        "filter_type": args.filter,
        "zero_level_uint8": 128,
        "files": {}
    }
    if args.export_raw:
        n_exp = min(total_frames, args.max_raw_frames)
        s1_list, s2_list, s3_list, s4_list, s5_list = [], [], [], [], []
        with open(raw_path, 'rb') as f:
            for fi in range(n_exp):
                frm = OpniRawIO.load_frame(f, header, fi)
                if frm is None:
                    break
                f32 = frm.astype(np.float32)
                s1_list.append(frm)
                s2_list.append(f32 - fpn_model)
                s3_list.append(trk_fpn_bp.preprocess_frame(f32))
                s4_list.append(trk_nofpn_bp.preprocess_frame(f32))
                s5_list.append(trk_optnavi.preprocess_frame(f32))

        stages = [
            ("01_original.raw", np.asarray(s1_list), True, "原始 RAW 影像"),
            ("02_after_fpn.raw", np.asarray(s2_list), False, "扣除 FPN 模型後的純淨微塵 RAW"),
            ("03_after_fpn_and_bp.raw", np.asarray(s3_list), False, f"扣除 FPN + {args.filter} 帶通特徵 RAW"),
            ("04_nofpn_bp_for_csbc.raw", np.asarray(s4_list), False, f"無 FPN 直接過 {args.filter} (CSBC 實際輸入特徵圖)"),
            ("05_after_optnavi_box2_sad3_corner.raw", np.asarray(s5_list), False, "對照組 OptNavi (box2 + saddle3 + 4-Corner) 特徵圖 RAW"),
        ]
        for fname, arr, is_orig, desc in stages:
            fpath = os.path.join(out_dir, fname)
            sh, sw = arr.shape[1], arr.shape[2]
            if is_orig:
                OpniRawIO.write_raw(fpath, arr, sw, sh, fps=fps, bit_depth=8)
                gain = 1.0
            else:
                u8_arr, gain = map_float_to_uint8_frames(arr)
                OpniRawIO.write_raw(fpath, u8_arr, sw, sh, fps=fps, bit_depth=8)
            export_scale_info["files"][fname] = {
                "description": desc,
                "width": int(sw),
                "height": int(sh),
                "num_frames": int(arr.shape[0]),
                "scale_gain_to_uint8": round(float(gain), 4),
                "float_min": round(float(np.min(arr)), 3),
                "float_max": round(float(np.max(arr)), 3),
                "float_std": round(float(np.std(arr)), 3)
            }

        with open(os.path.join(out_dir, "export_scale_info.json"), "w", encoding="utf-8") as f:
            json.dump(export_scale_info, f, indent=2, ensure_ascii=False)

    # 4. 繪製 pipeline_stages_gallery.png (4 幀 x 5 階段高清圖庫)
    if args.frames is not None:
        sel_frames = [max(0, min(total_frames - 1, fi)) for fi in args.frames]
    else:
        sel_frames = [
            0,
            max(1, total_frames // 4),
            max(2, total_frames // 2),
            max(3, (3 * total_frames) // 4),
        ]

    stage_titles = [
        "1. Original RAW\n(Shading + FPN + Dust)",
        "2. Raw - Full FPN\n(Pure Dust Signal)",
        f"3. (Raw-FPN)*{args.filter}\n(Clean Features)",
        f"4. No-FPN*{args.filter}\n(Input to CSBC)",
        "5. OptNavi Ctrl\n(box2+saddle3+corner)"
    ]
    n_rows = len(sel_frames)
    fig, axes = plt.subplots(n_rows, 5, figsize=(19, 3.6 * n_rows))
    if n_rows == 1:
        axes = axes[np.newaxis, :]

    with open(raw_path, 'rb') as f:
        for r_idx, fi in enumerate(sel_frames):
            frm = OpniRawIO.load_frame(f, header, fi).astype(np.float32)
            imgs = [
                frm,
                frm - fpn_model,
                trk_fpn_bp.preprocess_frame(frm),
                trk_nofpn_bp.preprocess_frame(frm),
                trk_optnavi.preprocess_frame(frm),
            ]
            for c_idx, img in enumerate(imgs):
                ax = axes[r_idx, c_idx]
                if c_idx == 0:
                    im = ax.imshow(img, cmap='gray', vmin=0, vmax=255)
                else:
                    lim = max(1e-3, float(np.percentile(np.abs(img), 99.0)) * 1.2)
                    im = ax.imshow(img, cmap='RdBu_r', vmin=-lim, vmax=lim)
                plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
                if r_idx == 0:
                    ax.set_title(stage_titles[c_idx], fontsize=10.5, fontweight='bold')
                if c_idx == 0:
                    ax.set_ylabel(f"Frame {fi}\n({img.shape[0]}x{img.shape[1]})", fontsize=10, fontweight='bold')
                ax.set_xlabel(f"{img.shape[0]}x{img.shape[1]} | std={np.std(img):.2f}", fontsize=8.5)
                ax.set_xticks([]); ax.set_yticks([])

    fig.suptitle(f"Multi-Stage Processing Gallery: {seq_name} (Filter={args.filter})", fontsize=14, fontweight='bold', y=0.995)
    plt.tight_layout(rect=[0, 0, 1, 0.97])
    gallery_path = os.path.join(out_dir, "pipeline_stages_gallery.png")
    plt.savefig(gallery_path, dpi=150)
    plt.close()
    print(f"[完成] 已輸出多階段圖庫: {gallery_path}")

    # 5. CSBC 原理視覺化圖 csbc_correlation_visualization.png
    max_lag = 4
    with open(raw_path, 'rb') as f_cal:
        B_S = NormalModeTracker.calibrate_csbc_table(
            raw_loader_fn=lambda idx: OpniRawIO.load_frame(f_cal, header, idx),
            total_frames=total_frames,
            preprocess_fn=trk_nofpn_bp.preprocess_frame,
            max_lag=max_lag
        )

    # 選一對相距約 2 像素真實位移的參考/當前幀，讓 P_raw 的靜態 (0,0) 鎖死與 P_clean 的真實峰值同時鮮明對比
    ref_idx = min(total_frames - 60, max(50, total_frames // 10))
    # 自動尋找與 ref_idx 互相關峰明確偏離 (0,0) ~2px 的 cur_idx
    best_pair = (min(total_frames - 1, ref_idx + 12), None, 0)
    with open(raw_path, 'rb') as f:
        f_ref = trk_nofpn_bp.preprocess_frame(OpniRawIO.load_frame(f, header, ref_idx).astype(np.float32))
        h_f, w_f = f_ref.shape
        for step_f in [6, 12, 24, 40, 60]:
            ci = min(total_frames - 1, ref_idx + step_f)
            fc = trk_nofpn_bp.preprocess_frame(OpniRawIO.load_frame(f, header, ci).astype(np.float32))
            p_r = np.zeros((2 * max_lag + 1, 2 * max_lag + 1), dtype=np.float32)
            for dy in range(-max_lag, max_lag + 1):
                for dx in range(-max_lag, max_lag + 1):
                    y1, y2 = max(0, -dy), min(h_f, h_f - dy)
                    x1, x2 = max(0, -dx), min(w_f, w_f - dx)
                    sR = f_ref[y1:y2, x1:x2]
                    sC = fc[y1+dy:y2+dy, x1+dx:x2+dx]
                    p_r[dy + max_lag, dx + max_lag] = float(np.mean((sR - np.mean(sR)) * (sC - np.mean(sC))))
            p_c = p_r - B_S
            vy, ux = np.unravel_index(np.argmax(p_c), p_c.shape)
            shift_dist = abs(ux - max_lag) + abs(vy - max_lag)
            if shift_dist >= 2 or best_pair[1] is None:
                cur_idx, P_raw, P_clean = ci, p_r, p_c
                if shift_dist >= 2:
                    break
            best_pair = (ci, p_r, p_c)
        else:
            cur_idx, P_raw, P_clean = best_pair

    fig, axes = plt.subplots(2, 3, figsize=(15, 9), gridspec_kw={'height_ratios': [1.3, 1.0]})
    ticks = np.arange(-max_lag, max_lag + 1)
    vmax_all = max(float(np.max(np.abs(P_raw))), float(np.max(np.abs(B_S))), float(np.max(np.abs(P_clean))))

    maps = [
        (P_raw,   f"1. Raw Covariance P_raw (F{ref_idx} vs F{cur_idx})\nPeak trapped near (0,0) by static FPN!"),
        (B_S,     "2. CSBC Static Bias Table B_S (82 Bytes)\nCalibration of static FPN self-correlation"),
        (P_clean, "3. Clean Surface P_clean = P_raw - B_S\nStatic hill removed! True dust peak emerges")
    ]
    for col, (mat, ttl) in enumerate(maps):
        ax = axes[0, col]
        im = ax.imshow(mat, cmap='viridis', vmin=float(np.min(mat)), vmax=float(np.max(mat)))
        plt.colorbar(im, ax=ax, fraction=0.046, pad=0.04)
        vy, ux = np.unravel_index(np.argmax(mat), mat.shape)
        ax.scatter(ux, vy, color='red', marker='*', s=140, edgecolors='white', label=f'Peak ({ticks[ux]:+d}, {ticks[vy]:+d})')
        ax.set_xticks(range(len(ticks))); ax.set_xticklabels(ticks)
        ax.set_yticks(range(len(ticks))); ax.set_yticklabels(ticks)
        ax.set_xlabel("dx (px)"); ax.set_ylabel("dy (px)")
        ax.set_title(ttl, fontsize=10.5, fontweight='bold')
        ax.legend(loc='upper right', fontsize=8.5)

        ax_p = axes[1, col]
        mid = max_lag
        ax_p.plot(ticks, mat[mid, :], 'o-', lw=2, color='#2563eb', label='Horizontal slice (dy=0)')
        ax_p.plot(ticks, mat[:, mid], 's--', lw=1.6, color='#059669', label='Vertical slice (dx=0)')
        ax_p.axhline(0, color='gray', lw=0.8, ls=':')
        ax_p.set_title("Center Cross-Section Profiles", fontsize=9.5, fontweight='bold')
        ax_p.set_xlabel("Lag Offset (pixels)")
        ax_p.set_ylabel("Covariance")
        ax_p.grid(True, alpha=0.4)
        ax_p.legend(loc='best', fontsize=8)

    fig.suptitle(f"CSBC Working Principle Visualization: {seq_name} ({args.filter})", fontsize=13.5, fontweight='bold', y=0.98)
    plt.tight_layout(rect=[0, 0, 1, 0.94])
    csbc_path = os.path.join(out_dir, "csbc_correlation_visualization.png")
    plt.savefig(csbc_path, dpi=150)
    plt.close()
    print(f"[完成] 已輸出 CSBC 原理視覺化圖: {csbc_path}")


if __name__ == '__main__':
    main()
