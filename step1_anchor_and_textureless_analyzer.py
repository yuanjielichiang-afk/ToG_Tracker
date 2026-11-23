import os
import struct
import json
import numpy as np
import matplotlib.pyplot as plt
from scipy.signal import convolve2d

class OpniRawIO:
    """Helper for reading and writing standard OPNI raw files."""
    HEADER_SIZE = 128

    @staticmethod
    def read_header(filepath):
        with open(filepath, 'rb') as f:
            hdr = f.read(OpniRawIO.HEADER_SIZE)
        if len(hdr) < OpniRawIO.HEADER_SIZE:
            raise ValueError(f"File too small: {filepath}")
        sig = hdr[:4].decode('ascii', errors='ignore')
        if sig != 'OPNI':
            raise ValueError(f"Invalid OPNI signature: {sig}")
        ver, w, h, frame_size, num_frames = struct.unpack('<HHHHI', hdr[4:16])
        fps, bit_depth = struct.unpack('<HH', hdr[28:32])
        return {
            'signature': sig,
            'version': ver,
            'width': w,
            'height': h,
            'frame_size': frame_size,
            'num_frames': num_frames,
            'fps': fps,
            'bit_depth': bit_depth
        }

    @staticmethod
    def load_frame(f, header, frame_idx):
        offset = OpniRawIO.HEADER_SIZE + frame_idx * header['frame_size']
        f.seek(offset)
        raw = f.read(header['frame_size'])
        if len(raw) < header['frame_size']:
            return None
        w, h = header['width'], header['height']
        pixels = np.frombuffer(raw[4: 4 + w * h], dtype=np.uint8)
        return pixels.reshape((h, w))

    @staticmethod
    def write_raw(filepath, frames, width, height, fps=30, bit_depth=8):
        frame_size = 4 + width * height + 4
        num_frames = len(frames)
        header = bytearray(OpniRawIO.HEADER_SIZE)
        header[:4] = b'OPNI'
        struct.pack_into('<H', header, 4, 1)            # version
        struct.pack_into('<H', header, 6, width)        # width
        struct.pack_into('<H', header, 8, height)       # height
        struct.pack_into('<H', header, 10, frame_size)  # frame_size
        struct.pack_into('<I', header, 12, num_frames)  # num_frames
        struct.pack_into('<H', header, 28, fps)         # fps
        struct.pack_into('<H', header, 30, bit_depth)   # bit_depth

        with open(filepath, 'wb') as f:
            f.write(header)
            for idx, frame in enumerate(frames):
                f.write(struct.pack('<I', idx))
                clipped = np.clip(frame, 0, 255).astype(np.uint8)
                f.write(clipped.tobytes())
                f.write(struct.pack('<I', 0)) # footer

        print(f"Successfully wrote {num_frames} frames to {filepath}")

