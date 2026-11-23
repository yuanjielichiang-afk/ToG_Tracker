"""
========================================================================================
ToG Tracker Core: Optical Mouse Tracking Algorithm Engine for Track-on-Glass (ToG)
========================================================================================
本模組為光學滑鼠在玻璃表面（Track-on-Glass, ToG）之核心追蹤演算法主體。
包含完整且詳細的光學滑鼠訊號處理鏈：
  1. FPN (Fixed Pattern Noise) 背景減除與亮暗校準
  2. 特徵品質與紋理強度評估（SQUAL - Laplacian / Variance Kernel）
  3. 軌跡速度預測器（Trajectory Predictor - IIR / Inertial Motion Extrapolation）
  4. 局部受限搜尋與重疊區域相關匹配（3x3 / 5x5 ZNCC Area-Compensated Matching）
  5. 拋物線次像素峰值內插（Subpixel Parabolic Peak Interpolation, 1/16 px）
  6. 定點數輸出量化（Fixed-Point Output Quantization）
  7. 紋理閘控參考幀更新機制（Texture-Gated Keyframe Management）
  8. 速度突波防污染過濾器（Velocity Spike / Innovation Rejection Gate）

提供三種運作模式：
  - NormalModeTracker: 純自主閉環追蹤（無任何外部介入，依賴內部速度預測與 3x3 匹配）
  - TeacherModeTracker: 老師引導模式（自主運作，當預測位置脫離 Ground Truth 3x3 捕捉窗口時，注入 GTH 校正）
  - IdealModeTracker: 理想解耦模式（永遠以 Ground Truth 為搜尋中心，解耦預測與匹配）
========================================================================================
"""

import math
import numpy as np
from scipy.signal import convolve2d


