import os, re, uuid, json, shutil, zipfile, threading, tempfile, logging
from pathlib import Path
from queue import Queue, Empty
from flask import (Flask, render_template, request, jsonify,
                   Response, send_file, stream_with_context)
import cv2, numpy as np, openpyxl, yt_dlp
from PIL import Image

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

BASE_DIR    = Path(__file__).parent
UPLOAD_DIR  = BASE_DIR / 'uploads'
OUTPUT_DIR  = BASE_DIR / 'output'
LOGO_PATH   = UPLOAD_DIR / 'logo.png'
COOKIES_PATH = BASE_DIR / 'cookies.txt'

for d in [UPLOAD_DIR, OUTPUT_DIR]:
    d.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO)
jobs: dict = {}


# ── helpers ───────────────────────────────────────────────────────────────────

def read_urls(path: Path) -> list:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    ig  = re.compile(r'instagram\.com', re.I)
    col = 1
    for cell in ws[1]:
        v = str(cell.value or '')
        if any(k in v.lower() for k in ('url','link','reel','instagram')):
            col = cell.column; break
    urls = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        v = row[col - 1]
        if v and ig.search(str(v)):
            urls.append(str(v).strip())
    wb.close()
    return urls


def brightness(frame, x, y, w, h):
    fh, fw = frame.shape[:2]
    r = frame[max(0,y):min(fh,y+h), max(0,x):min(fw,x+w)]
    return float(cv2.cvtColor(r, cv2.COLOR_BGR2GRAY).mean()) if r.size else 128.0


def complexity(frame, x, y, w, h):
    fh, fw = frame.shape[:2]
    r = frame[max(0,y):min(fh,y+h), max(0,x):min(fw,x+w)]
    return float(cv2.cvtColor(r, cv2.COLOR_BGR2GRAY).std()) if r.size else 0.0


def pick_corner(frame, lw, lh, margin):
    fw = frame.shape[1]
    bl = brightness(frame, margin, margin, lw, lh)
    br = brightness(frame, fw-margin-lw, margin, lw, lh)
    cl = complexity(frame, margin, margin, lw, lh)
    cr = complexity(frame, fw-margin-lw, margin, lw, lh)
    sl = cl + max(0, bl - 160) * 1.5
    sr = cr + max(0, br - 160) * 1.5
    return 'left' if sl <= sr else 'right'


def watermark(input_path: str, output_path: str, logo: Image.Image,
              size_pct=18, margin=18) -> bool:
    cap = cv2.VideoCapture(input_path)
    if not cap.isOpened(): return False
    fw  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    ret, frame = cap.read(); cap.release()
    if not ret: return False

    lw = max(40, int(fw * size_pct / 100))
    lh = int(logo.height * lw / logo.width)
    resized = logo.resize((lw, lh), Image.LANCZOS)
    corner  = pick_corner(frame, lw, lh, margin)

    tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
    resized.save(tmp.name); tmp.close()

    x   = str(margin) if corner == 'left' else f'W-overlay_w-{margin}'
    cmd = (f'ffmpeg -y -i "{input_path}" -i "{tmp.name}" '
           f'-filter_complex "overlay={x}:{margin}" '
           f'-c:v libx264 -crf 20 -preset fast -c:a copy '
           f'"{output_path}" -loglevel error')
    code = os.system(cmd)
    os.unlink(tmp.name)
    return code == 0


