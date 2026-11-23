"""
========================================================================================
ToG Tracker: One-Click End-to-End Pipeline
========================================================================================
本腳本提供使用者更換任意 RAW 檔案進行完整測試的一鍵自動化管線。

使用方式範例：
  python run_pipeline.py POS1-2.raw
  python run_pipeline.py POS2-1.raw
  python run_pipeline.py r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS3-1.raw"

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
import subprocess

def main():
    parser = argparse.ArgumentParser(description="ToG Tracker 一鍵全流程測試工具")
    parser.add_argument("raw", help="RAW 檔案名稱或完整路徑 (例如: POS1-2.raw 或 POS2-1)")
    args = parser.parse_args()

    raw_input = args.raw
    if not raw_input.endswith('.raw'):
        raw_input += '.raw'

    # 1. 定位 RAW 檔與對應 CSV 檔
    raw_dir = r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW"
    csv_dir = r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\CSV"

    if os.path.isabs(raw_input) and os.path.exists(raw_input):
        raw_path = raw_input
        raw_name = os.path.basename(raw_path)
    else:
        raw_name = os.path.basename(raw_input)
        raw_path = os.path.join(raw_dir, raw_name)

    if not os.path.exists(raw_path):
        print(f"[錯誤] 找不到 RAW 檔案: {raw_path}")
        sys.exit(1)

    base_tag = os.path.splitext(raw_name)[0]
    csv_path = os.path.join(csv_dir, f"{base_tag}.csv")
    if not os.path.exists(csv_path):
        print(f"[警告] 找不到對應的硬體 CSV: {csv_path}，將以名義參數進行輔助校準。")

    project_dir = r"C:\PROJECT\ToG_Tracker"
    step1_out = os.path.join(project_dir, "step1_output")
    step2_out = os.path.join(project_dir, "step2_output")
    step3_out = os.path.join(project_dir, "step3_output")
    gth_csv_path = os.path.join(step2_out, f"{base_tag}_ground_truth.csv")
    fpn_model_path = os.path.join(step1_out, "fpn_model.raw")

    print("\n" + "=" * 75)
    print(f"啟動 ToG Tracker 自動化測試管線")
    print(f"目標 RAW 序列: {raw_name} ({raw_path})")
    print("=" * 75)

    # 步驟 1: Step 1 特徵與 FPN 分析
    print("\n>>> [階段 1/4] 執行 Step 1: 特徵與 FPN 分析...")
    cmd1 = [sys.executable, os.path.join(project_dir, "step1_anchor_and_textureless_analyzer.py")]
    # 我們可以在內部呼叫模組或透過 subprocess
    from step1_anchor_and_textureless_analyzer import run_step1
    run_step1(raw_path, step1_out)

    # 步驟 2: Step 2 GTH 重建
    print("\n>>> [階段 2/4] 執行 Step 2: 閉環真值 (Ground Truth) 重建...")
    from step2_gth_reconstructor import run_step2
    run_step2(raw_path, csv_path if os.path.exists(csv_path) else "", step1_out, step2_out)

    # 步驟 3: Step 3 三模式評估
    print("\n>>> [階段 3/4] 執行 Step 3: 三模式追蹤演算法評估...")
    from step3_three_mode_evaluator import evaluate_three_modes
    # 重建出的 GTH 檔名為 POS1-1_ground_truth.csv，我們確保其正確對應
    cur_gth = os.path.join(step2_out, "POS1-1_ground_truth.csv")
    evaluate_three_modes(raw_path, cur_gth, fpn_model_path, step3_out)

    # 步驟 4: 匯出 TXT 與生成桌面 HTML 工具
    print("\n>>> [階段 4/4] 匯出四軌跡 TXT 並更新互動式 HTML 檢視器...")
    from export_tracks_and_build_html import run_export
    run_export()

    print("\n" + "=" * 75)
    print(f"🎉 測試完成！所有結果已就緒：")
    print(f"  1. 桌面互動檢視器: C:\\Users\\Shouder Wei\\Desktop\\滑鼠軌跡-Step3-ToG比較.html")
    print(f"  2. 專案內部 HTML : {os.path.join(step3_out, 'mouse_track_comparator.html')}")
    print(f"  3. 四軌跡詳細 TXT: {step3_out}\\Track_*.txt")
    print("=" * 75 + "\n")

if __name__ == '__main__':
    main()
