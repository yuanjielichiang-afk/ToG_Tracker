import os
import sys
import struct
import numpy as np

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO

def check_file(raw_name):
    raw_path = os.path.join(r'C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW', raw_name)
    hdr = OpniRawIO.read_header(raw_path)
    n = hdr['num_frames']
    fps = hdr['fps']
    
    # 1. Estimate temporal background
    sample_step = max(1, n // 200)
    samples = []
    with open(raw_path, 'rb') as f:
        for i in range(0, n, sample_step):
            frm = OpniRawIO.load_frame(f, hdr, i)
            if frm is not None:
                samples.append(frm.astype(np.float32))
    bg = np.median(samples, axis=0)

    # 2. Check motion start and motion end
    # Motion start: when diff from frame 0 exceeds noise threshold
    with open(raw_path, 'rb') as f:
        f0 = OpniRawIO.load_frame(f, hdr, 0).astype(np.float32)
        diffs_start = []
        for fi in range(0, min(600, n), 10):
            frm = OpniRawIO.load_frame(f, hdr, fi).astype(np.float32)
            diffs_start.append((fi, np.max(np.abs(frm - f0))))
            
        f_end = OpniRawIO.load_frame(f, hdr, n - 1).astype(np.float32)
        diffs_end = []
        for fi in range(n - 1, max(n - 600, 0), -10):
            frm = OpniRawIO.load_frame(f, hdr, fi).astype(np.float32)
            diffs_end.append((fi, np.max(np.abs(frm - f_end))))

    # 3. Check dust features at start and end
    # Are the dust specks in the first moving frame at the SAME location in the last moving frame?
    # If the circle is closed, the dust field at theta = 0 and theta = 2*pi must match!
    print(f"=== {raw_name} (Frames: {n}, FPS: {fps}) ===")
    print("Start diffs sample (frame: max_diff):", [(fi, int(d)) for fi, d in diffs_start[:10]])
    print("End diffs sample (frame: max_diff):", [(fi, int(d)) for fi, d in diffs_end[:10]])

if __name__ == '__main__':
    for name in ['POS1-1.raw', 'POS1-2.raw', 'POS1-3.raw', 'POS2-1.raw', 'POS2-2.raw']:
        check_file(name)
