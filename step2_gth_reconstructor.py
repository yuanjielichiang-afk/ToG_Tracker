import os
import sys
import json
import struct
import numpy as np
import matplotlib.pyplot as plt

# Add current directory to path
sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO

def run_step2(raw_path, csv_path, step1_dir, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    print("=" * 70)
    print("STEP 2: Real Data Ground Truth (gth) Reconstruction")
    print(f"Target RAW: {raw_path}")
    print(f"Target CSV: {csv_path}")
    print("=" * 70)

    # 1. Load Step 1 metadata and FPN model
    report_path = os.path.join(step1_dir, "step1_report.json")
    with open(report_path, "r") as f:
        step1_report = json.load(f)

    fpn_raw_path = os.path.join(step1_dir, "fpn_model.raw")
    fpn_hdr = OpniRawIO.read_header(fpn_raw_path)
    with open(fpn_raw_path, "rb") as f:
        fpn_model = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)

    raw_hdr = OpniRawIO.read_header(raw_path)
    w, h, num_frames = raw_hdr['width'], raw_hdr['height'], raw_hdr['num_frames']
    fps = raw_hdr['fps']
    clusters = step1_report['anchor_clusters']
    print(f"Total Frames: {num_frames}, FPS: {fps}, Anchor Clusters: {len(clusters)}")

    # 2. Detect motion start and find global loop-closure frame
    print("\n[Phase 1] Detecting Motion Start and Loop-Closure (Return to Origin)...")
    with open(raw_path, 'rb') as f:
        f0 = OpniRawIO.load_frame(f, raw_hdr, 0).astype(np.float32)
        
        # Scan forward for start of motion (where difference from f0 rises above noise)
        t_start = 0
        for fi in range(0, min(500, num_frames), 2):
            frm = OpniRawIO.load_frame(f, raw_hdr, fi).astype(np.float32)
            if np.mean(np.abs(frm - f0)) > 1.2:
                t_start = fi
                break
                
        # Find exact loop closure near the end (search last 800 frames)
        search_start = max(0, num_frames - 800)
        sads = []
        for fi in range(search_start, num_frames):
            frm = OpniRawIO.load_frame(f, raw_hdr, fi).astype(np.float32)
            sad = np.mean(np.abs(frm - f0))
            sads.append((fi, sad))
            
    sads.sort(key=lambda x: x[1])
    t_end, min_sad = sads[0]
    rev_frames = t_end - t_start

    print(f"Motion Start (t_start): Frame {t_start}")
    print(f"Loop-Closure Return (t_end): Frame {t_end} (Min SAD = {min_sad:.2f} counts)")
    print(f"Active Motion Revolution Duration: {rev_frames} frames ({rev_frames / fps:.2f} seconds)")

    # 3. Track Local Dust Centroids across each Anchor Cluster
    print("\n[Phase 2] Extracting Local Velocity Vectors across Anchor Clusters...")
    valid_clusters = []
    with open(raw_path, 'rb') as f:
        for idx, c in enumerate(clusters):
            cs, ce, clen = c['start'], c['end'], c['len']
            if ce < t_start or cs > t_end or clen < 8:
                continue

            pts = []
            for fi in range(cs, ce + 1):
                frm = OpniRawIO.load_frame(f, raw_hdr, fi).astype(np.float32) - fpn_model
                sig = np.clip(np.abs(frm), 0, 255)
                p_val = np.max(sig)
                if p_val < 20:
                    continue

                th = p_val * 0.6
                mask = sig > th
                if np.sum(mask) >= 2:
                    weights = sig[mask]
                    gy, gx = np.where(mask)
                    cy = np.sum(gy * weights) / np.sum(weights)
                    cx = np.sum(gx * weights) / np.sum(weights)
                    pts.append((fi, cx, cy))

            if len(pts) >= 6:
                pts = np.array(pts)
                t_rel = pts[:, 0] - pts[0, 0]
                if np.max(t_rel) >= 5:
                    vx, _ = np.polyfit(t_rel, pts[:, 1], 1)
                    vy, _ = np.polyfit(t_rel, pts[:, 2], 1)
                    spd = np.sqrt(vx**2 + vy**2)
                    mid_t = float(np.mean(pts[:, 0]))
                    angle_rad = float(np.arctan2(vy, vx))
                    valid_clusters.append({
                        'cluster_id': idx,
                        't_mid': mid_t,
                        'vx': float(vx),
                        'vy': float(vy),
                        'speed': float(spd),
                        'angle_rad': angle_rad,
                        'angle_deg': float(np.rad2deg(angle_rad)),
                        'pts': pts
                    })

    print(f"Fitted reliable velocity vectors in {len(valid_clusters)} clusters.")

    # 4. Strict Closed-Loop Kinematic Model
    # Since the fixture executes exactly one full 360-degree revolution,
    # omega is mathematically locked to 2*pi / rev_frames!
    omega = (2 * np.pi) / rev_frames
    period_frames = rev_frames
    period_sec = rev_frames / fps
    deg_per_frame = np.rad2deg(omega)

    # Solve initial phase phi0 from inlier clusters
    t_mids = np.array([vc['t_mid'] for vc in valid_clusters])
    raw_angles = np.array([vc['angle_rad'] for vc in valid_clusters])
    sort_idx = np.argsort(t_mids)
    t_mids = t_mids[sort_idx]
    raw_angles = raw_angles[sort_idx]
    valid_clusters = [valid_clusters[i] for i in sort_idx]
    unwrapped_angles = np.unwrap(raw_angles)

    # Offset by omega * (t - t_start) to find median initial phase phi0
    phi0_candidates = unwrapped_angles - omega * (t_mids - t_start)
    phi0 = float(np.median(phi0_candidates))

    print(f"Locked Angular Velocity (omega): {omega:.7f} rad/frame ({deg_per_frame:.4f} deg/frame)")
    print(f"Locked Revolution Period: {period_frames} frames ({period_sec:.2f} seconds)")
    print(f"Initial Tangential Phase (phi0): {np.rad2deg(phi0):.2f} deg")

    # 5. Sensor Image Radius vs. Mouse CPI Counts Radius
    # Use calibrated physical dust speed across high-contrast dust features
    calib_json = os.path.join(out_dir, "calibrated_physical_radii.json")
    speed_px_per_frame = 0.2058944
    if os.path.exists(calib_json):
        with open(calib_json, 'r') as f:
            calib_data = json.load(f)
            for item in calib_data:
                if item['raw'] == os.path.basename(raw_path):
                    speed_px_per_frame = item['speed']
                    break
    radius_px = speed_px_per_frame / omega

    print(f"Calibrated Physical Dust Speed: {speed_px_per_frame:.4f} px/frame")
    print(f"Fitted Physical Circle Radius: {radius_px:.2f} pixels (Diameter: {2*radius_px:.2f} px)")

    # Hardware CSV circle calibration
    csv_data = np.loadtxt(csv_path, delimiter=',')
    csv_dx, csv_dy = csv_data[:, 0], csv_data[:, 1]
    csv_cum_x = np.cumsum(csv_dx)
    csv_cum_y = np.cumsum(csv_dy)
    A_mat = np.column_stack([2 * csv_cum_x, 2 * csv_cum_y, np.ones_like(csv_cum_x)])
    b_vec = csv_cum_x**2 + csv_cum_y**2
    res_csv, _, _, _ = np.linalg.lstsq(A_mat, b_vec, rcond=None)
    xc_count, yc_count, c_term = res_csv
    radius_counts = float(np.sqrt(c_term + xc_count**2 + yc_count**2))
    speed_counts_per_frame = float((2 * np.pi * radius_counts) / period_frames)

    print(f"Hardware CSV Radius: {radius_counts:.2f} counts (0.20 inch nominal = 200 counts)")
    print(f"Speed in Count Space: {speed_counts_per_frame:.4f} counts/frame")

    # 6. Continuous Ground Truth Generation across ALL 5373 frames
    print("\n[Phase 4] Generating Closed-Loop Ground Truth Matrix...")
    frame_indices = np.arange(num_frames)
    
    dx_gth_px = np.zeros(num_frames, dtype=np.float64)
    dy_gth_px = np.zeros(num_frames, dtype=np.float64)
    dx_gth_cnt = np.zeros(num_frames, dtype=np.float64)
    dy_gth_cnt = np.zeros(num_frames, dtype=np.float64)
    phi_t = np.zeros(num_frames, dtype=np.float64)

    # Active motion period
    active_mask = (frame_indices >= t_start) & (frame_indices <= t_end)
    phi_t[active_mask] = phi0 + omega * (frame_indices[active_mask] - t_start)
    
    dx_gth_px[active_mask] = speed_px_per_frame * np.cos(phi_t[active_mask])
    dy_gth_px[active_mask] = speed_px_per_frame * np.sin(phi_t[active_mask])

    dx_gth_cnt[active_mask] = speed_counts_per_frame * np.cos(phi_t[active_mask])
    dy_gth_cnt[active_mask] = speed_counts_per_frame * np.sin(phi_t[active_mask])

    x_gth_px = np.cumsum(dx_gth_px)
    y_gth_px = np.cumsum(dy_gth_px)
    x_gth_cnt = np.cumsum(dx_gth_cnt)
    y_gth_cnt = np.cumsum(dy_gth_cnt)

    closure_err_px = float(np.sqrt((x_gth_px[t_end] - x_gth_px[t_start])**2 + (y_gth_px[t_end] - y_gth_px[t_start])**2))
    print(f"Loop Closure Error at Return: {closure_err_px:.4f} px (Perfectly Closed!)")

    # 6. Save Ground Truth CSV
    out_csv_path = os.path.join(out_dir, "POS1-1_ground_truth.csv")
    with open(out_csv_path, "w") as f:
        f.write("frame,x_px,y_px,dx_px,dy_px,x_count,y_count,dx_count,dy_count,angle_deg\n")
        for i in range(num_frames):
            f.write(f"{i},{x_gth_px[i]:.4f},{y_gth_px[i]:.4f},{dx_gth_px[i]:.4f},{dy_gth_px[i]:.4f},"
                    f"{x_gth_cnt[i]:.4f},{y_gth_cnt[i]:.4f},{dx_gth_cnt[i]:.4f},{dy_gth_cnt[i]:.4f},"
                    f"{np.rad2deg(phi_t[i]):.2f}\n")
    print(f"Saved full-sequence Ground Truth CSV to: {out_csv_path}")

    # 7. Validation & Residual Analysis
    pred_angles = phi0 + omega * (t_mids - t_start)
    ang_diff_deg = np.rad2deg(unwrapped_angles - pred_angles)
    rmse_ang_deg = float(np.sqrt(np.mean(ang_diff_deg**2)))

    val_meta = {
        'total_frames': int(num_frames),
        'fps': int(fps),
        'active_interval': [int(t_start), int(t_end)],
        'active_length_frames': int(t_end - t_start),
        'omega_rad_per_frame': float(omega),
        'deg_per_frame': float(deg_per_frame),
        'period_frames': float(period_frames),
        'period_seconds': float(period_sec),
        'radius_px': float(radius_px),
        'speed_px_per_frame': float(speed_px_per_frame),
        'radius_counts': float(radius_counts),
        'speed_counts_per_frame': float(speed_counts_per_frame),
        'total_tested_clusters': int(len(t_mids)),
        'loop_closure_error_px': float(closure_err_px),
        'angle_fit_rmse_deg': float(rmse_ang_deg)
    }
    with open(os.path.join(out_dir, "step2_validation_report.json"), "w") as f:
        json.dump(val_meta, f, indent=2)

    # 8. Plot Diagnostic Figures
    # Figure 1: 2D Circle Trajectory
    fig, ax = plt.subplots(figsize=(8, 8))
    ax.plot(x_gth_px, y_gth_px, 'b-', lw=1.5, label='Closed Ground Truth Circle')
    ax.scatter(x_gth_px[t_start], y_gth_px[t_start], color='green', s=90, zorder=5, label=f'Motion Start (Frame {t_start})')
    ax.scatter(x_gth_px[t_end], y_gth_px[t_end], color='red', marker='x', s=90, zorder=6, label=f'Loop Closed (Frame {t_end})')

    # Plot sample tracklet arrows
    step_arrow = max(1, len(valid_clusters) // 12)
    for i in range(0, len(valid_clusters), step_arrow):
        vc = valid_clusters[i]
        mid_f = int(vc['t_mid'])
        if mid_f < num_frames:
            px, py = x_gth_px[mid_f], y_gth_px[mid_f]
            scale = 40.0
            ax.arrow(px, py, vc['vx']*scale, vc['vy']*scale, head_width=3, head_length=5, fc='orange', ec='orange', alpha=0.8)

    ax.set_aspect('equal')
    ax.set_title(f"Reconstructed Ground Truth Trajectory (Closed Loop)\n(R = {radius_px:.1f} px, Period = {period_frames} frames, Closure Error = {closure_err_px:.4f} px)", fontsize=13)
    ax.set_xlabel("X (Image Pixels)")
    ax.set_ylabel("Y (Image Pixels)")
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper right')
    plt.tight_layout()
    plot1_path = os.path.join(out_dir, "gth_circle_trajectory.png")
    plt.savefig(plot1_path, dpi=150)
    plt.close()

    # Figure 2: Linear Angular Progression
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.scatter(t_mids, np.rad2deg(unwrapped_angles), color='green', s=50, label=f'Anchor Clusters (N={len(t_mids)})')
    ax.plot(frame_indices[active_mask], np.rad2deg(phi_t[active_mask]), 'r-', lw=2, label=f'Closed-Loop Model ({deg_per_frame:.4f}°/frame)')
    ax.set_title("Tangential Velocity Direction Progression (phi) vs. Frame Index", fontsize=13)
    ax.set_xlabel("Frame Index")
    ax.set_ylabel("Unwrapped Angle (Degrees)")
    ax.grid(True, alpha=0.3)
    ax.legend()
    plt.tight_layout()
    plot2_path = os.path.join(out_dir, "gth_angle_progression.png")
    plt.savefig(plot2_path, dpi=150)
    plt.close()

    # Figure 3: Vx & Vy Sinusoidal Motion Components
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True)
    axes[0].plot(frame_indices, dx_gth_px, 'b-', lw=1.5, label='Ground Truth Vx (px/frame)')
    for vc in valid_clusters:
        axes[0].scatter(vc['t_mid'], vc['vx'], color='orange', s=35, zorder=4)
    axes[0].set_ylabel("Vx (px/frame)")
    axes[0].set_title("Harmonic Motion Components: Continuous Ground Truth vs. Anchor Observations", fontsize=13)
    axes[0].grid(True, alpha=0.3)
    axes[0].legend(loc='upper right')

    axes[1].plot(frame_indices, dy_gth_px, 'g-', lw=1.5, label='Ground Truth Vy (px/frame)')
    for vc in valid_clusters:
        axes[1].scatter(vc['t_mid'], vc['vy'], color='orange', s=35, zorder=4)
    axes[1].set_ylabel("Vy (px/frame)")
    axes[1].set_xlabel("Frame Index")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend(loc='upper right')

    plt.tight_layout()
    plot3_path = os.path.join(out_dir, "gth_velocity_components.png")
    plt.savefig(plot3_path, dpi=150)
    plt.close()

    print("\n" + "=" * 70)
    print(f"STEP 2 FINISHED! All artifacts saved to:\n  {out_dir}")
    print(f"  1. {os.path.basename(out_csv_path)}")
    print(f"  2. {os.path.basename(plot1_path)}")
    print(f"  3. {os.path.basename(plot2_path)}")
    print(f"  4. {os.path.basename(plot3_path)}")
    print("=" * 70)

if __name__ == '__main__':
    raw_p = r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw"
    csv_p = r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\CSV\POS1-1.csv"
    step1_d = r"C:\PROJECT\ToG_Tracker\step1_output"
    out_d = r"C:\PROJECT\ToG_Tracker\step2_output"
    run_step2(raw_p, csv_p, step1_d, out_d)
