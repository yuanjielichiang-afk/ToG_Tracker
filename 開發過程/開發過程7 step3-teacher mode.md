Ran create step3_output directory
Created step3_three_mode_evaluator.py
Ran run Step 3 Three-Mode Evaluator
Ran copy Step 3 plots to brain directory
Ran list PNG files in brain dir
Created step3_report.md

**Step 3 已執行完成！**

我們已在 `C:\PROJECT\ToG_Tracker\` 建立了完整的 **光學滑鼠演算法三模式評估系統**（`step3_three_mode_evaluator.py`），並在 [POS1-1.raw](file:///C:/Data/testPattern玻璃/Circle_R0.2inch_IPS0.02_Glass/RAW/POS1-1.raw) 上完成了全部 5,373 幀的閉環量化對比與診斷。

詳細的數值報表與對比圖表已為您彙整在：
👉 **[Step 3 成果報告與圖表 (step3_report.md)](file:///C:/Users/Shouder%20Wei/.gemini/antigravity/brain/b44e5aa4-792c-4699-a87c-f69394583171/step3_report.md)**

---

### Step 3 核心成果與量化數據對比

我們嚴格落實了：
1. **$3 \times 3$ 局部搜尋（$r = 1$）與次像素拋物線內插**。
2. **特徵門檻更新防毒機制（Texture-Gated Keyframe Update）**：當 $\text{SQUAL} < 5.0$ 時自動凍結參考幀，避免空白玻璃污染。
3. **Capture Basin 失步與介入準則**：以預測位置是否偏離 Ground Truth 超過 $1\text{ px}$ 作為 Teacher 介入條件。

| 評估指標 | Mode 1: Normal (自主閉環) | Mode 2: Teacher (失步介入校正) | Mode 3: Ideal (真值中心解耦) |
| :--- | :---: | :---: | :---: |
| **最大累積軌跡誤差 (Max ATE)** | **107.57 px** (發散) | **2.96 px** (受控) | **2.11 px** (極限) |
| **終點漂移 (Final Drift)** | **22.03 px** | **1.72 px** | **1.72 px** |
| **$3\times 3$ 捕捉命中率 (In-Basin)** | **2.98%** | **23.49%** | **32.40%** |
| **Teacher 介入次數 (Mismatches)** | 0 (無介入) | **4,111 次** | 0 (純解耦) |
| **有特徵區位移 RMSE** | **0.3280 px/f** | **0.6077 px/f** | **0.4184 px/f** |

---

### 關鍵洞察

1. **量化證實了「雪崩式連鎖失步」**：
   在 **Normal Mode** 下，一旦進入空白玻璃區，簡單的一階 IIR 速度預測器因雜訊擾動而迅速偏轉，導致預測中心完全脫離 $3\times 3$ 捕捉範圍。當後續微塵重新出現時，搜尋窗口早已脫靶看不見它，累積誤差爆發至 **107.57 px**！
2. **Teacher Mode 成功阻斷錯誤擴散**：
   在每次預測脫靶時注入真值校正，使得全行程最大累積誤差被完美壓制在 **2.96 px** 以內，成功將「局部特徵匹配」與「軌跡預測」徹底解耦！這讓我們後續改良任何演算法時，能有一致且客觀的基線（例如：失步介入次數從 4,111 次降低到多少次）。

---

### 產出檔案位置

📂 `C:\PROJECT\ToG_Tracker\step3_output\`

* **[trajectory_comparison_3modes.png](file:///C:/PROJECT/ToG_Tracker/step3_output/trajectory_comparison_3modes.png)**：四軌跡（GTH、Normal、Teacher、Ideal）疊合圖。
* **[mismatch_and_basin_timeline.png](file:///C:/PROJECT/ToG_Tracker/step3_output/mismatch_and_basin_timeline.png)**：SQUAL 特徵能量、In-Basin 狀態與 Teacher 介入時點時序圖。
* **[tracking_error_curves.png](file:///C:/PROJECT/ToG_Tracker/step3_output/tracking_error_curves.png)**：瞬時位移誤差與累積軌跡誤差曲線圖。
* **[step3_summary_report.json](file:///C:/PROJECT/ToG_Tracker/step3_output/step3_summary_report.json)**：完整數值評估指標。

---

### 下一步建議 (Step 4)

現在評價框架已經成熟，下一步即可進入**演算法實質改良與突破**：
1. **Kalman Filter (二階恆加速模型)**：在進入空白區（$\text{SQUAL} < 5.0$）時調高觀測噪聲 $R$，依靠慣性狀態盲推滑行，大幅延長跨越空白區的有效捕捉壽命。
2. **例外模式擴展搜尋（類似專利 US9684384B2）**：當偵測到連續低信心度時，啟動分幀預算約束擴展搜尋（Budget-Constrained Search，由 $3\times 3$ 擴展至 $7\times 7$ 或 $9\times 5$），主動將脫靶的灰塵尋回。

請確認是否推進 Step 4 進行演算法改良？