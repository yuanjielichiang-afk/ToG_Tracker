r"""
========================================================================================
ToG Tracker: One-Click End-to-End Pipeline
========================================================================================
本腳本提供使用者更換任意 RAW 檔案進行完整測試的一鍵自動化管線。
每次執行會自動建立專屬實驗資料夾 (位於 experiments/<dataset_name>/<seq_name>/)，
確保不同 RAW 檔、不同測試目錄下的同名檔案絕不會互相覆蓋！

使用方式範例：
  python run_pipeline.py POS1-2.raw
  python run_pipeline.py "C:\Data\testPattern玻璃\Circle_R0.1inch_IPS0.005_Glass\POS1-2.raw"
  python run_pipeline.py "C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw"

自動執行流程：
  1. 智能識別 RAW 來源目錄與序列編號，建立獨立實驗目錄 (experiments/)
  2. Step 1: 分析特徵分佈、計算 FPN 模型、抽取微塵錨點與空白區
  3. Step 2: 物理速度次像素校準，生成閉環 Ground Truth (GTH) CSV
  4. Step 3: 運行 Normal / Teacher / Ideal 三模式評估與診斷圖表生成
  5. 匯出四軌跡 TXT 與自動生成專屬互動式 HTML 檢視器
========================================================================================
"""

import os
import sys
import shutil
import argparse

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import analyze_raw
from step2_gth_reconstructor import run_step2
from step3_three_mode_evaluator import evaluate_three_modes
from export_tracks_and_build_html import run_export

def parse_dataset_and_seq(raw_path):
    r"""
    智能解析資料集名稱與序列標籤
    例如:
      C:\Data\...\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw -> ('Circle_R0.2inch_IPS0.02_Glass', 'POS1-1')
      C:\Data\...\Circle_R0.1inch_IPS0.005_Glass\POS1-2.raw -> ('Circle_R0.1inch_IPS0.005_Glass', 'POS1-2')
    """
    abs_path = os.path.abspath(raw_path)
    parent_dir = os.path.dirname(abs_path)
    parent_name = os.path.basename(parent_dir)
    
    if parent_name.upper() in ['RAW', 'CSV', 'PNG']:
        grandparent_dir = os.path.dirname(parent_dir)
        dataset_name = os.path.basename(grandparent_dir)
    else:
        dataset_name = parent_name
        
    seq_name = os.path.splitext(os.path.basename(abs_path))[0]
    return dataset_name, seq_name

def main():
    parser = argparse.ArgumentParser(description="ToG Tracker 一鍵全流程測試工具")
    parser.add_argument("raw", help="RAW 檔案名稱或完整路徑 (例如: POS1-2.raw 或完整路徑)")
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
            r"C:\Data\testPattern玻璃\Circle_R0.1inch_IPS0.005_Glass",
            r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW",
            r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass",
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

    dataset_name, seq_name = parse_dataset_and_seq(raw_path)
    raw_dir = os.path.dirname(raw_path)

    # 尋找對應的硬體 CSV 檔
    csv_candidates = [
        os.path.join(raw_dir, f"{seq_name}.csv"),
        os.path.join(raw_dir, "..", "CSV", f"{seq_name}.csv"),
        os.path.join(raw_dir, "CSV", f"{seq_name}.csv")
    ]
    csv_path = ""
    for cand in csv_candidates:
        if os.path.exists(cand):
            csv_path = os.path.abspath(cand)
            break

    # 建立專屬實驗輸出目錄
    project_dir = r"C:\PROJECT\ToG_Tracker"
    exp_dir = os.path.join(project_dir, "experiments", dataset_name, seq_name)
    os.makedirs(exp_dir, exist_ok=True)

    print("\n" + "=" * 75)
    print(f"啟動 ToG Tracker 自動化測試管線")
    print(f"資料集名稱: {dataset_name}")
    print(f"序列編號  : {seq_name}")
    print(f"完整 RAW  : {raw_path}")
    print(f"實驗歸檔  : {exp_dir}")
    if csv_path:
        print(f"對應 CSV  : {csv_path}")
    else:
        print(f"對應 CSV  : [未提供，啟動影像微塵次像素模板完全自主校準]")
    print("=" * 75)

    # 步驟 1: Step 1 特徵與 FPN 分析 (寫入 exp_dir)
    print("\n>>> [階段 1/4] 執行 Step 1: 特徵與 FPN 分析...")
    analyze_raw(raw_path, exp_dir)
    fpn_model_path = os.path.join(exp_dir, "fpn_model.raw")

    # 步驟 2: Step 2 真值 (Ground Truth) 重建 (寫入 exp_dir)
    print("\n>>> [階段 2/4] 執行 Step 2: 閉環真值 (Ground Truth) 重建...")
    gth_csv_path = run_step2(raw_path, csv_path, exp_dir, exp_dir)

    # 步驟 3: Step 3 三模式評估 (寫入 exp_dir)
    print("\n>>> [階段 3/4] 執行 Step 3: 三模式閉環追蹤評估...")
    evaluate_three_modes(raw_path, gth_csv_path, fpn_model_path, exp_dir)

    # 步驟 4: 匯出 TXT 與生成專屬 HTML 檢視器 (寫入 exp_dir)
    print("\n>>> [階段 4/4] 匯出四軌跡 TXT 並生成專屬 HTML 檢視器...")
    exp_tag_label = f"{dataset_name} / {seq_name}"
    html_path = run_export(
        raw_path=raw_path, 
        gth_csv_path=gth_csv_path, 
        fpn_path=fpn_model_path, 
        out_dir=exp_dir, 
        step1_dir=exp_dir, 
        exp_tag=exp_tag_label
    )

    # 同步鏡像至 step1_output / step2_output / step3_output 作為「最新執行結果」(便利性捷徑)
    legacy_s1 = os.path.join(project_dir, "step1_output")
    legacy_s2 = os.path.join(project_dir, "step2_output")
    legacy_s3 = os.path.join(project_dir, "step3_output")
    os.makedirs(legacy_s1, exist_ok=True)
    os.makedirs(legacy_s2, exist_ok=True)
    os.makedirs(legacy_s3, exist_ok=True)

    # 複製常用檔案至 legacy 目錄
    for f in os.listdir(exp_dir):
        src_f = os.path.join(exp_dir, f)
        if os.path.isfile(src_f):
            try:
                if f.startswith('step1_') or f.startswith('fpn_') or f.startswith('sample_') or f.endswith('.raw'):
                    shutil.copy2(src_f, os.path.join(legacy_s1, f))
                if f.endswith('_ground_truth.csv') or f.startswith('gth_') or f.startswith('step2_'):
                    shutil.copy2(src_f, os.path.join(legacy_s2, f))
                if f.startswith('Track_') or f.startswith('step3_') or f.endswith('.html') or f.startswith('trajectory_') or f.startswith('tracking_') or f.startswith('mismatch_'):
                    shutil.copy2(src_f, os.path.join(legacy_s3, f))
            except PermissionError:
                pass

    print("\n" + "=" * 75)
    print(f"🎉 測試完成！所有結果已妥善獨立歸檔（不同序列絕不覆蓋）：")
    print(f"  📁 本次實驗目錄: {exp_dir}")
    print(f"  🌐 專屬互動檢視: {html_path}")
    print(f"  📄 四軌跡詳細 CSV: {exp_dir}\\Track_*.csv")
    print(f"  📊 重建真值 CSV  : {gth_csv_path}")
    print("=" * 75 + "\n")

if __name__ == '__main__':
    main()
