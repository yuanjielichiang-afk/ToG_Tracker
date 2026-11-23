import os
import sys
import json
import numpy as np

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO
from tracker_core import NormalModeTracker, TeacherModeTracker, IdealModeTracker

def run_export(raw_path=None, gth_csv_path=None, fpn_path=None, out_dir=None):
    if raw_path is None:
        raw_path = r'C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw'
    if gth_csv_path is None:
        gth_csv_path = r'C:\PROJECT\ToG_Tracker\step2_output\POS1-1_ground_truth.csv'
    if fpn_path is None:
        fpn_path = r'C:\PROJECT\ToG_Tracker\step1_output\fpn_model.raw'
    if out_dir is None:
        out_dir = r'C:\PROJECT\ToG_Tracker\step3_output'
    os.makedirs(out_dir, exist_ok=True)

    print("Loading Ground Truth and Raw Data...")
    gth_data = np.genfromtxt(gth_csv_path, delimiter=',', names=True)
    gth_x = gth_data['x_px']
    gth_y = gth_data['y_px']
    gth_dx = gth_data['dx_px']
    gth_dy = gth_data['dy_px']
    num_frames = len(gth_data)

    fpn_hdr = OpniRawIO.read_header(fpn_path)
    with open(fpn_path, 'rb') as f:
        fpn_model = OpniRawIO.load_frame(f, fpn_hdr, 0).astype(np.float32)

    raw_hdr = OpniRawIO.read_header(raw_path)
    frames = []
    with open(raw_path, 'rb') as f:
        for fi in range(num_frames):
            frm = OpniRawIO.load_frame(f, raw_hdr, fi)
            frames.append(frm)

    # Step 1 Anchor Clusters for T/TL zone identification
    step1_json = r'C:\PROJECT\ToG_Tracker\step1_output\step1_report.json'
    with open(step1_json, 'r') as f:
        s1 = json.load(f)
    anchor_clusters = s1.get('anchor_clusters', [])
    is_anchor = np.zeros(num_frames, dtype=bool)
    for c in anchor_clusters:
        is_anchor[c['start']: c['end'] + 1] = True

    tracks = {}
    
    # 1. Ground truth track points
    tracks['gth'] = [{
        'x': round(float(gth_x[i]), 3), 
        'y': round(float(gth_y[i]), 3),
        'is_t': bool(is_anchor[i]),
        'squal': 0.0
    } for i in range(num_frames)]

    # 2. Simulate the 3 modes using tracker_core
    print("Simulating Mode: NORMAL...")
    tracker_norm = NormalModeTracker(search_radius=1, subpixel_scale=16, 
                                     keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                                     fpn_model=fpn_model)
    norm_pts = []
    for fi in range(num_frames):
        res = tracker_norm.step(frames[fi], fi)
        pos_err_x = abs(round(res['pred_x']) - gth_x[fi])
        pos_err_y = abs(round(res['pred_y']) - gth_y[fi])
        in_basin = bool(pos_err_x <= 1.0 and pos_err_y <= 1.0)
        norm_pts.append({
            'x': round(float(res['est_x']), 3),
            'y': round(float(res['est_y']), 3),
            'err': round(float(np.sqrt((res['est_x'] - gth_x[fi])**2 + (res['est_y'] - gth_y[fi])**2)), 3),
            'in_basin': in_basin,
            'int': False,
            'is_t': bool(is_anchor[fi]),
            'squal': round(float(res['squal']), 1)
        })
    tracks['normal'] = norm_pts

    print("Simulating Mode: TEACHER...")
    tracker_teach = TeacherModeTracker(search_radius=1, subpixel_scale=16, 
                                       keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                                       fpn_model=fpn_model)
    teach_pts = []
    for fi in range(num_frames):
        g_pos = (gth_x[fi], gth_y[fi])
        g_vel = (gth_dx[fi], gth_dy[fi])
        res = tracker_teach.step(frames[fi], fi, g_pos, g_vel)
        teach_pts.append({
            'x': round(float(res['est_x']), 3),
            'y': round(float(res['est_y']), 3),
            'err': round(float(np.sqrt((res['est_x'] - gth_x[fi])**2 + (res['est_y'] - gth_y[fi])**2)), 3),
            'in_basin': res['in_basin'],
            'int': res['teacher_intervened'],
            'is_t': bool(is_anchor[fi]),
            'squal': round(float(res['squal']), 1)
        })
    tracks['teacher'] = teach_pts

    print("Simulating Mode: IDEAL...")
    tracker_ideal = IdealModeTracker(search_radius=1, subpixel_scale=16, 
                                     keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                                     fpn_model=fpn_model)
    ideal_pts = []
    for fi in range(num_frames):
        g_pos = (gth_x[fi], gth_y[fi])
        g_vel = (gth_dx[fi], gth_dy[fi])
        res = tracker_ideal.step(frames[fi], fi, g_pos, g_vel)
        ideal_pts.append({
            'x': round(float(res['est_x']), 3),
            'y': round(float(res['est_y']), 3),
            'err': round(float(np.sqrt((res['est_x'] - gth_x[fi])**2 + (res['est_y'] - gth_y[fi])**2)), 3),
            'in_basin': True,
            'int': False,
            'is_t': bool(is_anchor[fi]),
            'squal': round(float(res['squal']), 1)
        })
    tracks['ideal'] = ideal_pts

    # 3. Export separate TXT files for the desktop tool
    # Track_C_TeacherMode.txt now contains rich diagnostic columns!
    with open(os.path.join(out_dir, 'Track_A_GroundTruth.txt'), 'w') as f:
        f.write("#frame,x,y,zone\n")
        for i, p in enumerate(tracks['gth']):
            f.write(f"{i},{p['x']:.3f},{p['y']:.3f},{'T' if p['is_t'] else 'TL'}\n")

    with open(os.path.join(out_dir, 'Track_B_NormalMode.txt'), 'w') as f:
        f.write("#frame,x,y,err,zone,squal\n")
        for i, p in enumerate(tracks['normal']):
            f.write(f"{i},{p['x']:.3f},{p['y']:.3f},{p['err']:.3f},{'T' if p['is_t'] else 'TL'},{p['squal']:.1f}\n")

    with open(os.path.join(out_dir, 'Track_C_TeacherMode.txt'), 'w') as f:
        f.write("#frame,x,y,err,intervened,zone,squal\n")
        for i, p in enumerate(tracks['teacher']):
            f.write(f"{i},{p['x']:.3f},{p['y']:.3f},{p['err']:.3f},{1 if p['int'] else 0},{'T' if p['is_t'] else 'TL'},{p['squal']:.1f}\n")

    with open(os.path.join(out_dir, 'Track_D_IdealMode.txt'), 'w') as f:
        f.write("#frame,x,y,err,zone,squal\n")
        for i, p in enumerate(tracks['ideal']):
            f.write(f"{i},{p['x']:.3f},{p['y']:.3f},{p['err']:.3f},{'T' if p['is_t'] else 'TL'},{p['squal']:.1f}\n")

    print("Exported all 4 TXT files with detailed columns to step3_output!")

    # 4. Generate Preloaded HTML Viewer based on user's exact template
    html_template = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ToG 光學滑鼠四軌跡比較工具 (Step 3)</title>
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <!-- React & Babel -->
    <script crossorigin src="https://unpkg.com/react@18/umd/react.development.js"></script>
    <script crossorigin src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
    <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
    
    <style>
        body { margin: 0; padding: 0; overflow: hidden; background-color: #f8fafc; }
        .canvas-container { user-select: none; }
    </style>
</head>
<body>
    <div id="root"></div>

    <script type="text/babel">
        const { useState, useRef, useEffect, useCallback } = React;

        // Preloaded Track Data from Simulation
        const PRELOADED_TRACKS_DATA = __PRELOADED_DATA_PLACEHOLDER__;

        const Icon = ({ path, color = "currentColor", size = 20 }) => (
            <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                {path}
            </svg>
        );

        const Icons = {
            Layers: <Icon path={<><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></>} />,
            ZoomIn: <Icon path={<><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></>} />,
            ZoomOut: <Icon path={<><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="8" y1="11" x2="14" y2="11"/></>} />,
            RefreshCw: <Icon path={<><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></>} />,
            Download: <Icon path={<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></>} />,
            Upload: <Icon path={<><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></>} size={14}/>,
            Eye: <Icon path={<><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></>} size={18}/>,
            EyeOff: <Icon path={<><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07-2.3 2.3"/><line x1="1" y1="1" x2="23" y2="23"/></>} size={18}/>,
            Move: <Icon path={<><polyline points="5 9 2 12 5 15"/><polyline points="9 5 12 2 15 5"/><polyline points="15 19 12 22 9 19"/><polyline points="19 15 22 12 19 9"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="12" y1="2" x2="12" y2="22"/></>} size={48} color="#9ca3af" />
        };

        const MouseTrackComparator = () => {
            const canvasRef = useRef(null);
            const containerRef = useRef(null);

            const INITIAL_TRACKS = [
                { id: 'A', color: '#0f172a', points: PRELOADED_TRACKS_DATA.gth, fileName: 'Ground Truth (真值圓)', visible: true, label: '軌跡 A - Ground Truth (黑)', isGth: true },
                { id: 'B', color: '#2563eb', points: PRELOADED_TRACKS_DATA.normal, fileName: 'Normal Mode (閉環自適應)', visible: true, label: '軌跡 B - Normal Mode (藍)' },
                { id: 'C', color: '#ea580c', points: PRELOADED_TRACKS_DATA.teacher, fileName: 'Teacher Mode (失步即時校正)', visible: true, label: '軌跡 C - Teacher Mode (橘)' },
                { id: 'D', color: '#16a34a', points: PRELOADED_TRACKS_DATA.ideal, fileName: 'Ideal Mode (完全解耦)', visible: true, label: '軌跡 D - Ideal Mode (綠)' },
            ];

            const [tracks, setTracks] = useState(INITIAL_TRACKS);
            const [scale, setScale] = useState(1);
            const [offset, setOffset] = useState({ x: 0, y: 0 });
            const [isDragging, setIsDragging] = useState(false);
            const [lastPos, setLastPos] = useState({ x: 0, y: 0 });
            const [hoverInfo, setHoverInfo] = useState(null);
            const [canvasSize, setCanvasSize] = useState({ width: 800, height: 600 });
            const [showInterventions, setShowInterventions] = useState(true);
            const [forceAllInterventions, setForceAllInterventions] = useState(false);
            const [showTextureHeatmap, setShowTextureHeatmap] = useState(true);

            // 初始化視窗大小與自動最適視角
            useEffect(() => {
                const updateSize = () => {
                    if (containerRef.current) {
                        setCanvasSize({
                            width: containerRef.current.clientWidth,
                            height: containerRef.current.clientHeight
                        });
                    }
                };
                window.addEventListener('resize', updateSize);
                updateSize();

                // Initial fit
                const allPoints = INITIAL_TRACKS.filter(t => t.visible).flatMap(t => t.points);
                setTimeout(() => fitViewToPoints(allPoints), 100);

                return () => window.removeEventListener('resize', updateSize);
            }, []);

            const parseFile = (file, callback) => {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const content = e.target.result;
                    const lines = content.split('\\n');
                    const newPoints = [];
                    lines.forEach((line) => {
                        const parts = line.split(',').map(p => parseFloat(p.trim()));
                        if (parts.length >= 2 && !isNaN(parts[0]) && !isNaN(parts[1])) {
                            newPoints.push({ x: parts[0], y: parts[1] });
                        }
                    });
                    callback(newPoints, file.name);
                };
                reader.readAsText(file);
            };

            const handleUpload = (index, e) => {
                const file = e.target.files[0];
                if (!file) return;

                parseFile(file, (points, name) => {
                    setTracks(prev => {
                        const newTracks = [...prev];
                        newTracks[index] = { ...newTracks[index], points, fileName: name, visible: true };
                        const allVisiblePoints = newTracks.filter(t => t.visible).flatMap(t => t.points);
                        requestAnimationFrame(() => fitViewToPoints(allVisiblePoints));
                        return newTracks;
                    });
                });
            };

            const toggleVisibility = (index) => {
                setTracks(prev => {
                    const newTracks = [...prev];
                    newTracks[index] = { ...newTracks[index], visible: !newTracks[index].visible };
                    return newTracks;
                });
            };

            const fitViewToPoints = (allPoints) => {
                if (!allPoints || allPoints.length === 0) return;

                let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
                allPoints.forEach(p => {
                    minX = Math.min(minX, p.x);
                    maxX = Math.max(maxX, p.x);
                    minY = Math.min(minY, p.y);
                    maxY = Math.max(maxY, p.y);
                });

                const dataWidth = maxX - minX || 100;
                const dataHeight = maxY - minY || 100;
                const padding = 100;
                
                const currentW = containerRef.current ? containerRef.current.clientWidth : 800;
                const currentH = containerRef.current ? containerRef.current.clientHeight : 600;

                const scaleX = (currentW - padding * 2) / dataWidth;
                const scaleY = (currentH - padding * 2) / dataHeight;
                const newScale = Math.min(scaleX, scaleY, 5);
                
                const centerX = (minX + maxX) / 2;
                const centerY = (minY + maxY) / 2;
                
                const offsetX = (currentW / 2) - (centerX * newScale);
                const offsetY = (currentH / 2) - (centerY * newScale);

                setScale(newScale);
                setOffset({ x: offsetX, y: offsetY });
            };

            const drawGridAndRulers = (ctx, w, h) => {
                ctx.save();
                ctx.strokeStyle = '#e2e8f0';
                ctx.fillStyle = '#64748b';
                ctx.font = '11px monospace';
                ctx.lineWidth = 1;

                const minPixelSpacing = 70;
                const rawStep = minPixelSpacing / scale;
                const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
                const residual = rawStep / magnitude;
                let step = residual > 5 ? 10 * magnitude : residual > 2 ? 5 * magnitude : residual > 1 ? 2 * magnitude : magnitude;

                ctx.beginPath();
                const startWorldX = Math.floor((-offset.x / scale) / step) * step;
                const endWorldX = Math.ceil(((w - offset.x) / scale) / step) * step;
                const startWorldY = Math.floor((-offset.y / scale) / step) * step;
                const endWorldY = Math.ceil(((h - offset.y) / scale) / step) * step;

                for (let wx = startWorldX; wx <= endWorldX; wx += step) {
                    const sx = Math.floor(wx * scale + offset.x) + 0.5;
                    if (sx < 0 || sx > w) continue;
                    ctx.moveTo(sx, 0); ctx.lineTo(sx, h);
                    ctx.fillText(Number(wx.toFixed(1)).toString() + "px", sx + 6, 16);
                }
                for (let wy = startWorldY; wy <= endWorldY; wy += step) {
                    const sy = Math.floor(wy * scale + offset.y) + 0.5;
                    if (sy < 0 || sy > h) continue;
                    ctx.moveTo(0, sy); ctx.lineTo(w, sy);
                    ctx.fillText(Number(wy.toFixed(1)).toString() + "px", 6, sy - 6);
                }
                ctx.stroke();
                ctx.restore();
            };

            const drawTrack = (ctx, track) => {
                if (!track.visible || track.points.length === 0) return;

                ctx.save();
                ctx.strokeStyle = track.color;
                ctx.lineWidth = (track.isGth ? 2.5 : 1.8) / scale;
                if (track.isGth) {
                    ctx.setLineDash([4 / scale, 3 / scale]);
                } else {
                    ctx.setLineDash([]);
                }
                ctx.lineJoin = 'round';
                ctx.lineCap = 'round';

                ctx.beginPath();
                ctx.moveTo(track.points[0].x, track.points[0].y);
                for (let i = 1; i < track.points.length; i++) {
                    ctx.lineTo(track.points[i].x, track.points[i].y);
                }
                ctx.stroke();

                // 起點 (Circle)
                ctx.setLineDash([]);
                ctx.beginPath();
                ctx.fillStyle = track.color;
                ctx.arc(track.points[0].x, track.points[0].y, 4 / scale, 0, Math.PI * 2);
                ctx.fill();
                ctx.lineWidth = 1 / scale;
                ctx.strokeStyle = '#fff';
                ctx.stroke();

                // 終點 (Square)
                const last = track.points[track.points.length - 1];
                const size = 7 / scale;
                ctx.fillStyle = track.color;
                ctx.fillRect(last.x - size/2, last.y - size/2, size, size);
                ctx.strokeRect(last.x - size/2, last.y - size/2, size, size);

                // Teacher Interventions (Red cross markers)
                if (track.id === 'C' && showInterventions) {
                    ctx.fillStyle = '#dc2626';
                    ctx.strokeStyle = '#dc2626';
                    ctx.lineWidth = 1.2 / scale;
                    // 當放大到 scale >= 6 或使用者開啟 forceAllInterventions 時，逐點 100% 繪製紅叉！
                    const step = (scale >= 6 || forceAllInterventions) ? 1 : Math.max(2, Math.floor(25 / scale));
                    for (let i = 0; i < track.points.length; i += step) {
                        if (track.points[i].int) {
                            const px = track.points[i].x;
                            const py = track.points[i].y;
                            const sz = Math.max(2.0 / scale, 0.04);
                            ctx.beginPath();
                            ctx.moveTo(px - sz, py - sz); ctx.lineTo(px + sz, py + sz);
                            ctx.moveTo(px + sz, py - sz); ctx.lineTo(px - sz, py + sz);
                            ctx.stroke();
                        }
                    }
                }

                ctx.restore();
            };

            const draw = useCallback(() => {
                const canvas = canvasRef.current;
                if (!canvas) return;
                const ctx = canvas.getContext('2d');
                
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                drawGridAndRulers(ctx, canvas.width, canvas.height);

                ctx.save();
                ctx.translate(offset.x, offset.y);
                ctx.scale(scale, scale);

                // 坐標原點 (0, 0)
                ctx.beginPath();
                ctx.strokeStyle = '#94a3b8';
                ctx.lineWidth = 1.5 / scale;
                ctx.moveTo(-15 / scale, 0); ctx.lineTo(15 / scale, 0);
                ctx.moveTo(0, -15 / scale); ctx.lineTo(0, 15 / scale);
                ctx.stroke();

                // 若開啟 T 區熱區高亮，在底層繪製微塵區域弧帶
                if (showTextureHeatmap && PRELOADED_TRACKS_DATA.gth) {
                    ctx.save();
                    ctx.strokeStyle = 'rgba(34, 197, 94, 0.35)';
                    ctx.lineWidth = 16 / scale;
                    ctx.lineCap = 'round';
                    ctx.beginPath();
                    let inT = false;
                    PRELOADED_TRACKS_DATA.gth.forEach((pt, i) => {
                        if (pt.is_t) {
                            if (!inT) { ctx.moveTo(pt.x, pt.y); inT = true; }
                            else { ctx.lineTo(pt.x, pt.y); }
                        } else {
                            inT = false;
                        }
                    });
                    ctx.stroke();
                    ctx.restore();
                }

                tracks.forEach(track => {
                    drawTrack(ctx, track);
                });

                if (hoverInfo) {
                    ctx.beginPath();
                    ctx.fillStyle = '#f59e0b';
                    ctx.arc(hoverInfo.point.x, hoverInfo.point.y, 6 / scale, 0, Math.PI * 2);
                    ctx.fill();
                    ctx.lineWidth = 2 / scale;
                    ctx.strokeStyle = '#fff';
                    ctx.stroke();
                }
                ctx.restore();

                // UI 圖例浮水印
                ctx.save();
                ctx.font = 'bold 13px sans-serif';
                ctx.shadowColor = 'rgba(255, 255, 255, 0.9)';
                ctx.shadowBlur = 4;
                
                let legendY = 25;
                tracks.forEach(track => {
                    if (track.visible && track.fileName) {
                        ctx.fillStyle = track.color;
                        ctx.fillText(`${track.id}: ${track.fileName} (${track.points.length} 點)`, 16, legendY);
                        legendY += 20;
                    }
                });
                ctx.restore();

            }, [tracks, offset, scale, hoverInfo, canvasSize, showInterventions, forceAllInterventions, showTextureHeatmap]);

            useEffect(() => {
                requestAnimationFrame(draw);
            }, [draw]);

            const handleMouseDown = (e) => { setIsDragging(true); setLastPos({ x: e.clientX, y: e.clientY }); };
            const handleMouseUp = () => setIsDragging(false);
            const handleMouseMove = (e) => {
                if (isDragging) {
                    const dx = e.clientX - lastPos.x;
                    const dy = e.clientY - lastPos.y;
                    setOffset(p => ({ x: p.x + dx, y: p.y + dy }));
                    setLastPos({ x: e.clientX, y: e.clientY });
                } else {
                    const hasPoints = tracks.some(t => t.points.length > 0);
                    if (!hasPoints) return;

                    const rect = canvasRef.current.getBoundingClientRect();
                    const mouseX = e.clientX - rect.left;
                    const mouseY = e.clientY - rect.top;
                    const worldX = (mouseX - offset.x) / scale;
                    const worldY = (mouseY - offset.y) / scale;

                    let minDist = Infinity;
                    let nearest = null;
                    const captureRadius = 25 / scale;

                    tracks.forEach(track => {
                        if (!track.visible) return;
                        track.points.forEach((p, i) => {
                            const dist = Math.sqrt((p.x - worldX)**2 + (p.y - worldY)**2);
                            if (dist < minDist && dist < captureRadius) {
                                minDist = dist;
                                nearest = { point: p, index: i, source: track.id, color: track.color, label: track.label };
                            }
                        });
                    });

                    if (nearest) setHoverInfo({ ...nearest, screenX: mouseX, screenY: mouseY });
                    else setHoverInfo(null);
                }
            };

            const handleWheel = (e) => {
                e.preventDefault();
                const factor = 1 + (e.deltaY < 0 ? 0.15 : -0.15);
                const newScale = Math.max(0.02, Math.min(scale * factor, 5000));
                const rect = canvasRef.current.getBoundingClientRect();
                const mouseX = e.clientX - rect.left;
                const mouseY = e.clientY - rect.top;
                setOffset({
                    x: mouseX - (mouseX - offset.x) * (newScale / scale),
                    y: mouseY - (mouseY - offset.y) * (newScale / scale)
                });
                setScale(newScale);
            };

            const zoomIn = () => setScale(s => Math.min(s * 1.4, 5000));
            const zoomOut = () => setScale(s => Math.max(s / 1.4, 0.02));
            const resetView = () => {
                const allVisiblePoints = tracks.filter(t => t.visible).flatMap(t => t.points);
                fitViewToPoints(allVisiblePoints);
            };

            const saveImage = () => {
                if (!canvasRef.current) return;
                const link = document.createElement('a');
                link.download = `ToG-Tracks-Comparison.png`;
                link.href = canvasRef.current.toDataURL("image/png");
                link.click();
            };

            return (
                <div className="flex flex-col h-screen font-sans">
                    {/* Header Controls */}
                    <div className="bg-white shadow-sm p-4 border-b border-gray-200 z-10 flex flex-col gap-3">
                        <div className="flex flex-wrap items-center justify-between gap-4">
                            <div className="flex items-center gap-3">
                                <h1 className="text-lg font-bold text-gray-800 flex items-center">
                                    <span className="mr-2 text-indigo-600">{Icons.Layers}</span> ToG 光學滑鼠四軌跡互動比較工具 (Step 3)
                                </h1>
                                <span className="text-xs bg-indigo-50 text-indigo-700 px-2 py-1 rounded font-medium border border-indigo-200">
                                    已預載 POS1-1.raw 5,373 幀完整數據
                                </span>
                            </div>
                            <div className="flex flex-wrap items-center gap-2">
                                <label className="text-xs flex items-center gap-1 text-gray-700 bg-gray-100 hover:bg-gray-200 px-2 py-1.5 rounded cursor-pointer border">
                                    <input type="checkbox" checked={showInterventions} onChange={e => setShowInterventions(e.target.checked)} className="rounded" />
                                    <span>Teacher 介入標記 (紅叉)</span>
                                </label>
                                <label className="text-xs flex items-center gap-1 text-gray-700 bg-gray-100 hover:bg-gray-200 px-2 py-1.5 rounded cursor-pointer border" title="縮小時預設抽樣以防卡頓，勾選後100%全數繪製">
                                    <input type="checkbox" checked={forceAllInterventions} onChange={e => setForceAllInterventions(e.target.checked)} className="rounded" />
                                    <span>強制顯示 100% 紅叉</span>
                                </label>
                                <label className="text-xs flex items-center gap-1 text-gray-700 bg-emerald-50 hover:bg-emerald-100 px-2 py-1.5 rounded cursor-pointer border border-emerald-300" title="在軌跡底層以綠色弧帶標示有微塵錨點的T區">
                                    <input type="checkbox" checked={showTextureHeatmap} onChange={e => setShowTextureHeatmap(e.target.checked)} className="rounded text-emerald-600" />
                                    <span className="text-emerald-800 font-medium">T區 (微塵帶) 標示</span>
                                </label>
                                <div className="text-xs bg-gray-800 text-white font-mono px-2 py-1.5 rounded">
                                    縮放: {scale.toFixed(1)}x
                                </div>
                                <button onClick={zoomOut} className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-700" title="Zoom Out">{Icons.ZoomOut}</button>
                                <button onClick={zoomIn} className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-700" title="Zoom In">{Icons.ZoomIn}</button>
                                <button onClick={resetView} className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-700" title="全視角對齊">{Icons.RefreshCw}</button>
                                <button onClick={saveImage} className="p-2 bg-emerald-600 text-white hover:bg-emerald-700 rounded flex items-center font-bold text-sm" title="儲存高解析截圖">
                                    <span className="mr-1">{Icons.Download}</span> 存圖
                                </button>
                            </div>
                        </div>

                        {/* 4 Track Cards */}
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
                            {tracks.map((track, idx) => (
                                <div key={track.id} className="flex items-center gap-2 p-2.5 rounded-lg border transition shadow-sm" style={{ borderColor: track.color + '50', backgroundColor: track.color + '0d' }}>
                                    <div className="w-3.5 h-3.5 rounded-full flex-shrink-0" style={{backgroundColor: track.color}}></div>
                                    <label className="flex-grow cursor-pointer flex flex-col min-w-0">
                                        <span className="text-xs font-bold truncate" style={{color: track.color}}>{track.label}</span>
                                        <div className="flex items-center gap-1 text-xs text-gray-600 truncate hover:text-gray-900 mt-0.5">
                                            {Icons.Upload}
                                            <span className="truncate">{track.fileName || "更換檔案..."}</span>
                                        </div>
                                        <input type="file" accept=".txt,.csv" onChange={(e) => handleUpload(idx, e)} className="hidden" />
                                    </label>
                                    <button onClick={() => toggleVisibility(idx)} className="p-1.5 rounded-md hover:bg-white/80 text-gray-600 transition" title={track.visible ? "隱藏此軌跡" : "顯示此軌跡"}>
                                        {track.visible ? Icons.Eye : Icons.EyeOff}
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Canvas Area */}
                    <div className="flex-grow relative overflow-hidden bg-slate-50 cursor-crosshair canvas-container" ref={containerRef}>
                        <canvas
                            ref={canvasRef}
                            width={canvasSize.width}
                            height={canvasSize.height}
                            className="block touch-none"
                            onMouseDown={handleMouseDown}
                            onMouseMove={handleMouseMove}
                            onMouseUp={handleMouseUp}
                            onMouseLeave={handleMouseUp}
                            onWheel={handleWheel}
                        />

                        {/* Hover Tooltip */}
                        {hoverInfo && (
                            <div className="absolute pointer-events-none bg-gray-900/95 text-white text-xs p-3 rounded-lg shadow-xl z-20 backdrop-blur-md border-l-4 leading-relaxed min-w-[210px]"
                                style={{ left: Math.min(hoverInfo.screenX + 15, canvasSize.width - 240), top: Math.min(hoverInfo.screenY + 15, canvasSize.height - 180), borderColor: hoverInfo.color }}>
                                <div className="font-bold border-b border-gray-700 pb-1 mb-1 flex items-center justify-between" style={{color: hoverInfo.color}}>
                                    <span>{hoverInfo.label}</span>
                                    <span className="text-gray-400 font-mono text-[10px]">#{hoverInfo.index}</span>
                                </div>
                                <div>X 坐標: <span className="font-mono">{hoverInfo.point.x.toFixed(3)} px</span></div>
                                <div>Y 坐標: <span className="font-mono">{hoverInfo.point.y.toFixed(3)} px</span></div>
                                {hoverInfo.point.err !== undefined && (
                                    <div className="text-amber-300 font-medium">與真值誤差: <span className="font-bold font-mono">{hoverInfo.point.err.toFixed(3)} px</span></div>
                                )}
                                <div className="mt-1 pt-1 border-t border-gray-800">
                                    表面區域: <span className={`font-bold px-1.5 py-0.5 rounded text-[10px] ${hoverInfo.point.is_t ? 'bg-emerald-800 text-emerald-200' : 'bg-gray-800 text-gray-300'}`}>
                                        {hoverInfo.point.is_t ? '【T區 - 有微塵】' : '【TL區 - 空白玻璃】'}
                                    </span>
                                </div>
                                {hoverInfo.point.squal !== undefined && (
                                    <div className="text-gray-300">紋理強度 (SQUAL): <span className="font-mono">{hoverInfo.point.squal}</span></div>
                                )}
                                {hoverInfo.point.int !== undefined && (
                                    <div className="mt-1 font-bold">
                                        Teacher 狀態: <span className={hoverInfo.point.int ? 'text-red-400 font-bold' : 'text-emerald-400'}>
                                            {hoverInfo.point.int ? '★ 脫靶介入校正' : '✓ 窗口內自主追蹤'}
                                        </span>
                                    </div>
                                )}
                            </div>
                        )}
                        
                        {/* Status bar */}
                        <div className="absolute bottom-4 left-4 bg-white/90 backdrop-blur px-3 py-2 rounded-lg shadow border border-gray-200 text-xs text-gray-700 pointer-events-none flex gap-4">
                            <div><span className="font-bold">縮放比率:</span> {(scale * 100).toFixed(0)}% (滾輪任意縮放)</div>
                            <div><span className="font-bold">圓點:</span> 起點 | <span className="font-bold">方點:</span> 終點</div>
                            <div><span className="font-bold">提示:</span> 點擊上方眼睛圖示可單獨開啟/關閉軌跡，進行微觀比對</div>
                        </div>
                    </div>
                </div>
            );
        };

        const root = ReactDOM.createRoot(document.getElementById('root'));
        root.render(<MouseTrackComparator />);
    </script>
</body>
</html>
"""

    # Inject JSON data into template
    data_json_str = json.dumps(tracks)
    full_html = html_template.replace('__PRELOADED_DATA_PLACEHOLDER__', data_json_str)

    html_out_path = os.path.join(out_dir, "mouse_track_comparator.html")
    with open(html_out_path, 'w', encoding='utf-8') as f:
        f.write(full_html)
    print(f"Generated Interactive HTML tool: {html_out_path}")

    # Also place a copy on user's Desktop for ultra-fast access
    desktop_html = r"C:\Users\Shouder Wei\Desktop\滑鼠軌跡-Step3-ToG比較.html"
    try:
        with open(desktop_html, 'w', encoding='utf-8') as f:
            f.write(full_html)
        print(f"Placed Desktop copy at: {desktop_html}")
    except Exception as e:
        print(f"Notice: Could not write to desktop: {e}")

if __name__ == '__main__':
    run_export()
