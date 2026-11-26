1. band pass 雖然已降至5x5, 但是後面還有一個feature extraction(saddle + corner)。這樣對IC來說，line buffer多。需要進一步化簡，減少IC面積。
2. 現在依靠前面一個FPN model。這個FPN需要儲存一整張frame。這也耗費IC 面積。有什麼樣的替代的方案? 我能想到的是：
  - LDC 簡單的model。
  - 更強力的feature extraction 能無視lens shading 的低頻變化。
3. 說到低頻變化。7x7 這招是別的同事發明的。他用7x7加上其他手段(我還不知道)，就能有不錯的效果，而且也不需要FPN model。我在猜，是因為9x9 recpetive field 大，所以在lens shading 的影響下，還能抽取出真正的特徵。
4. 續上，那麼，kernel 可以設計成長方形的嗎? 


---


Ran list project directory files
Ran list files in development logs
Analyzed tracker_core.py:L150-320
Analyzed 開發過程19-bandpass 改善計劃.md:L1-120
Analyzed 開發過程15.md:L1-120
Ran inspect OptNavi.cpp filter methods
Analyzed OptNavi.cpp:L230-620
Analyzed OptNavi.cpp:L1350-1495
Ran search Navi project for 7x7 and LDC
Analyzed optmouse-3950raw.cfg:L1-150
Analyzed OpmSys.cpp:L1-200
Ran analyze FPN vs Lens Shading vs Bandpass response
Ran check polynomial moments of 7x7 and 5x5 kernels
Ran separate smooth lens shading from pixel FPN · Exited with code 1
Ran analyze smooth shading vs pixel FPN after 7x7 BP
Ran test tracking with no FPN model
Ran inspect spatial location of static BP response
Ran check Saddle3 vs 7x7 BP on FPN and Dust
Ran benchmark rectangular 5x7 and 3x7 kernels with/without FPN

正在針對您提出的 IC 面積優化構想，實測評估「長方形卷積核（`5x7` 與 `3x7`）」以及「完全移除 FPN 模型（No-FPN）」在 13,629 幀真實序列上的閉環表現，稍後立即為您呈現完整分析與數據報告。

Ran verify 3x7 NO-FPN across all 3 Dirty Glass replicates

正在交叉驗證 `3x7` 長方形卷積核於「完全不載入 FPN」模式下在全部 3 組重複測試集的穩定性與再現性，馬上完成。

這四個問題完全切中了 **光學滑鼠 ASIC 晶片設計（SRAM / Line Buffer 面積、算力與成本）** 的最核心命脈！