def analyze_raw(raw_path, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    header = OpniRawIO.read_header(raw_path)
    w, h, num_frames = header['width'], header['height'], header['num_frames']
    print(f"Analyzing {raw_path}: {w}x{h}, {num_frames} frames, FPS: {header['fps']}")

    # Pass 1: Compute high-precision FPN baseline
    # We sample across the sequence to build a temporal median/trimmed-mean background
    print("Pass 1: Estimating FPN / Background Model...")
    sample_step = max(1, num_frames // 400)
    sampled_frames = []
    with open(raw_path, 'rb') as f:
        for idx in range(0, num_frames, sample_step):
            frame = OpniRawIO.load_frame(f, header, idx)
            if frame is not None:
                sampled_frames.append(frame.astype(np.float32))

    sampled_arr = np.array(sampled_frames) # (N, H, W)
    # Using temporal median effectively rejects moving dust particles
    fpn_model = np.median(sampled_arr, axis=0)

    # Save FPN model as raw and PNG
    fpn_raw_path = os.path.join(out_dir, "fpn_model.raw")
    OpniRawIO.write_raw(fpn_raw_path, [fpn_model], w, h, fps=header['fps'])
    
    plt.figure(figsize=(6, 5))
    plt.imshow(fpn_model, cmap='gray')
    plt.colorbar(label='Pixel Value (0-255)')
    plt.title(f"FPN / Static Background Model ({w}x{h})\nTemporal Median (N={len(sampled_frames)})")
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "fpn_model.png"), dpi=150)
    plt.close()

    # Pass 2: Frame-by-frame analysis with FPN subtracted
    print("Pass 2: Scanning all frames for moving dust vs textureless...")
    lap_kernel = np.array([[0, -1, 0], [-1, 4, -1], [0, -1, 0]], dtype=np.float32)

    stats = {
        'frame_idx': [],
        'max_diff': [],
        'mean_abs_diff': [],
        'squal': [],
        'feature_pixel_count': [],
        'is_textureless': [],
        'is_anchor': []
    }

    # Thresholds:
    # Sensor readout noise std is ~1.5 - 2.0 counts.
    # 3-sigma noise is ~5-6 counts; with slight illumination drift, <= 10 counts is textureless.
    TL_DIFF_TH = 10.0      # Max difference <= 10 counts is flat glass (Textureless)
    ANCHOR_DIFF_TH = 25.0  # Max difference >= 25 counts has unmistakable dust contrast
    ANCHOR_PIX_TH = 3      # At least 3 connected/prominent pixels above noise

    textureless_frames = []
    textureless_indices = []
    anchor_frames = []
    anchor_indices = []

    with open(raw_path, 'rb') as f:
        for idx in range(num_frames):
            frame = OpniRawIO.load_frame(f, header, idx)
            if frame is None:
                break
            
            frame_f = frame.astype(np.float32)
            # Subtract static FPN
            diff = frame_f - fpn_model
            abs_diff = np.abs(diff)
            
            max_d = float(np.max(abs_diff))
            mean_abs_d = float(np.mean(abs_diff))
            
            # SQUAL: Energy of high-frequency Laplacian of moving signal
            lap_signal = convolve2d(diff, lap_kernel, mode='valid')
            squal = float(np.var(lap_signal))
            
            # Pixels above significant dust threshold (15 counts)
            feat_pixels = int(np.sum(abs_diff > 15.0))

            is_tl = bool(max_d <= TL_DIFF_TH and feat_pixels == 0)
            is_anchor = bool(max_d >= ANCHOR_DIFF_TH and feat_pixels >= ANCHOR_PIX_TH and squal > 5.0)

            stats['frame_idx'].append(idx)
            stats['max_diff'].append(max_d)
            stats['mean_abs_diff'].append(mean_abs_d)
            stats['squal'].append(squal)
            stats['feature_pixel_count'].append(feat_pixels)
            stats['is_textureless'].append(is_tl)
            stats['is_anchor'].append(is_anchor)

            if is_tl:
                textureless_indices.append(idx)
                if len(textureless_frames) < 500: # save up to 500 frames for visual inspection
                    textureless_frames.append(frame)

            if is_anchor:
                anchor_indices.append(idx)
                if len(anchor_frames) < 500:
                    anchor_frames.append(frame)

            if idx % 1000 == 0 or idx == num_frames - 1:
                print(f"Processed {idx:4d}/{num_frames} frames... TL found: {len(textureless_indices)}, Anchors: {len(anchor_indices)}")

    # Save Textureless RAW file
    tl_raw_path = os.path.join(out_dir, "textureless_extracted.raw")
    OpniRawIO.write_raw(tl_raw_path, textureless_frames, w, h, fps=header['fps'])

    # Save Anchor RAW file
    anchor_raw_path = os.path.join(out_dir, "anchor_extracted.raw")
    OpniRawIO.write_raw(anchor_raw_path, anchor_frames, w, h, fps=header['fps'])

    # Find Anchor Clusters (consecutive frames with anchor features)
    clusters = []
    if anchor_indices:
        start = anchor_indices[0]
        prev = anchor_indices[0]
        for cur in anchor_indices[1:]:
            if cur == prev + 1:
                prev = cur
            else:
                if prev - start + 1 >= 5: # Cluster must be at least 5 frames
                    clusters.append((start, prev, prev - start + 1))
                start = cur
                prev = cur
        if prev - start + 1 >= 5:
            clusters.append((start, prev, prev - start + 1))

    print(f"\n--- Analysis Summary ---")
    print(f"Total Frames: {num_frames}")
    print(f"Textureless (TL) Frames: {len(textureless_indices)} ({len(textureless_indices)/num_frames*100:.1f}%)")
    print(f"Anchor Frames: {len(anchor_indices)} ({len(anchor_indices)/num_frames*100:.1f}%)")
    print(f"Anchor Clusters (>=5 consecutive frames): {len(clusters)}")
    for i, (cs, ce, clen) in enumerate(clusters[:10]):
        print(f"  Cluster {i+1}: Frames {cs:4d} ~ {ce:4d} (length={clen} frames)")
    if len(clusters) > 10:
        print(f"  ... and {len(clusters)-10} more clusters.")

    # Save metadata JSON
    meta = {
        'raw_file': raw_path,
        'width': w,
        'height': h,
        'total_frames': num_frames,
        'textureless_count': len(textureless_indices),
        'anchor_count': len(anchor_indices),
        'textureless_indices_sample': textureless_indices[:50],
        'anchor_clusters': [{'start': c[0], 'end': c[1], 'len': c[2]} for c in clusters]
    }
    with open(os.path.join(out_dir, "step1_report.json"), "w") as f:
        json.dump(meta, f, indent=2)

    # Plot comprehensive timeline
    fig, axes = plt.subplots(3, 1, figsize=(14, 9), sharex=True)

    frames = np.array(stats['frame_idx'])
    max_d = np.array(stats['max_diff'])
    squal = np.array(stats['squal'])
    feat_pix = np.array(stats['feature_pixel_count'])

    # 1. Max Dust Contrast
    axes[0].plot(frames, max_d, color='#1f77b4', lw=1.2, label='Max Contrast (|Raw - FPN|)')
    axes[0].axhline(TL_DIFF_TH, color='red', linestyle='--', label=f'TL Threshold ({TL_DIFF_TH})')
    axes[0].axhline(ANCHOR_DIFF_TH, color='green', linestyle='--', label=f'Anchor Threshold ({ANCHOR_DIFF_TH})')
    axes[0].set_ylabel('Contrast (counts)')
    axes[0].set_title(f"Step 1: ToG Feature & Textureless Profile ({os.path.basename(raw_path)})")
    axes[0].legend(loc='upper right')
    axes[0].grid(True, alpha=0.3)

    # 2. SQUAL (Laplacian Energy of Moving Feature)
    axes[1].plot(frames, squal, color='#2ca02c', lw=1.2, label='SQUAL (Feature Energy)')
    axes[1].set_ylabel('SQUAL')
    axes[1].set_yscale('log')
    axes[1].legend(loc='upper right')
    axes[1].grid(True, alpha=0.3)

    # 3. State Timeline (Anchor vs TL vs Marginal)
    state = np.zeros(num_frames) # 0: Marginal
    state[np.array(stats['is_textureless'])] = -1 # -1: Textureless
    state[np.array(stats['is_anchor'])] = 1       # +1: Anchor

    axes[2].fill_between(frames, 0, state, where=(state == 1), color='green', alpha=0.6, label='Anchor Region (T)')
    axes[2].fill_between(frames, 0, state, where=(state == -1), color='red', alpha=0.6, label='Textureless Region (TL)')
    axes[2].fill_between(frames, 0, state, where=(state == 0), color='orange', alpha=0.3, label='Marginal / Weak Dust')
    axes[2].set_ylabel('Region State')
    axes[2].set_yticks([-1, 0, 1])
    axes[2].set_yticklabels(['TL (Empty)', 'Marginal', 'Anchor'])
    axes[2].set_xlabel('Frame Index')
    axes[2].legend(loc='upper right')
    axes[2].grid(True, alpha=0.3)

    plt.tight_layout()
    plot_path = os.path.join(out_dir, "step1_feature_timeline.png")
    plt.savefig(plot_path, dpi=150)
    plt.close()
    print(f"Saved feature timeline plot to {plot_path}")

    # Generate a visual comparison panel of samples
    if len(textureless_frames) > 0 and len(anchor_frames) > 0:
        fig, axes = plt.subplots(2, 4, figsize=(12, 6))
        # Top row: Textureless samples
        for col in range(4):
            idx = col * (len(textureless_frames) // 4)
            axes[0, col].imshow(textureless_frames[idx], cmap='gray', vmin=0, vmax=255)
            axes[0, col].set_title(f"TL Frame #{textureless_indices[idx]}")
            axes[0, col].axis('off')
        # Bottom row: Anchor samples
        for col in range(4):
            idx = col * (len(anchor_frames) // 4)
            axes[1, col].imshow(anchor_frames[idx], cmap='gray', vmin=0, vmax=255)
            axes[1, col].set_title(f"Anchor Frame #{anchor_indices[idx]}")
            axes[1, col].axis('off')
        plt.suptitle("Textureless (Top) vs Anchor / Dust (Bottom) Sample Visuals", fontsize=14)
        plt.tight_layout()
        sample_path = os.path.join(out_dir, "sample_comparison.png")
        plt.savefig(sample_path, dpi=150)
        plt.close()
        print(f"Saved sample comparison plot to {sample_path}")

if __name__ == '__main__':
    raw_file = r"C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw"
    output_directory = r"C:\Users\Shouder Wei\.gemini\antigravity\scratch\tog_tracker\step1_output"
    analyze_raw(raw_file, output_directory)
