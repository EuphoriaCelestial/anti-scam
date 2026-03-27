import os
import csv
import uuid
import hashlib
import json
import io
import random
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, request, jsonify, send_from_directory,
                   render_template, send_file, abort)
import jwt

# ── App setup ────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-secret-change-in-prod')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app.config['UPLOAD_PDF_FOLDER']   = os.path.join(BASE_DIR, 'uploads', 'pdf')
app.config['UPLOAD_VIDEO_FOLDER'] = os.path.join(BASE_DIR, 'uploads', 'video')
app.config['STATIC_FOLDER']       = os.path.join(BASE_DIR, 'static')
app.config['MAX_CONTENT_LENGTH']  = 500 * 1024 * 1024   # 500 MB

ALLOWED_PDF   = {'pdf'}
ALLOWED_VIDEO = {'mp4', 'webm', 'ogg', 'mov', 'avi', 'mkv'}

os.makedirs(app.config['UPLOAD_PDF_FOLDER'],   exist_ok=True)
os.makedirs(app.config['UPLOAD_VIDEO_FOLDER'], exist_ok=True)

# ── Database (PostgreSQL via psycopg2; falls back to sqlite3) ─────────────────
DATABASE_URL = os.environ.get('DATABASE_URL', '')   # e.g. postgresql://user:pass@host:5432/dbname

def get_db():
    if DATABASE_URL:
        import psycopg2, psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn, 'pg'
    else:
        import sqlite3
        db_path = os.path.join(os.path.dirname(__file__), 'quiz.db')
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn, 'sqlite'

def wait_for_db(retries=15, delay=2):
    """Wait until the database is reachable (useful on Docker startup)."""
    import time
    for i in range(retries):
        try:
            conn, _ = get_db()
            conn.close()
            print("Database connection established.")
            return
        except Exception as e:
            print(f"Waiting for database... ({i+1}/{retries}): {e}")
            time.sleep(delay)
    raise RuntimeError("Could not connect to the database after multiple retries.")

