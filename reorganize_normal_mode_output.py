r"""
========================================================================================
ToG Tracker: Normal Mode Legacy Output Reorganizer
========================================================================================
將既有 `normal_mode_output/` 下平鋪或帶標籤的歷史輸出目錄（例如 `POS1-1_bp5_csbc`、
`POS1-1_bp7_csbc`、`POS1-1_optnavi_*` 及 `20260529/` 等）依實際來源與 `run_meta.json`
重新歸位到統一的版本化階層架構：
  normal_mode_output/<dataset_subpath>/<seq_name>/
    ├── runs/<YYYYMMDD_HHMMSS>_<flt>_<fpn>[_gn<mode>]/
    ├── latest/          (同步最新一次結果)
    └── runs_index.csv   (依執行時間排列之完整索引)

無 `run_meta.json` 之早期平鋪舊輸出與重複副本資料夾 (` - 副本`) 則安全歸檔至：
  normal_mode_output/_legacy_archive/

本腳本為冪等設計，可重複安全執行。
========================================================================================
"""

import os
import sys
import csv
import json
import shutil
from datetime import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.append(SCRIPT_DIR)

from run_normal_mode import _extract_subpath_and_seq

NORMAL_OUT = os.path.join(SCRIPT_DIR, "normal_mode_output")
ARCHIVE_ROOT = os.path.join(NORMAL_OUT, "_legacy_archive")

FILTER_SHORT_MAP = {
    'bandpass3x7': 'bp3x7',
    'bandpass3x7_r0': 'bp3x7r0',
    'bandpass5x7': 'bp5x7',
    'bandpass7': 'bp7',
    'bandpass5': 'bp5',
    'bandpass5_sep': 'bp5sep',
    'optnavi_ctrl': 'optnavi',
    'none': 'none',
}


def _has_reserved_segment(rel_path):
    parts = [p.lower() for p in os.path.normpath(rel_path).split(os.sep)]
    return any(p in ("runs", "latest", "_legacy_archive") for p in parts)


def _format_ts(ts_str, fallback_path):
    if ts_str:
        try:
            dt = datetime.fromisoformat(ts_str)
            return dt.strftime("%Y%m%d_%H%M%S")
        except Exception:
            pass
    mtime = os.path.getmtime(fallback_path)
    return datetime.fromtimestamp(mtime).strftime("%Y%m%d_%H%M%S")


def rebuild_seq_index_and_latest(seq_root):
    """根據 runs/*/run_meta.json 依時間排序重建 runs_index.csv 並同步 latest/"""
    runs_dir = os.path.join(seq_root, "runs")
    if not os.path.isdir(runs_dir):
        return
    entries = []
    for run_id in sorted(os.listdir(runs_dir)):
        rdir = os.path.join(runs_dir, run_id)
        if not os.path.isdir(rdir):
            continue
        meta_p = os.path.join(rdir, "run_meta.json")
        if not os.path.exists(meta_p):
            continue
        try:
            with open(meta_p, "r", encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            continue
        ex = m.get("execution_info", {})
        cfg = m.get("hardware_and_algorithm_config", {})
        met = m.get("tracking_metrics", {})
        entries.append({
            "timestamp": ex.get("timestamp", ""),
            "run_id": run_id,
            "filter": cfg.get("filter_type", "unknown"),
            "fpn_mode": cfg.get("fpn_mode") or "full",
            "gain_norm": cfg.get("gain_norm_mode", "none"),
            "search_radius": cfg.get("search_radius", ""),
            "zncc_coast_th": "" if cfg.get("zncc_coast_th") is None else cfg.get("zncc_coast_th"),
            "closure_error_px": met.get("closure_error_px", ""),
            "radius_px": met.get("estimated_circle_radius_px", ""),
            "radial_rmse_px": met.get("radial_rmse_px", ""),
            "global_traj_rmse_px": met.get("global_traj_rmse_px", ""),
            "rmse_ref_type": met.get("rmse_ref_type", ""),
            "aspect_xy": met.get("aspect_ratio_xy", ""),
            "output_dir": os.path.abspath(rdir),
        })

    if not entries:
        return

    entries.sort(key=lambda e: (e["timestamp"], e["run_id"]))
    index_csv = os.path.join(seq_root, "runs_index.csv")
    with open(index_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "timestamp", "run_id", "filter", "fpn_mode", "gain_norm",
            "search_radius", "zncc_coast_th", "closure_error_px", "radius_px",
            "radial_rmse_px", "global_traj_rmse_px", "rmse_ref_type", "aspect_xy", "output_dir"
        ])
        for e in entries:
            w.writerow([
                e["timestamp"], e["run_id"], e["filter"], e["fpn_mode"], e["gain_norm"],
                e["search_radius"], e["zncc_coast_th"], e["closure_error_px"], e["radius_px"],
                e["radial_rmse_px"], e["global_traj_rmse_px"], e["rmse_ref_type"], e["aspect_xy"], e["output_dir"]
            ])

    latest_run_dir = entries[-1]["output_dir"]
    latest_dir = os.path.join(seq_root, "latest")
    os.makedirs(latest_dir, exist_ok=True)
    for fname in ("Track_B_NormalMode.csv", "run_meta.json", "Track_B_NormalMode_plot.png"):
        src = os.path.join(latest_run_dir, fname)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(latest_dir, fname))


