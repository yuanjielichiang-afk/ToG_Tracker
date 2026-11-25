我覺得你的gth 預估程式有問題。
"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\CSV\POS1-1.csv"
這是原相P3950的輸出，裡面是每個frame的(dx,dy)，累加後，就能在excel畫出一個圓。
"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\CSV\POS1-1 - 複製.csv" 我在這檔案裡累加x,y 且畫出圓來。
圓的起點終點，都在圓的最左邊。(切線垂直)

但是，
file:///C:/PROJECT/ToG_Tracker/experiments/Circle_R0.2inch_IPS0.02_Glass/POS1-1/mouse_track_comparator.html
打開後，黑色軌跡A，他的起點並不在圖的最左側。(切線並不是垂直的)


---

Ran read step2_gth_reconstructor.py
Ran read lines 100-260 of step2_gth_reconstructor.py
Ran read head of POS1-1.csv
Ran check CSV cumulative values
Ran inspect POS1-1 - 複製.csv
Ran read first 25 lines of Track_A in POS1-1
Ran read lines 165-190 of Track_A in POS1-1
Ran check Track_B around frame 170
Ran inspect step1 anchor clusters
Ran check cluster velocities and angles
Ran check CSV range and start/end
Ran check Circle_R0.1inch CSV start vectors
Ran measure real RAW displacement at motion start
Ran verify vertical tangent ground truth circle
Ran read lines 190-245 of step2_gth_reconstructor.py
Ran test exact circle with phi0 = -pi/2
Ran check phi_0 from CSV
Ran read lines 120-150 of step2_gth_reconstructor.py
Ran read lines 120-145 of step2_gth_reconstructor.py
Edited step2_gth_reconstructor.py
Ran run pipeline with corrected vertical phi0
Checked task Run pipeline with corrected vertical phi0
Checked task Run pipeline with corrected vertical phi0
Ran list files in project directory
Analyzed step2_gth_reconstructor.py:L1-150
Analyzed step2_gth_reconstructor.py:L151-300
Ran check files in experiments POS1-1
Analyzed Track_A_GroundTruth.txt:L1-50
Ran check when motion starts in Track A
Ran check end points of Track A
Ran check EMBEDDED_TRACKS in html
Ran search Track_A in export_tracks_and_build_html.py
Ran find how tracks are embedded
Analyzed export_tracks_and_build_html.py:L740-782
Analyzed export_tracks_and_build_html.py:L100-160
Analyzed export_tracks_and_build_html.py:L60-100
Analyzed export_tracks_and_build_html.py:L20-60
Analyzed run_pipeline.py:L50-120
Ran check points in mouse_track_comparator.html · Exited with code 1
Ran inspect HTML INITIAL_TRACKS · Exited with code 1
Ran find INITIAL_TRACKS in template
Ran inspect GTH points in comparator html · Exited with code 1
Ran check parsed JSON points
Ran check git log · Exited with code 1
Ran check git log in ToG_Tracker
Ran show commit 83522f0
Ran check git diff for step2_gth_reconstructor.py
Ran inspect Track A trajectory stats
Ran list files in Circle_R0.1inch directory
Ran read POS1-2.csv first 20 rows
Ran check POS1-2 file dates
Ran check POS1-1 in 0.1inch file dates
Ran verify active points in current comparator html
Ran test CSV initial angles
Analyzed step2_gth_reconstructor.py:L120-145
Edited step2_gth_reconstructor.py
Ran run pipeline for Circle_R0.2inch POS1-1
Listed 1 task

我已精準鎖定物理機台運動幾何模型（將真值初始切線方向角 $\phi_0$ 嚴格鎖定為 $-90.0^\circ$ 垂直向下起步），目前正在背景為 `Circle_R0.2inch_IPS0.02_Glass/POS1-1` 重新執行完整的分析管線，完成後將即刻為您彙報並提供驗收。

Ran run pipeline on Circle_R0.1inch POS1-1

目前正在背景為 `Circle_R0.1inch_IPS0.005_Glass/POS1-1` 重新產生全新校準的閉環真值與各模式軌跡，完成後將繼續更新 `POS1-2`。

Ran run pipeline on Circle_R0.1inch POS1-2

目前正在背景為 `Circle_R0.1inch_IPS0.005_Glass/POS1-2` 執行管線，完成後將為您進行全面彙整。

Ran verify all 3 sequences have strictly vertical tangent
Ran check git status
Ran commit and push fix to git

非常感謝您透過原相 P3950 硬體實測 CSV（`POS1-1.csv` / `POS1-1 - 複製.csv`）在 Excel 中的累加繪圖，精準點出這個核心運動幾何問題！

---

