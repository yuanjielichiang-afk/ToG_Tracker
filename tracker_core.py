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
    def __init__(self, search_radius=1, subpixel_scale=32, 
                 keyframe_dist=2.5, squal_th=5.0, v_alpha=0.25,
                 fpn_model=None, max_ref_dist=6.0,
                 use_corner_filter=False, corner_flat_bias=2.0,
                 use_bandpass_filter=False, bandpass_type='bandpass3x7',
                 subpixel_method='parabolic_2d',
                 max_phys_v=None,
                 static_bias_table=None):
        self.r = search_radius
        self.subpixel_scale = subpixel_scale
        self.keyframe_dist = keyframe_dist
        self.squal_th = squal_th
        self.v_alpha = v_alpha
        self.fpn_model = fpn_model
        self.max_ref_dist = max_ref_dist
        self.use_corner_filter = use_corner_filter
        self.corner_flat_bias = corner_flat_bias
        self.use_bandpass_filter = use_bandpass_filter
        self.bandpass_type = bandpass_type.lower() if bandpass_type else 'bandpass3x7'
        self.subpixel_method = subpixel_method
        self.max_phys_v = max_phys_v if max_phys_v is not None else max(0.5, self.r * 1.0)
        self.static_bias_table = static_bias_table
        if static_bias_table is not None:
            self.csbc_max_lag = (static_bias_table.shape[0] - 1) // 2
            self.csbc_bs00 = max(1e-4, float(static_bias_table[self.csbc_max_lag, self.csbc_max_lag]))
        else:
            self.csbc_max_lag = 0
            self.csbc_bs00 = 0.0

        # 核心卷積核
        self.laplacian_kernel = np.array([
            [ 0.0, -1.0,  0.0],
            [-1.0,  4.0, -1.0],
            [ 0.0, -1.0,  0.0]
        ], dtype=np.float32)

        # 0a. 第三階段 IC 極致輕量化主力：3x7 扁長方形零均值帶通核 (僅需 2 條 Line Buffer! 21 MAC / 對稱摺疊僅 6 乘法)
        # 中心補償至 116 達成嚴格零均值 (sum = 0.0)，消除二次拋物面 Lens Shading 空間變異，且垂直僅 3 列不引進上下光斑截止懸崖假影
        self.k_bandpass3x7 = np.array([
            [-1, -11, -15,   7, -15, -11, -1],
            [-1, -17,   7, 116,   7, -17, -1],
            [-1, -11, -15,   7, -15, -11, -1]
        ], dtype=np.float32)
        self.k_bandpass3x7_norm = self.k_bandpass3x7 / (np.sum(np.abs(self.k_bandpass3x7)) + 1e-6) * 4.0

        # 0b. 3x7 逐列零和雙消失矩帶通核 (每一列水平總和嚴格為 0，100% 物理消滅水平 Row FPN 與二次拋物面光暈，僅需 2 條 Line Buffer)
        self.k_bandpass3x7_r0 = np.array([
            [ 4,  -4,  -8,  16,  -8,  -4,  4],
            [ 6, -20,   0,  28,   0, -20,  6],
            [ 4,  -4,  -8,  16,  -8,  -4,  4]
        ], dtype=np.float32)
        self.k_bandpass3x7_r0_norm = self.k_bandpass3x7_r0 / (np.sum(np.abs(self.k_bandpass3x7_r0)) + 1e-6) * 4.0

        # 0c. 5x7 扁長方形零均值帶通核 (需 4 條 Line Buffer, 35 MAC, 中心補償至 206 使總和為 0.0)
        self.k_bandpass5x7 = np.array([
            [ 2,  -5, -11, -17, -11,  -5,   2],
            [-1, -11, -15,   7, -15, -11,  -1],
            [-1, -17,   7, 206,   7, -17,  -1],
            [-1, -11, -15,   7, -15, -11,  -1],
            [ 2,  -5, -11, -17, -11,  -5,   2]
        ], dtype=np.float32)
        self.k_bandpass5x7_norm = self.k_bandpass5x7 / (np.sum(np.abs(self.k_bandpass5x7)) + 1e-6) * 4.0

        # 1. OptNavi.cpp 原廠 7x7 同心零均值帶通卷積核 (需要 6~7 條 Line Buffer, 49 次 MAC, 總和嚴格為 0.0)
        # 由 10 個同心圓半徑係數 d = [184, 7, -15, -17, -11, -5, -1, -1, 2, 5] 嚴格按距離平方 r^2 分佈
        self.k_bandpass7 = np.array([
            [ 5,   2,  -1,  -1,  -1,   2,   5],
            [ 2,  -5, -11, -17, -11,  -5,   2],
            [-1, -11, -15,   7, -15, -11,  -1],
            [-1, -17,   7, 184,   7, -17,  -1],
            [-1, -11, -15,   7, -15, -11,  -1],
            [ 2,  -5, -11, -17, -11,  -5,   2],
            [ 5,   2,  -1,  -1,  -1,   2,   5]
        ], dtype=np.float32)
        self.k_bandpass7_norm = self.k_bandpass7 / (np.sum(np.abs(self.k_bandpass7)) + 1e-6) * 4.0

        # 2. 5x5 同心零均值帶通卷積核 (僅需 5 條 Line Buffer，SRAM 減省 28.6%~44.4%，25 次 MAC 減省 49%~69%)
        # 中心補償至 208 使總和恰好為 0 (零直流偏置，徹底消除低頻不均勻光暈)
        self.k_bandpass5 = np.array([
            [-5, -11, -17, -11, -5],
            [-11, -15,   7, -15, -11],
            [-17,   7, 208,   7, -17],
            [-11, -15,   7, -15, -11],
            [-5, -11, -17, -11, -5]
        ], dtype=np.float32)
        self.k_bandpass5_norm = self.k_bandpass5 / (np.sum(np.abs(self.k_bandpass5)) + 1e-6) * 4.0

        # 3. 5x5 可分離 1D 卷積分解 (SVD Rank-1 逼近，運算量降至 5+5=10 MAC，節省 80%~88% 算力)
        v_sep = np.array([1.160315, -0.524294, -14.422272, -0.524294, 1.160315], dtype=np.float32)
        sep_2d = np.outer(v_sep, v_sep)
        scale = math.sqrt(4.0 / (np.sum(np.abs(sep_2d)) + 1e-6))
        self.k_bandpass5_1d = v_sep * scale

        # 4. 舊版相容 9x9 同心帶通卷積核 (需要 9 條 Line Buffer, 81 次 MAC)
        d = [184, 7, -15, -17, -11, -5, -1, -1, 2, 5]
        self.k_bandpass9 = np.array([
            [d[4], d[3], d[2], d[1], d[0], d[1], d[2], d[3], d[4]],
            [d[3], d[9], d[8], d[7], d[6], d[7], d[8], d[9], d[3]],
            [d[2], d[8], d[5], d[4], d[3], d[4], d[5], d[8], d[2]],
            [d[1], d[7], d[4], d[2], d[1], d[2], d[4], d[7], d[1]],
            [d[0], d[6], d[3], d[1], d[0], d[1], d[3], d[6], d[0]],
            [d[1], d[7], d[4], d[2], d[1], d[2], d[4], d[7], d[1]],
            [d[2], d[8], d[5], d[4], d[3], d[4], d[5], d[8], d[2]],
            [d[3], d[9], d[8], d[7], d[6], d[7], d[8], d[9], d[3]],
            [d[4], d[3], d[2], d[1], d[0], d[1], d[2], d[3], d[4]]
        ], dtype=np.float32)
        self.k_bandpass9_norm = self.k_bandpass9 / (np.sum(np.abs(self.k_bandpass9)) + 1e-6) * 4.0

        # OptNavi.cpp 4-Corner 紋理對齊卷積核 (打破滑鼠墊經緯編織 repeat pattern)
        self.k_cor000 = np.array([
            [ 0.0, -1.0,  0.0],
            [ 0.0,  2.0,  0.0],
            [ 0.0, -1.0,  0.0]
        ], dtype=np.float32)
        self.k_cor045 = np.array([
            [-1.0,  0.0,  0.0],
            [ 0.0,  2.0,  0.0],
            [ 0.0,  0.0, -1.0]
        ], dtype=np.float32)
        self.k_cor090 = self.k_cor000.T
        self.k_cor135 = np.fliplr(self.k_cor045)

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

    def apply_bandpass_filter(self, frame):
        """
        帶通濾波器前處理：凸顯微米微塵顆粒邊緣並濾除低頻光暈漸變。
        支援：
          - 'bandpass3x7'    : 3x7 扁長方形零均值核 (IC 首選！僅需 2 條 Line Buffer, 21 MAC / 對稱僅 6 乘法, valid 內縮無邊界假影)
          - 'bandpass3x7_r0' : 3x7 逐列零和雙消失矩核 (僅需 2 條 Line Buffer, 100% 消滅水平 Row FPN 與二次光暈)
          - 'bandpass5x7'    : 5x7 扁長方形零均值核 (需 4 條 Line Buffer, 35 MAC)
          - 'bandpass7'      : 7x7 同心零均值核 (原廠基準，需 6 條 Line Buffer, 49 MAC)
          - 'bandpass5'      : 5x5 同心零均值核 (需 4 條 Line Buffer, 25 MAC)
          - 'bandpass5_sep'  : 5x5 可分離 1D 水平+1D 垂直卷積 (需 4 條 Line Buffer, 10 MAC)
          - 'bandpass9'      : 舊版 9x9 帶通核 (需 8 條 Line Buffer, 81 MAC)
        """
        if self.bandpass_type == 'bandpass3x7':
            return convolve2d(frame, self.k_bandpass3x7_norm, mode='valid')
        elif self.bandpass_type == 'bandpass3x7_r0':
            return convolve2d(frame, self.k_bandpass3x7_r0_norm, mode='valid')
        elif self.bandpass_type == 'bandpass5x7':
            return convolve2d(frame, self.k_bandpass5x7_norm, mode='valid')
        elif self.bandpass_type == 'bandpass7':
            return convolve2d(frame, self.k_bandpass7_norm, mode='same', boundary='symm')
        elif self.bandpass_type == 'bandpass5':
            return convolve2d(frame, self.k_bandpass5_norm, mode='same', boundary='symm')
        elif self.bandpass_type == 'bandpass5_sep':
            v_kernel = self.k_bandpass5_1d.reshape(-1, 1)
            h_kernel = self.k_bandpass5_1d.reshape(1, -1)
            tmp = convolve2d(frame, v_kernel, mode='same', boundary='symm')
            return convolve2d(tmp, h_kernel, mode='same', boundary='symm')
        elif self.bandpass_type == 'bandpass9':
            return convolve2d(frame, self.k_bandpass9_norm, mode='same', boundary='symm')
        else:
            return frame

    def preprocess_frame(self, raw_frame):
        """
        影像前處理標準管線：
          - 若啟用單級 Corner 模式且關閉帶通 (滑鼠墊極簡模式)，僅需 2 條 Line Buffer 直接抽取 4-Corner 特徵，免串聯！
        """
        cur_frame = self.remove_fpn(raw_frame)
        if self.use_bandpass_filter:
            cur_frame = self.apply_bandpass_filter(cur_frame)
        if self.use_corner_filter:
            cur_frame = self.apply_corner_filter(cur_frame)
        return cur_frame

    def apply_corner_filter(self, frame):
        """
        OptNavi::xSurfaceAlignFilterCorner 實現:
        利用 4 方向 (0°, 45°, 90°, 135°) 二階差分卷積，檢測各方向的紋理響應。
        當檢測到強方向性 (abs(max) - abs(min) > abs(no_align) + bias) 時，
        判定為布質滑鼠墊的經緯編織週期條紋 (1D Repeat Pattern)，
        主動抑制主方向強度，改取最小響應 abs(minAlignVal)，打破週期織紋假峰，消除次像素鋸齒狀跳動。
        """
        c000 = convolve2d(frame, self.k_cor000, mode='same', boundary='symm')
        c045 = convolve2d(frame, self.k_cor045, mode='same', boundary='symm')
        c090 = convolve2d(frame, self.k_cor090, mode='same', boundary='symm')
        c135 = convolve2d(frame, self.k_cor135, mode='same', boundary='symm')

        no_align = (c045 + c135) * 0.5
        cor_stack = np.stack([c000, c045, c090, c135], axis=-1)
        abs_cor = np.abs(cor_stack)

        max_idx = np.argmax(abs_cor, axis=-1)
        min_idx = np.argmin(abs_cor, axis=-1)
        max_val = np.take_along_axis(cor_stack, max_idx[..., None], axis=-1).squeeze(-1)
        min_val = np.take_along_axis(cor_stack, min_idx[..., None], axis=-1).squeeze(-1)

        cond = (np.abs(max_val) - np.abs(min_val)) > (np.abs(no_align) + self.corner_flat_bias)
        surf_align = np.where(cond, np.abs(min_val), no_align)
        return surf_align

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

    def eval_csbc_cov(self, ref_f, cur_f, ux, uy):
        """
        互相關域靜態偏置扣除法 (Correlation-Domain Static Bias Cancellation, CSBC)：
        不需儲存 36x36 像素級 FPN 影像 (0 Bytes 影像級 FPN！)，
        直接在重疊區協方差中減去微小中心對稱純量偏置表 B_S(uy, ux) (僅 41 個 16-bit 純量 = 82 Bytes)，
        精確消除感測器高頻固定像素散斑 / PRNU 在零位移處的自相關吸附尖峰。
        """
        h, w = ref_f.shape
        y1 = max(0, -uy)
        y2 = min(h, h - uy)
        x1 = max(0, -ux)
        x2 = min(w, w - ux)
        if y2 - y1 < 8 or x2 - x1 < 8:
            return -1e6
        subR = ref_f[y1:y2, x1:x2]
        subC = cur_f[y1+uy:y2+uy, x1+ux:x2+ux]
        subR_z = subR - np.mean(subR)
        subC_z = subC - np.mean(subC)
        cov = float(np.mean(subR_z * subC_z))
        L = self.csbc_max_lag
        if self.static_bias_table is not None and abs(ux) <= L and abs(uy) <= L:
            cov -= float(self.static_bias_table[uy + L, ux + L])
        return cov

    def compute_correlation_surface(self, cur, ref, px, py):
        """步驟 4: 局部受限搜尋與重疊區域互相關 (支援 CSBC 無影像 FPN 扣除模式 或 標準 ZNCC)"""
        r = self.r
        surface = np.zeros((2*r + 1, 2*r + 1), dtype=np.float32)

        if self.static_bias_table is not None:
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    surface[dy + r, dx + r] = self.eval_csbc_cov(ref, cur, px + dx, py + dy)
            return surface

        h, w = cur.shape
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

    def parabolic_2d(self, patch):
        """
        步驟 5 (升級版): 2D 橢圓拋物面最小平方法峰值解析解
        在 3x3 互相關曲面上擬合二元二次曲面: z(x,y) = a0 + a1*x + a2*y + a3*x^2 + a4*y^2 + a5*x*y
        考慮對角交叉耦合項 a5*x*y，徹底消除獨立 1D 拋物線在斜向圓弧運動下的幾何咬合偏差 (Peak-Locking Bias)。
        """
        z = patch
        a1 = float(np.sum(z[:, 2] - z[:, 0]) / 6.0)
        a2 = float(np.sum(z[2, :] - z[0, :]) / 6.0)
        a3 = float(np.sum(z[:, 2] + z[:, 0] - 2.0 * z[:, 1]) / 6.0)
        a4 = float(np.sum(z[2, :] + z[0, :] - 2.0 * z[1, :]) / 6.0)
        a5 = float((z[2, 2] - z[2, 0] - z[0, 2] + z[0, 0]) / 4.0)

        det = 4.0 * a3 * a4 - a5 * a5
        if det <= 1e-6 or a3 >= 0 or a4 >= 0:
            return 0.0, 0.0

        dx = (-2.0 * a4 * a1 + a5 * a2) / det
        dy = (-2.0 * a3 * a2 + a5 * a1) / det
        return max(-0.5, min(0.5, dx)), max(-0.5, min(0.5, dy))

    def compute_subpixel_offset(self, surface, v_idx, u_idx):
        """依據 subpixel_method 計算次像素峰值位移 (dx_sub, dy_sub)"""
        r = self.r
        if not (0 < u_idx < 2*r and 0 < v_idx < 2*r):
            return 0.0, 0.0
        if self.subpixel_method == 'parabolic_2d':
            patch = surface[v_idx-1:v_idx+2, u_idx-1:u_idx+2]
            return self.parabolic_2d(patch)
        else:
            dx_sub = self.parabolic_interpolate(surface[v_idx, u_idx-1], surface[v_idx, u_idx], surface[v_idx, u_idx+1])
            dy_sub = self.parabolic_interpolate(surface[v_idx-1, u_idx], surface[v_idx, u_idx], surface[v_idx+1, u_idx])
            return dx_sub, dy_sub

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
        max_phys_v = self.max_phys_v
        # 檢查是否為網格翻轉產生的突波
        diff_vx = abs(frame_dx - self.v_x)
        diff_vy = abs(frame_dy - self.v_y)
        
        # 若單幀速度超過合理物理範圍，予以飽和鉗位，保護 IIR 預測器不被污染
        clamped_dx = max(-max_phys_v, min(max_phys_v, frame_dx))
        clamped_dy = max(-max_phys_v, min(max_phys_v, frame_dy))
        
        if diff_vx > (max_phys_v * 1.1) or diff_vy > (max_phys_v * 1.1):
            # 劇烈跳變：判定為交界翻轉突波，減小更新權重
            alpha = self.v_alpha * 0.2
        else:
            alpha = self.v_alpha

        self.v_x = (1.0 - alpha) * self.v_x + alpha * clamped_dx
        self.v_y = (1.0 - alpha) * self.v_y + alpha * clamped_dy

    @staticmethod
    def calibrate_csbc_table(raw_loader_fn, total_frames, preprocess_fn, max_lag=4):
        """
        線上/跨幀自校準微小靜態自相關偏置表 B_S(dy, dx) (完全不儲存任何 36x36 像素級 FPN 影像！)
        利用位移後微塵互不相關、而感測器靜態圖案恆定的物理性質：
          E[ F_t(x,y) * F_{t+lag}(x+dx, y+dy) ] == B_S(dy, dx)
        對稱摺疊後僅需 41 個 16-bit 純量 (82 Bytes)，比 1,296 Bytes 整張 FPN 節省 93.7% 面積！
        """
        lag_f = max(40, total_frames // 30)
        if total_frames <= lag_f + 5:
            lag_f = max(1, total_frames // 3)
        step = max(1, (total_frames - lag_f) // 80)
        samples_bs = {(dy, dx): [] for dy in range(-max_lag, max_lag + 1) for dx in range(-max_lag, max_lag + 1)}

        for idx in range(0, max(1, total_frames - lag_f), step):
            fa = raw_loader_fn(idx)
            fb = raw_loader_fn(idx + lag_f)
            if fa is None or fb is None:
                continue
            fa_f = fa.astype(np.float32)
            fb_f = fb.astype(np.float32)
            if np.std(fa_f - fb_f) < 0.4:
                continue
            A = preprocess_fn(fa_f)
            B = preprocess_fn(fb_f)
            h, w = A.shape
            for dy in range(-max_lag, max_lag + 1):
                for dx in range(-max_lag, max_lag + 1):
                    y1 = max(0, -dy)
                    y2 = min(h, h - dy)
                    x1 = max(0, -dx)
                    x2 = min(w, w - dx)
                    subA = A[y1:y2, x1:x2]
                    subB = B[y1+dy:y2+dy, x1+dx:x2+dx]
                    subA_z = subA - np.mean(subA)
                    subB_z = subB - np.mean(subB)
                    samples_bs[(dy, dx)].append(float(np.mean(subA_z * subB_z)))

        B_S = np.zeros((2 * max_lag + 1, 2 * max_lag + 1), dtype=np.float32)
        for dy in range(-max_lag, max_lag + 1):
            for dx in range(-max_lag, max_lag + 1):
                vals = samples_bs[(dy, dx)]
                B_S[dy + max_lag, dx + max_lag] = float(np.median(vals)) if vals else 0.0
        # 強制滿足自相關數學中心對稱性 B_S(-dy, -dx) == B_S(dy, dx)，硬體儲存量再減半！
        B_S = 0.5 * (B_S + B_S[::-1, ::-1])
        return B_S

    def step(self, raw_frame, frame_idx):
        """自主模式單幀處理主流程"""
        cur_frame = self.preprocess_frame(raw_frame)
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

        # 相關匹配
        surface = self.compute_correlation_surface(cur_frame, self.ref_frame, px, py)

        r = self.r
        v_idx, u_idx = np.unravel_index(np.argmax(surface), surface.shape)
        best_score = float(surface[v_idx, u_idx])

        if self.static_bias_table is not None:
            # CSBC 無影像 FPN 模式：純淨微塵能量檢測與無紋理玻璃慣性滑行 (Coasting)
            bs00 = self.csbc_bs00
            cur_clean_var = float(np.var(cur_frame)) - bs00
            squal = max(0.0, cur_clean_var * 25.0)

            if best_score < 0.05 * bs00 or cur_clean_var < 0.03 * bs00:
                frame_dx = self.v_x
                frame_dy = self.v_y
                dx_q = self.last_dx_rel + frame_dx
                dy_q = self.last_dy_rel + frame_dy
            else:
                dx_int = px + (u_idx - r)
                dy_int = py + (v_idx - r)
                if 0 < u_idx < 2*r and 0 < v_idx < 2*r:
                    dx_sub, dy_sub = self.compute_subpixel_offset(surface, v_idx, u_idx)
                else:
                    # 峰值落於搜尋邊界時，以 (dx_int, dy_int) 為中心重取 3x3 局部曲面做無偏 2D 拋物面內插
                    surf_re = np.zeros((3, 3), dtype=np.float32)
                    for dy2 in range(-1, 2):
                        for dx2 in range(-1, 2):
                            surf_re[dy2 + 1, dx2 + 1] = self.eval_csbc_cov(
                                self.ref_frame, cur_frame, dx_int + dx2, dy_int + dy2
                            )
                    dx_sub, dy_sub = self.parabolic_2d(surf_re)

                dx_tot = dx_int + dx_sub
                dy_tot = dy_int + dy_sub
                dx_q = round(dx_tot * self.subpixel_scale) / self.subpixel_scale
                dy_q = round(dy_tot * self.subpixel_scale) / self.subpixel_scale
                frame_dx = dx_q - self.last_dx_rel
                frame_dy = dy_q - self.last_dy_rel

            self.update_velocity_filter(frame_dx, frame_dy)
            self.global_x += frame_dx
            self.global_y += frame_dy
            self.last_dx_rel = dx_q
            self.last_dy_rel = dy_q

            dist_from_ref = math.hypot(self.last_dx_rel, self.last_dy_rel)
            kf_d = min(self.keyframe_dist, 2.0)
            max_d = min(self.max_ref_dist, max(2.5, float(self.csbc_max_lag) - 0.5))
            if (dist_from_ref >= kf_d and cur_clean_var >= 0.08 * bs00) or (dist_from_ref >= max_d):
                self.ref_frame = cur_frame
                self.ref_idx = frame_idx
                self.last_dx_rel = 0.0
                self.last_dy_rel = 0.0
        else:
            dx_int = px + (u_idx - r)
            dy_int = py + (v_idx - r)

            dx_sub, dy_sub = self.compute_subpixel_offset(surface, v_idx, u_idx)

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
            'pred_rel_x': pred_dx_rel,
            'pred_rel_y': pred_dy_rel,
            'score': best_score,
            'squal': squal,
            'ref_idx': self.ref_idx
        }


class TeacherModeTracker(NormalModeTracker):
    """
    老師引導模式 (Teacher Mode)
    
    運作特點：
      - 平時以自主模式進行預測與比對。
      - 每幀檢查感測器陣列上的相對預測位置是否落在 Ground Truth (GTH) 的 3x3 捕捉窗口 (Capture Basin: 相對誤差 <= 1.0 px) 內。
      - 排除歷史累積漂移干擾，只衡量「當前相對搜尋窗口是否套中特徵」。
      - 當預測脫靶 (Mismatch) 時，老師 (Ground Truth) 強制介入，重設感測器搜尋中心、速度與全局座標。
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ref_gth = None

    def step(self, raw_frame, frame_idx, gth_pos, gth_vel):
        cur_frame = self.preprocess_frame(raw_frame)
        squal = self.compute_squal(cur_frame)

        if self.ref_frame is None:
            self.ref_frame = cur_frame
            self.ref_idx = frame_idx
            self.ref_gth = (gth_pos[0], gth_pos[1])
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

        # 1. 物理感測器陣列相對位移 (排除全局歷史累積漂移)
        true_rel_x = gth_pos[0] - self.ref_gth[0]
        true_rel_y = gth_pos[1] - self.ref_gth[1]

        # 2. 自主預測 (在感測器陣列上的相對位移)
        px_auto, py_auto, pred_dx_rel, pred_dy_rel = self.predict_displacement()

        # 3. 捕捉窗口判斷 (Relative Basin Check: 感測器陣列預測誤差 <= 1.0 px)
        pos_err_x = abs(true_rel_x - px_auto)
        pos_err_y = abs(true_rel_y - py_auto)
        in_basin = bool(pos_err_x <= 1.0 and pos_err_y <= 1.0)

        teacher_intervened = False
        if in_basin:
            px, py = px_auto, py_auto
        else:
            teacher_intervened = True
            px = int(round(true_rel_x))
            py = int(round(true_rel_y))
            self.v_x = gth_vel[0]
            self.v_y = gth_vel[1]

        # 相關匹配
        surface = self.compute_correlation_surface(cur_frame, self.ref_frame, px, py)

        r = self.r
        v_idx, u_idx = np.unravel_index(np.argmax(surface), surface.shape)
        best_score = float(surface[v_idx, u_idx])

        dx_int = px + (u_idx - r)
        dy_int = py + (v_idx - r)

        dx_sub, dy_sub = self.compute_subpixel_offset(surface, v_idx, u_idx)

        dx_tot = dx_int + dx_sub
        dy_tot = dy_int + dy_sub

        dx_q = round(dx_tot * self.subpixel_scale) / self.subpixel_scale
        dy_q = round(dy_tot * self.subpixel_scale) / self.subpixel_scale

        frame_dx = dx_q - self.last_dx_rel
        frame_dy = dy_q - self.last_dy_rel

        # 速度更新 (防污染過濾)
        self.update_velocity_filter(frame_dx, frame_dy)

        if teacher_intervened:
            # 老師介入時：將全局累積座標精確同步回 (參考幀真值 + 感測器實測位移)，徹底消除歷史累積漂移
            self.global_x = self.ref_gth[0] + dx_q
            self.global_y = self.ref_gth[1] + dy_q
        else:
            self.global_x += frame_dx
            self.global_y += frame_dy

        self.last_dx_rel = dx_q
        self.last_dy_rel = dy_q

        if self.update_keyframe_if_needed(cur_frame, frame_idx, squal):
            self.ref_gth = (gth_pos[0], gth_pos[1])

        return {
            'frame': frame_idx,
            'est_x': self.global_x,
            'est_y': self.global_y,
            'est_dx': frame_dx,
            'est_dy': frame_dy,
            'pred_x': self.global_x - self.last_dx_rel + pred_dx_rel,
            'pred_y': self.global_y - self.last_dy_rel + pred_dy_rel,
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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ref_gth = None

    def step(self, raw_frame, frame_idx, gth_pos, gth_vel):
        cur_frame = self.preprocess_frame(raw_frame)
        squal = self.compute_squal(cur_frame)

        if self.ref_frame is None:
            self.ref_frame = cur_frame
            self.ref_idx = frame_idx
            self.ref_gth = (gth_pos[0], gth_pos[1])
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

        # 永遠鎖定真值相對位移為搜尋中心 (徹底解耦預測誤差)
        px = int(round(gth_pos[0] - self.ref_gth[0]))
        py = int(round(gth_pos[1] - self.ref_gth[1]))

        surface = self.compute_correlation_surface(cur_frame, self.ref_frame, px, py)

        r = self.r
        v_idx, u_idx = np.unravel_index(np.argmax(surface), surface.shape)
        best_score = float(surface[v_idx, u_idx])

        dx_int = px + (u_idx - r)
        dy_int = py + (v_idx - r)

        dx_sub, dy_sub = self.compute_subpixel_offset(surface, v_idx, u_idx)

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

        if self.update_keyframe_if_needed(cur_frame, frame_idx, squal):
            self.ref_gth = (gth_pos[0], gth_pos[1])

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
