"""
========================================================================================
Step 4: Kinematic Kalman Filter & PixArt US9684384B2 Skating Tracker
========================================================================================
Sub-item 4.1: 運動學卡爾曼濾波器 (CV Model, SQUAL/Contrast Adaptive R, Innovation Gating)
Sub-item 4.2: 原相專利 US9684384B2 Skating 分區分幀搜尋機制 (5-Partition Interleaved & Consistency Lock)
Sub-item 4.3: 孤立微塵質心快速重定位導引 (Dust Centroid Fast Relocalization)
Sub-item 4.4: 雙向測試資料庫綜合評估 (Dual Benchmark Pipeline)
========================================================================================
"""

import os
import sys
import math
import json
import numpy as np
import matplotlib.pyplot as plt

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO
from tracker_core import NormalModeTracker, TeacherModeTracker


class KinematicKalmanFilterCV:
    """
    Sub-item 4.1: 恆速度 (Constant Velocity, CV) 運動學卡爾曼濾波器
    狀態向量: x = [p_x, p_y, v_x, v_y]^T (4D)
    觀測向量: z = [p_x, p_y]^T (2D)
    """
    def __init__(self, dt=1.0, q_pos=1e-5, q_vel=5e-5, r_std=0.08):
        self.dt = dt
        self.x = np.zeros(4, dtype=np.float64) # [px, py, vx, vy]
        self.P = np.eye(4, dtype=np.float64) * 0.05
        
        # 狀態轉移矩陣 F
        self.F = np.array([
            [1.0, 0.0, dt,  0.0],
            [0.0, 1.0, 0.0, dt],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]
        ], dtype=np.float64)

        # 過程噪聲 Q
        self.Q = np.zeros((4, 4), dtype=np.float64)
        self.Q[0, 0] = q_pos; self.Q[1, 1] = q_pos
        self.Q[2, 2] = q_vel; self.Q[3, 3] = q_vel

        # 觀測矩陣 H
        self.H = np.array([
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0]
        ], dtype=np.float64)

        self.r_std = r_std
        self.rejected_by_gate = False
        self.last_d2 = 0.0

    def init_state(self, px, py, vx, vy):
        self.x = np.array([px, py, vx, vy], dtype=np.float64)
        self.P = np.eye(4, dtype=np.float64) * 0.01

    def predict(self):
        """卡爾曼時間更新 (Prior State & Covariance)"""
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        sigma_p = math.sqrt(max(1e-8, self.P[0, 0] + self.P[1, 1]))
        return self.x.copy(), sigma_p

    def update(self, z_meas, is_textured, score=1.0):
        """
        卡爾曼測量更新 (SQUAL/Contrast Conditioned)
        附帶新息閘控 (Innovation Gating)
        """
        self.rejected_by_gate = False
        if (not is_textured) or (score < 0.50):
            # 空白無特徵 TL 區或低相關度底噪：完全不更新位置測量，純慣性盲推
            return self.x.copy(), math.sqrt(self.P[0, 0] + self.P[1, 1])

        # 紋理區正常更新
        R = np.eye(2, dtype=np.float64) * (self.r_std ** 2)
        y = z_meas - self.H @ self.x
        S = self.H @ self.P @ self.H.T + R
        inv_S = np.linalg.inv(S)
        
        # 新息閘控 (3-sigma Chi-Square threshold = 9.21)
        d2 = float(y.T @ inv_S @ y)
        self.last_d2 = d2
        if d2 > 9.21:
            self.rejected_by_gate = True
            return self.x.copy(), math.sqrt(self.P[0, 0] + self.P[1, 1])

        # 卡爾曼增益更新 (Joseph form)
        K = self.P @ self.H.T @ inv_S
        self.x = self.x + K @ y
        I = np.eye(4, dtype=np.float64)
        self.P = (I - K @ self.H) @ self.P @ (I - K @ self.H).T + K @ R @ K.T
        
        pos_sigma = math.sqrt(max(1e-8, self.P[0, 0] + self.P[1, 1]))
        return self.x.copy(), pos_sigma