class NormalModeTracker:
    """
    光學滑鼠自主追蹤主體 (Normal Mode)
    
    運作特點：
      - 完全閉環自持，不依賴任何外部 Ground Truth。
      - 僅在上一幀所預估的 3x3 (或 5x5) 窗口內做相關比對。
      - 具備速度突波防污染保護，防止網格交界翻轉產生的差分雜訊污染速度預測器。
    """
    def __init__(self, search_radius=1, subpixel_scale=16, 
                 keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                 fpn_model=None, max_ref_dist=6.0):
        self.r = search_radius
        self.subpixel_scale = subpixel_scale
        self.keyframe_dist = keyframe_dist
        self.squal_th = squal_th
        self.v_alpha = v_alpha
        self.fpn_model = fpn_model
        self.max_ref_dist = max_ref_dist

        # 核心卷積核
        self.laplacian_kernel = np.array([
            [ 0.0, -1.0,  0.0],
            [-1.0,  4.0, -1.0],
            [ 0.0, -1.0,  0.0]
        ], dtype=np.float32)

        # 狀態變數
        self.ref_frame = None          # 當前參考幀 (Keyframe)
        self.ref_idx = -1              # 參考幀所屬幀號
        self.last_dx_rel = 0.0         # 當前幀相對於參考幀的 X 累積位移
        self.last_dy_rel = 0.0         # 當前幀相對於參考幀的 Y 累積位移
        self.global_x = 0.0            # 追蹤器的全局 X 累積座標
        self.global_y = 0.0            # 追蹤器的全局 Y 累積座標
        self.v_x = 0.0                 # 內部 IIR 平滑速度預估 (px/frame)
        self.v_y = 0.0                 # 內部 IIR 平滑速度預估 (px/frame)

    def remove_fpn(self, raw_frame):
        """步驟 1: 消除感測器 Fixed Pattern Noise (FPN) 背景底噪"""
        if self.fpn_model is not None:
            return raw_frame.astype(np.float32) - self.fpn_model
        return raw_frame.astype(np.float32)

    def compute_squal(self, frame):
        """步驟 2: 計算特徵品質分數 (SQUAL - Laplacian Variance)"""
        lap = convolve2d(frame, self.laplacian_kernel, mode='valid')
        return float(np.var(lap))

    def predict_displacement(self):
        """步驟 3: 運動軌跡預測 (Trajectory Prediction)"""
        pred_dx_rel = self.last_dx_rel + self.v_x
        pred_dy_rel = self.last_dy_rel + self.v_y
        px = int(round(pred_dx_rel))
        py = int(round(pred_dy_rel))
        return px, py, pred_dx_rel, pred_dy_rel

    def compute_correlation_surface(self, cur, ref, px, py):
        """步驟 4: 局部受限搜尋與重疊區域正規化互相關 (Area-Compensated ZNCC)"""
        h, w = cur.shape
        r = self.r
        surface = np.zeros((2*r + 1, 2*r + 1), dtype=np.float32)

        margin = r + 2
        ry_s, rx_s = margin, margin
        roi_h, roi_w = h - 2*margin, w - 2*margin
        ref_roi = ref[ry_s:ry_s+roi_h, rx_s:rx_s+roi_w]

        ref_zero = ref_roi - np.mean(ref_roi)
        ref_std = np.std(ref_zero) + 1e-6
        ref_norm = ref_zero / ref_std

        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                cy_s = ry_s + py + dy
                cx_s = rx_s + px + dx

                y1 = max(0, cy_s)
                y2 = min(h, cy_s + roi_h)
                x1 = max(0, cx_s)
                x2 = min(w, cx_s + roi_w)

                if y2 <= y1 or x2 <= x1:
                    surface[dy+r, dx+r] = -1.0
                    continue

                cur_sub = cur[y1:y2, x1:x2]
                ry_off = y1 - cy_s
                rx_off = x1 - cx_s
                ref_sub_norm = ref_norm[ry_off:ry_off+(y2-y1), rx_off:rx_off+(x2-x1)]

                cur_zero = cur_sub - np.mean(cur_sub)
                cur_std = np.std(cur_zero) + 1e-6
                cur_sub_norm = cur_zero / cur_std

                score = np.mean(ref_sub_norm * cur_sub_norm)
                surface[dy+r, dx+r] = score

        return surface

    def parabolic_interpolate(self, s1, s2, s3):
        """步驟 5: 1D 拋物線次像素峰值擬合"""
        if not (math.isfinite(s1) and math.isfinite(s2) and math.isfinite(s3)):
            return 0.0
        num = s1 - s3
        den = 2.0 * (s1 + s3 - 2.0 * s2)
        if abs(den) < 1e-6:
            return 0.0
        offset = num / den
        return max(-0.5, min(0.5, offset))

    def update_keyframe_if_needed(self, cur_frame, frame_idx, squal):
        """
        步驟 7: 紋理閘控參考幀更新機制 (含最大距離強制換幀保護)
        """
        dist_from_ref = math.sqrt(self.last_dx_rel**2 + self.last_dy_rel**2)
        # 條件 A: 累積位移達到門檻且紋理足夠
        # 條件 B: 位移超過視野極限 (max_ref_dist)，強制更新，避免舊特徵徹底出界
        if (dist_from_ref >= self.keyframe_dist and squal >= self.squal_th) or (dist_from_ref >= self.max_ref_dist):
            self.ref_frame = cur_frame
            self.ref_idx = frame_idx
            self.last_dx_rel = 0.0
            self.last_dy_rel = 0.0
            return True
        return False

    def update_velocity_filter(self, frame_dx, frame_dy):
        """
        步驟 8: 速度突波防污染過濾器 (Velocity Spike / Innovation Gate)
        """
        max_phys_v = 0.40 # 物理最高合理幀速度 (真實值約 0.206)
        # 檢查是否為網格翻轉產生的突波
        diff_vx = abs(frame_dx - self.v_x)
        diff_vy = abs(frame_dy - self.v_y)
        
        # 若單幀速度超過合理物理範圍，予以飽和鉗位，保護 IIR 預測器不被污染
        clamped_dx = max(-max_phys_v, min(max_phys_v, frame_dx))
        clamped_dy = max(-max_phys_v, min(max_phys_v, frame_dy))
        
        if diff_vx > 0.45 or diff_vy > 0.45:
            # 劇烈跳變：判定為交界翻轉突波，減小更新權重
            alpha = self.v_alpha * 0.2
        else:
            alpha = self.v_alpha

        self.v_x = (1.0 - alpha) * self.v_x + alpha * clamped_dx
        self.v_y = (1.0 - alpha) * self.v_y + alpha * clamped_dy

    def step(self, raw_frame, frame_idx):
        """自主模式單幀處理主流程"""
        cur_frame = self.remove_fpn(raw_frame)
        squal = self.compute_squal(cur_frame)

        if self.ref_frame is None:
            self.ref_frame = cur_frame
            self.ref_idx = frame_idx
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
                'ref_idx': self.ref_idx
            }

        # 預測搜尋中心
        px, py, pred_dx_rel, pred_dy_rel = self.predict_displacement()
        pred_gx = (self.global_x - self.last_dx_rel) + pred_dx_rel
        pred_gy = (self.global_y - self.last_dy_rel) + pred_dy_rel

        # 3x3 相關匹配
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

        # 更新速度濾波器 (防污染保護)
        self.update_velocity_filter(frame_dx, frame_dy)

        # 更新全局累積位置
        self.global_x += frame_dx
        self.global_y += frame_dy
        self.last_dx_rel = dx_q
        self.last_dy_rel = dy_q

        # 更新 Keyframe
        self.update_keyframe_if_needed(cur_frame, frame_idx, squal)

        return {
            'frame': frame_idx,
            'est_x': self.global_x,
            'est_y': self.global_y,
            'est_dx': frame_dx,
            'est_dy': frame_dy,
            'pred_x': pred_gx,
            'pred_y': pred_gy,
            'score': best_score,
            'squal': squal,
            'ref_idx': self.ref_idx
        }


