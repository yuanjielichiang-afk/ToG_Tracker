import os
import sys
import numpy as np

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO
from tracker_core import IdealModeTracker

raw_path = r'C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw'
hdr = OpniRawIO.read_header(raw_path)
fpn_path = r'C:\PROJECT\ToG_Tracker\step1_output\fpn_model.raw'
fpn_hdr = OpniRawIO.read_header(fpn_path)
with open(fpn_path, 'rb') as f:
    fpn = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)

gth_csv_path = r'C:\PROJECT\ToG_Tracker\step2_output\POS1-1_ground_truth.csv'
gth_data = np.genfromtxt(gth_csv_path, delimiter=',', names=True)
gth_x = gth_data['x_px']
gth_y = gth_data['y_px']
gth_dx = gth_data['dx_px']
gth_dy = gth_data['dy_px']

tracker = IdealModeTracker(search_radius=1, subpixel_scale=16, 
                           keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                           fpn_model=fpn)

with open(raw_path, 'rb') as f:
    for fi in range(3274):
        frm = OpniRawIO.load_frame(f, hdr, fi)
        tracker.step(frm, fi, (gth_x[fi], gth_y[fi]), (gth_dx[fi], gth_dy[fi]))
    
    print("=== Detailed inspection at 3274-3277 ===")
    for fi in range(3274, 3278):
        frm = OpniRawIO.load_frame(f, hdr, fi)
        ref_ox = tracker.global_x - tracker.last_dx_rel
        ref_oy = tracker.global_y - tracker.last_dy_rel
        px = int(round(gth_x[fi] - ref_ox))
        py = int(round(gth_y[fi] - ref_oy))
        cur_clean = frm.astype(np.float32) - fpn
        surf = tracker.compute_correlation_surface(cur_clean, tracker.ref_frame, px, py)
        res = tracker.step(frm, fi, (gth_x[fi], gth_y[fi]), (gth_dx[fi], gth_dy[fi]))
        print(f"Frame {fi}: px={px}, py={py}, ref_idx={tracker.ref_idx}, last_rel=({tracker.last_dx_rel:.2f}, {tracker.last_dy_rel:.2f})")
        print("Surface (dy from -1 to +1, dx from -1 to +1):")
        print(np.round(surf, 3))
        print(f"Result: est_dx={res['est_dx']:.2f}, est_dy={res['est_dy']:.2f}, score={res['score']:.3f}\n")
