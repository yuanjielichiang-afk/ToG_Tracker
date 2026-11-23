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
gth_x, gth_y = gth_data['x_px'], gth_data['y_px']
gth_dx, gth_dy = gth_data['dx_px'], gth_data['dy_px']

class FixedIdealTracker(IdealModeTracker):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ref_gth_x = 0.0
        self.ref_gth_y = 0.0

    def step(self, raw_frame, frame_idx, gth_pos, gth_vel):
        cur_frame = self.remove_fpn(raw_frame)
        squal = self.compute_squal(cur_frame)
        if self.ref_frame is None:
            self.ref_frame = cur_frame
            self.ref_idx = frame_idx
            self.ref_gth_x = gth_pos[0]
            self.ref_gth_y = gth_pos[1]
            self.global_x = gth_pos[0]
            self.global_y = gth_pos[1]
            return {'frame': frame_idx, 'est_x': self.global_x, 'est_y': self.global_y, 'est_dx': 0.0, 'est_dy': 0.0, 'score': 1.0, 'squal': squal}

        # True GTH offset from reference keyframe!
        px = int(round(gth_pos[0] - self.ref_gth_x))
        py = int(round(gth_pos[1] - self.ref_gth_y))

        surface = self.compute_correlation_surface(cur_frame, self.ref_frame, px, py)
        r = self.r
        v_idx, u_idx = np.unravel_index(np.argmax(surface), surface.shape)
        best_score = float(surface[v_idx, u_idx])
        dx_int = px + (u_idx - r)
        dy_int = py + (v_idx - r)

        dx_sub, dy_sub = 0.0, 0.0
        if 0 < u_idx < 2*r:
            dx_sub = self.parabolic_interpolate(surface[v_idx, u_idx-1], surface[v_idx, u_idx], surface[v_idx, u_idx+1])
        if 0 < v_idx < 2*r:
            dy_sub = self.parabolic_interpolate(surface[v_idx-1, u_idx], surface[v_idx, u_idx], surface[v_idx+1, u_idx])

        dx_tot = dx_int + dx_sub
        dy_tot = dy_int + dy_sub
        dx_q = round(dx_tot * self.subpixel_scale) / self.subpixel_scale
        dy_q = round(dy_tot * self.subpixel_scale) / self.subpixel_scale

        frame_dx = dx_q - self.last_dx_rel
        frame_dy = dy_q - self.last_dy_rel

        self.global_x += frame_dx
        self.global_y += frame_dy
        self.last_dx_rel = dx_q
        self.last_dy_rel = dy_q

        # Keyframe update
        dist_from_ref = np.sqrt(self.last_dx_rel**2 + self.last_dy_rel**2)
        if dist_from_ref >= self.keyframe_dist and squal >= self.squal_th:
            self.ref_frame = cur_frame
            self.ref_idx = frame_idx
            self.ref_gth_x = gth_pos[0]
            self.ref_gth_y = gth_pos[1]
            self.last_dx_rel = 0.0
            self.last_dy_rel = 0.0

        return {'frame': frame_idx, 'est_x': self.global_x, 'est_y': self.global_y, 'est_dx': frame_dx, 'est_dy': frame_dy, 'score': best_score, 'squal': squal}

tracker = FixedIdealTracker(search_radius=1, subpixel_scale=16, keyframe_dist=1.5, squal_th=5.0, fpn_model=fpn)
with open(raw_path, 'rb') as f:
    for fi in range(3265):
        frm = OpniRawIO.load_frame(f, hdr, fi)
        tracker.step(frm, fi, (gth_x[fi], gth_y[fi]), (gth_dx[fi], gth_dy[fi]))

    print('Fixed Ideal Mode at 3265-3300:')
    for fi in range(3265, 3300):
        frm = OpniRawIO.load_frame(f, hdr, fi)
        res = tracker.step(frm, fi, (gth_x[fi], gth_y[fi]), (gth_dx[fi], gth_dy[fi]))
        edx = res['est_dx']
        edy = res['est_dy']
        gdx = gth_dx[fi]
        gdy = gth_dy[fi]
        sc = res['score']
        print(f"f{fi}: GTH=({gdx:+.2f}, {gdy:+.2f}), EST=({edx:+.2f}, {edy:+.2f}), Score={sc:.2f}")