class PixArtSkatingSearcher:
    """
    Sub-item 4.2: 原相專利 US9684384B2 Skating 分區分幀搜尋機制
    將擴展搜尋範圍分割為 5 個 3x3 子分區，分幀時空交錯輪詢，並以連續 M 幀鎖定防誤判。
    """
    def __init__(self, lock_threshold=3, min_lock_score=0.60):
        self.sken = False               # Skating Enable 狀態標誌
        self.lock_threshold = lock_threshold # 連續確認幀數 M
        self.min_lock_score = min_lock_score
        self.current_sub_idx = 0        # 當前輪詢分區索引 (0~4)
        self.lock_counter = 0           # 連續一致性計數器
        
        # 5 個 3x3 子分區相對中心偏移量 (offset_x, offset_y)
        self.sub_partitions = [
            ( 0,  0),  # Partition 0: 中心區 [-1, +1] x [-1, +1]
            (-2,  0),  # Partition 1: 西區/左方 [-3, -1] x [-1, +1]
            ( 2,  0),  # Partition 2: 東區/右方 [+1, +3] x [-1, +1]
            ( 0, -2),  # Partition 3: 北區/上方 [-1, +1] x [-3, -1]
            ( 0,  2)   # Partition 4: 南區/下方 [-1, +1] x [+1, +3]
        ]

    def trigger_skating(self):
        self.sken = True
        self.lock_counter = 0
        self.current_sub_idx = 0

    def get_search_center(self, nominal_px, nominal_py, guided_hint=None):
        if not self.sken:
            return nominal_px, nominal_py, 0, 1 # (center_x, center_y, sub_idx, radius=1)

        if guided_hint is not None:
            gh_x, gh_y = guided_hint
            best_idx = 0
            best_dist = 999.0
            for i, (off_x, off_y) in enumerate(self.sub_partitions):
                d = (gh_x - off_x)**2 + (gh_y - off_y)**2
                if d < best_dist:
                    best_dist = d
                    best_idx = i
            sub_idx = best_idx
        else:
            sub_idx = self.current_sub_idx
            self.current_sub_idx = (self.current_sub_idx + 1) % len(self.sub_partitions)

        off_x, off_y = self.sub_partitions[sub_idx]
        return nominal_px + off_x, nominal_py + off_y, sub_idx, 1

    def update_lock_consistency(self, score):
        if not self.sken:
            return False

        if score >= self.min_lock_score:
            self.lock_counter += 1
            if self.lock_counter >= self.lock_threshold:
                self.sken = False
                self.lock_counter = 0
                return True
        else:
            self.lock_counter = max(0, self.lock_counter - 1)

        return False


