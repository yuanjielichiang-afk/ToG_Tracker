step1 開始。
我要如何檢視結果? 用OPNI_raw_viewer 這類工具嗎?

--

Ran check opni raw tools directory
Ran search for viewer tools in C:\PROJECT
Analyzed OPNI_raw_viewer.html:L1-60
Ran read POS1-1.raw header
Ran inspect POS1-1.csv length
Ran analyze CSV counts
Ran check scratch directory
Ran sample stats of POS1-1.raw
Analyzed tog_fe_tool.py:L1-100
Analyzed tog_fe_tool.py:L101-180
Ran test feature thresholds on POS1-1.raw
Checked task Test feature thresholds on POS1-1.raw
Ran scan all frames in POS1-1.raw with bits=8
Ran check difference from background
Ran create project directory
Created step1_anchor_and_textureless_analyzer.py
Ran run Step 1 Analyzer
Ran check anchor clusters summary
Ran check max diff quantiles · Exited with code 1
Ran compute quantiles of max_diff
Ran count TL frames with different thresholds
Edited step1_anchor_and_textureless_analyzer.py
Ran run updated Step 1 Analyzer
Analyzed OPNI_raw_viewer.html:L61-150
Ran copy OPNI viewer to output dir
Ran list step1_output files
Ran copy visual plots to brain directory
Ran verify copied PNGs
Created step1_report.md

**Step 1 已執行完成！** 

我們已對真實錄製的 ToG 圓周運動檔案 [POS1-1.raw](file:///C:/Data/testPattern玻璃/Circle_R0.2inch_IPS0.02_Glass/RAW/POS1-1.raw)（共 5,373 幀、$36\times 36$、82 FPS）完成了全序列分析與抽樣，提煉出靜態 FPN 背景模型，並成功將 **無特徵畫面（Textureless）** 與 **高品質灰塵錨點（Anchor）** 分離。

詳細的數據統計與時序圖表已整理在成果報告中：
👉 **[Step 1 成果報告與圖表 (step1_report.md)](file:///C:/Users/Shouder%20Wei/.gemini/antigravity/brain/b44e5aa4-792c-4699-a87c-f69394583171/step1_report.md)**

---

### 如何檢視產出結果？

您完全可以用 **OPNI_raw_viewer** 來檢視！我們已經把檢視工具與產出的 raw 檔備齊在輸出目錄：
📂 `C:\Users\Shouder Wei\.gemini\antigravity\scratch\tog_tracker\step1_output\`

#### 方式一：使用網頁版 OPNI Raw Viewer 逐幀播放與檢視
1. 使用 Chrome 或 Edge 瀏覽器開啟已為您備好的檢視器：
   👉 [OPNI_raw_viewer.html](file:///C:/Users/Shouder%20Wei/.gemini/antigravity/scratch/tog_tracker/step1_output/OPNI_raw_viewer.html)（亦可使用您原有的 [C:/PROJECT/ai_mouse/OPNI_raw_viewer.html](file:///C:/PROJECT/ai_mouse/OPNI_raw_viewer.html)）。
2. 點擊 **「選擇檔案」** 或直接將以下檔案拖曳進網頁：
   * **[textureless_extracted.raw](file:///C:/Users/Shouder%20Wei/.gemini/antigravity/scratch/tog_tracker/step1_output/textureless_extracted.raw)**：內含 500 幀純平坦玻璃畫面，您可以肉眼確認完全無灰塵滑過，僅有純感測器讀出雜訊。
   * **[anchor_extracted.raw](file:///C:/Users/Shouder%20Wei/.gemini/antigravity/scratch/tog_tracker/step1_output/anchor_extracted.raw)**：內含 500 幀清晰微塵畫面，可觀察灰塵顆粒隨機台畫圓平移的過程。
   * **[fpn_model.raw](file:///C:/Users/Shouder%20Wei/.gemini/antigravity/scratch/tog_tracker/step1_output/fpn_model.raw)**：提煉出的靜態固定圖樣噪聲與光斑底圖。

#### 方式二：直接檢視靜態圖表分析
* **[sample_comparison.png](file:///C:/Users/Shouder%20Wei/.gemini/antigravity/scratch/tog_tracker/step1_output/sample_comparison.png)**：Textureless 與 Anchor 抽樣畫面的並列對比圖。
* **[step1_feature_timeline.png](file:///C:/Users/Shouder%20Wei/.gemini/antigravity/scratch/tog_tracker/step1_output/step1_feature_timeline.png)**：整段 5,373 幀的灰塵對比度、SQUAL 能量及特徵分佈時序圖。

---

### Step 1 分析核心摘要

1. **空白玻璃區（TL）**：共 **856 幀 (15.9%)**，扣除 FPN 後最大波動 $\le 10$ counts，無任何可追蹤特徵點。
2. **高信任度錨點（Anchor）**：共 **1,915 幀 (35.6%)**，微塵對比顯著（$> 25$ counts）。
3. **連續錨點簇（Anchor Clusters）**：共識別出 **42 個連續特徵段**（最長連續段達 186 幀）。

您檢視無誤後，請告知我，我們即可推進 **Step 2：在錨點簇上進行次像素局部追蹤，並透過 RANSAC 圓形幾何擬合，重建出全序列每一幀的連續 Ground Truth (gth)**！