import os
import sys
import json
import numpy as np
from scipy.signal import correlate2d

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO

def subpixel_peak(corr_map):
    # Find peak and fit 2D quadratic
    cy, cx = np.unravel_index(np.argmax(corr_map), corr_map.shape)
    h, w = corr_map.shape
    if 0 < cy < h - 1 and 0 < cx < w - 1:
        # Parabolic fit along x
        dx = (corr_map[cy, cx+1] - corr_map[cy, cx-1]) / (2 * (2 * corr_map[cy, cx] - corr_map[cy, cx+1] - corr_map[cy, cx-1]) + 1e-6)
        # Parabolic fit along y
        dy = (corr_map[cy+1, cx] - corr_map[cy-1, cx]) / (2 * (2 * corr_map[cy, cx] - corr_map[cy+1, cx] - corr_map[cy-1, cx]) + 1e-6)
        return cy + dy, cx + dx, corr_map[cy, cx]
    return cy, cx, corr_map[cy, cx]

def measure_raw_dust_radius(raw_name):
    raw_path = os.path.join(r'C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW', raw_name)
    hdr = OpniRawIO.read_header(raw_path)
    n = hdr['num_frames']
    fps = hdr['fps']
    
    # Load FPN
    fpn_path = r'C:\PROJECT\ToG_Tracker\step1_output\fpn_model.raw'
    fpn_hdr = OpniRawIO.read_header(fpn_path)
    with open(fpn_path, 'rb') as f:
        fpn = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)

    # Detect loop closure
    with open(raw_path, 'rb') as f:
        f0 = OpniRawIO.load_frame(f, hdr, 0).astype(np.float32)
        t_start = 0
        for fi in range(0, min(500, n), 2):
            frm = OpniRawIO.load_frame(f, hdr, fi).astype(np.float32)
            if np.mean(np.abs(frm - f0)) > 1.2:
                t_start = fi
                break
        
        search_start = max(0, n - 800)
        sads = []
        for fi in range(search_start, n):
            frm = OpniRawIO.load_frame(f, hdr, fi).astype(np.float32)
            sad = np.mean(np.abs(frm - f0))
            sads.append((fi, sad))
        sads.sort(key=lambda x: x[1])
        t_end = sads[0][0]

    rev_frames = t_end - t_start
    omega = (2 * np.pi) / rev_frames

    # Scan across raw in strides of 20 frames, find high-contrast dust regions
    # and measure 20-frame displacement
    delta_f = 20
    measurements = []
    with open(raw_path, 'rb') as f:
        for fi in range(t_start + 10, t_end - delta_f - 10, 15):
            f1 = OpniRawIO.load_frame(f, hdr, fi).astype(np.float32) - fpn
            f2 = OpniRawIO.load_frame(f, hdr, fi + delta_f).astype(np.float32) - fpn
            
            # Check contrast in center 24x24
            center_patch1 = f1[6:30, 6:30]
            std1 = np.std(center_patch1)
            ptp1 = np.ptp(center_patch1)
            
            # Only measure if there is a distinct dust particle
            if std1 > 3.0 and ptp1 > 15.0:
                # 16x16 template from center
                tmpl = f1[10:26, 10:26]
                tmpl = tmpl - np.mean(tmpl)
                norm_tmpl = np.linalg.norm(tmpl)
                if norm_tmpl < 1e-2:
                    continue
                
                # Search in f2 within search window [4:32, 4:32]
                search_area = f2[4:32, 4:32]
                search_area = search_area - np.mean(search_area)
                
                # Cross correlation
                # compute correlation grid
                corr_grid = np.zeros((9, 9), dtype=np.float32)
                for dy in range(-4, 5):
                    for dx in range(-4, 5):
                        cand = f2[10+dy:26+dy, 10+dx:26+dx]
                        cand = cand - np.mean(cand)
                        nc = np.linalg.norm(cand)
                        if nc > 1e-2:
                            corr_grid[dy+4, dx+4] = np.sum(tmpl * cand) / (norm_tmpl * nc)
                
                max_val = np.max(corr_grid)
                if max_val > 0.85: # High confidence match
                    py, px, pval = subpixel_peak(corr_grid)
                    disp_y = py - 4.0
                    disp_x = px - 4.0
                    spd = np.sqrt(disp_x**2 + disp_y**2) / delta_f
                    # filter out 0 speed (stuck/residual FPN)
                    if spd > 0.10:
                        measurements.append({
                            'fi': fi,
                            'disp_x': disp_x,
                            'disp_y': disp_y,
                            'speed': spd,
                            'corr': max_val,
                            'std': std1
                        })

    if len(measurements) > 0:
        speeds = [m['speed'] for m in measurements]
        med_spd = float(np.median(speeds))
        std_spd = float(np.std(speeds))
        calc_r = med_spd / omega
        print(f"[{raw_name}] Rev: {rev_frames}f, Confident Dust Samples: {len(measurements)}")
        print(f"  Speed: {med_spd:.4f} +/- {std_spd:.4f} px/f")
        print(f"  Calibrated Radius: {calc_r:.2f} px (Diameter: {2*calc_r:.2f} px)")
        return {
            'raw': raw_name,
            'rev_frames': rev_frames,
            't_start': t_start,
            't_end': t_end,
            'samples': len(measurements),
            'speed': med_spd,
            'radius_px': calc_r,
            'diameter_px': 2 * calc_r
        }
    else:
        print(f"[{raw_name}] No strong dust samples found.")
        return None

if __name__ == '__main__':
    raws = ['POS1-1.raw', 'POS1-2.raw', 'POS1-3.raw', 'POS2-1.raw', 'POS2-2.raw', 'POS2-3.raw', 'POS3-1.raw', 'POS3-2.raw', 'POS3-3.raw']
    results = []
    for r in raws:
        res = measure_raw_dust_radius(r)
        if res:
            results.append(res)
    with open(r'C:\PROJECT\ToG_Tracker\step2_output\calibrated_physical_radii.json', 'w') as f:
        json.dump(results, f, indent=2)