class Step4KalmanSkatingTracker(NormalModeTracker):
    """
    Step 4 完整整合追蹤器
    """
    def __init__(self, search_radius=1, subpixel_scale=16, 
                 keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                 fpn_model=None, max_ref_dist=4.0):
        super().__init__(search_radius=search_radius, subpixel_scale=subpixel_scale,
                         keyframe_dist=keyframe_dist, squal_th=squal_th, 
                         v_alpha=v_alpha, fpn_model=fpn_model, max_ref_dist=max_ref_dist)
        
        self.kf = KinematicKalmanFilterCV(dt=1.0, q_pos=1e-5, q_vel=5e-5, r_std=0.08)
        self.skating = PixArtSkatingSearcher(lock_threshold=3, min_lock_score=0.60)
        self.skating_events = []

    def check_surface_dust(self, clean_frame):
        safe_sig = np.abs(clean_frame[5:31, 5:31])
        max_contrast = float(np.max(safe_sig))
        is_textured = bool(max_contrast >= 18.0)
        return is_textured, max_contrast

    def extract_dust_centroid(self, clean_frame, min_contrast=18.0):
        """Sub-item 4.3: 孤立微塵質心快速重定位導引"""
        sig = np.abs(clean_frame)
        safe_sig = sig[6:30, 6:30]
        max_v = float(np.max(safe_sig))
        if max_v < min_contrast:
            return None
        
        mask = (sig > max_v * 0.70)
        mask[:6, :] = False; mask[30:, :] = False
        mask[:, :6] = False; mask[:, 30:] = False
        
        cnt = np.sum(mask)
        if 1 <= cnt <= 12:
            gy, gx = np.where(mask)
            weights = sig[mask]
            cx = float(np.sum(gx * weights) / np.sum(weights))
            cy = float(np.sum(gy * weights) / np.sum(weights))
            return (cx - 18.0, cy - 18.0)
        return None

    def step(self, raw_frame, frame_idx):
        cur_frame = self.remove_fpn(raw_frame)
        squal = self.compute_squal(cur_frame)
        is_textured, contrast = self.check_surface_dust(cur_frame)

        if self.ref_frame is None:
            self.ref_frame = cur_frame
            self.ref_idx = frame_idx
            self.kf.init_state(self.global_x, self.global_y, 0.0, 0.0)
            return {
                'frame': frame_idx,
                'est_x': self.global_x,
                'est_y': self.global_y,
                'est_dx': 0.0,
                'est_dy': 0.0,
                'pred_x': self.global_x,
                'pred_y': self.global_y,
                'score': 1.0,
                'squal': squal,
                'sken': False,
                'sigma_p': 0.0,
                'ref_idx': self.ref_idx
            }

        # 1. 卡爾曼時間預測先驗
        kf_state, sigma_p = self.kf.predict()
        kf_pred_x, kf_pred_y = kf_state[0], kf_state[1]

        # 2. 觸發 Skating 機制
        if sigma_p > 0.8 and not self.skating.sken:
            self.skating.trigger_skating()
            self.skating_events.append(frame_idx)

        # 3. 孤立微塵質心導引 (Sub-item 4.3)
        guided_hint = None
        if is_textured:
            guided_hint = self.extract_dust_centroid(cur_frame)

        # 4. 決定搜尋中心 (Nominal vs Skating 分區)
        ref_origin_x = self.global_x - self.last_dx_rel
        ref_origin_y = self.global_y - self.last_dy_rel
        nominal_px = int(round(kf_pred_x - ref_origin_x))
        nominal_py = int(round(kf_pred_y - ref_origin_y))

        search_px, search_py, sub_idx, r = self.skating.get_search_center(
            nominal_px, nominal_py, guided_hint=guided_hint
        )

        # 5. 計算互相關曲面 (維持 3x3 運算量)
        surface = self.compute_correlation_surface(cur_frame, self.ref_frame, search_px, search_py)
        v_idx, u_idx = np.unravel_index(np.argmax(surface), surface.shape)
        best_score = float(surface[v_idx, u_idx])

        dx_int = search_px + (u_idx - r)
        dy_int = search_py + (v_idx - r)

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
        meas_global_pos = np.array([ref_origin_x + dx_q, ref_origin_y + dy_q], dtype=np.float64)

        # 6. Skating 一致性確認
        if self.skating.sken:
            self.skating.update_lock_consistency(best_score)

        # 7. 卡爾曼測量更新
        kf_post_state, post_sigma_p = self.kf.update(meas_global_pos, is_textured, score=best_score)

        # 8. 軌跡更新
        self.global_x += frame_dx
        self.global_y += frame_dy
        self.last_dx_rel = dx_q
        self.last_dy_rel = dy_q

        # 9. 更新 Keyframe
        self.update_keyframe_if_needed(cur_frame, frame_idx, squal)

        return {
            'frame': frame_idx,
            'est_x': self.global_x,
            'est_y': self.global_y,
            'est_dx': frame_dx,
            'est_dy': frame_dy,
            'pred_x': kf_pred_x,
            'pred_y': kf_pred_y,
            'score': best_score,
            'squal': squal,
            'is_textured': is_textured,
            'sken': self.skating.sken,
            'sigma_p': post_sigma_p,
            'gate_rejected': self.kf.rejected_by_gate,
            'ref_idx': self.ref_idx
        }


