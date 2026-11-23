import os
import sys
import json
import numpy as np

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step3_three_mode_evaluator import ToGTracker, OpniRawIO

def compute_normal_mode_benchmark():
    raw_path = r'C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw'
    gth_csv_path = r'C:\PROJECT\ToG_Tracker\step2_output\POS1-1_ground_truth.csv'
    fpn_path = r'C:\PROJECT\ToG_Tracker\step1_output\fpn_model.raw'
    step1_rep_path = r'C:\PROJECT\ToG_Tracker\step1_output\step1_report.json'

    # Load Ground Truth
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
    frames = []
    with open(raw_path, 'rb') as f:
        for fi in range(num_frames):
            frm = OpniRawIO.load_frame(f, raw_hdr, fi).astype(np.float32) - fpn_model
            frames.append(frm)

    # Run Normal Mode tracker
    tracker = ToGTracker(mode='normal', search_radius=1, subpixel_scale=16, 
                         keyframe_dist=1.5, squal_th=5.0)
    records = []
    for fi in range(num_frames):
        cur_f = frames[fi]
        g_pos = (gth_x[fi], gth_y[fi])
        g_vel = (gth_dx[fi], gth_dy[fi])
        res = tracker.process_frame(cur_f, fi, g_pos, g_vel)
        records.append(res)

    # Extract arrays
    pred_x = np.array([r['pred_x'] for r in records])
    pred_y = np.array([r['pred_y'] for r in records])
    est_dx = np.array([r['est_dx'] for r in records])
    est_dy = np.array([r['est_dy'] for r in records])
    squals = np.array([r['squal'] for r in records])

    # 1. Prediction Accuracy: Within 3x3 Capture Basin (max(|round(pred) - gth|) <= 1.0)
    pred_err_x = np.abs(np.round(pred_x) - gth_x)
    pred_err_y = np.abs(np.round(pred_y) - gth_y)
    pred_correct = (pred_err_x <= 1.0) & (pred_err_y <= 1.0)

    # 2. Matching Accuracy: Incremental displacement error
    dx_err = est_dx - gth_dx
    dy_err = est_dy - gth_dy
    disp_err = np.sqrt(dx_err**2 + dy_err**2)

    match_correct_025 = disp_err <= 0.25 # strict
    match_correct_050 = disp_err <= 0.50 # standard
    match_correct_100 = disp_err <= 1.00 # capture tolerance

    # 3. Masks for segmentation
    # Active motion interval: 170 to 5351
    active_mask = np.zeros(num_frames, dtype=bool)
    active_mask[170:5352] = True
    
    # Texture zones vs Textureless zones
    textured_mask = squals >= 5.0
    textureless_mask = ~textured_mask

    def get_stats(mask, name):
        total = int(np.sum(mask))
        if total == 0:
            return {}
        p_cor = int(np.sum(pred_correct & mask))
        m_cor_025 = int(np.sum(match_correct_025 & mask))
        m_cor_050 = int(np.sum(match_correct_050 & mask))
        m_cor_100 = int(np.sum(match_correct_100 & mask))
        both_cor_050 = int(np.sum(pred_correct & match_correct_050 & mask))

        return {
            'subset_name': name,
            'total_frames': total,
            'pred_correct_count': p_cor,
            'pred_correct_rate_pct': round(p_cor / total * 100.0, 2),
            'match_correct_025_count': m_cor_025,
            'match_correct_025_rate_pct': round(m_cor_025 / total * 100.0, 2),
            'match_correct_050_count': m_cor_050,
            'match_correct_050_rate_pct': round(m_cor_050 / total * 100.0, 2),
            'match_correct_100_count': m_cor_100,
            'match_correct_100_rate_pct': round(m_cor_100 / total * 100.0, 2),
            'both_pred_and_match_050_count': both_cor_050,
            'both_pred_and_match_050_rate_pct': round(both_cor_050 / total * 100.0, 2),
            'mean_disp_err_px': round(float(np.mean(disp_err[mask])), 4),
            'rmse_disp_err_px': round(float(np.sqrt(np.mean(disp_err[mask]**2))), 4)
        }

    stats_all = get_stats(np.ones(num_frames, dtype=bool), "全序列 (All 5373 Frames)")
    stats_active = get_stats(active_mask, "主動圓周運動區 (Active Motion 5181 Frames)")
    stats_textured = get_stats(active_mask & textured_mask, "主動運動 + 有微塵特徵區 (Textured / Dust)")
    stats_textureless = get_stats(active_mask & textureless_mask, "主動運動 + 空白玻璃區 (Textureless / Blank Glass)")

    full_report = {
        'overall': stats_all,
        'active_motion': stats_active,
        'textured_zones': stats_textured,
        'textureless_zones': stats_textureless
    }

    out_json = r'C:\PROJECT\ToG_Tracker\step3_output\normal_mode_baseline_stats.json'
    with open(out_json, 'w', encoding='utf-8') as f:
        json.dump(full_report, f, indent=2, ensure_ascii=False)
        
    rep_json = r'C:\PROJECT\ToG_Tracker\reports\normal_mode_baseline_stats.json'
    with open(rep_json, 'w', encoding='utf-8') as f:
        json.dump(full_report, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 70)
    print("NORMAL MODE BASELINE STATISTICAL BENCHMARK:")
    print("=" * 70)
    for k, s in full_report.items():
        print(f"\n--- {s['subset_name']} (Total: {s['total_frames']} 幀) ---")
        print(f"  [預測正確率] 落在 gth 的 3x3 捕捉範圍內: {s['pred_correct_count']} 幀 ({s['pred_correct_rate_pct']}%)")
        print(f"  [匹配正確率] 位移誤差 <= 0.25 px (高精):  {s['match_correct_025_count']} 幀 ({s['match_correct_025_rate_pct']}%)")
        print(f"  [匹配正確率] 位移誤差 <= 0.50 px (標準):  {s['match_correct_050_count']} 幀 ({s['match_correct_050_rate_pct']}%)")
        print(f"  [匹配正確率] 位移誤差 <= 1.00 px (寬容):  {s['match_correct_100_count']} 芝 ({s['match_correct_100_rate_pct']}%)")
        print(f"  [雙重正確] 預測正確且匹配誤差 <= 0.5 px:  {s['both_pred_and_match_050_count']} 幀 ({s['both_pred_and_match_050_rate_pct']}%)")
        print(f"  [平均位移誤差]: {s['mean_disp_err_px']:.4f} px/f, RMSE: {s['rmse_disp_err_px']:.4f} px/f")

if __name__ == '__main__':
    compute_normal_mode_benchmark()
