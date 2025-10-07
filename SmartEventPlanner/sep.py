import os
import sqlite3
import smtplib
import random
import secrets
from email.mime.text import MIMEText
from datetime import datetime, timedelta, time, date
from collections import defaultdict

from flask import (Flask, render_template, request, flash, redirect,
                   url_for, g, session)
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps

# ────────────────────────── アプリ設定 ──────────────────────────
app = Flask(__name__)
app.config['SECRET_KEY'] = os.urandom(24)
DATABASE = 'smart_event.db'

# ────────────────────────── メール設定 ──────────────────────────
SMTP_SERVER   = "smtp.kuku.lu"
SMTP_PORT     = 465
SMTP_SENDER   = "smarteventplanner@postm.net"
SMTP_PASSWORD = "J[I2tH)gMIEr"        # ← 変更してください
OTP_EXPIRY_MINUTES = 10

# ────────────────────────── DB ヘルパ ──────────────────────────
def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exc):
    db = getattr(g, '_database', None)
    if db:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        db.execute('PRAGMA foreign_keys = ON;')

        db.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          email TEXT UNIQUE NOT NULL,
          password_hash TEXT NOT NULL,
          is_confirmed INTEGER DEFAULT 0,
          one_time_code TEXT,
          otp_expiry DATETIME
        );

        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          organizer_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          start_datetime TEXT NOT NULL,
          end_datetime TEXT NOT NULL,
          status TEXT DEFAULT 'pending',
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(organizer_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS invitees(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          event_id INTEGER NOT NULL,
          email TEXT NOT NULL,
          token TEXT UNIQUE NOT NULL,
          status TEXT DEFAULT 'pending',
          responded_at DATETIME,
          FOREIGN KEY(event_id) REFERENCES events(id)
        );

        CREATE TABLE IF NOT EXISTS responses(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          invitee_id INTEGER NOT NULL,
          available_slot TEXT NOT NULL,
          FOREIGN KEY(invitee_id) REFERENCES invitees(id)
        );

        CREATE TABLE IF NOT EXISTS schedules(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id INTEGER NOT NULL,
          title TEXT NOT NULL,
          event_date TEXT NOT NULL,
          start_time TEXT,
          end_time TEXT,
          is_all_day INTEGER DEFAULT 0,
          location TEXT,
          description TEXT,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)
        db.commit()

# ────────────────────────── 認証デコレーター ──────────────────────────
def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if 'user_id' not in session:
            flash('ログインが必要です。', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return wrapper

# ────────────────────────── メール送信 ──────────────────────────
def send_email(recipient, subject, body):
    msg = MIMEText(body, 'html')
    msg['Subject'] = subject
    msg['From']    = SMTP_SENDER
    msg['To']      = recipient
    try:
        with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT) as server:
            server.login(SMTP_SENDER, SMTP_PASSWORD)
            server.send_message(msg)
        return True
    except Exception as e:
        print(f"メール送信エラー: {e}")
        return False

# ────────────────────────── 認証ルート（register / confirm / login / logout） ──────────────────────────
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email    = request.form['email']
        password = request.form['password']

        db   = get_db()
        user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()

        if user and user['is_confirmed']:
            flash('このメールアドレスは既に登録されています。', 'danger')
            return redirect(url_for('register'))

        otp = str(random.randint(100000, 999999))
        otp_expiry = datetime.utcnow() + timedelta(minutes=OTP_EXPIRY_MINUTES)

        password_hash = generate_password_hash(password)
        if user:
            db.execute('UPDATE users SET password_hash=?, one_time_code=?, otp_expiry=? WHERE email=?',
                       (password_hash, otp, otp_expiry, email))
        else:
            db.execute('INSERT INTO users(email,password_hash,one_time_code,otp_expiry) VALUES(?,?,?,?)',
                       (email, password_hash, otp, otp_expiry))
        db.commit()

        subject = f"認証コード {otp}"
        body = f"""
        <p>スマートイベントプランナーへようこそ！</p>
        <p>以下の認証コードを入力してください。（{OTP_EXPIRY_MINUTES}分間有効）</p>
        <h2>{otp}</h2>
        """
        send_email(email, subject, body)
        flash('認証コードを送信しました。', 'info')
        return redirect(url_for('confirm', email=email))
    return render_template('register.html')

@app.route('/confirm', methods=['GET', 'POST'])
def confirm():
    email = request.args.get('email')
    if request.method == 'POST':
        email = request.form['email']
        otp   = request.form['otp']
        db    = get_db()
        user  = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if (not user) or (user['one_time_code'] != otp):
            flash('認証失敗。', 'danger')
            return redirect(url_for('confirm', email=email))
        if datetime.strptime(user['otp_expiry'], '%Y-%m-%d %H:%M:%S.%f') < datetime.utcnow():
            flash('認証コードの有効期限が切れています。', 'danger')
            return redirect(url_for('register'))
        db.execute('UPDATE users SET is_confirmed=1, one_time_code=NULL, otp_expiry=NULL WHERE id=?',
                   (user['id'],))
        db.commit()
        flash('認証完了。ログインしてください。', 'success')
        return redirect(url_for('login'))
    return render_template('confirm.html', email=email)

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email    = request.form['email']
        password = request.form['password']
        db   = get_db()
        user = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if not user or not check_password_hash(user['password_hash'], password):
            flash('メールまたはパスワードが違います。', 'danger')
            return redirect(url_for('login'))
        if not user['is_confirmed']:
            flash('認証が完了していません。', 'warning')
            return redirect(url_for('confirm', email=email))
        session.clear()
        session['user_id']    = user['id']
        session['user_email'] = user['email']
        return redirect(url_for('calendar'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('ログアウトしました。', 'success')
    return redirect(url_for('login'))

# ────────────────────────── 基本画面 ──────────────────────────
@app.route('/')
def index():
    return redirect(url_for('calendar')) if 'user_id' in session else redirect(url_for('login'))

@app.route('/calendar')
@login_required
def calendar():
    db = get_db()
    rows = db.execute('SELECT * FROM schedules WHERE user_id=? ORDER BY event_date,start_time',
                      (session['user_id'],)).fetchall()
    schedules = [dict(r) for r in rows]
    return render_template('event.html', schedules=schedules)

@app.route('/create', methods=['GET', 'POST'])
@login_required
def create():
    if request.method == 'POST':
        is_all_day = 'all-day' in request.form
        db = get_db()
        db.execute('''
          INSERT INTO schedules(user_id,title,event_date,start_time,end_time,is_all_day,location,description)
          VALUES(?,?,?,?,?,?,?,?)
        ''', (session['user_id'], request.form['event-title'], request.form['event-date'],
              None if is_all_day else request.form['start-time'],
              None if is_all_day else request.form['end-time'],
              is_all_day, request.form['event-location'], request.form['event-description']))
        db.commit()
        flash('予定を作成しました。', 'success')
        return redirect(url_for('calendar'))
    return render_template('create.html')

# ────────────────────────── 招待作成（時間帯指定） ──────────────────────────
@app.route('/invite', methods=['GET', 'POST'])
@login_required
def invite():
    if request.method == 'POST':
        sd = request.form['start-date']   # YYYY-MM-DD
        ed = request.form['end-date']
        st = request.form['start-time']   # HH:MM
        et = request.form['end-time']

        start_dt = f"{sd}T{st}"
        end_dt   = f"{ed}T{et}"

        db   = get_db()
        cur  = db.execute('''
            INSERT INTO events(organizer_id,title,start_datetime,end_datetime)
            VALUES(?,?,?,?)
        ''', (session['user_id'], request.form['event-title'], start_dt, end_dt))
        db.commit()
        event_id = cur.lastrowid

        emails = request.form.getlist('emails[]')
        for email in emails:
            if not email:
                continue
            token = secrets.token_urlsafe(16)
            db.execute('INSERT INTO invitees(event_id,email,token) VALUES(?,?,?)',
                       (event_id, email, token))
            db.commit()

            url_ = url_for('respond', token=token, _external=True)
            subject = f"【出欠確認】{request.form['event-title']}"
            body = f"""
            <p>{session['user_email']} さんからの招待です。</p>
            <p><a href="{url_}">こちら</a> からご回答ください。</p>
            """
            send_email(email, subject, body)
        flash(f'{len(emails)}名に招待を送信しました。', 'success')
        return redirect(url_for('invite_list'))
    return render_template('create-invite.html')

# ────────────────────────── 参加回答 ──────────────────────────
@app.route('/respond/<token>', methods=['GET', 'POST'])
def respond(token):
    db = get_db()
    invitee = db.execute('SELECT * FROM invitees WHERE token=?', (token,)).fetchone()
    if not invitee:
        return "無効なリンクです。", 404
    event = db.execute('SELECT * FROM events WHERE id=?', (invitee['event_id'],)).fetchone()

    # POST 処理
    if request.method == 'POST':
        action = request.form.get('action')
        if action == 'decline':
            db.execute('UPDATE invitees SET status=\"declined\", responded_at=? WHERE id=?',
                       (datetime.utcnow(), invitee['id']))
            db.commit()
            return "<h2>不参加で受け付けました。</h2>"
        if action == 'attend':
            db.execute('DELETE FROM responses WHERE invitee_id=?', (invitee['id'],))
            for slot in request.form.getlist('available_slots'):
                db.execute('INSERT INTO responses(invitee_id,available_slot) VALUES(?,?)',
                           (invitee['id'], slot))
            db.execute('UPDATE invitees SET status=\"attending\", responded_at=? WHERE id=?',
                       (datetime.utcnow(), invitee['id']))
            db.commit()
            return "<h2>ご回答ありがとうございました！</h2>"

    # 時間帯フィルタ
    start_dt = datetime.fromisoformat(event['start_datetime'])
    end_dt   = datetime.fromisoformat(event['end_datetime'])
    daily_start = start_dt.hour
    daily_end   = end_dt.hour

    slots = defaultdict(list)
    cur_day = start_dt.date()
    while cur_day <= end_dt.date():
        for h in range(daily_start, daily_end):
            slot_dt = datetime.combine(cur_day, time(h))
            day_key = slot_dt.strftime('%Y年%m月%d日 (%a)')
            slots[day_key].append({'value': slot_dt.isoformat(),
                                   'display': slot_dt.strftime('%H:%M')})
        cur_day += timedelta(days=1)

    return render_template('respond.html', event=event,
                           time_slots=slots, token=token)

# ────────────────────────── 参加希望集計 ──────────────────────────
def find_best_schedule(event_id):
    """
    responses / invitees テーブルから
      - 各 1 時間スロットの参加可能人数
      - 最も参加人数が多いスロット
      - 参加率
    を計算して辞書で返す。
    必ず `details` キーを含むため、テンプレート側で安全に参照できる。
    """
    db = get_db()

    # 招待人数
    total_invitees = db.execute(
        'SELECT COUNT(*) FROM invitees WHERE event_id = ?',
        (event_id,)
    ).fetchone()[0]

    # 参加可回答
    responses = db.execute('''
        SELECT r.available_slot
          FROM responses r
          JOIN invitees i ON r.invitee_id = i.id
         WHERE i.event_id = ? AND i.status = 'attending'
    ''', (event_id,)).fetchall()

    # ❶ まだ誰も回答していない場合
    if not responses:
        return {
            "message": "まだ参加者から候補日時が集まっていません。",
            "total_invitees": total_invitees,
            "details": []          # 空配列を返しておくのがポイント
        }

    # ❷ スロットごとに人数をカウント
    from collections import defaultdict
    slot_counts = defaultdict(int)
    for res in responses:
        slot_dt = datetime.fromisoformat(res['available_slot'])
        key = slot_dt.strftime("%Y年%m月%d日 %H:%M")
        slot_counts[key] += 1

    # 人数の多い順に並べ替え
    sorted_slots = sorted(slot_counts.items(), key=lambda x: x[1], reverse=True)
    best_slot, max_attendees = sorted_slots[0]

    details = [{"time": k, "count": v} for k, v in sorted_slots]

    # ❸ 結果を辞書で返す
    return {
        "best_schedule": best_slot,
        "attendees": max_attendees,
        "total_invitees": total_invitees,
        "participation_rate": f"{(max_attendees / total_invitees * 100):.1f}%" if total_invitees else "0%",
        "details": details
    }

# ────────────────────────── 結果表示 ──────────────────────────
@app.route('/event/<int:event_id>/results')
@login_required
def event_results(event_id):
    db = get_db()
    event = db.execute('SELECT * FROM events WHERE id=? AND organizer_id=?',
                       (event_id, session['user_id'])).fetchone()
    if not event:
        return "アクセス権がありません。", 403
    result = find_best_schedule(event_id)
    result['event_title'] = event['title']
    invitees = db.execute('SELECT * FROM invitees WHERE event_id=?', (event_id,)).fetchall()
    return render_template('invite_result.html', result=result,
                           invitees=invitees, event=event)

# ────────────────────────── 招待リスト ──────────────────────────
@app.route('/invites')
@login_required
def invite_list():
    db = get_db()
    events = db.execute('''
        SELECT e.id, e.title, date(e.created_at) AS created_on,
               (SELECT COUNT(*) FROM invitees WHERE event_id=e.id)                     AS total,
               (SELECT COUNT(*) FROM invitees WHERE event_id=e.id AND status='attending') AS attending,
               (SELECT COUNT(*) FROM invitees WHERE event_id=e.id AND status='pending')   AS pending
          FROM events e
         WHERE organizer_id=?
         ORDER BY e.created_at DESC
    ''', (session['user_id'],)).fetchall()
    return render_template('invite_list.html', events=events)

# ────────────────────────── 予定確定 ──────────────────────────
@app.route('/event/<int:event_id>/finalize', methods=['GET', 'POST'])
@login_required
def finalize_event(event_id):
    """
    GET : 候補日時＋参加人数を一覧表示（choices）
    POST: 選択した日時でイベントを confirmed にし
          参加希望者全員へ決定メールを送信
    """
    db = get_db()

    # イベントが自分のものか確認
    event = db.execute(
        'SELECT * FROM events WHERE id=? AND organizer_id=?',
        (event_id, session['user_id'])
    ).fetchone()
    if not event:
        flash('イベントが見つからないか、権限がありません。', 'danger')
        return redirect(url_for('invite_list'))

    # 参加希望集計
    result = find_best_schedule(event_id)

    # 参加候補（日本語表示 → ISO 変換）
    choices = []
    for d in result.get('details', []):
        iso = datetime.strptime(d['time'], '%Y年%m月%d日 %H:%M').isoformat()
        choices.append({'iso': iso, 'display': d['time'], 'count': d['count']})

    # ───────── POST ─────────
    if request.method == 'POST':
        chosen_iso  = request.form.get('final_datetime')
        new_title   = request.form.get('final_title') or event['title']
        custom_msg  = request.form.get('custom_message', '').strip()

        if not chosen_iso:
            flash('日時を選択してください。', 'warning')
            return redirect(request.url)

        start_dt = datetime.fromisoformat(chosen_iso)
        end_dt   = start_dt + timedelta(hours=1)

        # イベントを confirmed に更新
        db.execute("""
            UPDATE events
               SET title=?, start_datetime=?, end_datetime=?, status='confirmed'
             WHERE id=?
        """, (new_title, start_dt.isoformat(), end_dt.isoformat(), event_id))
        db.commit()

        # 出席予定者メール一覧
        attendees = db.execute("""
            SELECT email FROM invitees
             WHERE event_id=? AND status='attending'
        """, (event_id,)).fetchall()

        subject = f"【決定】{new_title}"
        body = (f"<p>以下のイベントが確定しました。</p>"
                f"<p><strong>{new_title}</strong><br>"
                f"{start_dt.strftime('%Y年%m月%d日 %H:%M')}〜</p>")

        # カスタム本文を追加（改行を <br> に置換）
        if custom_msg:
            body += "<p>{}</p>".format(custom_msg.replace('\n', '<br>'))

        body += "<p>当日のご参加をお待ちしております。</p>"

        # メール送信
        for row in attendees:
            send_email(row['email'], subject, body)

        flash(f'決定メールを {len(attendees)} 名に送信しました。', 'success')
        return redirect(url_for('invite_list'))

    # ───────── GET ─────────
    if not choices:
        flash('まだ参加希望が集まっていないため確定できません。', 'warning')
        return redirect(url_for('event_results', event_id=event_id))

    return render_template(
        'finalize_event.html',
        event=event,
        choices=choices,
        details=[{'time': c['display'], 'count': c['count']} for c in choices]  # 旧テンプレ互換
    )
# ─────────────────── メイン ───────────────────
if __name__ == '__main__':
    init_db()

    # 証明書と秘密鍵（パスは実態に合わせて絶対パス推奨）
    ssl_context = (
        'smarteventplanner.coreone.work-crt.pem',   # サーバー証明書
        'smarteventplanner.coreone.work-key.pem',   # 秘密鍵
    )

    # ポートはそのまま 5000
    app.run(
        host='0.0.0.0',        # 外部公開するなら 0.0.0.0 が便利
        port=5000,
        ssl_context=ssl_context,
        debug=False             # 本番で使う場合は False に
    )