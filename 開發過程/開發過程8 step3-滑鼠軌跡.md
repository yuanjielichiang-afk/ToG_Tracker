你的結果圖，軌跡都擠在一起，看不清楚。
"C:\PROJECT\ToG_Tracker\step3_output\trajectory_comparison_3modes.png"
用類似這樣的工具，我方便觀察
"C:\Users\Shouder Wei\Desktop\滑鼠軌跡-4-track.html"

---

Analyzed 滑鼠軌跡-4-track.html:L1-100
Analyzed 滑鼠軌跡-4-track.html:L101-250
Analyzed 滑鼠軌跡-4-track.html:L251-430
Ran check GTH trajectory points
Created export_tracks_and_build_html.py
Ran run export_tracks_and_build_html.py
Ran verify exported files

已為您打造完成專屬的 **四軌跡互動比較工具**！

靜態 PNG 圖表由於需要涵蓋整個運動尺度，確實容易讓微觀次像素的軌跡「擠在一起」看不清楚。參考您桌面的 `滑鼠軌跡-4-track.html` 工具架構，我們升級並生成了**已預載完整 5,373 幀數據的互動式 HTML 工具**。

---

### 如何使用與檢視？

#### 方式一：一鍵開啟互動工具（最推薦，數據已直接預載）
我們已在您的**桌面**與專案目錄建立好 HTML 工具：
👉 **桌面捷徑**：[滑鼠軌跡-Step3-ToG比較.html](file:///C:/Users/Shouder%20Wei/Desktop/滑鼠軌跡-Step3-ToG比較.html)
（專案備份：[C:/PROJECT/ToG_Tracker/step3_output/mouse_track_comparator.html](file:///C:/PROJECT/ToG_Tracker/step3_output/mouse_track_comparator.html)）

**直接使用瀏覽器（Chrome 或 Edge）雙擊打開即可，無需手動上傳任何檔案**！
* **已自動預載 4 條軌跡**：
  * **軌跡 A (黑虛線)**：Ground Truth (真值圓，5,373 點)
  * **軌跡 B (藍實線)**：Normal Mode (自主閉環，展示空白區如何發散)
  * **軌跡 C (橘實線)**：Teacher Mode (失步即時校正，並支援開關「紅叉介入點」)
  * **軌跡 D (綠實線)**：Ideal Mode (完全解耦，展示局部匹配極限)
* **核心操作功能**：
  1. **滑鼠滾輪**：可任意放大縮小（支援放大至 **300 倍**，可直接拉大到微觀次像素級別，看清楚每一幀的細微分離與漂移）。
  2. **滑鼠拖曳**：平移畫布至感興趣的軌跡弧段。
  3. **眼睛圖示**：點擊右上角卡片上的「眼睛」圖示，可隨意隱藏/單獨顯示特定軌跡（例如只看 A 與 C，排除干擾）。
  4. **懸停檢視（Hover Tooltip）**：滑鼠移到軌跡線上，會即時顯示該點的 **Frame 幀編號、(X, Y) 坐標、以及與 Ground Truth 的精確誤差**。

---

#### 方式二：載入至您原有的桌面工具
若您習慣直接使用原先的 [滑鼠軌跡-4-track.html](file:///C:/Users/Shouder%20Wei/Desktop/滑鼠軌跡-4-track.html)，我們也已將 4 條軌跡獨立轉存為標準的 `(x, y)` TXT 檔：
📂 目錄位置：`C:\PROJECT\ToG_Tracker\step3_output\`
* **[Track_A_GroundTruth.txt](file:///C:/PROJECT/ToG_Tracker/step3_output/Track_A_GroundTruth.txt)**
* **[Track_B_NormalMode.txt](file:///C:/PROJECT/ToG_Tracker/step3_output/Track_B_NormalMode.txt)**
* **[Track_C_TeacherMode.txt](file:///C:/PROJECT/ToG_Tracker/step3_output/Track_C_TeacherMode.txt)**
* **[Track_D_IdealMode.txt](file:///C:/PROJECT/ToG_Tracker/step3_output/Track_D_IdealMode.txt)**

您只需點擊您工具上的「選擇檔案」，分別載入上述 4 個檔案即可！