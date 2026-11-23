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
    for fi in range(3265):
        frm = OpniRawIO.load_frame(f, hdr, fi)
        tracker.step(frm, fi, (gth_x[fi], gth_y[fi]), (gth_dx[fi], gth_dy[fi]))
    
    print("=== Diagnostic for Frame 3265 to 3330 ===")
    for fi in range(3265, 3330):
        frm = OpniRawIO.load_frame(f, hdr, fi)
        res = tracker.step(frm, fi, (gth_x[fi], gth_y[fi]), (gth_dx[fi], gth_dy[fi]))
        edx = res['est_dx']
        edy = res['est_dy']
        gdx = gth_dx[fi]
        gdy = gth_dy[fi]
        sc = res['score']
        sq = res['squal']
        ref = res['ref_idx']
        print(f"f{fi:4d}: GTH=({gdx:+.2f}, {gdy:+.2f}), EST=({edx:+.2f}, {edy:+.2f}), "
              f"Score={sc:.2f}, SQUAL={sq:5.1f}, Ref={ref:4d}, CurEst=({res['est_x']:.2f}, {res['est_y']:.2f})")
