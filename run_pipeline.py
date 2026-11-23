r"""
========================================================================================
ToG Tracker: One-Click End-to-End Pipeline
========================================================================================
本腳本提供使用者更換任意 RAW 檔案進行完整測試的一鍵自動化管線。

使用方式範例：
  python run_pipeline.py POS1-2.raw
  python run_pipeline.py POS2-1.raw
  python run_pipeline.py "C:\Data\testPattern玻璃\Circle_R0.1inch_IPS0.005_Glass\POS1-1.raw"

自動執行流程：
  1. Step 1: 分析特徵分佈、計算 FPN 模型、抽取微塵錨點與空白區
  2. Step 2: 物理速度次像素校準，生成閉環 Ground Truth (GTH) CSV
  3. Step 3: 運行 Normal / Teacher / Ideal 三模式評估與診斷圖表生成
  4. 匯出四軌跡 TXT 與自動生成桌面互動式 HTML 檢視器
========================================================================================
"""

import os
import sys
import argparse

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import analyze_raw
from step2_gth_reconstructor import run_step2
from step3_three_mode_evaluator import evaluate_three_modes
from export_tracks_and_build_html import run_export

def main():
    parser = argparse.ArgumentParser(description="ToG Tracker 一鍵全流程測試工具")
    parser.add_argument("raw", help="RAW 檔案名稱或完整路徑 (例如: POS1-2.raw 或 POS2-1)")
    args = parser.parse_args()

    raw_input = args.raw
    if not raw_input.endswith('.raw'):
        raw_input += '.raw'

    # 1. 智能定位 RAW 檔與對應 CSV 檔
    if os.path.isabs(raw_input) and os.path.exists(raw_input):
        raw_path = os.path.abspath(raw_input)
    else:
        # 在預設候選目錄中尋找
        candidate_dirs = [
            r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW",
            r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass",
            r"C:\Data\testPattern玻璃\Circle_R0.1inch_IPS0.005_Glass",
            r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_MousePad\RAW",
            r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_MousePad"
        ]
        raw_path = None
        for cdir in candidate_dirs:
            cand = os.path.join(cdir, os.path.basename(raw_input))
            if os.path.exists(cand):
                raw_path = cand
                break
        if raw_path is None:
            raw_path = os.path.join(candidate_dirs[0], os.path.basename(raw_input))

    if not os.path.exists(raw_path):
        print(f"[錯誤] 找不到 RAW 檔案: {raw_path}")
        sys.exit(1)

    raw_dir = os.path.dirname(raw_path)
    raw_name = os.path.basename(raw_path)
    base_tag = os.path.splitext(raw_name)[0]

    # 尋找對應的 CSV 檔
    csv_candidates = [
        os.path.join(raw_dir, f"{base_tag}.csv"),
        os.path.join(raw_dir, "..", "CSV", f"{base_tag}.csv"),
        os.path.join(raw_dir, "CSV", f"{base_tag}.csv"),
        os.path.join(r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\CSV", f"{base_tag}.csv")
    ]
    csv_path = ""
    for cand in csv_candidates:
        if os.path.exists(cand):
            csv_path = cand
            break

    if csv_path:
        print(f"找到對應硬體 CSV: {csv_path}")
    else:
        print(f"[提示] 未找到對應硬體 CSV，將透過影像微塵次像素模板比對完全自主校準。")

    project_dir = r"C:\PROJECT\ToG_Tracker"
    step1_out = os.path.join(project_dir, "step1_output")
    step2_out = os.path.join(project_dir, "step2_output")
    step3_out = os.path.join(project_dir, "step3_output")
    fpn_model_path = os.path.join(step1_out, "fpn_model.raw")

    print("\n" + "=" * 75)
    print(f"啟動 ToG Tracker 自動化測試管線")
    print(f"目標 RAW 序列: {raw_name}")
    print(f"完整路徑: {raw_path}")
    print("=" * 75)

    # 步驟 1: Step 1 特徵與 FPN 分析
    print("\n>>> [階段 1/4] 執行 Step 1: 特徵與 FPN 分析...")
    analyze_raw(raw_path, step1_out)

    # 步驟 2: Step 2 GTH 重建
    print("\n>>> [階段 2/4] 執行 Step 2: 閉環真值 (Ground Truth) 重建...")
    gth_csv_path = run_step2(raw_path, csv_path, step1_out, step2_out)

    # 步驟 3: Step 3 三模式評估
    print("\n>>> [階段 3/4] 執行 Step 3: 三模式追蹤演算法評估...")
    evaluate_three_modes(raw_path, gth_csv_path, fpn_model_path, step3_out)

    # 步驟 4: 匯出 TXT 與生成桌面 HTML 工具
    print("\n>>> [階段 4/4] 匯出四軌跡 TXT 並更新互動式 HTML 檢視器...")
    run_export(raw_path=raw_path, gth_csv_path=gth_csv_path, fpn_path=fpn_model_path, out_dir=step3_out)

    print("\n" + "=" * 75)
    print(f"🎉 測試完成！所有結果已就緒：")
    print(f"  1. 桌面互動檢視器: C:\\Users\\Shouder Wei\\Desktop\\滑鼠軌跡-Step3-ToG比較.html")
    print(f"  2. 專案內部 HTML : {os.path.join(step3_out, 'mouse_track_comparator.html')}")
    print(f"  3. 四軌跡詳細 TXT: {step3_out}\\Track_*.txt")
    print(f"  4. 重建真值 CSV  : {gth_csv_path}")
    print("=" * 75 + "\n")

if __name__ == '__main__':
    main()
