import os
import sys
import numpy as np
import matplotlib.pyplot as plt

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO

def analyze_and_close_circle(raw_name):
    raw_path = os.path.join(r'C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW', raw_name)
    hdr = OpniRawIO.read_header(raw_path)
    n = hdr['num_frames']
    fps = hdr['fps']
    
    # 1. Precise Loop-Closure Detection
    # Frame 0 to 100 is static baseline
    with open(raw_path, 'rb') as f:
        f0 = OpniRawIO.load_frame(f, hdr, 0).astype(np.float32)
        
        # Detect start of motion: frame where difference from f0 starts rising above noise
        t_start = 0
        for fi in range(0, 400, 2):
            frm = OpniRawIO.load_frame(f, hdr, fi).astype(np.float32)
            if np.mean(np.abs(frm - f0)) > 1.2: # noise is ~0.6
                t_start = fi
                break
                
        # Find exact loop-closure frame near the end (search last 800 frames)
        search_start = max(0, n - 800)
        sads = []
        for fi in range(search_start, n):
            frm = OpniRawIO.load_frame(f, hdr, fi).astype(np.float32)
            sad = np.mean(np.abs(frm - f0))
            sads.append((fi, sad))
            
    sads.sort(key=lambda x: x[1])
    t_end, min_sad = sads[0]
    
    # Motion duration for 1 full revolution
    rev_frames = t_end - t_start
    omega = (2 * np.pi) / rev_frames
    
    # Sensor radius from nominal speed (0.02 ips * 1000 cpi / fps or calibrated)
    # R_nominal = 0.2 inch = 200 counts.
    # Speed = 0.02 ips = 20 counts/s. At fps: 20 / fps counts/frame.
    # Speed in px/frame: roughly 0.15 px/frame -> R_px = speed / omega
    speed_px = 0.158
    R_px = speed_px / omega
    
    # Generate trajectory
    frame_indices = np.arange(n)
    phi0 = 0.0 # start at 0
    
    # Active motion angle: 0 before t_start, progresses to 2*pi at t_end, stays at 2*pi after t_end
    angles = np.zeros(n)
    active_mask = (frame_indices >= t_start) & (frame_indices <= t_end)
    angles[active_mask] = omega * (frame_indices[active_mask] - t_start)
    angles[frame_indices > t_end] = 2 * np.pi
    
    # Tangential displacements
    dx = np.zeros(n)
    dy = np.zeros(n)
    dx[active_mask] = speed_px * np.cos(angles[active_mask] + np.pi/2)
    dy[active_mask] = speed_px * np.sin(angles[active_mask] + np.pi/2)
    
    x = np.cumsum(dx)
    y = np.cumsum(dy)
    
    # Loop closure error: distance between start and end
    closure_error = np.sqrt((x[t_end] - x[t_start])**2 + (y[t_end] - y[t_start])**2)
    
    print(f"[{raw_name}] Total={n}f | t_start={t_start:3d}, t_end={t_end:4d} | Rev Frames={rev_frames:4d} ({rev_frames/fps:.2f}s) | Min SAD={min_sad:.2f} | Closure Error={closure_error:.4f} px")
    return {
        'name': raw_name,
        'n': n,
        't_start': t_start,
        't_end': t_end,
        'rev_frames': rev_frames,
        'closure_error': closure_error,
        'min_sad': min_sad,
        'x': x,
        'y': y
    }

if __name__ == '__main__':
    raws = ['POS1-1.raw', 'POS1-2.raw', 'POS1-3.raw', 'POS2-1.raw', 'POS2-2.raw', 'POS2-3.raw', 'POS3-1.raw']
    results = []
    for r in raws:
        results.append(analyze_and_close_circle(r))
        
    # Plot all trajectories in a multi-panel figure
    fig, axes = plt.subplots(2, 4, figsize=(16, 8))
    axes = axes.flatten()
    for i, res in enumerate(results):
        ax = axes[i]
        ax.plot(res['x'], res['y'], 'b-', lw=1.5, label='Closed GTH Circle')
        ax.scatter(res['x'][res['t_start']], res['y'][res['t_start']], color='green', s=60, label='Start')
        ax.scatter(res['x'][res['t_end']], res['y'][res['t_end']], color='red', marker='x', s=60, label='End (Closed)')
        ax.set_aspect('equal')
        ax.set_title(f"{res['name']}\nLoop Error = {res['closure_error']:.3f} px", fontsize=11)
        ax.grid(True, alpha=0.3)
        if i == 0:
            ax.legend(loc='upper right', fontsize=8)
            
    # Hide unused subplot
    axes[-1].axis('off')
    plt.suptitle("Ground Truth Closed Circle Reconstruction across Multiple RAW Sequences", fontsize=14)
    plt.tight_layout()
    out_plot = r'C:\PROJECT\ToG_Tracker\step2_output\multi_raw_closed_circles.png'
    plt.savefig(out_plot, dpi=150)
    plt.close()
    print(f"\nSaved multi-raw comparison plot to: {out_plot}")
