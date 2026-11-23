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
  7. 紋理閘控參考幀更新機制（Texture-Gated Keyframe Management, 防止毒化空白幀）

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
      - 當進入空白無特徵區時，參考幀維持凍結，避免記錄空白噪聲；由內部慣性速度持續外推。
    """
    def __init__(self, search_radius=1, subpixel_scale=16, 
                 keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                 fpn_model=None):
        """
        參數說明：
          search_radius: 搜尋半徑 (1 表示 3x3 窗口, 2 表示 5x5 窗口)
          subpixel_scale: 次像素量化解析度 (16 表示 1/16 pixel)
          keyframe_dist: 更新參考幀的位移閾值 (pixels)
          squal_th: 允許更新參考幀的最小紋理品質閾值 (防止空白區毒化)
          v_alpha: 速度 IIR 低通平滑濾波系數 (0~1)
          fpn_model: 預先計算之固定圖樣噪聲矩陣 (36x36 float32)
        """
        self.r = search_radius
        self.subpixel_scale = subpixel_scale
        self.keyframe_dist = keyframe_dist
        self.squal_th = squal_th
        self.v_alpha = v_alpha
        self.fpn_model = fpn_model

        # 核心卷積核定義
        # 1. 2D 離散拉普拉斯卷積核（Laplacian Kernel）: 用於計算高頻邊緣特徵強度與 SQUAL
        self.laplacian_kernel = np.array([
            [ 0.0, -1.0,  0.0],
            [-1.0,  4.0, -1.0],
            [ 0.0, -1.0,  0.0]
        ], dtype=np.float32)

        # 2. 水平與垂直 Sobel 梯度特徵核
        self.sobel_x = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32) / 8.0
        self.sobel_y = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=np.float32) / 8.0

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
        """
        步驟 2: 計算特徵品質分數 (Surface Quality, SQUAL)
        
        數學原理：
          以 3x3 離散拉普拉斯算子與影像進行 2D 卷積，提取高頻微塵粒子邊緣。
          SQUAL 定義為拉普拉斯響應的統計變異數 Var(Laplacian)。
          在純淨空白玻璃上，Var(Laplacian) 接近底噪 (< 5.0 counts^2)；
          當視野內出現微塵粒子時，Var(Laplacian) 激增至 20~200+ counts^2。
        """
        lap = convolve2d(frame, self.laplacian_kernel, mode='valid')
        return float(np.var(lap))

    def predict_displacement(self):
        """
        步驟 3: 運動軌跡預測 (Trajectory Prediction)
        
        數學原理：
          在短時間內，物理運動具備動量連續性。
          預測當前幀相對於參考幀的位置 = 上一幀相對位移 + 內部平滑速度 (IIR Velocity)。
            pred_dx_rel = last_dx_rel + v_x
            pred_dy_rel = last_dy_rel + v_y
          局部搜尋中心 (px, py) 取整數網格：
            px = round(pred_dx_rel)
            py = round(pred_dy_rel)
        """
        pred_dx_rel = self.last_dx_rel + self.v_x
        pred_dy_rel = self.last_dy_rel + self.v_y
        px = int(round(pred_dx_rel))
        py = int(round(pred_dy_rel))
        return px, py, pred_dx_rel, pred_dy_rel

    def compute_correlation_surface(self, cur, ref, px, py):
        """
        步驟 4: 局部受限搜尋與重疊區域正規化互相關 (Area-Compensated ZNCC)
        
        搜尋窗口大小: (2r + 1) x (2r + 1)
          - 當 r = 1 時為 3x3 搜尋窗口 (dx in {-1, 0, 1}, dy in {-1, 0, 1})
          - 當 r = 2 時為 5x5 搜尋窗口
          
        數學原理：
          零均值正規化互相關 (Zero-mean Normalized Cross-Correlation, ZNCC)：
            Score(u, v) = sum( (I_cur - mu_cur) * (I_ref - mu_ref) ) / (sigma_cur * sigma_ref * N)
          範圍為 [-1.0, 1.0]，對光照亮度波動、LED 衰減完全不敏感。
          針對位移導致邊界像素裁切的情況，進行動態有效重疊面積補償 (Area Compensation)。
        """
        h, w = cur.shape
        r = self.r
        surface = np.zeros((2*r + 1, 2*r + 1), dtype=np.float32)

        # 提取參考幀中心有效 ROI 區域（預留邊界 margin = r + 2）
        margin = r + 2
        ry_s, rx_s = margin, margin
        roi_h, roi_w = h - 2*margin, w - 2*margin
        ref_roi = ref[ry_s:ry_s+roi_h, rx_s:rx_s+roi_w]

        # 零均值標準化參考範本
        ref_zero = ref_roi - np.mean(ref_roi)
        ref_std = np.std(ref_zero) + 1e-6
        ref_norm = ref_zero / ref_std

        # 在 (px + dx, py + dy) 局部窗口遍歷比對
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                cy_s = ry_s + py + dy
                cx_s = rx_s + px + dx

                # 邊界防禦檢查
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

                # 計算重疊區域均值乘積
                score = np.mean(ref_sub_norm * cur_sub_norm)
                surface[dy+r, dx+r] = score

        return surface

    def parabolic_interpolate(self, s1, s2, s3):
        """
        步驟 5: 1D 拋物線次像素峰值內插 (Parabolic Subpixel Peak Fitting)
        
        給定三點相關係數值: s1 = Score(-1), s2 = Score(0) (頂峰), s3 = Score(+1)
        二次拋物線極值點解析解：
          delta = (s1 - s3) / ( 2 * (s1 + s3 - 2 * s2) )
        約束於 [-0.5, 0.5] 像素之內。
        """
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
        步驟 7: 紋理閘控參考幀更新機制 (Texture-Gated Keyframe Management)
        
        防止參考幀毒化核心策略：
          條件 1 (空間位移足夠): 與當前參考幀累積位移 dist >= keyframe_dist (例如 1.5 像素)
          條件 2 (紋理品質合格): 當前幀 SQUAL >= squal_th (確保非空白玻璃)
          只有兩者同時滿足，才更新 Keyframe！
          若進入空白無特徵區，即使位移已遠，也絕對不更新參考幀，避免空白底噪成為比對範本。
        """
        dist_from_ref = math.sqrt(self.last_dx_rel**2 + self.last_dy_rel**2)
        if dist_from_ref >= self.keyframe_dist:
            if squal >= self.squal_th:
                self.ref_frame = cur_frame
                self.ref_idx = frame_idx
                self.last_dx_rel = 0.0
                self.last_dy_rel = 0.0
                return True
            else:
                # 紋理不足：凍結參考幀！
                return False
        return False

    def step(self, raw_frame, frame_idx):
        """
        處理單一影像幀之主流程
        
        返回 dict 包含：
          - est_x, est_y: 估算之全局絕對座標
          - est_dx, est_dy: 該幀估算之微小增量位移
          - pred_x, pred_y: 預測之全局座標
          - score: 最佳匹配相關係數 (ZNCC Score)
          - squal: 當前影像紋理強度
          - ref_idx: 當前所屬參考幀索引
        """
        # 1. FPN 背景校準
        cur_frame = self.remove_fpn(raw_frame)
        squal = self.compute_squal(cur_frame)

        # 第 0 幀初始化
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

        # 2. 軌跡速度預測
        px, py, pred_dx_rel, pred_dy_rel = self.predict_displacement()
        pred_gx = (self.global_x - self.last_dx_rel) + pred_dx_rel
        pred_gy = (self.global_y - self.last_dy_rel) + pred_dy_rel

        # 3. 3x3 局部重疊相關比對
        surface = self.compute_correlation_surface(cur_frame, self.ref_frame, px, py)

        # 4. 尋找整數頂峰
        r = self.r
        best_idx = np.unravel_index(np.argmax(surface), surface.shape)
        v_idx, u_idx = best_idx
        best_score = float(surface[v_idx, u_idx])

        dx_int = px + (u_idx - r)
        dy_int = py + (v_idx - r)

        # 5. 次像素拋物線擬合
        dx_sub, dy_sub = 0.0, 0.0
        if 0 < u_idx < 2*r:
            dx_sub = self.parabolic_interpolate(surface[v_idx, u_idx-1], surface[v_idx, u_idx], surface[v_idx, u_idx+1])
        if 0 < v_idx < 2*r:
            dy_sub = self.parabolic_interpolate(surface[v_idx-1, u_idx], surface[v_idx, u_idx], surface[v_idx+1, u_idx])

        dx_tot = dx_int + dx_sub
        dy_tot = dy_int + dy_sub

        # 6. 定點數次像素量化 (1/16 px)
        dx_q = round(dx_tot * self.subpixel_scale) / self.subpixel_scale
        dy_q = round(dy_tot * self.subpixel_scale) / self.subpixel_scale

        # 計算本幀幀間增量位移
        frame_dx = dx_q - self.last_dx_rel
        frame_dy = dy_q - self.last_dy_rel

        # 7. 更新內部 IIR 速度濾波器
        self.v_x = (1.0 - self.v_alpha) * self.v_x + self.v_alpha * frame_dx
        self.v_y = (1.0 - self.v_alpha) * self.v_y + self.v_alpha * frame_dy

        # 8. 更新累積全局座標與相對位移
        self.global_x += frame_dx
        self.global_y += frame_dy
        self.last_dx_rel = dx_q
        self.last_dy_rel = dy_q

        # 9. 紋理閘控關鍵幀更新
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
      - 當預測脫靶 (Mismatch) 時，老師 (Ground Truth) 強制介入，將 GTH 指派給當前搜尋中心與速度濾波器，
        防止因局部空白區導致後續整條軌跡崩潰發散。
      - 統計「Teacher 介入次數」即可精準客觀量化演算法在長程追蹤中的失敗頻率。
    """
    def step(self, raw_frame, frame_idx, gth_pos, gth_vel):
        """
        gth_pos: (gth_x, gth_y)
        gth_vel: (gth_dx, gth_dy)
        """
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
            # 預測脫靶：老師介入！
            teacher_intervened = True
            ref_origin_x = self.global_x - self.last_dx_rel
            ref_origin_y = self.global_y - self.last_dy_rel
            px = int(round(gth_pos[0] - ref_origin_x))
            py = int(round(gth_pos[1] - ref_origin_y))
            # 重設速度預測器至真值速度
            self.v_x = gth_vel[0]
            self.v_y = gth_vel[1]

        # 3. 相關匹配
        surface = self.compute_correlation_surface(cur_frame, self.ref_frame, px, py)

        r = self.r
        best_idx = np.unravel_index(np.argmax(surface), surface.shape)
        v_idx, u_idx = best_idx
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

        self.v_x = (1.0 - self.v_alpha) * self.v_x + self.v_alpha * frame_dx
        self.v_y = (1.0 - self.v_alpha) * self.v_y + self.v_alpha * frame_dy

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
      - 永遠以 Ground Truth 的真實位置作為搜尋中心。
      - 完全排除軌跡預測誤差對匹配的干擾。
      - 用於評估「當預測 100% 準確時，特徵匹配與次像素內插所能達到的理論極限精度」。
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
        best_idx = np.unravel_index(np.argmax(surface), surface.shape)
        v_idx, u_idx = best_idx
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