def db_execute(sql, params=(), fetchall=False, fetchone=False, lastrowid=False):
    conn, dialect = get_db()
    try:
        # Convert %s style (pg) ↔ ? style (sqlite)
        if dialect == 'sqlite':
            sql = sql.replace('%s', '?').replace('RETURNING id', '')
        cur = conn.cursor()
        cur.execute(sql, params)
        result = None
        if fetchall:
            rows = cur.fetchall()
            result = [dict(r) if dialect == 'sqlite' else dict(zip([d[0] for d in cur.description], r))
                      for r in rows]
        elif fetchone:
            row = cur.fetchone()
            if row:
                result = dict(row) if dialect == 'sqlite' else dict(zip([d[0] for d in cur.description], row))
        elif lastrowid:
            if dialect == 'pg':
                row = cur.fetchone()
                result = row[0] if row else None
            else:
                result = cur.lastrowid
        conn.commit()
        return result
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def init_db():
    conn, dialect = get_db()
    serial = 'SERIAL' if dialect == 'pg' else 'INTEGER'
    ph = '%s' if dialect == 'pg' else '?'
    try:
        cur = conn.cursor()
        stmts = [
            f"""CREATE TABLE IF NOT EXISTS questions (
                id {serial} PRIMARY KEY,
                question TEXT NOT NULL,
                correct_answer TEXT NOT NULL,
                wrong1 TEXT NOT NULL,
                wrong2 TEXT NOT NULL DEFAULT '',
                wrong3 TEXT NOT NULL DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            f"""CREATE TABLE IF NOT EXISTS players (
                id {serial} PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT UNIQUE NOT NULL,
                address TEXT,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            f"""CREATE TABLE IF NOT EXISTS high_scores (
                id {serial} PRIMARY KEY,
                player_id INTEGER,
                player_name TEXT NOT NULL,
                score INTEGER NOT NULL,
                time_seconds INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
            f"""CREATE TABLE IF NOT EXISTS admins (
                id {serial} PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL
            )""",
            f"""CREATE TABLE IF NOT EXISTS files (
                id {serial} PRIMARY KEY,
                filename TEXT NOT NULL,
                original_name TEXT NOT NULL,
                file_type TEXT NOT NULL,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )""",
        ]
        for s in stmts:
            cur.execute(s)
        conn.commit()
        # Seed default admin
        cur.execute(f"SELECT id FROM admins WHERE username={ph}", ('admin',))
        if not cur.fetchone():
            pw = hashlib.sha256('admin123'.encode()).hexdigest()
            cur.execute(f"INSERT INTO admins (username, password_hash) VALUES ({ph},{ph})", ('admin', pw))
        conn.commit()
    finally:
        conn.close()

# ── Helpers ───────────────────────────────────────────────────────────────────
def hash_password(pw):
    return hashlib.sha256(pw.encode()).hexdigest()

def make_token(payload):
    payload['exp'] = datetime.utcnow() + timedelta(hours=24)
    return jwt.encode(payload, app.config['SECRET_KEY'], algorithm='HS256')

def decode_token(token):
    return jwt.decode(token, app.config['SECRET_KEY'], algorithms=['HS256'])

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': 'Token missing'}), 401
        try:
            data = decode_token(token)
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token expired'}), 401
        except Exception:
            return jsonify({'error': 'Invalid token'}), 401
        return f(data, *args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization', '').replace('Bearer ', '')
        if not token:
            return jsonify({'error': 'Token missing'}), 401
        try:
            data = decode_token(token)
            if data.get('role') != 'admin':
                return jsonify({'error': 'Admin only'}), 403
        except Exception:
            return jsonify({'error': 'Invalid token'}), 401
        return f(data, *args, **kwargs)
    return decorated

def allowed_file(filename, allowed):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed

def safe_filename(original):
    ext  = original.rsplit('.', 1)[-1].lower() if '.' in original else ''
    return f"{uuid.uuid4().hex}.{ext}"

# ── Auth routes ───────────────────────────────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def register():
    d = request.get_json()
    name     = (d.get('name') or '').strip()
    phone    = (d.get('phone') or '').strip()
    address  = (d.get('address') or '').strip()
    password = d.get('password', '')
    if not name or not phone or not password:
        return jsonify({'error': 'name, phone and password are required'}), 400
    existing = db_execute("SELECT id FROM players WHERE phone=%s", (phone,), fetchone=True)
    if existing:
        return jsonify({'error': 'Phone already registered'}), 409
    pw_hash = hash_password(password)
    db_execute("INSERT INTO players (name, phone, address, password_hash) VALUES (%s,%s,%s,%s)",
               (name, phone, address, pw_hash))
    return jsonify({'message': 'Registered successfully'}), 201

@app.route('/api/login', methods=['POST'])
def login():
    d        = request.get_json()
    username = (d.get('username') or d.get('phone') or '').strip()
    password = d.get('password', '')
    pw_hash  = hash_password(password)

    # Check admin first
    admin = db_execute("SELECT id, username FROM admins WHERE username=%s AND password_hash=%s",
                       (username, pw_hash), fetchone=True)
    if admin:
        token = make_token({'id': admin['id'], 'username': admin['username'], 'role': 'admin'})
        return jsonify({'token': token, 'role': 'admin', 'name': admin['username']})

    # Check player
    player = db_execute("SELECT id, name, phone FROM players WHERE phone=%s AND password_hash=%s",
                        (username, pw_hash), fetchone=True)
    if player:
        token = make_token({'id': player['id'], 'name': player['name'], 'role': 'player'})
        return jsonify({'token': token, 'role': 'player', 'name': player['name']})

    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    # JWT is stateless; client just discards the token
    return jsonify({'message': 'Logged out'})

# ── Question routes ───────────────────────────────────────────────────────────
@app.route('/api/questions', methods=['GET'])
@token_required
def get_all_questions(user):
    rows = db_execute("SELECT * FROM questions ORDER BY id", fetchall=True)
    return jsonify(rows)

@app.route('/api/questions/random', methods=['GET'])
@token_required
def get_random_questions(user):
    rows = db_execute("SELECT * FROM questions", fetchall=True)
    sample = random.sample(rows, min(10, len(rows)))
    # Shuffle answers
    result = []
    for q in sample:
        answers = [a for a in [q['correct_answer'], q['wrong1'], q['wrong2'], q['wrong3']] if a and a.strip()]
        random.shuffle(answers)
        result.append({
            'id': q['id'],
            'question': q['question'],
            'answers': answers,
            'correct_answer': q['correct_answer']
        })
    return jsonify(result)

@app.route('/api/questions', methods=['POST'])
@admin_required
def add_question(user):
    d = request.get_json()
    q  = d.get('question', '').strip()
    c  = d.get('correct_answer', '').strip()
    w1 = d.get('wrong1', '').strip()
    w2 = d.get('wrong2', '').strip()
    w3 = d.get('wrong3', '').strip()
    if not all([q, c, w1]):
        return jsonify({'error': 'Vui lòng điền đầy đủ thông tin'}), 400
    db_execute("INSERT INTO questions (question, correct_answer, wrong1, wrong2, wrong3) VALUES (%s,%s,%s,%s,%s)",
               (q, c, w1, w2, w3))
    return jsonify({'message': 'Đã thêm câu hỏi'}), 201

@app.route('/api/questions/<int:qid>', methods=['PUT'])
@admin_required
def update_question(user, qid):
    d  = request.get_json()
    q  = d.get('question', '').strip()
    c  = d.get('correct_answer', '').strip()
    w1 = d.get('wrong1', '').strip()
    w2 = d.get('wrong2', '').strip()
    w3 = d.get('wrong3', '').strip()
    if not all([q, c, w1]):
        return jsonify({'error': 'Vui lòng điền đầy đủ thông tin'}), 400
    db_execute("UPDATE questions SET question=%s, correct_answer=%s, wrong1=%s, wrong2=%s, wrong3=%s WHERE id=%s",
               (q, c, w1, w2, w3, qid))
    return jsonify({'message': 'Đã cập nhật'})

@app.route('/api/questions/<int:qid>', methods=['DELETE'])
@admin_required
def delete_question(user, qid):
    db_execute("DELETE FROM questions WHERE id=%s", (qid,))
    return jsonify({'message': 'Deleted'})

@app.route('/api/questions/all', methods=['DELETE'])
@admin_required
def delete_all_questions(user):
    db_execute("DELETE FROM questions")
    return jsonify({'message': 'All questions deleted'})

@app.route('/api/questions/upload', methods=['POST'])
@admin_required
def upload_questions(user):
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    f = request.files['file']
    if not f.filename.endswith('.csv'):
        return jsonify({'error': 'CSV only'}), 400
    stream   = io.StringIO(f.stream.read().decode('utf-8'))
    reader   = csv.reader(stream)
    inserted = 0
    errors   = []
    for i, row in enumerate(reader, 1):
        if len(row) < 3:
            errors.append(f"Hàng {i}: cần ít nhất 3 cột"); continue
        row = [x.strip() for x in row]
        if not row or not row[0]: continue
        q, c = row[0], row[1]
        answers = row[2:]  # remaining are wrong answers
        w1 = answers[0] if len(answers) > 0 else ''
        w2 = answers[1] if len(answers) > 1 else ''
        w3 = answers[2] if len(answers) > 2 else ''
        if not all([q, c, w1]):
            errors.append(f"Hàng {i}: thiếu thông tin"); continue
        try:
            db_execute("INSERT INTO questions (question, correct_answer, wrong1, wrong2, wrong3) VALUES (%s,%s,%s,%s,%s)",
                       (q, c, w1, w2, w3))
            inserted += 1
        except Exception as e:
            errors.append(f"Hàng {i}: {e}")
    return jsonify({'inserted': inserted, 'errors': errors})

# ── High score routes ─────────────────────────────────────────────────────────
@app.route('/api/highscores', methods=['GET'])
def get_highscores():
    rows = db_execute(
        "SELECT player_name, score, time_seconds, created_at FROM high_scores ORDER BY score DESC, time_seconds ASC LIMIT 20",
        fetchall=True)
    return jsonify(rows)

@app.route('/api/highscores', methods=['POST'])
@token_required
def save_highscore(user):
    d    = request.get_json()
    score = d.get('score')
    time_seconds = d.get('time_seconds', 0)
    name  = user.get('name', 'Anonymous')
    pid   = user.get('id')
    if score is None:
        return jsonify({'error': 'score required'}), 400
    db_execute("INSERT INTO high_scores (player_id, player_name, score, time_seconds) VALUES (%s,%s,%s,%s)",
               (pid, name, score, time_seconds))
    return jsonify({'message': 'Score saved'}), 201

# ── File upload / serve routes ────────────────────────────────────────────────
@app.route('/api/files', methods=['GET'])
@token_required
def list_files(user):
    rows = db_execute("SELECT * FROM files ORDER BY uploaded_at DESC", fetchall=True)
    return jsonify(rows)

@app.route('/api/upload/pdf', methods=['POST'])
@admin_required
def upload_pdf(user):
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    if not allowed_file(f.filename, ALLOWED_PDF):
        return jsonify({'error': 'PDF only'}), 400
    fname = safe_filename(f.filename)
    f.save(os.path.join(app.config['UPLOAD_PDF_FOLDER'], fname))
    db_execute("INSERT INTO files (filename, original_name, file_type) VALUES (%s,%s,%s)",
               (fname, f.filename, 'pdf'))
    return jsonify({'message': 'PDF uploaded', 'filename': fname}), 201

@app.route('/api/upload/video', methods=['POST'])
@admin_required
def upload_video(user):
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    f = request.files['file']
    if not allowed_file(f.filename, ALLOWED_VIDEO):
        return jsonify({'error': 'Video file only'}), 400
    fname = safe_filename(f.filename)
    f.save(os.path.join(app.config['UPLOAD_VIDEO_FOLDER'], fname))
    db_execute("INSERT INTO files (filename, original_name, file_type) VALUES (%s,%s,%s)",
               (fname, f.filename, 'video'))
    return jsonify({'message': 'Video uploaded', 'filename': fname}), 201

@app.route('/api/files/<int:fid>', methods=['DELETE'])
@admin_required
def delete_file(user, fid):
    row = db_execute("SELECT * FROM files WHERE id=%s", (fid,), fetchone=True)
    if not row:
        return jsonify({'error': 'Not found'}), 404
    folder = app.config['UPLOAD_PDF_FOLDER'] if row['file_type'] == 'pdf' else app.config['UPLOAD_VIDEO_FOLDER']
    fpath  = os.path.join(folder, row['filename'])
    if os.path.exists(fpath):
        os.remove(fpath)
    db_execute("DELETE FROM files WHERE id=%s", (fid,))
    return jsonify({'message': 'Deleted'})

@app.route('/uploads/pdf/<path:filename>')
def serve_pdf(filename):
    return send_from_directory(app.config['UPLOAD_PDF_FOLDER'], filename)

@app.route('/uploads/video/<path:filename>')
def serve_video(filename):
    return send_from_directory(app.config['UPLOAD_VIDEO_FOLDER'], filename)


# ── Frontend (SPA catch-all) ──────────────────────────────────────────────────
@app.route('/')
def serve_index():
    return send_from_directory(app.config['STATIC_FOLDER'], 'index.html')

@app.route('/assets/<path:filename>')
def serve_assets(filename):
    assets_dir = os.path.join(BASE_DIR, 'static', 'assets')
    return send_from_directory(assets_dir, filename)

@app.route('/<path:path>')
def serve_spa(path):
    if path.startswith('api/') or path.startswith('uploads/'):
        abort(404)
    static_file = os.path.join(app.config['STATIC_FOLDER'], path)
    if os.path.exists(static_file):
        return send_from_directory(app.config['STATIC_FOLDER'], path)
    return send_from_directory(app.config['STATIC_FOLDER'], 'index.html')

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    wait_for_db()
    init_db()
    print("Default admin → username: admin  password: admin123")
    app.run(debug=False, host='0.0.0.0', port=5000)