class TeacherModeTracker(NormalModeTracker):
    """
    老師引導模式 (Teacher Mode)
    
    運作特點：
      - 平時以自主模式進行預測與比對。
      - 每幀檢查預測位置是否落在 Ground Truth (GTH) 的 3x3 捕捉窗口 (Capture Basin: 誤差 <= 1.0 px) 內。
      - 當預測脫靶 (Mismatch) 時，老師 (Ground Truth) 強制介入，重設搜尋中心與速度。
    """
    def step(self, raw_frame, frame_idx, gth_pos, gth_vel):
        cur_frame = self.remove_fpn(raw_frame)
        squal = self.compute_squal(cur_frame)

        if self.ref_frame is None:
            self.ref_frame = cur_frame
            self.ref_idx = frame_idx
            self.global_x = gth_pos[0]
            self.global_y = gth_pos[1]
            return {
                'frame': frame_idx,
                'est_x': self.global_x,
                'est_y': self.global_y,
                'est_dx': 0.0,
                'est_dy': 0.0,
                'pred_x': self.global_x,
                'pred_y': self.global_y,
                'gth_x': gth_pos[0],
                'gth_y': gth_pos[1],
                'in_basin': True,
                'teacher_intervened': False,
                'score': 1.0,
                'squal': squal,
                'ref_idx': self.ref_idx
            }

        # 1. 自主預測
        px_auto, py_auto, pred_dx_rel, pred_dy_rel = self.predict_displacement()
        pred_gx = (self.global_x - self.last_dx_rel) + pred_dx_rel
        pred_gy = (self.global_y - self.last_dy_rel) + pred_dy_rel

        # 2. 捕捉窗口判斷 (Capture Basin Check)
        pos_err_x = abs(round(pred_gx) - gth_pos[0])
        pos_err_y = abs(round(pred_gy) - gth_pos[1])
        in_basin = bool(pos_err_x <= 1.0 and pos_err_y <= 1.0)

        teacher_intervened = False
        if in_basin:
            px, py = px_auto, py_auto
        else:
            teacher_intervened = True
            ref_origin_x = self.global_x - self.last_dx_rel
            ref_origin_y = self.global_y - self.last_dy_rel
            px = int(round(gth_pos[0] - ref_origin_x))
            py = int(round(gth_pos[1] - ref_origin_y))
            self.v_x = gth_vel[0]
            self.v_y = gth_vel[1]

        # 3. 相關匹配
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

        # 速度更新 (防污染過濾)
        self.update_velocity_filter(frame_dx, frame_dy)

        self.global_x += frame_dx
        self.global_y += frame_dy
        self.last_dx_rel = dx_q
        self.last_dy_rel = dy_q

        self.update_keyframe_if_needed(cur_frame, frame_idx, squal)

        return {
            'frame': frame_idx,
            'est_x': self.global_x,
            'est_y': self.global_y,
            'est_dx': frame_dx,
            'est_dy': frame_dy,
            'pred_x': pred_gx,
            'pred_y': pred_gy,
            'gth_x': gth_pos[0],
            'gth_y': gth_pos[1],
            'in_basin': in_basin,
            'teacher_intervened': teacher_intervened,
            'score': best_score,
            'squal': squal,
            'ref_idx': self.ref_idx
        }


class IdealModeTracker(NormalModeTracker):
    """
    理想解耦模式 (Ideal Mode)
    
    運作特點：
      - 永遠以 Ground Truth 相對於參考幀的真實位移作為搜尋中心。
      - 解耦預測誤差，單純檢驗特徵相關比對能力。
    """
    def step(self, raw_frame, frame_idx, gth_pos, gth_vel):
        cur_frame = self.remove_fpn(raw_frame)
        squal = self.compute_squal(cur_frame)

        if self.ref_frame is None:
            self.ref_frame = cur_frame
            self.ref_idx = frame_idx
            self.global_x = gth_pos[0]
            self.global_y = gth_pos[1]
            return {
                'frame': frame_idx,
                'est_x': self.global_x,
                'est_y': self.global_y,
                'est_dx': 0.0,
                'est_dy': 0.0,
                'gth_x': gth_pos[0],
                'gth_y': gth_pos[1],
                'score': 1.0,
                'squal': squal,
                'ref_idx': self.ref_idx
            }

        # 永遠鎖定真值為中心
        ref_origin_x = self.global_x - self.last_dx_rel
        ref_origin_y = self.global_y - self.last_dy_rel
        px = int(round(gth_pos[0] - ref_origin_x))
        py = int(round(gth_pos[1] - ref_origin_y))

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

        # 速度更新 (防污染過濾)
        self.update_velocity_filter(frame_dx, frame_dy)

        self.global_x += frame_dx
        self.global_y += frame_dy
        self.last_dx_rel = dx_q
        self.last_dy_rel = dy_q

        self.update_keyframe_if_needed(cur_frame, frame_idx, squal)

        return {
            'frame': frame_idx,
            'est_x': self.global_x,
            'est_y': self.global_y,
            'est_dx': frame_dx,
            'est_dy': frame_dy,
            'gth_x': gth_pos[0],
            'gth_y': gth_pos[1],
            'score': best_score,
            'squal': squal,
            'ref_idx': self.ref_idx
        }
