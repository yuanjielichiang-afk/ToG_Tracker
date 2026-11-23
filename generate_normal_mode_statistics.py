import os
import sys
import json
import numpy as np

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO
from tracker_core import NormalModeTracker

def run():
    raw_path = r'C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw'
    gth_csv_path = r'C:\PROJECT\ToG_Tracker\step2_output\POS1-1_ground_truth.csv'
    fpn_path = r'C:\PROJECT\ToG_Tracker\step1_output\fpn_model.raw'
    step1_json = r'C:\PROJECT\ToG_Tracker\step1_output\step1_report.json'

    # Load GTH
    gth_data = np.genfromtxt(gth_csv_path, delimiter=',', names=True)
    gth_x = gth_data['x_px']
    gth_y = gth_data['y_px']
    gth_dx = gth_data['dx_px']
    gth_dy = gth_data['dy_px']
    num_frames = len(gth_data)

    # Load Step 1 textureless indices
    with open(step1_json, 'r') as f:
        s1 = json.load(f)
    anchor_clusters = s1['anchor_clusters']

    is_anchor = np.zeros(num_frames, dtype=bool)
    for c in anchor_clusters:
        is_anchor[c['start']: c['end'] + 1] = True

    # Load FPN
    fpn_hdr = OpniRawIO.read_header(fpn_path)
    with open(fpn_path, 'rb') as f:
        fpn_model = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)

    raw_hdr = OpniRawIO.read_header(raw_path)
    frames = []
    with open(raw_path, 'rb') as f:
        for fi in range(num_frames):
            frm = OpniRawIO.load_frame(f, raw_hdr, fi)
            frames.append(frm)

    tracker = NormalModeTracker(search_radius=1, subpixel_scale=16, 
                                keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                                fpn_model=fpn_model)
    
    records = []
    for fi in range(num_frames):
        res = tracker.step(frames[fi], fi)
        records.append(res)

    est_dx = np.array([r['est_dx'] for r in records])
    est_dy = np.array([r['est_dy'] for r in records])
    pred_x = np.array([r['pred_x'] for r in records])
    pred_y = np.array([r['pred_y'] for r in records])
    est_x = np.array([r['est_x'] for r in records])
    est_y = np.array([r['est_y'] for r in records])
    squals = np.array([r['squal'] for r in records])

    # 1. Single frame displacement error
    disp_err = np.sqrt((est_dx - gth_dx)**2 + (est_dy - gth_dy)**2)
    # 2. Global Cumulative Trajectory Error (ATE)
    pos_err = np.sqrt((est_x - gth_x)**2 + (est_y - gth_y)**2)
    # 3. Global Capture Basin: |pred_pos - gth_pos| <= 1.0 px
    basin_err_x = np.abs(np.round(pred_x) - gth_x)
    basin_err_y = np.abs(np.round(pred_y) - gth_y)
    global_in_basin = (basin_err_x <= 1.0) & (basin_err_y <= 1.0)

    # 4. Local Step Prediction Error (Velocity matching)
    step_err = np.sqrt((pred_x - est_x - (gth_dx))**2 + (pred_y - est_y - (gth_dy))**2)
    local_step_correct = step_err <= 0.20

    m_correct_005 = disp_err <= 0.05
    m_correct_010 = disp_err <= 0.10
    m_correct_020 = disp_err <= 0.20

    active = np.zeros(num_frames, dtype=bool)
    active[170:5352] = True
    
    subsets = {
        'all': (np.ones(num_frames, dtype=bool), "全序列 (全部 5,373 幀)"),
        'active': (active, "機台主動畫圓運動區間 (Frame 170 ~ 5351，共 5,182 幀)"),
        'anchor': (active & is_anchor, "有微塵錨點區 (Anchor / Dust Clusters，共 1,564 幀)"),
        'textureless': (active & (~is_anchor), "稀疏/空白玻璃區 (Textureless / Sparse Gap，共 3,618 幀)")
    }

    report = {}
    for key, (mask, name) in subsets.items():
        total = int(np.sum(mask))
        g_basin = int(np.sum(global_in_basin & mask))
        l_step = int(np.sum(local_step_correct & mask))
        m_005 = int(np.sum(m_correct_005 & mask))
        m_010 = int(np.sum(m_correct_010 & mask))
        m_020 = int(np.sum(m_correct_020 & mask))

        report[key] = {
            'name': name,
            'total_frames': total,
            'global_in_basin_count': g_basin,
            'global_in_basin_rate': round(g_basin / total * 100.0, 2),
            'local_step_pred_count': l_step,
            'local_step_pred_rate': round(l_step / total * 100.0, 2),
            'disp_err_le_005_count': m_005,
            'disp_err_le_005_rate': round(m_005 / total * 100.0, 2),
            'disp_err_le_010_count': m_010,
            'disp_err_le_010_rate': round(m_010 / total * 100.0, 2),
            'disp_err_le_020_count': m_020,
            'disp_err_le_020_rate': round(m_020 / total * 100.0, 2),
            'mean_disp_err_px': round(float(np.mean(disp_err[mask])), 4),
            'rmse_disp_err_px': round(float(np.sqrt(np.mean(disp_err[mask]**2))), 4),
            'mean_ate_px': round(float(np.mean(pos_err[mask])), 2),
            'max_ate_px': round(float(np.max(pos_err[mask])), 2)
        }

    # Save to reports and step3_output
    for out_p in [r'C:\PROJECT\ToG_Tracker\reports\normal_mode_stats.json', 
                  r'C:\PROJECT\ToG_Tracker\step3_output\normal_mode_stats.json']:
        with open(out_p, 'w', encoding='utf-8') as fp:
            json.dump(report, fp, indent=2, ensure_ascii=False)

    print(json.dumps(report, indent=2, ensure_ascii=False))

if __name__ == '__main__':
    run()