def reorganize():
    if not os.path.isdir(NORMAL_OUT):
        print(f"[略過] 找不到 {NORMAL_OUT}")
        return

    os.makedirs(ARCHIVE_ROOT, exist_ok=True)
    moved_runs = 0
    archived_dirs = 0
    touched_seq_roots = set()

    # 1. 收集所有尚未在 runs/latest/_legacy_archive 內的 run_meta.json 目錄
    meta_dirs = []
    for root, dirs, files in os.walk(NORMAL_OUT):
        rel = os.path.relpath(root, NORMAL_OUT)
        if rel != "." and _has_reserved_segment(rel):
            continue
        if "run_meta.json" in files:
            meta_dirs.append(root)

    for src_dir in meta_dirs:
        rel = os.path.relpath(src_dir, NORMAL_OUT)
        meta_path = os.path.join(src_dir, "run_meta.json")
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)

        raw_abs = meta.get("raw_file_info", {}).get("absolute_path", "")
        if ("副本" in rel) or (not raw_abs):
            arch_dst = os.path.join(ARCHIVE_ROOT, rel)
            os.makedirs(os.path.dirname(arch_dst), exist_ok=True)
            if os.path.exists(arch_dst):
                arch_dst += "_" + _format_ts("", meta_path)
            shutil.move(src_dir, arch_dst)
            archived_dirs += 1
            print(f"[歸檔] {rel} -> _legacy_archive/")
            continue

        subpath, seq_name = _extract_subpath_and_seq(raw_abs)
        seq_root = os.path.abspath(os.path.join(NORMAL_OUT, subpath, seq_name))
        cfg = meta.get("hardware_and_algorithm_config", {})
        ts_str = _format_ts(meta.get("execution_info", {}).get("timestamp", ""), meta_path)
        filter_type = cfg.get("filter_type", "unknown").lower()
        fpn_mode = (cfg.get("fpn_mode") or "full").lower()
        gain_norm = (cfg.get("gain_norm_mode") or "none").lower()

        flt_short = FILTER_SHORT_MAP.get(filter_type, filter_type)
        parts = [ts_str, flt_short, fpn_mode]
        if gain_norm != "none":
            parts.append(f"gn{gain_norm}")
        run_id = "_".join(parts)

        runs_dir = os.path.join(seq_root, "runs")
        os.makedirs(runs_dir, exist_ok=True)
        dest_dir = os.path.join(runs_dir, run_id)
        if os.path.exists(dest_dir):
            sidx = 1
            while os.path.exists(f"{dest_dir}_{sidx:02d}"):
                sidx += 1
            run_id = f"{run_id}_{sidx:02d}"
            dest_dir = os.path.join(runs_dir, run_id)
        os.makedirs(dest_dir, exist_ok=True)

        # 逐檔搬移 (支援 src_dir == seq_root 的情況)
        for item in os.listdir(src_dir):
            if item.lower() in ("runs", "latest", "_legacy_archive", "runs_index.csv"):
                continue
            shutil.move(os.path.join(src_dir, item), os.path.join(dest_dir, item))

        moved_runs += 1
        touched_seq_roots.add(seq_root)
        print(f"[搬移] {rel} -> {os.path.relpath(dest_dir, NORMAL_OUT)}")

    # 2. 將其餘無 run_meta.json 的早期輸出目錄安全歸檔至 _legacy_archive/
    for item in sorted(os.listdir(NORMAL_OUT)):
        item_path = os.path.join(NORMAL_OUT, item)
        if item == "_legacy_archive" or not os.path.isdir(item_path):
            continue
        # 若整個一級子目錄內不含 runs/ (例如 cleanGlass-*, POS1-1..3, MousePad-1/2)，直接整批歸檔
        has_runs = False
        for root, dirs, files in os.walk(item_path):
            if "runs" in dirs or "runs_index.csv" in files:
                has_runs = True
                break
        if not has_runs:
            arch_dst = os.path.join(ARCHIVE_ROOT, item)
            if os.path.exists(arch_dst):
                shutil.rmtree(arch_dst)
            shutil.move(item_path, arch_dst)
            archived_dirs += 1
            print(f"[舊版歸檔] {item} -> _legacy_archive/{item}")

    # 3. 清理空目錄並檢查所有既有的 seq_root 重建 runs_index.csv 與 latest/
    for root, dirs, files in os.walk(NORMAL_OUT, topdown=False):
        rel = os.path.relpath(root, NORMAL_OUT)
        if rel == "." or _has_reserved_segment(rel):
            continue
        if "runs" in dirs:
            touched_seq_roots.add(os.path.abspath(root))
        try:
            if not os.listdir(root):
                os.rmdir(root)
        except OSError:
            pass

    for sr in sorted(touched_seq_roots):
        rebuild_seq_index_and_latest(sr)

    print(f"\n[整理完成] 共歸位 {moved_runs} 個版本化執行紀錄，安全歸檔 {archived_dirs} 個早期/副本目錄，重建 {len(touched_seq_roots)} 個 runs_index.csv。")


if __name__ == "__main__":
    reorganize()
