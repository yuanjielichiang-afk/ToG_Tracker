import sys
import numpy as np

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO

raw_path = r'C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw'
hdr = OpniRawIO.read_header(raw_path)
fpn_path = r'C:\PROJECT\ToG_Tracker\step1_output\fpn_model.raw'
fpn_hdr = OpniRawIO.read_header(fpn_path)
with open(fpn_path, 'rb') as f:
    fpn = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)

gth_csv_path = r'C:\PROJECT\ToG_Tracker\step2_output\POS1-1_ground_truth.csv'
gth_data = np.genfromtxt(gth_csv_path, delimiter=',', names=True)
gth_x, gth_y = gth_data['x_px'], gth_data['y_px']
gth_dx, gth_dy = gth_data['dx_px'], gth_data['dy_px']

from test_fixed_ideal import FixedIdealTracker
tracker = FixedIdealTracker(search_radius=1, subpixel_scale=16, keyframe_dist=1.5, squal_th=5.0, fpn_model=fpn)

with open(raw_path, 'rb') as f:
    for fi in range(3267):
        frm = OpniRawIO.load_frame(f, hdr, fi)
        tracker.step(frm, fi, (gth_x[fi], gth_y[fi]), (gth_dx[fi], gth_dy[fi]))

    for fi in range(3267, 3272):
        frm = OpniRawIO.load_frame(f, hdr, fi)
        old_ref = tracker.ref_idx
        old_last_rel = (tracker.last_dx_rel, tracker.last_dy_rel)
        res = tracker.step(frm, fi, (gth_x[fi], gth_y[fi]), (gth_dx[fi], gth_dy[fi]))
        print(f"Frame {fi}: old_ref={old_ref}, old_last_rel=({old_last_rel[0]:.2f}, {old_last_rel[1]:.2f}) -> "
              f"new_ref={tracker.ref_idx}, new_last_rel=({tracker.last_dx_rel:.2f}, {tracker.last_dy_rel:.2f}), "
              f"dx_q=({res['est_dx']:.2f}, {res['est_dy']:.2f}), global=({tracker.global_x:.2f}, {tracker.global_y:.2f})")