def run_benchmark(raw_path, gth_csv_path, fpn_path, out_dir, tag_name=""):
    os.makedirs(out_dir, exist_ok=True)
    print("=" * 75)
    print(f"STEP 4 BENCHMARK: {tag_name}")
    print(f"RAW File: {raw_path}")
    print(f"GTH CSV : {gth_csv_path}")
    print("=" * 75)

    gth_data = np.genfromtxt(gth_csv_path, delimiter=',', names=True)
    gth_x = gth_data['x_px']
    gth_y = gth_data['y_px']

    fpn_hdr = OpniRawIO.read_header(fpn_path)
    with open(fpn_path, 'rb') as f:
        fpn_model = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)

    raw_hdr = OpniRawIO.read_header(raw_path)
    total_frames = min(len(gth_data), raw_hdr['num_frames'])
    gth_x = gth_x[:total_frames]
    gth_y = gth_y[:total_frames]
    num_frames = total_frames

    frames = []
    with open(raw_path, 'rb') as f:
        for fi in range(num_frames):
            fr = OpniRawIO.load_frame(f, raw_hdr, fi)
            if fr is None:
                num_frames = fi
                break
            frames.append(fr)
    gth_x = gth_x[:num_frames]
    gth_y = gth_y[:num_frames]

    print(f"Evaluating {num_frames} frames...")
    orig_tracker = NormalModeTracker(search_radius=1, subpixel_scale=16, fpn_model=fpn_model)
    s4_tracker = Step4KalmanSkatingTracker(search_radius=1, subpixel_scale=16, fpn_model=fpn_model)

    orig_records = []
    s4_records = []
    for fi in range(num_frames):
        orig_records.append(orig_tracker.step(frames[fi], fi))
        s4_records.append(s4_tracker.step(frames[fi], fi))

    orig_x = np.array([r['est_x'] for r in orig_records])
    orig_y = np.array([r['est_y'] for r in orig_records])
    s4_x = np.array([r['est_x'] for r in s4_records])
    s4_y = np.array([r['est_y'] for r in s4_records])

    orig_ate = np.sqrt((orig_x - gth_x)**2 + (orig_y - gth_y)**2)
    s4_ate = np.sqrt((s4_x - gth_x)**2 + (s4_y - gth_y)**2)

    mean_orig = float(np.mean(orig_ate))
    mean_s4 = float(np.mean(s4_ate))
    max_orig = float(np.max(orig_ate))
    max_s4 = float(np.max(s4_ate))
    final_orig = float(orig_ate[-1])
    final_s4 = float(s4_ate[-1])
    sk_count = len(s4_tracker.skating_events)

    print("\n" + "-" * 75)
    print(f"RESULTS FOR {tag_name}:")
    print(f"1. Mean Trajectory ATE: {mean_orig:.2f} px -> {mean_s4:.2f} px (Improvement: {(1.0 - mean_s4/mean_orig)*100:+.1f}%)")
    print(f"2. Max ATE Drift      : {max_orig:.2f} px -> {max_s4:.2f} px (Improvement: {(1.0 - max_s4/max_orig)*100:+.1f}%)")
    print(f"3. Final Closed Drift : {final_orig:.2f} px -> {final_s4:.2f} px")
    print(f"4. Skating Triggers   : {sk_count} times")
    print("-" * 75)

    # 繪製對比圖
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    axes[0].plot(gth_x, gth_y, 'k--', lw=2, label='Ground Truth (GTH)')
    axes[0].plot(orig_x, orig_y, 'b-', lw=1.2, alpha=0.7, label=f'Original Normal (Mean ATE: {mean_orig:.1f} px)')
    axes[0].plot(s4_x, s4_y, 'r-', lw=1.5, label=f'Step 4 KF+Skating (Mean ATE: {mean_s4:.1f} px)')
    axes[0].set_aspect('equal')
    axes[0].set_title(f"Step 4 Trajectory: {tag_name}", fontsize=13)
    axes[0].set_xlabel("X (Pixels)")
    axes[0].set_ylabel("Y (Pixels)")
    axes[0].grid(True, alpha=0.3)
    axes[0].legend()

    axes[1].plot(orig_ate, 'b-', lw=1.2, alpha=0.7, label=f'Original Normal (Max: {max_orig:.1f} px)')
    axes[1].plot(s4_ate, 'r-', lw=1.5, label=f'Step 4 KF+Skating (Max: {max_s4:.1f} px)')
    axes[1].set_title(f"ATE Error Over Frames ({tag_name})", fontsize=13)
    axes[1].set_xlabel("Frame Index")
    axes[1].set_ylabel("ATE Error (Pixels)")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()

    plt.tight_layout()
    plot_file = os.path.join(out_dir, f"step4_kalman_skating_benchmark_{tag_name}.png")
    plt.savefig(plot_file, dpi=150)
    plt.close()
    print(f"Saved benchmark figure to: {plot_file}")

    return {
        'tag': tag_name,
        'mean_orig': mean_orig,
        'mean_s4': mean_s4,
        'max_orig': max_orig,
        'max_s4': max_s4,
        'final_orig': final_orig,
        'final_s4': final_s4,
        'sk_count': sk_count,
        'plot_file': plot_file
    }


if __name__ == '__main__':
    raw1 = r'C:\Data\testPattern玻璃\Circle_R0.1inch_IPS0.005_Glass\POS1-1.raw'
    gth1 = r'C:\PROJECT\ToG_Tracker\step2_output\POS1-1_ground_truth.csv'
    fpn1 = r'C:\PROJECT\ToG_Tracker\step1_output\fpn_model.raw'
    out_dir = r'C:\PROJECT\ToG_Tracker\step3_output'
    
    res1 = run_benchmark(raw1, gth1, fpn1, out_dir, tag_name="Circle_R0.1_IPS0.005")
