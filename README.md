# Track-on-Glass (ToG) Optical Mouse Tracking & Evaluation Engine
**玻璃表面光學滑鼠追蹤演算法、真值重建系統與三模式評估框架**

---

## 專案概述 (Project Overview)
本專案針對光學滑鼠在透明玻璃表面（Track-on-Glass, ToG）的追蹤挑戰，利用原相 P3950 感測器錄製之真實 RAW 序列（36x36 解析度，82 fps），建立一套具備 Ground Truth (GTH) 基準真值的評估與追蹤演算法系統。

針對玻璃表面微塵極其稀疏、經常遇到大範圍無特徵空白區（Textureless Area）的嚴苛情境，演算法解耦了特徵匹配（Feature Matching）與運動軌跡預測（Trajectory Prediction），並設計了三大模式進行獨立與聯合評估：
1. **Normal Mode（自主閉環模式）**：純依賴內部慣性速度預測與 $3\times 3$ 局部相關匹配，評估自主追蹤能力。
2. **Teacher Mode（老師介入模式）**：當預測位置脫靶（偏離 Ground Truth $3\times 3$ 捕捉窗口）時，由 Ground Truth 強制注入校正位置與速度，防止單一空白區造成後續軌跡整串發散，精確統計失步次數。
3. **Ideal Mode（理想解耦模式）**：始終以 Ground Truth 為搜尋中心，完全解耦預測誤差，評估特徵匹配與拋物線次像素內插的理論極限精度。

---

## 目錄結構 (Directory Structure)
```text
C:\PROJECT\ToG_Tracker\
├── tracker_core.py                      # 核心模組：NormalModeTracker / TeacherModeTracker / IdealModeTracker
├── step1_anchor_and_textureless_analyzer.py # Step 1: FPN 模型抽取、微塵錨點與空白區辨識
├── step2_gth_reconstructor.py           # Step 2: 封閉圓幾何閉環與 Ground Truth 矩陣重建
├── step3_three_mode_evaluator.py         # Step 3: 三模式批次評估引擎與診斷圖表生成
├── calibrate_physical_radius.py          # 多 RAW 序列微塵次像素速度與物理半徑精確校準
├── export_tracks_and_build_html.py       # 匯出四軌跡 TXT 並生成單頁互動式 HTML 檢視器
├── generate_normal_mode_statistics.py    # 產出 Normal Mode 基準統計指標 JSON/Markdown
├── reports/                             # 完整報告集中管理 (支援跨裝置檢視)
│   ├── step1_report.md
│   ├── step2_report.md
│   ├── step3_report.md
│   ├── normal_mode_stats.json
│   └── *.png                            # 所有診斷圖表 (相對路徑引用)
├── step1_output/                        # Step 1 產出 (FPN 模型, 特徵分佈圖)
├── step2_output/                        # Step 2 產出 (POS1-1_ground_truth.csv, 物理校準結果)
└── step3_output/                        # Step 3 產出 (TXT 軌跡, 評估 JSON, 比較圖, HTML 檢視器)
```

---

## 核心演算法流程 (Tracker Architecture)
1. **FPN 消除 (Fixed Pattern Noise Subtraction)**：扣除感測器固定圖樣底噪，使玻璃微塵訊號凸顯。
2. **特徵強度與 SQUAL (Surface Quality)**：使用 $3\times 3$ 離散拉普拉斯卷積核計算邊緣變異數，客觀區分微塵區與空白區。
3. **軌跡運動預測器 (Trajectory Prediction)**：一階 IIR 速度慣性濾波器，提供下一幀相對鍵幀的位移外推。
4. **局部受限搜尋 (Constrained Matching)**：以預測位置為中心，進行 $3\times 3$ 零均值正規化互相關（ZNCC），並施加邊界重疊面積補償（Area Compensation）。
5. **拋物線次像素擬合 (Parabolic Subpixel Peak Fitting)**：二次拋物線極值解析解，提供高達 $1/16$ 像素解析度。
6. **紋理閘控參考幀更新 (Texture-Gated Keyframe Update)**：僅在空間位移足夠且當前幀紋理合格時才更新 Keyframe，徹底杜絕在空白玻璃區把噪聲存為參考幀的「參考幀毒化（Poisoning）」問題。

---

## 互動式視覺化工具 (Interactive HTML Viewer)
- 檔案路徑：`step3_output/mouse_track_comparator.html`（或桌面直接捷徑）。
- 支援功能：
  - 自由切換四條軌跡：Ground Truth (藍)、Normal Mode (綠)、Teacher Mode (橘)、Ideal Mode (紫)。
  - 紅叉標記 Teacher 模式介入點（失步發生處）。
  - 滑鼠懸停顯示當前幀號、座標與即時誤差。
  - 最高支援 300 倍平移縮放觀察微觀次像素行為。
