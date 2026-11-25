import os
import sys
import json
import math
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import convolve2d

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO
from tracker_core import NormalModeTracker, TeacherModeTracker, IdealModeTracker

def evaluate_three_modes(raw_path, gth_csv_path, fpn_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    print("=" * 70)
    print("STEP 3: Optical Mouse Algorithm Evaluation Engine (Three Modes)")
    print(f"Target RAW: {raw_path}")
    print(f"Ground Truth CSV: {gth_csv_path}")
    print("=" * 70)

    # Load Ground Truth CSV
    gth_data = np.genfromtxt(gth_csv_path, delimiter=',', names=True)
    gth_x = gth_data['x_px']
    gth_y = gth_data['y_px']
    gth_dx = gth_data['dx_px']
    gth_dy = gth_data['dy_px']
    num_frames = len(gth_data)

    # Load FPN model
    fpn_hdr = OpniRawIO.read_header(fpn_path)
    with open(fpn_path, 'rb') as f:
        fpn_model = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)

    raw_hdr = OpniRawIO.read_header(raw_path)
    use_corner_filter = 'mousepad' in raw_path.lower() or 'mousepad' in out_dir.lower()
    if use_corner_filter:
        print("[OptNavi] 啟用 4-Corner 角度紋理對齊濾波器 (打破滑鼠墊經緯編織 repeat pattern)")
        
    # 動態自適應搜尋半徑：對於高解析度或高速序列 (如 FAMS 64x64 或 4 ips)，使用 search_radius=2 (5x5 窗口)
    search_radius = 2 if (raw_hdr.get('width', 36) >= 64 or '4ips' in raw_path.lower()) else 1
    print(f"動態搜尋半徑: {search_radius} (窗口: {2*search_radius+1}x{2*search_radius+1})")
    print(f"Evaluating {num_frames} frames under: Normal, Teacher, and Ideal modes...")

    # Preload frames
    print("Pre-loading raw frames...")
    frames = []
    with open(raw_path, 'rb') as f:
        for fi in range(num_frames):
            frm = OpniRawIO.load_frame(f, raw_hdr, fi)
            frames.append(frm)

    results = {}

    # 1. Normal Mode
    print("\nRunning Mode: NORMAL...")
    normal_tracker = NormalModeTracker(search_radius=search_radius, subpixel_scale=16, 
                                       keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                                       fpn_model=fpn_model, use_corner_filter=use_corner_filter)
    normal_records = []
    for fi in range(num_frames):
        cur_f = frames[fi]
        res = normal_tracker.step(cur_f, fi)
        # Basin check against GTH (感測器陣列相對位移誤差 <= 1.0 px，排除歷史累積漂移干擾)
        ref_k = res['ref_idx']
        true_rel_x = gth_x[fi] - gth_x[ref_k]
        true_rel_y = gth_y[fi] - gth_y[ref_k]
        pred_rel_x = res.get('pred_rel_x', res['pred_x'] - gth_x[ref_k])
        pred_rel_y = res.get('pred_rel_y', res['pred_y'] - gth_y[ref_k])
        pos_err_x = abs(true_rel_x - pred_rel_x)
        pos_err_y = abs(true_rel_y - pred_rel_y)
        in_basin = bool(pos_err_x <= 1.0 and pos_err_y <= 1.0)
        res['in_basin'] = in_basin
        res['teacher_intervened'] = False
        res['gth_x'] = gth_x[fi]
        res['gth_y'] = gth_y[fi]
        res['gth_dx'] = gth_dx[fi]
        res['gth_dy'] = gth_dy[fi]
        normal_records.append(res)
        if fi % 1500 == 0:
            print(f"  [normal] Frame {fi:4d}/{num_frames}: Est=({res['est_x']:.1f}, {res['est_y']:.1f}), Score={res['score']:.2f}")
    results['normal'] = normal_records

    # 2. Teacher Mode
    print("\nRunning Mode: TEACHER...")
    teacher_tracker = TeacherModeTracker(search_radius=search_radius, subpixel_scale=16, 
                                         keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                                         fpn_model=fpn_model, use_corner_filter=use_corner_filter)
    teacher_records = []
    for fi in range(num_frames):
        cur_f = frames[fi]
        g_pos = (gth_x[fi], gth_y[fi])
        g_vel = (gth_dx[fi], gth_dy[fi])
        res = teacher_tracker.step(cur_f, fi, g_pos, g_vel)
        teacher_records.append(res)
        if fi % 1500 == 0:
            print(f"  [teacher] Frame {fi:4d}/{num_frames}: Est=({res['est_x']:.1f}, {res['est_y']:.1f}), Score={res['score']:.2f}")
    results['teacher'] = teacher_records

    # 3. Ideal Mode
    print("\nRunning Mode: IDEAL...")
    ideal_tracker = IdealModeTracker(search_radius=search_radius, subpixel_scale=16, 
                                     keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                                     fpn_model=fpn_model, use_corner_filter=use_corner_filter)
    ideal_records = []
    for fi in range(num_frames):
        cur_f = frames[fi]
        g_pos = (gth_x[fi], gth_y[fi])
        g_vel = (gth_dx[fi], gth_dy[fi])
        res = ideal_tracker.step(cur_f, fi, g_pos, g_vel)
        res['in_basin'] = True
        res['teacher_intervened'] = False
        res['pred_x'] = gth_x[fi]
        res['pred_y'] = gth_y[fi]
        res['gth_dx'] = gth_dx[fi]
        res['gth_dy'] = gth_dy[fi]
        ideal_records.append(res)
        if fi % 1500 == 0:
            print(f"  [ideal] Frame {fi:4d}/{num_frames}: Est=({res['est_x']:.1f}, {res['est_y']:.1f}), Score={res['score']:.2f}")
    results['ideal'] = ideal_records

    # Compile Summary Metrics
    summary = {}
    modes = ['normal', 'teacher', 'ideal']
    for mode in modes:
        recs = results[mode]
        est_x = np.array([r['est_x'] for r in recs])
        est_y = np.array([r['est_y'] for r in recs])
        est_dx = np.array([r['est_dx'] for r in recs])
        est_dy = np.array([r['est_dy'] for r in recs])
        in_basin = np.array([r['in_basin'] for r in recs])
        teacher_int = np.array([r['teacher_intervened'] for r in recs])
        squals = np.array([r['squal'] for r in recs])

        # Displacement errors
        err_dx = est_dx - gth_dx
        err_dy = est_dy - gth_dy
        inst_err = np.sqrt(err_dx**2 + err_dy**2)
        
        # Absolute trajectory error (ATE)
        pos_err = np.sqrt((est_x - gth_x)**2 + (est_y - gth_y)**2)
        
        # High-texture vs low-texture masks
        t_mask = squals >= 5.0
        tl_mask = squals < 5.0

        summary[mode] = {
            'total_frames': num_frames,
            'final_drift_px': float(pos_err[-1]),
            'max_ate_px': float(np.max(pos_err)),
            'mean_ate_px': float(np.mean(pos_err)),
            'in_basin_rate_all': float(np.mean(in_basin) * 100.0),
            'in_basin_rate_texture_zone': float(np.mean(in_basin[t_mask]) * 100.0),
            'mismatch_interventions_count': int(np.sum(teacher_int)),
            'inst_dx_rmse_px': float(np.sqrt(np.mean(err_dx**2))),
            'inst_dy_rmse_px': float(np.sqrt(np.mean(err_dy**2))),
            'inst_speed_rmse_px': float(np.sqrt(np.mean(inst_err**2))),
            'inst_speed_rmse_textured_px': float(np.sqrt(np.mean(inst_err[t_mask]**2)))
        }

    # Save summary JSON
    with open(os.path.join(out_dir, "step3_summary_report.json"), "w") as f:
        json.dump(summary, f, indent=2)

    print("\n" + "=" * 70)
    print("THREE MODES EVALUATION SUMMARY:")
    for mode in modes:
        s = summary[mode]
        print(f"\n--- Mode: {mode.upper()} ---")
        print(f"  Final Drift: {s['final_drift_px']:.2f} px (Max ATE: {s['max_ate_px']:.2f} px)")
        print(f"  In-Basin Rate (All Frames): {s['in_basin_rate_all']:.2f}%")
        print(f"  In-Basin Rate (Textured Zones): {s['in_basin_rate_texture_zone']:.2f}%")
        print(f"  Teacher Mismatch Interventions: {s['mismatch_interventions_count']}")
        print(f"  Displacement RMSE (Textured): {s['inst_speed_rmse_textured_px']:.4f} px/frame")

    # Generate Figures
    print("\nGenerating Diagnostic Figures...")
    
    # 1. Trajectory Overlay Plot
    fig, ax = plt.subplots(figsize=(9, 9))
    ax.plot(gth_x, gth_y, 'k--', lw=2.0, label='Ground Truth (gth)')
    ax.plot([r['est_x'] for r in results['ideal']], [r['est_y'] for r in results['ideal']], 
            color='#2ca02c', lw=1.5, alpha=0.9, label='Ideal Mode (Oracle GTH-Centered)')
    ax.plot([r['est_x'] for r in results['teacher']], [r['est_y'] for r in results['teacher']], 
            color='#ff7f0e', lw=1.5, alpha=0.9, label='Teacher Mode (Intervened on Mismatch)')
    ax.plot([r['est_x'] for r in results['normal']], [r['est_y'] for r in results['normal']], 
            color='#1f77b4', lw=1.5, alpha=0.7, label='Normal Mode (Autonomous Closed-Loop)')
    
    # Mark intervention points on Teacher mode
    teacher_int_pts = [i for i, r in enumerate(results['teacher']) if r['teacher_intervened']]
    if teacher_int_pts:
        tx = [results['teacher'][i]['est_x'] for i in teacher_int_pts]
        ty = [results['teacher'][i]['est_y'] for i in teacher_int_pts]
        ax.scatter(tx, ty, color='red', marker='x', s=40, zorder=5, label=f'Teacher Interventions (N={len(tx)})')

    ax.set_aspect('equal')
    ax.set_title("Three-Mode Trajectory Tracking Comparison on Glass ToG\n(Normal vs. Teacher vs. Ideal vs. GTH)", fontsize=13)
    ax.set_xlabel("X Position (Image Pixels)")
    ax.set_ylabel("Y Position (Image Pixels)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper right', fontsize=9)
    plt.tight_layout()
    plot1_path = os.path.join(out_dir, "trajectory_comparison_3modes.png")
    plt.savefig(plot1_path, dpi=150)
    plt.close()

    # 2. Timeline of In-Basin Status & Teacher Interventions
    fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True)
    frames_arr = np.arange(num_frames)
    normal_basin = np.array([r['in_basin'] for r in results['normal']], dtype=int)
    teacher_basin = np.array([r['in_basin'] for r in results['teacher']], dtype=int)
    teacher_intervened = np.array([r['teacher_intervened'] for r in results['teacher']], dtype=int)
    squals = np.array([r['squal'] for r in results['normal']])

    # Row 1: SQUAL & Low-Texture Zones
    axes[0].plot(frames_arr, squals, color='#2ca02c', lw=1.2, label='SQUAL (Feature Quality)')
    axes[0].axhline(5.0, color='red', linestyle='--', label='SQUAL Threshold (5.0)')
    axes[0].set_yscale('log')
    axes[0].set_ylabel('SQUAL')
    axes[0].set_title("Step 3: Timeline Diagnostics - In-Basin State & Teacher Interventions", fontsize=13)
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(loc='upper right')

    # Row 2: In-Basin State (Normal vs Teacher)
    axes[1].plot(frames_arr, normal_basin, color='#1f77b4', lw=1.2, alpha=0.7, label='Normal Mode In-Basin (1=Yes, 0=No)')
    axes[1].plot(frames_arr, teacher_basin + 0.05, color='#ff7f0e', lw=1.2, alpha=0.8, label='Teacher Mode In-Basin (1=Yes, 0=No)')
    axes[1].set_ylabel('In-Basin State')
    axes[1].set_yticks([0, 1])
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(loc='center right')

    # Row 3: Teacher Interventions
    axes[2].scatter(frames_arr[teacher_intervened == 1], np.ones(np.sum(teacher_intervened)), 
                    color='red', marker='|', s=100, label=f'Teacher Interventions (Total: {np.sum(teacher_intervened)})')
    axes[2].set_ylabel('Intervention')
    axes[2].set_yticks([1])
    axes[2].set_xlabel('Frame Index')
    axes[2].grid(True, alpha=0.3)
    axes[2].legend(loc='upper right')

    plt.tight_layout()
    plot2_path = os.path.join(out_dir, "mismatch_and_basin_timeline.png")
    plt.savefig(plot2_path, dpi=150)
    plt.close()

    # 3. Instantaneous Error vs Time
    fig, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
    norm_ate = np.sqrt(([r['est_x'] for r in results['normal']] - gth_x)**2 + 
                       ([r['est_y'] for r in results['normal']] - gth_y)**2)
    teach_ate = np.sqrt(([r['est_x'] for r in results['teacher']] - gth_x)**2 + 
                        ([r['est_y'] for r in results['teacher']] - gth_y)**2)
    ideal_ate = np.sqrt(([r['est_x'] for r in results['ideal']] - gth_x)**2 + 
                        ([r['est_y'] for r in results['ideal']] - gth_y)**2)

    axes[0].plot(frames_arr, norm_ate, color='#1f77b4', lw=1.2, label='Normal Mode Cumulative ATE (px)')
    axes[0].plot(frames_arr, teach_ate, color='#ff7f0e', lw=1.2, label='Teacher Mode Cumulative ATE (px)')
    axes[0].plot(frames_arr, ideal_ate, color='#2ca02c', lw=1.2, label='Ideal Mode Cumulative ATE (px)')
    axes[0].set_ylabel('Absolute Trajectory Error (px)')
    axes[0].set_title("Tracking Error Over Time (ATE & Speed Error)", fontsize=13)
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(loc='upper left')

    # Instantaneous displacement error
    norm_dx_err = np.abs(np.array([r['est_dx'] for r in results['normal']]) - gth_dx)
    teach_dx_err = np.abs(np.array([r['est_dx'] for r in results['teacher']]) - gth_dx)
    axes[1].plot(frames_arr, norm_dx_err, color='#1f77b4', lw=0.8, alpha=0.5, label='Normal |dx - gth_dx|')
    axes[1].plot(frames_arr, teach_dx_err, color='#ff7f0e', lw=0.8, alpha=0.7, label='Teacher |dx - gth_dx|')
    axes[1].set_ylabel('Instantaneous dx Error (px)')
    axes[1].set_xlabel('Frame Index')
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(loc='upper left')

    plt.tight_layout()
    plot3_path = os.path.join(out_dir, "tracking_error_curves.png")
    plt.savefig(plot3_path, dpi=150)
    plt.close()

    print(f"\nAll Step 3 evaluation artifacts saved to: {out_dir}")

if __name__ == '__main__':
    raw_p = r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw"
    gth_p = r"C:\PROJECT\ToG_Tracker\step2_output\POS1-1_ground_truth.csv"
    fpn_p = r"C:\PROJECT\ToG_Tracker\step1_output\fpn_model.raw"
    out_d = r"C:\PROJECT\ToG_Tracker\step3_output"
    evaluate_three_modes(raw_p, gth_p, fpn_p, out_d)