def process_job(job_id: str, excel_path: Path, logo: Image.Image):

    def emit(event: str, data: dict):
        jobs[job_id]['events'].append({'event': event, 'data': data})
        jobs[job_id]['queue'].put({'event': event, 'data': data})

    job_out = OUTPUT_DIR / job_id
    job_out.mkdir(exist_ok=True)
    tmp_dl  = BASE_DIR / f'tmp_{job_id}'
    tmp_dl.mkdir(exist_ok=True)

    try:
        urls = read_urls(excel_path)
        if not urls:
            emit('error', {'message': 'No Instagram URLs found in the Excel file.'})
            return

        total = len(urls)
        emit('start', {'total': total})
        ok = fail = 0

        for idx, url in enumerate(urls, 1):
            short = url.split('?')[0].rstrip('/')
            name  = short.split('/')[-1] or f'reel_{idx}'

            emit('progress', {
                'index': idx, 'total': total, 'url': short,
                'status': 'downloading',
                'message': f'Downloading reel {idx}/{total}…'
            })

            raw_path = None
            ydl_opts = {
                'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
                'outtmpl': str(tmp_dl / '%(id)s.%(ext)s'),
                'quiet': True, 'no_warnings': True,
                'merge_output_format': 'mp4',
                'postprocessors': [{'key': 'FFmpegVideoConvertor', 'preferedformat': 'mp4'}],
            }
            # use cookies if available
            if COOKIES_PATH.exists():
                ydl_opts['cookiefile'] = str(COOKIES_PATH)

            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    base = Path(ydl.prepare_filename(info))
                    mp4  = base.with_suffix('.mp4')
                    raw_path = str(mp4) if mp4.exists() else next(
                        (str(f) for f in tmp_dl.glob(f"{info['id']}*")), None)
            except Exception as e:
                emit('progress', {
                    'index': idx, 'total': total, 'url': short,
                    'status': 'failed', 'message': f'Download failed: {str(e)[:120]}'
                })
                fail += 1; continue

            if not raw_path:
                emit('progress', {
                    'index': idx, 'total': total, 'url': short,
                    'status': 'failed', 'message': 'Could not locate downloaded file.'
                })
                fail += 1; continue

            emit('progress', {
                'index': idx, 'total': total, 'url': short,
                'status': 'watermarking',
                'message': f'Adding logo to reel {idx}/{total}…'
            })

            out_file = job_out / f'{name}_branded.mp4'
            success  = watermark(raw_path, str(out_file), logo)

            if success:
                os.remove(raw_path)
                ok += 1
                emit('progress', {
                    'index': idx, 'total': total, 'url': short,
                    'status': 'done', 'message': f'✔ Reel {idx} complete'
                })
            else:
                fail += 1
                emit('progress', {
                    'index': idx, 'total': total, 'url': short,
                    'status': 'failed', 'message': f'Watermarking failed for reel {idx}'
                })

        # zip everything
        zip_path = OUTPUT_DIR / f'{job_id}.zip'
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            for f in job_out.glob('*.mp4'):
                zf.write(f, f.name)

        shutil.rmtree(job_out, ignore_errors=True)
        emit('done', {'ok': ok, 'fail': fail, 'download_url': f'/download/{job_id}'})
        jobs[job_id]['status'] = 'done'

    except Exception as e:
        emit('error', {'message': str(e)})
        jobs[job_id]['status'] = 'error'
    finally:
        shutil.rmtree(tmp_dl, ignore_errors=True)
        try: excel_path.unlink()
        except: pass


# ── routes ────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html', has_logo=LOGO_PATH.exists())


@app.route('/upload-logo', methods=['POST'])
def upload_logo():
    f = request.files.get('logo')
    if not f or not f.filename:
        return jsonify(error='No file'), 400
    if not f.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
        return jsonify(error='Logo must be PNG, JPG, or WebP'), 400
    Image.open(f).convert('RGBA').save(LOGO_PATH)
    return jsonify(ok=True)


@app.route('/start', methods=['POST'])
def start():
    if not LOGO_PATH.exists():
        return jsonify(error='No logo uploaded yet.'), 400
    excel = request.files.get('excel')
    if not excel or not excel.filename:
        return jsonify(error='No Excel file provided'), 400
    if not excel.filename.lower().endswith(('.xlsx', '.xls')):
        return jsonify(error='File must be .xlsx or .xls'), 400

    job_id   = str(uuid.uuid4())
    xls_path = UPLOAD_DIR / f'{job_id}.xlsx'
    excel.save(xls_path)

    logo = Image.open(LOGO_PATH).convert('RGBA')
    jobs[job_id] = {'queue': Queue(), 'status': 'running', 'events': []}
    threading.Thread(target=process_job, args=(job_id, xls_path, logo), daemon=True).start()
    return jsonify(job_id=job_id)


@app.route('/stream/<job_id>')
def stream(job_id):
    if job_id not in jobs:
        return Response('Job not found', status=404)

    def generate():
        sent = 0
        while True:
            events = jobs[job_id]['events']
            while sent < len(events):
                ev = events[sent]
                yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'])}\n\n"
                sent += 1
            if jobs[job_id]['status'] in ('done', 'error') and sent >= len(events):
                break
            try:
                ev = jobs[job_id]['queue'].get(timeout=1)
                # already appended to events list in emit(); just flush
                yield f"event: {ev['event']}\ndata: {json.dumps(ev['data'])}\n\n"
                sent += 1
            except Empty:
                yield ': ping\n\n'

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
    )


@app.route('/download/<job_id>')
def download(job_id):
    zip_path = OUTPUT_DIR / f'{job_id}.zip'
    if not zip_path.exists():
        return 'File not found', 404
    return send_file(zip_path, as_attachment=True,
                     download_name='branded_reels.zip',
                     mimetype='application/zip')


@app.route('/logo-status')
def logo_status():
    return jsonify(has_logo=LOGO_PATH.exists())


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)