import sys
sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO
import json
import numpy as np

with open(r'C:\PROJECT\ToG_Tracker\step1_output\step1_report.json') as f:
    report = json.load(f)

raw_path = r'C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw'
hdr = OpniRawIO.read_header(raw_path)
fpn_hdr = OpniRawIO.read_header(r'C:\PROJECT\ToG_Tracker\step1_output\fpn_model.raw')
with open(r'C:\PROJECT\ToG_Tracker\step1_output\fpn_model.raw', 'rb') as f:
    fpn = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)

clusters = report['anchor_clusters']
print(f"Total clusters: {len(clusters)}")

valid_vectors = []
with open(raw_path, 'rb') as f:
    for idx, c in enumerate(clusters):
        cs = c['start']
        ce = c['end']
        clen = c['len']
        if clen < 8:
            continue
        
        pts = []
        for fi in range(cs, ce + 1):
            frm = OpniRawIO.load_frame(f, hdr, fi).astype(np.float32) - fpn
            sig = np.clip(np.abs(frm), 0, 255)
            p_val = np.max(sig)
            if p_val < 20:
                continue
            
            th = p_val * 0.6
            mask = sig > th
            if np.sum(mask) >= 2:
                weights = sig[mask]
                gy, gx = np.where(mask)
                cy = np.sum(gy * weights) / np.sum(weights)
                cx = np.sum(gx * weights) / np.sum(weights)
                pts.append((fi, cx, cy))
        
        if len(pts) >= 6:
            pts = np.array(pts)
            t_rel = pts[:, 0] - pts[0, 0]
            if np.max(t_rel) >= 5:
                vx, _ = np.polyfit(t_rel, pts[:, 1], 1)
                vy, _ = np.polyfit(t_rel, pts[:, 2], 1)
                spd = np.sqrt(vx**2 + vy**2)
                mid_t = np.mean(pts[:, 0])
                angle = np.arctan2(vy, vx)
                valid_vectors.append({
                    'cluster_id': idx,
                    't_mid': float(mid_t),
                    'vx': float(vx),
                    'vy': float(vy),
                    'speed': float(spd),
                    'angle_deg': float(np.rad2deg(angle)),
                    'n_pts': len(pts)
                })

print(f"Successfully fitted velocity vectors in {len(valid_vectors)} clusters:")
for v in valid_vectors:
    cid = v['cluster_id']
    tm = v['t_mid']
    vx = v['vx']
    vy = v['vy']
    spd = v['speed']
    ang = v['angle_deg']
    pts = v['n_pts']
    print(f"  Cluster {cid:2d} @ t={tm:6.1f}: vx={vx:+.4f}, vy={vy:+.4f}, speed={spd:6.4f} px/f, angle={ang:6.1f} deg (pts={pts})")