### 一、 先前 GTH 傾斜的根本原因

1. **舊演算法過度依賴微塵擬合**：
   - 舊版 [step2_gth_reconstructor.py](file:///C:/PROJECT/ToG_Tracker/step2_gth_reconstructor.py) 試圖利用玻璃上的微塵質心漂移角度，逆推初始切線相位角 $\phi_0$。
   - 但在 `POS1-1.raw` 中，**前 170 幀是靜止期，第 170~296 幀則是完全無微塵的純白玻璃（TL 區）**，第一個微塵簇直到第 302 幀才出現（此時機台早已旋轉了一段角度）。
   - 加上感測器像素座標離散抖動，逆推回推時產生了約 $20^\circ$ 的系統傾角偏差（被誤算為 $-70.87^\circ$），導致整個真值圓傾斜，在 $(0, 0)$ 原點呈現傾斜切線。

2. **真實機台的物理真值**：
   - 如您在 Excel 累加 $X, Y$ 所呈現，XY-table 測試機台的圓形軌跡**起點與終點必然座落於圓的最左端（$X=0$），且初始運動方向嚴格垂直向下（$dx=0, dy=-1$）**。

---

### 二、 修正措施與嚴格運動學模型

我們已在 [step2_gth_reconstructor.py](file:///C:/PROJECT/ToG_Tracker/step2_gth_reconstructor.py) 中，將真值的初始切線方向角嚴格鎖定為：
$$\phi_0 = -\frac{\pi}{2} = -90.00^\circ$$

完整的幾何參數方程式如下：
$$X(t) = R \cdot \big(1 - \cos(\omega t)\big) \ge 0$$
$$Y(t) = -R \cdot \sin(\omega t)$$

- **起點**：$t=0 \implies (X, Y) = (0, 0)$，位於圓的最左側。
- **初始切線速度**：$\mathbf{v}(0) = \big[\dot{X}(0), \dot{Y}(0)\big]^T = [0, -speed]^T$（**切線嚴格垂直向下**）。
- **軌跡範圍**：$X \in [0, 2R]$（整顆圓皆在 $X \ge 0$ 的右半側），$Y \in [-R, +R]$（上下對稱）。
- **閉環**：$t=T \implies (X, Y) = (0, 0)$（完美於最左側原點相接閉環）。

---

### 三、 修正後之實測數據驗證

我們已針對所有測試序列重新執行管線，各序列之 Track A (Ground Truth) 數據如下：

| 實驗序列名稱 | 幀數 | X 軸範圍 (全部 $\ge 0$) | Y 軸範圍 (上下對稱) | 運動起始幀 | 初始位移向量 $(dx, dy)$ | 初始切線角 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **0.2inch / POS1-1** | 5,373 | $[0.000, 339.984]$ | $[-170.10, +169.89]$ | Frame 170 | $(0.000, -0.206)$ | **$-90.00^\circ$（垂直向下）** |
| **0.1inch / POS1-1** | 10,543 | $[0.000, 165.219]$ | $[-82.63, +82.58]$ | Frame 136 | $(0.000, -0.050)$ | **$-90.00^\circ$（垂直向下）** |
| **0.1inch / POS1-2** | 9,163 | $[0.000, 138.688]$ | $[-69.37, +69.32]$ | Frame 96 | $(0.000, -0.050)$ | **$-90.00^\circ$（垂直向下）** |

---

### 四、 互動檢視器驗收連結

請直接在瀏覽器中**重新整理**打開以下檢視器，即可檢視起點完全位於最左側且切線垂直的全新黑色真值圓：

1. **0.2inch POS1-1 檢視器**：
   [Circle_R0.2inch_IPS0.02_Glass/POS1-1/mouse_track_comparator.html](file:///C:/PROJECT/ToG_Tracker/experiments/Circle_R0.2inch_IPS0.02_Glass/POS1-1/mouse_track_comparator.html)
2. **0.1inch POS1-1 檢視器**：
   [Circle_R0.1inch_IPS0.005_Glass/POS1-1/mouse_track_comparator.html](file:///C:/PROJECT/ToG_Tracker/experiments/Circle_R0.1inch_IPS0.005_Glass/POS1-1/mouse_track_comparator.html)
3. **0.1inch POS1-2 檢視器**：
   [Circle_R0.1inch_IPS0.005_Glass/POS1-2/mouse_track_comparator.html](file:///C:/PROJECT/ToG_Tracker/experiments/Circle_R0.1inch_IPS0.005_Glass/POS1-2/mouse_track_comparator.html)

> 程式碼與所有實驗數據已同步推送至 GitHub：[ToG_Tracker](https://github.com/yuanjielichiang-afk/ToG_Tracker.git)。