為了不流於紙上談兵，我們剛剛直接提取真實玻璃測試序列（`POS1-1` 純淨玻璃與 `Dirty Glass 0.5"`），並結合貴司現有參考架構（[OptNavi.cpp](file:///C:/PROJECT/Navi/modules/navi/src/OptNavi.cpp) 與 [optmouse-3950raw.cfg](file:///C:/PROJECT/Navi/cfg/optmouse-3950raw.cfg)）做了物理成分拆解與原型實測，發現了幾個**極具突破性的數據與結論**。以下逐題為您深度剖析：

---

### 1. 前端 `Band-Pass` + 後端 `Saddle + Corner` 串聯導致 Line Buffer 過多，如何進一步化簡減少 IC 面積？

#### (1) 先算清楚串聯管線（Cascaded Pipeline）的 IC 面積痛點
在 CMOS 感測器逐行掃描（Raster-Scan）的 RTL 硬體架構中，一個高為 $H_k$、寬為 $W_k$ 的卷積視窗需要 **$H_k - 1$ 條** 寬度為影像寬度（如 36 pixels）的 Line Buffer：
* 若採**多級串聯**：`5x5 Band-Pass`（需 **4 條** 8-bit Line Buffer）$\rightarrow$ 輸出位元膨脹為 12~14 bit $\rightarrow$ `3x3 Saddle`（再加 **2 條** 14-bit Line Buffer）$\rightarrow$ `3x3 Corner`（再加 **2 條** 14-bit Line Buffer）。
* **後果**：總共耗費 **8 條 Line Buffer**，且後級位元寬度翻倍，面積反而比單顆 `7x7` 更大！

#### (2) 三大 IC 級化簡方案（從 8 條砍到 **2～4 條**）

1. **線性核預先融合（Kernel Fusion）+ 消除功能重疊**：
   * 根據卷積結合律 $(I * K_{\text{BP}}) * K_{\text{Saddle}} = I * (K_{\text{BP}} * K_{\text{Saddle}})$，所有**線性濾波級**在數學上都能預先捲成「單一顆 Kernel」，直接消滅中間層 Line Buffer（[OptNavi.cpp](file:///C:/PROJECT/Navi/modules/navi/src/OptNavi.cpp#L392) 中將 `box2` 與 `saddle3` 預捲為 `m_combFlt` 即是此意）。
   * **更關鍵的是消除重疊**：`Band-Pass` 的目的是「濾除低頻光暈 + 平滑高頻熱雜訊」，而 `Saddle`（`[-1,0,1; 0,0,0; 1,0,-1]`）與 `Corner`（四方向 `[-1, 2, -1]`）**本身就已經是二階零均值高通算子**！
   * **玻璃 vs. 滑鼠墊多工共用同一組 Line Buffer（Mode-Multiplexed Single Stage）**：
     * `Saddle + 4-Corner` 的非線性 `min/max` 比較主要是為了打破**布質滑鼠墊的經緯編織週期條紋（Repeat Pattern）**；在**玻璃表面**上微塵是孤立點狀特徵，單靠零均值帶通核即可追蹤。
     * 硬體上只需配置**唯一一組 4 條（甚至 2 條，見第 4 點長方形核）8-bit Line Buffer**，直接從同一組視窗暫存器拉線同時算帶通或四方向差分，**全晶片徹底實現「單級 Line Buffer（Single-Stage Line Buffer）」**！
2. **特徵位元深度極致壓縮（2-bit / 4-bit Clipped Feature）**：
   * 除了 Line Buffer，IC 裡**面積佔比最大**的其實是要存下一整張參考幀的 **`Reference Feature Buffer`**（如 `30x30`）！
   * 若將濾波後的特徵經動態右移（`AlignShift`）並飽和截斷為 **2-bit（Ternary `{-1, 0, +1}`）或 4-bit signed**（如 [optmouse-3950raw.cfg](file:///C:/PROJECT/Navi/cfg/optmouse-3950raw.cfg#L52) 甚至壓到 `AlignBitDepth: 1`），不僅 `Reference Buffer` SRAM 面積立減 **50%～87%**，連後端 Cross-Correlation 的**乘法器陣列都能退化為簡單的加減法器 / XNOR 邏輯閘**！

---

### 2. 取代「儲存一整張 FPN Frame」的省面積方案有哪些？

為了精確評估您的兩個構想（**簡單 LDC 模型** vs. **強力無視低頻變化的 Feature Extraction**），我們剛剛將真實玻璃 RAW 檔的靜態背景拆解為「**平滑 4 階光學暗角（Lens Shading）**」與「**像素級高頻固定雜訊（Pixel/Speckle FPN）**」，得到了一組非常關鍵的物理實測數據：

| 測試序列 (36x36) | 原始靜態背景起伏 ($\sigma_{\text{total}}$) | ① 平滑 Lens Shading ($\sigma_{\text{shading}}$) | ② 高頻固定雜訊 ($\sigma_{\text{pix\_fpn}}$) | 過 **7x7 帶通核** 後的 Shading 殘差 | 過 **7x7 帶通核** 後的高頻 FPN 殘差 | 過 **7x7 帶通核** 後的真實移動微塵 |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **POS1-1 (極乾淨玻璃)** | $54.50\text{ LSB}$ (16~228) | **$53.59\text{ LSB}$ (佔 96.7% 能量)** | $9.90\text{ LSB}$ (峰值 36) | **僅 $0.22\text{ LSB}$ (已消滅 99.6%!)** | **$3.24\text{ LSB}$** | **$2.42\text{ LSB}$** (小於高頻 FPN!) |
| **Dirty Glass 0.5"** | $56.05\text{ LSB}$ (25~225) | **$55.63\text{ LSB}$ (佔 98.5% 能量)** | $6.81\text{ LSB}$ (峰值 58) | **僅 $0.47\text{ LSB}$ (已消滅 99.2%!)** | **$3.58\text{ LSB}$** | **$9.94\text{ LSB}$** (大於高頻 FPN!) |

#### 物理真相診斷與替代方案排序：
> [!IMPORTANT]
> **關鍵發現：為什麼單純的「簡單平滑 LDC 模型」不夠，而真正的兇手另有其人？**
> * 佔總能量 **97%～98.5% 的平滑 Lens Shading**，光靠零均值帶通濾波器就已經被壓制到只剩 `0.22 LSB`（**帶通濾波器天生就已經無視平滑 Lens Shading 了，因此再加平滑多項式 LDC 效益重疊**）！
> * 在極乾淨玻璃（`POS1-1`）上若不減 FPN 會被吸死在 `(0,0)`，**真正的元兇是那 1.5%～3.3% 的「高頻固定圖案」（過完帶通仍有 `3.24 LSB`，強於乾淨玻璃微塵的 `2.42 LSB`）**——包含 CMOS 讀出電路的直橫條紋（Row/Col FPN）以及光學鏡片內部固定的雷射干涉斑點（Static Laser Speckle / 鏡面微疵）！

因此，要**徹底拿掉整張 1,296 Bytes 的 FPN Frame Buffer**，最有競爭力的 IC 替代方案如下：

1. **【0 Bytes SRAM，最推薦】特徵幅度飽和削峰（Amplitude Clipping / 2-bit Quantization）+ 原點死斑跳脫（Zero-Lag Notch）**：
   * 固定死斑雖然峰值高達 `13～36 LSB`，但數量極少；移動微塵雖然只有 `2～4 LSB`，但分佈在多個像素。
   * 透過自適應右移與低位元飽和截斷（Clipping），**將強死斑與弱微塵削平到同一個幅度等級（例如 $\pm 1$ 或 $\pm 3$）**，死斑瞬間失去主導相關曲面的霸權！再配合運動時搜尋視窗隨速度預測跳離 `(0,0)` 原點（死斑只在 `dx=0, dy=0` 自相關時才會重疊），即可零記憶體破除鎖死！
2. **【省 94% SRAM：僅 72 Bytes】1D 行列分離式補償（1D Row + Column FPN Vectors）**：
   * 只存 1 條水平列偏置（36 Bytes）+ 1 條垂直行偏置（36 Bytes），消除 CMOS 讀出條紋，其餘圓對稱 Lens Shading 直接交給零均值帶通濾波器消滅。
3. **【省 95% SRAM：僅 32～64 Bytes】稀疏強死點遮罩表（Top-K Hot-Spot Coordinate Mask）**：
   * 實測顯示過完帶通仍大於 `6 LSB` 的強固定亮點全圖不到 16～24 顆。只需十幾個座標暫存器，將這些固定座標的特徵值 mask 為 `0`，就能以極微小代價達到近似整張 FPN 的淨化效果！

---

### 3. 解密同事「7x7 + 其他手段（不需 FPN）」為什麼有效？是因為大 Receptive Field 嗎？

您的猜測**非常精準地命中了一半的核心，另一半則是「多項式消失矩」與「特徵正規化削峰」的巧妙配合**：

1. **為什麼 7x7 大感受野能無視 Lens Shading？——「外圈正值環的二階矩補償」与「散焦微塵匹配」**：
   * 對於任意二次拋物面光暈 $L(x,y) = a_0 + a_1 x + a_2 y + a_3 x^2 + a_4 y^2 + a_5 xy$：
     * 只要卷積核滿足零均值（$M_{00} = \sum K = 0$）且對稱（$M_{10}=M_{01}=M_{11}=0$），它對常數項、一階斜坡項、交叉鞍面項的響應**嚴格等於 0**；對二次曲率 $x^2, y^2$ 的響應則退化為一個全圖相等的常數偏置 $C = a_3 M_{20} + a_4 M_{02}$（空間標準差為 `0.000`）！
   * 更巧妙的是，觀察同事設計的 7x7 外圈有一圈正數 `[5, 2, ...]`：這使得二階矩 $|M_{20}| = |\sum x^2 K|$ 從 5x5 的 `482` 大幅收斂至 **`256`（恰好為 $2^8$）**，形成類似四階雙諧波算子（$\nabla^4$）的效果，連 4 階高斯光斑邊緣陡坡都能壓到 `0.22 LSB`！
   * 同時，玻璃有一定厚度，微塵在斜射雷射下成像常是 `2x2`～`3x3` 的微散焦光斑，大感受野能完整包覆「中心亮斑 + 外圍散射暗環」，並平均掉周圍像素的隨機散粒雜訊（Shot Noise），大幅拉高微塵 SNR。
2. **同事搭配的「其他手段」是什麼？**：
   * 對照 [OptNavi.cpp](file:///C:/PROJECT/Navi/modules/navi/src/OptNavi.cpp) 與 [optmouse-3950raw.cfg](file:///C:/PROJECT/Navi/cfg/optmouse-3950raw.cfg)，正是結合了：**① Valid 內縮視窗**（自動切掉最外圈光斑懸崖邊界，不做邊界延拓污染）、**② `AutoAlignShift` + 低位元 Clipping 飽和截斷**（壓制高頻死斑權重）、以及 **③ `ExcZCCor`（零位移相關峰異常保護與慣性預測跳脫）**！

---

### 4. Kernel 可以設計成長方形的嗎？（驚喜實測揭曉！）

**答案是：不僅完全可以，而且在 IC 架構上，「扁長方形 Kernel（高 $H_k <$ 寬 $W_k$，例如 `3x7`）」是能同時兼顧「極致壓縮 Line Buffer」與「大感受野抗光暈」的王牌設計！**

#### (1) IC 硬體面積優勢：水平加寬「免收 Line Buffer 稅」！
在水平逐行掃描（Horizontal Raster Scan）架構中：
* **垂直高度 $H_k$** 決定了必須存 **$H_k - 1$ 條** 完整寬度的 Line Buffer。
* **水平寬度 $W_k$** 只需要在當下運算管線多串幾個單像素 D-Flip-Flop（Shift Registers），**Line Buffer 成本為 0**！
* 因此，把 `7x7`（需 **6 條** Line Buffer，49 MAC）改為扁長方形 **`3x7`（高 3、寬 7）**，**Line Buffer 直接從 6 條暴降至 2 條（節省 66.7% Line Buffer 面積！），運算量從 49 降至 21 MAC（節省 57% 算力）**！

#### (2) 幾何對稱性疑慮：長方形 Kernel 會不會讓畫圓變橢圓？
* **不會改變 CPI 比例尺**：因為參考幀（Ref）與當前幀（Cur）經過的是**同一個 `3x7` 核**，同一個微塵在兩張圖上的特徵形狀完全一致，平移 `(dx, dy)` 時相關峰頂位置完全不偏！
* 唯一影響是相關曲面在 X 軸較平緩、Y 軸較尖銳（橢圓等高線），而我們第一階段導入的 **「解析解 2D 橢圓拋物面擬合（$a_3 x^2 + a_4 y^2 + a_5 xy$）」** 天生就是為不同 X/Y 曲率量身打造的，能無偏解出峰值！

#### (3) 真實數據說話：我們剛設計了一顆 `3x7` 零均值核並跑完 13,629 幀（含「完全無 FPN」測試）！

我們取原廠 7x7 最核心的中間 3 列，將中心補償為 `116` 使其嚴格滿足**零均值（$\sum K = 0$）**，構造出這顆僅需 **2 條 Line Buffer** 的 **`3x7` 扁長方形帶通核**：

```text
  -1  -11  -15    7  -15  -11   -1
  -1  -17    7  116    7  -17   -1
  -1  -11  -15    7  -15  -11   -1
```

接著直接在 `Dirty Glass 0.5 inch`（13,629 幀 / 331 FPS）上測試 **「有 FPN」** 與 **「完全拔掉 FPN（`fpn_model = None`，0 Bytes FPN SRAM！）」**，結果令人驚豔：

| 濾波器形狀與配置 | 所需 Line Buffer | FPN 記憶體 | MAC / Pix | 閉環誤差 ($E_{\text{close}}$) | 擬合圓半徑 ($R_{\text{est}}$) | 備註 |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **原相 P3950 硬體晶片** | - | - | - | $14.03\text{ px}$ (17.2 cnts) | $423.66$ cnts | 市場標竿 |
| **原廠 `7x7` (有 FPN)** | 6 條 (7列) | 1,296 B | 49 | $9.91\text{ px}$ | $428.82\text{ px}$ | 基準 |
| **長方形 `3x7` (有 FPN)** | **2 條 (3列)** | 1,296 B | **21** | **$2.22\text{ px}$ (!)** | **$430.34\text{ px}$** | **誤差縮小至極致！** |
| **長方形 `3x7` (無 FPN - 第1組)**| **2 條 (3列)** | **0 Bytes!** | **21** | **$8.14\text{ px}$** | **$425.49\text{ px}$** | **X跨859, Y跨837，正圓無橢圓失真！** |
| **長方形 `3x7` (無 FPN - 第2組)**| **2 條 (3列)** | **0 Bytes!** | **21** | **$8.49\text{ px}$** | **$426.79\text{ px}$** | **高度穩定重現！** |
| **長方形 `3x7` (無 FPN - 第3組)**| **2 條 (3列)** | **0 Bytes!** | **21** | **$14.19\text{ px}$** | **$423.63\text{ px}$** | **3 組平均誤差 10.27 px，媲美 P3950！** |

*(註：為什麼 `3x7` 在無 FPN 下表現甚至優於 `7x7` 無 FPN 的 42.86 px？因為垂直高度只有 3 列，半徑僅 1 pixel，完全不會把感測器上下邊緣劇烈的雷射光斑截止懸崖捲進內部相關搜尋 ROI 中！)*

---

### 下一步行動建議（第三階段：IC 極致輕量化無 FPN 架構）

綜合以上突破，我們找到了一條極具商業競爭力的 IC 晶片化路線：
1. **單級扁長方形核（`3x7` 或 `3x5`，僅需 2 條 Line Buffer）**：取代多級串聯，保留水平大感受野抗光暈能力，並與滑鼠墊模式的 `3x3 Corner` 共用同一組 2 條 Line Buffer。
2. **無 FPN 破鎖死機制（0 Bytes FPN SRAM）**：導入 **Valid 邊界內縮 + 低位元飽和削峰（Amplitude Clipping / 2~4 bit 特徵量化）+ 原點靜態峰抑制**，攻克像 `POS1-1` 這類極度乾淨玻璃在無 FPN 下的原點吸附問題。

您看我們接下來是否要直接針對 **「`3x7` 長方形單級核 + 完全移除 FPN 模型（搭配低位元 Clipping 與破鎖死機制）」** 進行實作，並在 `Clean Glass / POS1-1` 與 `Dirty Glass` 全套數據集上跑出量化評分對比？

