import os
import sys
import json
import numpy as np

sys.path.append(r'C:\PROJECT\ToG_Tracker')
from step1_anchor_and_textureless_analyzer import OpniRawIO
from tracker_core import NormalModeTracker, TeacherModeTracker, IdealModeTracker

def run_export(raw_path=None, gth_csv_path=None, fpn_path=None, out_dir=None, step1_dir=None, exp_tag=None, use_corner_filter=None):
    if raw_path is None:
        raw_path = r'C:\Data\testPattern玻璃\Circle_R0.2inch_IPS0.02_Glass\RAW\POS1-1.raw'
    if gth_csv_path is None:
        gth_csv_path = r'C:\PROJECT\ToG_Tracker\step2_output\POS1-1_ground_truth.csv'
    if fpn_path is None:
        fpn_path = r'C:\PROJECT\ToG_Tracker\step1_output\fpn_model.raw'
    if out_dir is None:
        out_dir = r'C:\PROJECT\ToG_Tracker\step3_output'
    if step1_dir is None:
        step1_dir = os.path.dirname(fpn_path)
    os.makedirs(out_dir, exist_ok=True)

    raw_name = os.path.basename(raw_path)
    if exp_tag is None:
        exp_tag = raw_name

    if use_corner_filter is None:
        # 對布質/有紋理滑鼠墊自動啟用 OptNavi Corner 紋理對齊過濾器
        use_corner_filter = 'mousepad' in raw_path.lower() or 'mousepad' in exp_tag.lower()
    
    if use_corner_filter:
        print("[OptNavi] 啟用 4-Corner 角度紋理對齊濾波器 (打破經緯編織 repeat pattern)")

    print(f"Loading Ground Truth and Raw Data for [{exp_tag}]...")
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
    total_frames = min(num_frames, raw_hdr['num_frames'])
    gth_x = gth_x[:total_frames]
    gth_y = gth_y[:total_frames]
    gth_dx = gth_dx[:total_frames]
    gth_dy = gth_dy[:total_frames]
    num_frames = total_frames

    frames = []
    with open(raw_path, 'rb') as f:
        for fi in range(num_frames):
            frm = OpniRawIO.load_frame(f, raw_hdr, fi)
            if frm is None:
                num_frames = fi
                break
            frames.append(frm)

    gth_x = gth_x[:num_frames]
    gth_y = gth_y[:num_frames]
    gth_dx = gth_dx[:num_frames]
    gth_dy = gth_dy[:num_frames]

    # Step 1 Anchor Clusters for T/TL zone identification
    step1_json = os.path.join(step1_dir, "step1_report.json")
    if not os.path.exists(step1_json):
        step1_json = r'C:\PROJECT\ToG_Tracker\step1_output\step1_report.json'
    
    is_anchor = np.zeros(num_frames, dtype=bool)
    if os.path.exists(step1_json):
        with open(step1_json, 'r') as f:
            s1 = json.load(f)
        anchor_clusters = s1.get('anchor_clusters', [])
        for c in anchor_clusters:
            start = max(0, min(num_frames - 1, c['start']))
            end = max(0, min(num_frames - 1, c['end']))
            is_anchor[start: end + 1] = True

    tracks = {}
    
    # 1. Ground truth track points
    tracks['gth'] = [{
        'x': round(float(gth_x[i]), 3), 
        'y': round(float(gth_y[i]), 3),
        'dx': round(float(gth_dx[i]), 3),
        'dy': round(float(gth_dy[i]), 3),
        'is_t': bool(is_anchor[i]),
        'squal': 0.0
    } for i in range(num_frames)]

    # 2. Simulate the 3 modes using tracker_core
    print("Simulating Mode: NORMAL...")
    tracker_norm = NormalModeTracker(search_radius=1, subpixel_scale=16, 
                                     keyframe_dist=1.5, squal_th=5.0, v_alpha=0.25,
                                     fpn_model=fpn_model, use_corner_filter=use_corner_filter)
    norm_pts = []
    for fi in range(num_frames):
        res = tracker_norm.step(frames[fi], fi)
        pos_err_x = abs(round(res['pred_x']) - gth_x[fi])
        pos_err_y = abs(round(res['pred_y']) - gth_y[fi])
        in_basin = bool(pos_err_x <= 1.0 and pos_err_y <= 1.0)
        norm_pts.append({
            'x': round(float(res['est_x']), 3),
            'y': round(float(res['est_y']), 3),
            'dx': round(float(res['est_dx']), 3),
            'dy': round(float(res['est_dy']), 3),
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
                                       fpn_model=fpn_model, use_corner_filter=use_corner_filter)
    teach_pts = []
    for fi in range(num_frames):
        g_pos = (gth_x[fi], gth_y[fi])
        g_vel = (gth_dx[fi], gth_dy[fi])
        res = tracker_teach.step(frames[fi], fi, g_pos, g_vel)
        teach_pts.append({
            'x': round(float(res['est_x']), 3),
            'y': round(float(res['est_y']), 3),
            'dx': round(float(res['est_dx']), 3),
            'dy': round(float(res['est_dy']), 3),
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
                                     fpn_model=fpn_model, use_corner_filter=use_corner_filter)
    ideal_pts = []
    for fi in range(num_frames):
        g_pos = (gth_x[fi], gth_y[fi])
        g_vel = (gth_dx[fi], gth_dy[fi])
        res = tracker_ideal.step(frames[fi], fi, g_pos, g_vel)
        ideal_pts.append({
            'x': round(float(res['est_x']), 3),
            'y': round(float(res['est_y']), 3),
            'dx': round(float(res['est_dx']), 3),
            'dy': round(float(res['est_dy']), 3),
            'err': round(float(np.sqrt((res['est_x'] - gth_x[fi])**2 + (res['est_y'] - gth_y[fi])**2)), 3),
            'in_basin': True,
            'int': False,
            'is_t': bool(is_anchor[fi]),
            'squal': round(float(res['squal']), 1)
        })
    tracks['ideal'] = ideal_pts

    # 3. Export separate TXT files (包含單幀相對位移 dx, dy 與累積位置 x, y)
    txt_gth = os.path.join(out_dir, 'Track_A_GroundTruth.txt')
    with open(txt_gth, 'w', encoding='utf-8') as f:
        f.write("#frame,dx,dy,x,y,zone\n")
        for i, p in enumerate(tracks['gth']):
            f.write(f"{i},{p['dx']:.3f},{p['dy']:.3f},{p['x']:.3f},{p['y']:.3f},{'T' if p['is_t'] else 'TL'}\n")

    txt_norm = os.path.join(out_dir, 'Track_B_NormalMode.txt')
    with open(txt_norm, 'w', encoding='utf-8') as f:
        f.write("#frame,dx,dy,x,y,err,zone,squal\n")
        for i, p in enumerate(tracks['normal']):
            f.write(f"{i},{p['dx']:.3f},{p['dy']:.3f},{p['x']:.3f},{p['y']:.3f},{p['err']:.3f},{'T' if p['is_t'] else 'TL'},{p['squal']:.1f}\n")

    txt_teach = os.path.join(out_dir, 'Track_C_TeacherMode.txt')
    with open(txt_teach, 'w', encoding='utf-8') as f:
        f.write("#frame,dx,dy,x,y,err,intervened,zone,squal\n")
        for i, p in enumerate(tracks['teacher']):
            f.write(f"{i},{p['dx']:.3f},{p['dy']:.3f},{p['x']:.3f},{p['y']:.3f},{p['err']:.3f},{1 if p['int'] else 0},{'T' if p['is_t'] else 'TL'},{p['squal']:.1f}\n")

    txt_ideal = os.path.join(out_dir, 'Track_D_IdealMode.txt')
    with open(txt_ideal, 'w', encoding='utf-8') as f:
        f.write("#frame,dx,dy,x,y,err,zone,squal\n")
        for i, p in enumerate(tracks['ideal']):
            f.write(f"{i},{p['dx']:.3f},{p['dy']:.3f},{p['x']:.3f},{p['y']:.3f},{p['err']:.3f},{'T' if p['is_t'] else 'TL'},{p['squal']:.1f}\n")

    print(f"Exported all 4 TXT files to: {out_dir}")

    # 4. Generate Preloaded HTML Viewer
    html_template = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ToG 光學滑鼠軌跡多模式比較檢視器 - __EXP_TAG_PLACEHOLDER__</title>
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
        const EXP_INFO = "__EXP_TAG_PLACEHOLDER__";
        const TOTAL_FRAMES_STR = "__TOTAL_FRAMES_PLACEHOLDER__";

        const Icon = ({ path, color = "currentColor", size = 20 }) => (
            <svg xmlns="http://www.w3.org/2000/svg" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                {path}
            </svg>
        );

        const Icons = {
            Layers: <Icon path={<><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 17 12 22 22 17"/><polyline points="2 12 12 17 22 12"/></>} />,
            ZoomIn: <Icon path={<><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></>} />,
            ZoomOut: <Icon path={<><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/><line x1="11" y1="8" x2="11" y2="14"/><line x1="8" y1="11" x2="14" y2="11"/></>} />,
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
                { id: 'A', color: '#0f172a', points: PRELOADED_TRACKS_DATA.gth, fileName: 'Track A - Ground Truth', visible: true, label: '軌跡 A - Ground Truth (黑)', isGth: true },
                { id: 'B', color: '#2563eb', points: PRELOADED_TRACKS_DATA.normal, fileName: 'Track B - Normal Mode', visible: true, label: '軌跡 B - Normal Mode (藍)' },
                { id: 'C', color: '#ea580c', points: PRELOADED_TRACKS_DATA.teacher, fileName: 'Track C - Teacher Mode', visible: true, label: '軌跡 C - Teacher Mode (橘)' },
                { id: 'D', color: '#16a34a', points: PRELOADED_TRACKS_DATA.ideal, fileName: 'Track D - Ideal Mode', visible: true, label: '軌跡 D - Ideal Mode (綠)' },
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

            // 響應式畫布大小
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

                const allPoints = INITIAL_TRACKS.filter(t => t.visible).flatMap(t => t.points);
                setTimeout(() => fitViewToPoints(allPoints), 100);

                return () => window.removeEventListener('resize', updateSize);
            }, []);

            // 智能檔案解析 (支援純座標、含表頭、多欄診斷 TXT)
            const parseFile = (file, callback) => {
                const reader = new FileReader();
                reader.onload = (e) => {
                    const content = e.target.result;
                    const lines = content.split(/\\r?\\n/).map(l => l.trim()).filter(l => l.length > 0);
                    if (lines.length === 0) return callback([], file.name);

                    let colX = -1, colY = -1, colDx = -1, colDy = -1, colErr = -1, colInt = -1, colZone = -1, colSqual = -1;
                    let startLine = 0;

                    // 檢查第一行是否為表頭 (Header)
                    const firstLine = lines[0].toLowerCase();
                    if (firstLine.startsWith('#') || firstLine.includes('x') || firstLine.includes('frame')) {
                        startLine = 1;
                        const headerCols = firstLine.replace(/^#/, '').split(',').map(c => c.trim());
                        headerCols.forEach((col, idx) => {
                            if (col === 'x') colX = idx;
                            else if (col === 'y') colY = idx;
                            else if (col === 'dx') colDx = idx;
                            else if (col === 'dy') colDy = idx;
                            else if (col === 'err') colErr = idx;
                            else if (col === 'intervened' || col === 'int') colInt = idx;
                            else if (col === 'zone') colZone = idx;
                            else if (col === 'squal') colSqual = idx;
                        });
                    }

                    // 自適應猜測欄位
                    if (colX === -1 || colY === -1) {
                        const sampleParts = lines[startLine].split(',').map(p => p.trim());
                        if (sampleParts.length >= 5 && !isNaN(parseFloat(sampleParts[3])) && !isNaN(parseFloat(sampleParts[4]))) {
                            // 格式: frame, dx, dy, x, y, ...
                            colDx = 1;
                            colDy = 2;
                            colX = 3;
                            colY = 4;
                            if (sampleParts.length >= 6) colErr = 5;
                        } else if (sampleParts.length >= 3) {
                            // 若有 3 欄以上，檢查第 0 欄是否為幀號 (整數)
                            const p0 = parseFloat(sampleParts[0]);
                            if (Number.isInteger(p0) && p0 >= 0) {
                                colX = 1; // 第 1 欄為 X
                                colY = 2; // 第 2 欄為 Y
                                if (sampleParts.length >= 4) colErr = 3;
                            } else {
                                colX = 0;
                                colY = 1;
                            }
                        } else if (sampleParts.length >= 2) {
                            colX = 0;
                            colY = 1;
                        }
                    }

                    const newPoints = [];
                    for (let i = startLine; i < lines.length; i++) {
                        const parts = lines[i].split(',').map(p => p.trim());
                        if (parts.length > Math.max(colX, colY)) {
                            const x = parseFloat(parts[colX]);
                            const y = parseFloat(parts[colY]);
                            if (!isNaN(x) && !isNaN(y)) {
                                const pt = { x, y };
                                if (colDx >= 0 && colDx < parts.length && !isNaN(parseFloat(parts[colDx]))) {
                                    pt.dx = parseFloat(parts[colDx]);
                                }
                                if (colDy >= 0 && colDy < parts.length && !isNaN(parseFloat(parts[colDy]))) {
                                    pt.dy = parseFloat(parts[colDy]);
                                }
                                if (colErr >= 0 && colErr < parts.length && !isNaN(parseFloat(parts[colErr]))) {
                                    pt.err = parseFloat(parts[colErr]);
                                }
                                if (colInt >= 0 && colInt < parts.length) {
                                    pt.int = (parts[colInt] === '1' || parts[colInt].toLowerCase() === 'true');
                                }
                                if (colZone >= 0 && colZone < parts.length) {
                                    pt.is_t = (parts[colZone].toUpperCase() === 'T');
                                }
                                if (colSqual >= 0 && colSqual < parts.length && !isNaN(parseFloat(parts[colSqual]))) {
                                    pt.squal = parseFloat(parts[colSqual]);
                                }
                                newPoints.push(pt);
                            }
                        }
                    }
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

                const padding = 60;
                const availableWidth = canvasSize.width - padding * 2;
                const availableHeight = canvasSize.height - padding * 2;

                const rangeX = maxX - minX || 1;
                const rangeY = maxY - minY || 1;

                const scaleX = availableWidth / rangeX;
                const scaleY = availableHeight / rangeY;
                const newScale = Math.min(scaleX, scaleY);

                const centerX = (minX + maxX) / 2;
                const centerY = (minY + maxY) / 2;

                setScale(newScale);
                setOffset({
                    x: canvasSize.width / 2 - centerX * newScale,
                    y: canvasSize.height / 2 - centerY * newScale
                });
            };

            // 繪圖核心
            const draw = useCallback(() => {
                const canvas = canvasRef.current;
                if (!canvas) return;
                const ctx = canvas.getContext('2d');
                ctx.clearRect(0, 0, canvas.width, canvas.height);

                // 畫刻度與格線 (自適應動態世界座標刻度尺)
                ctx.save();
                ctx.strokeStyle = '#e2e8f0';
                ctx.fillStyle = '#64748b';
                ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
                ctx.lineWidth = 1;

                const minPixelSpacing = 60;
                const rawStep = minPixelSpacing / scale;
                const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
                const residual = rawStep / magnitude;
                let step = residual > 5 ? 10 * magnitude : residual > 2 ? 5 * magnitude : 2 * magnitude;
                if (step <= 0) step = 1;

                ctx.beginPath();
                const startWorldX = Math.floor((-offset.x / scale) / step) * step;
                const endWorldX = Math.ceil(((canvas.width - offset.x) / scale) / step) * step;
                const startWorldY = Math.floor((-offset.y / scale) / step) * step;
                const endWorldY = Math.ceil(((canvas.height - offset.y) / scale) / step) * step;

                // 繪製垂直網格與 X 刻度數值
                for (let wx = startWorldX; wx <= endWorldX; wx += step) {
                    const sx = Math.floor(wx * scale + offset.x) + 0.5;
                    if (sx < 0 || sx > canvas.width) continue;
                    ctx.moveTo(sx, 0); ctx.lineTo(sx, canvas.height);
                    const label = Number(wx.toFixed(2)).toString();
                    ctx.fillText(label, sx + 4, 14);
                }
                // 繪製水平網格與 Y 刻度數值
                for (let wy = startWorldY; wy <= endWorldY; wy += step) {
                    const sy = Math.floor(wy * scale + offset.y) + 0.5;
                    if (sy < 0 || sy > canvas.height) continue;
                    ctx.moveTo(0, sy); ctx.lineTo(canvas.width, sy);
                    const label = Number(wy.toFixed(2)).toString();
                    ctx.fillText(label, 6, sy - 4);
                }
                ctx.stroke();

                // 畫原點 (0, 0) 主十字軸
                ctx.strokeStyle = '#94a3b8';
                ctx.lineWidth = 1.5;
                ctx.beginPath();
                ctx.moveTo(offset.x, 0); ctx.lineTo(offset.x, canvas.height);
                ctx.moveTo(0, offset.y); ctx.lineTo(canvas.width, offset.y);
                ctx.stroke();
                ctx.restore();

                // 繪製 T 區 (微塵帶) 綠色光帶
                if (showTextureHeatmap) {
                    const gthTrack = tracks.find(t => t.id === 'A');
                    if (gthTrack && gthTrack.points.length > 0) {
                        ctx.save();
                        ctx.lineWidth = Math.max(12, 10 * scale);
                        ctx.strokeStyle = 'rgba(16, 185, 129, 0.28)'; // Emerald-500 透明光環
                        ctx.lineCap = 'round';
                        ctx.lineJoin = 'round';

                        let inTZone = false;
                        ctx.beginPath();
                        gthTrack.points.forEach((p, idx) => {
                            const scrX = p.x * scale + offset.x;
                            const scrY = p.y * scale + offset.y;
                            if (p.is_t) {
                                if (!inTZone) {
                                    ctx.moveTo(scrX, scrY);
                                    inTZone = true;
                                } else {
                                    ctx.lineTo(scrX, scrY);
                                }
                            } else {
                                inTZone = false;
                            }
                        });
                        ctx.stroke();
                        ctx.restore();
                    }
                }

                // 繪製軌跡
                tracks.forEach((track) => {
                    if (!track.visible || track.points.length === 0) return;

                    ctx.save();
                    ctx.strokeStyle = track.color;
                    ctx.fillStyle = track.color;
                    ctx.lineWidth = track.isGth ? 2.5 : 1.8;
                    ctx.lineJoin = 'round';
                    ctx.lineCap = 'round';

                    // 1. 線條
                    ctx.beginPath();
                    track.points.forEach((point, i) => {
                        const scrX = point.x * scale + offset.x;
                        const scrY = point.y * scale + offset.y;
                        if (i === 0) ctx.moveTo(scrX, scrY);
                        else ctx.lineTo(scrX, scrY);
                    });
                    ctx.stroke();

                    // 2. 起點與終點標記
                    const pStart = track.points[0];
                    const pEnd = track.points[track.points.length - 1];
                    const sStartX = pStart.x * scale + offset.x;
                    const sStartY = pStart.y * scale + offset.y;
                    const sEndX = pEnd.x * scale + offset.x;
                    const sEndY = pEnd.y * scale + offset.y;

                    // 起點 (實心圓)
                    ctx.beginPath();
                    ctx.arc(sStartX, sStartY, 6, 0, Math.PI * 2);
                    ctx.fillStyle = '#10b981';
                    ctx.fill();
                    ctx.strokeStyle = '#ffffff';
                    ctx.lineWidth = 2;
                    ctx.stroke();

                    // 終點 (方塊)
                    ctx.fillStyle = '#ef4444';
                    ctx.fillRect(sEndX - 5, sEndY - 5, 10, 10);
                    ctx.strokeStyle = '#ffffff';
                    ctx.lineWidth = 2;
                    ctx.strokeRect(sEndX - 5, sEndY - 5, 10, 10);

                    // 3. 次像素點標記 (高倍率)
                    if (scale > 10) {
                        track.points.forEach(p => {
                            const sx = p.x * scale + offset.x;
                            const sy = p.y * scale + offset.y;
                            ctx.beginPath();
                            ctx.arc(sx, sy, Math.min(3, 0.4 * scale), 0, Math.PI * 2);
                            ctx.fillStyle = track.color;
                            ctx.fill();
                        });
                    }

                    // 4. Teacher 介入點標記 (紅叉)
                    if (track.id === 'C' && showInterventions) {
                        ctx.strokeStyle = '#dc2626';
                        ctx.lineWidth = 2;
                        
                        // 動態自適應步長：放大時 100% 逐點繪製
                        const step = forceAllInterventions ? 1 : (scale >= 6 ? 1 : (scale > 2 ? 3 : 15));
                        
                        track.points.forEach((point, i) => {
                            if (point.int && i % step === 0) {
                                const px = point.x * scale + offset.x;
                                const py = point.y * scale + offset.y;
                                const r = Math.max(3, Math.min(6, 1.5 * scale));
                                ctx.beginPath();
                                ctx.moveTo(px - r, py - r); ctx.lineTo(px + r, py + r);
                                ctx.moveTo(px + r, py - r); ctx.lineTo(px - r, py + r);
                                ctx.stroke();
                            }
                        });
                    }

                    ctx.restore();
                });

                // 繪製懸停點
                if (hoverInfo) {
                    ctx.save();
                    const scrX = hoverInfo.point.x * scale + offset.x;
                    const scrY = hoverInfo.point.y * scale + offset.y;
                    ctx.beginPath();
                    ctx.arc(scrX, scrY, 7, 0, Math.PI * 2);
                    ctx.fillStyle = '#ffffff';
                    ctx.fill();
                    ctx.lineWidth = 3;
                    ctx.strokeStyle = hoverInfo.color;
                    ctx.stroke();
                    ctx.restore();
                }

                // 圖例
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
                link.download = `ToG-Tracks-${EXP_INFO.replace(/[^a-zA-Z0-9_-]/g, '_')}.png`;
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
                                    <span className="mr-2 text-indigo-600">{Icons.Layers}</span> ToG 光學滑鼠軌跡多模式比較檢視器
                                </h1>
                                <span className="text-xs bg-indigo-50 text-indigo-700 px-2 py-1 rounded font-medium border border-indigo-200">
                                    實驗序列: {EXP_INFO} ({TOTAL_FRAMES_STR} 幀)
                                </span>
                            </div>
                            <div className="flex flex-wrap items-center gap-2">
                                <label className="text-xs flex items-center gap-1 text-gray-700 bg-gray-100 hover:bg-gray-200 px-2 py-1.5 rounded cursor-pointer border">
                                    <input type="checkbox" checked={showInterventions} onChange={e => setShowInterventions(e.target.checked)} className="rounded" />
                                    <span>Teacher 介入標記 (紅叉)</span>
                                </label>
                                <label className="text-xs flex items-center gap-1 text-gray-700 bg-gray-100 hover:bg-gray-200 px-2 py-1.5 rounded cursor-pointer border" title="強制關閉抽樣以縮圖呈現，切換後100%全數繪製">
                                    <input type="checkbox" checked={forceAllInterventions} onChange={e => setForceAllInterventions(e.target.checked)} className="rounded" />
                                    <span>強制顯示 100% 紅叉</span>
                                </label>
                                <label className="text-xs flex items-center gap-1 text-gray-700 bg-emerald-50 hover:bg-emerald-100 px-2 py-1.5 rounded cursor-pointer border border-emerald-300" title="在軌跡底層以綠色弧帶標示有微塵特徵點的T區">
                                    <input type="checkbox" checked={showTextureHeatmap} onChange={e => setShowTextureHeatmap(e.target.checked)} className="rounded text-emerald-600" />
                                    <span className="text-emerald-800 font-medium">T區 (微塵帶) 標示</span>
                                </label>
                                <div className="text-xs bg-gray-800 text-white font-mono px-2 py-1.5 rounded">
                                    縮放: {scale.toFixed(1)}x
                                </div>
                                <button onClick={zoomOut} className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-700" title="Zoom Out">{Icons.ZoomOut}</button>
                                <button onClick={zoomIn} className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-700" title="Zoom In">{Icons.ZoomIn}</button>
                                <button onClick={resetView} className="p-2 bg-gray-100 hover:bg-gray-200 rounded text-gray-700" title="重設視角">{Icons.RefreshCw}</button>
                                <button onClick={saveImage} className="p-2 bg-emerald-600 text-white hover:bg-emerald-700 rounded flex items-center font-bold text-sm" title="匯出高解析截圖">
                                    <span className="mr-1">{Icons.Download}</span> 截圖
                                </button>
                            </div>
                        </div>

                        {/* 4 Track Cards */}
                        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-3">
                            {tracks.map((track, idx) => (
                                <div key={track.id} className="flex items-center justify-between p-2.5 rounded-lg border bg-gray-50 border-gray-200 text-sm">
                                    <div className="flex items-center gap-2 overflow-hidden">
                                        <div className="w-3.5 h-3.5 rounded-full flex-shrink-0" style={{ backgroundColor: track.color }} />
                                        <div className="truncate">
                                            <div className="font-bold text-xs" style={{ color: track.color }}>{track.label}</div>
                                            <div className="text-[11px] text-gray-500 truncate" title={track.fileName}>{track.fileName}</div>
                                        </div>
                                    </div>
                                    <div className="flex items-center gap-1.5 flex-shrink-0">
                                        <label className="cursor-pointer p-1.5 hover:bg-white rounded border border-gray-300 text-gray-600 hover:text-indigo-600 shadow-sm" title="載入外部自訂軌跡 TXT/CSV">
                                            {Icons.Upload}
                                            <input type="file" accept=".txt,.csv" className="hidden" onChange={(e) => handleUpload(idx, e)} />
                                        </label>
                                        <button onClick={() => toggleVisibility(idx)} className="p-1.5 hover:bg-white rounded border border-gray-300 text-gray-600 hover:text-indigo-600 shadow-sm">
                                            {track.visible ? Icons.Eye : Icons.EyeOff}
                                        </button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>

                    {/* Canvas Stage */}
                    <div ref={containerRef} className="flex-1 relative cursor-grab active:cursor-grabbing bg-slate-50 overflow-hidden">
                        <canvas
                            ref={canvasRef}
                            width={canvasSize.width}
                            height={canvasSize.height}
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
                                {hoverInfo.point.dx !== undefined && hoverInfo.point.dy !== undefined && (
                                    <div className="text-cyan-300 font-medium">單幀位移 (dx, dy): <span className="font-mono">({hoverInfo.point.dx.toFixed(3)}, {hoverInfo.point.dy.toFixed(3)}) px</span></div>
                                )}
                                <div>全域累積 (X, Y): <span className="font-mono">({hoverInfo.point.x.toFixed(3)}, {hoverInfo.point.y.toFixed(3)}) px</span></div>
                                {hoverInfo.point.err !== undefined && (
                                    <div className="text-amber-300 font-medium">距真值誤差: <span className="font-bold font-mono">{hoverInfo.point.err.toFixed(3)} px</span></div>
                                )}
                                <div className="mt-1 pt-1 border-t border-gray-800">
                                    表面狀態: <span className={`font-bold px-1.5 py-0.5 rounded text-[10px] ${hoverInfo.point.is_t ? 'bg-emerald-800 text-emerald-200' : 'bg-gray-800 text-gray-300'}`}>
                                        {hoverInfo.point.is_t ? '【T區 - 有微塵】' : '【TL區 - 空白玻璃】'}
                                    </span>
                                </div>
                                {hoverInfo.point.squal !== undefined && (
                                    <div className="text-gray-300">紋理強度 (SQUAL): <span className="font-mono">{hoverInfo.point.squal}</span></div>
                                )}
                                {hoverInfo.point.int !== undefined && (
                                    <div className="mt-1 font-bold">
                                        Teacher 狀態: <span className={hoverInfo.point.int ? 'text-red-400 font-bold' : 'text-emerald-400'}>
                                            {hoverInfo.point.int ? '❌ 脫靶介入校正' : '✔ 捕捉窗內自主運行'}
                                        </span>
                                    </div>
                                )}
                            </div>
                        )}
                        
                        {/* Status bar */}
                        <div className="absolute bottom-4 left-4 bg-white/90 backdrop-blur px-3 py-2 rounded-lg shadow border border-gray-200 text-xs text-gray-700 pointer-events-none flex gap-4">
                            <div><span className="font-bold">縮放比例:</span> {(scale * 100).toFixed(0)}% (滾輪任意縮放)</div>
                            <div><span className="font-bold">綠點:</span> 起點 | <span className="font-bold">紅方:</span> 終點</div>
                            <div><span className="font-bold">提示:</span> 點擊右上角上傳圖示可載入自訂 TXT 軌跡，自動對齊比較</div>
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

    # Inject JSON data and Experiment info into template
    data_json_str = json.dumps(tracks)
    full_html = html_template.replace('__PRELOADED_DATA_PLACEHOLDER__', data_json_str)
    full_html = full_html.replace('__EXP_TAG_PLACEHOLDER__', exp_tag)
    full_html = full_html.replace('__TOTAL_FRAMES_PLACEHOLDER__', f"{num_frames:,}")

    html_out_path = os.path.join(out_dir, "mouse_track_comparator.html")
    with open(html_out_path, 'w', encoding='utf-8') as f:
        f.write(full_html)
    print(f"Generated Interactive HTML tool: {html_out_path}")

    return html_out_path

if __name__ == '__main__':
    run_export()
