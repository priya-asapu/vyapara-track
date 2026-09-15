from flask import Flask, jsonify, request, send_from_directory, session, redirect
import json, os, sqlite3, secrets, hashlib, smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from werkzeug.security import generate_password_hash, check_password_hash

BASE=os.path.dirname(os.path.abspath(__file__))
SQLITE_PATH=os.path.join(BASE,'vyapara_track.db')
DATABASE_URL=os.environ.get('DATABASE_URL','').strip()
SECRET_KEY=os.environ.get('FLASK_SECRET_KEY','').strip() or 'change-this-secret-in-render'
app=Flask(__name__,static_folder=BASE,static_url_path='')
app.secret_key=SECRET_KEY
app.config.update(SESSION_COOKIE_HTTPONLY=True,SESSION_COOKIE_SAMESITE='Lax',SESSION_COOKIE_SECURE=True)
DEFAULT={'products':[],'sales':[],'pending':[],'expenses':[],'cash':[],'drafts':{}}

def pg_url():
    url=DATABASE_URL
    if url and 'sslmode=' not in url:url += ('&' if '?' in url else '?')+'sslmode=require'
    return url

def pg_conn():
    import psycopg
    return psycopg.connect(pg_url())

def db_init():
    if DATABASE_URL:
        with pg_conn() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS users(id SERIAL PRIMARY KEY,name TEXT NOT NULL,phone TEXT NOT NULL,email TEXT UNIQUE NOT NULL,username TEXT UNIQUE,password_hash TEXT NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),reset_token_hash TEXT,reset_expires TIMESTAMPTZ)''')
            c.execute('''CREATE TABLE IF NOT EXISTS user_state(user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,data JSONB NOT NULL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS app_state(id INTEGER PRIMARY KEY,data JSONB NOT NULL)''')
            # migrate legacy state into the first admin user only once
            if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0:
                legacy=c.execute('SELECT data FROM app_state WHERE id=1').fetchone()
                data=legacy[0] if legacy else DEFAULT
                if isinstance(data,str): data=json.loads(data)
                oldacc=data.get('account',{}) if isinstance(data,dict) else {}
                u=oldacc.get('username','admin'); pw=oldacc.get('password','1234')
                c.execute('INSERT INTO users(name,phone,email,username,password_hash) VALUES(%s,%s,%s,%s,%s)',('Admin','', 'admin@vyaparatrack.local',u,generate_password_hash(pw)))
                uid=c.execute('SELECT id FROM users WHERE username=%s',(u,)).fetchone()[0]
                c.execute('INSERT INTO user_state(user_id,data) VALUES(%s,%s)',(uid,json.dumps({k:data.get(k,DEFAULT[k]) for k in DEFAULT})))
        return
    c=sqlite3.connect(SQLITE_PATH)
    c.execute('''CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,phone TEXT NOT NULL,email TEXT UNIQUE NOT NULL,username TEXT UNIQUE,password_hash TEXT NOT NULL,created_at TEXT NOT NULL,reset_token_hash TEXT,reset_expires TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_state(user_id INTEGER PRIMARY KEY,data TEXT NOT NULL)''')
    if c.execute('SELECT COUNT(*) FROM users').fetchone()[0]==0:
        c.execute('INSERT INTO users(name,phone,email,username,password_hash,created_at) VALUES(?,?,?,?,?,?)',('Admin','','admin@vyaparatrack.local','admin',generate_password_hash('1234'),datetime.now(timezone.utc).isoformat()))
        uid=c.execute('SELECT id FROM users WHERE username=?',('admin',)).fetchone()[0]
        c.execute('INSERT INTO user_state(user_id,data) VALUES(?,?)',(uid,json.dumps(DEFAULT)))
    c.commit();c.close()

def user_row(uid):
    if DATABASE_URL:
        with pg_conn() as c:return c.execute('SELECT id,name,phone,email,username,password_hash,created_at,reset_token_hash,reset_expires FROM users WHERE id=%s',(uid,)).fetchone()
    c=sqlite3.connect(SQLITE_PATH);r=c.execute('SELECT id,name,phone,email,username,password_hash,created_at,reset_token_hash,reset_expires FROM users WHERE id=?',(uid,)).fetchone();c.close();return r

def public_user(r):
    return {'id':r[0],'name':r[1],'phone':r[2],'email':r[3],'username':r[4],'created_at':r[6].isoformat() if hasattr(r[6],'isoformat') else r[6]}

def get_state(uid):
    if DATABASE_URL:
        with pg_conn() as c:
            r=c.execute('SELECT data FROM user_state WHERE user_id=%s',(uid,)).fetchone()
            if not r:return DEFAULT.copy()
            return r[0] if isinstance(r[0],dict) else json.loads(r[0])
    c=sqlite3.connect(SQLITE_PATH);r=c.execute('SELECT data FROM user_state WHERE user_id=?',(uid,)).fetchone();c.close();return json.loads(r[0]) if r else DEFAULT.copy()

def save_state(uid,data):
    state={k:data.get(k,DEFAULT[k]) for k in DEFAULT}
    if DATABASE_URL:
        with pg_conn() as c:c.execute('''INSERT INTO user_state(user_id,data) VALUES(%s,%s) ON CONFLICT(user_id) DO UPDATE SET data=EXCLUDED.data''',(uid,json.dumps(state,separators=(',',':'))))
    else:
        c=sqlite3.connect(SQLITE_PATH);c.execute('INSERT INTO user_state(user_id,data) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data',(uid,json.dumps(state,separators=(',',':'))));c.commit();c.close()

def find_login(login):
    if DATABASE_URL:
        with pg_conn() as c:return c.execute('SELECT id,name,phone,email,username,password_hash,created_at,reset_token_hash,reset_expires FROM users WHERE lower(email)=lower(%s) OR phone=%s OR lower(username)=lower(%s)',(login,login,login)).fetchone()
    c=sqlite3.connect(SQLITE_PATH);r=c.execute('SELECT id,name,phone,email,username,password_hash,created_at,reset_token_hash,reset_expires FROM users WHERE lower(email)=lower(?) OR phone=? OR lower(username)=lower(?)',(login,login,login)).fetchone();c.close();return r

def create_user(name,phone,email,password):
    username=email.split('@')[0][:30]
    if DATABASE_URL:
        with pg_conn() as c:
            if c.execute('SELECT 1 FROM users WHERE lower(email)=lower(%s)',(email,)).fetchone():raise ValueError('Email already registered.')
            c.execute('INSERT INTO users(name,phone,email,username,password_hash) VALUES(%s,%s,%s,%s,%s)',(name,phone,email,username,generate_password_hash(password)))
            uid=c.execute('SELECT id FROM users WHERE lower(email)=lower(%s)',(email,)).fetchone()[0]
            c.execute('INSERT INTO user_state(user_id,data) VALUES(%s,%s)',(uid,json.dumps(DEFAULT)))
            return
    c=sqlite3.connect(SQLITE_PATH)
    try:
        c.execute('INSERT INTO users(name,phone,email,username,password_hash,created_at) VALUES(?,?,?,?,?,?)',(name,phone,email,username,generate_password_hash(password),datetime.now(timezone.utc).isoformat()));uid=c.execute('SELECT id FROM users WHERE email=?',(email,)).fetchone()[0];c.execute('INSERT INTO user_state(user_id,data) VALUES(?,?)',(uid,json.dumps(DEFAULT)));c.commit()
    except sqlite3.IntegrityError:raise ValueError('Email or username already registered.')
    finally:c.close()

def update_reset(uid,h,expires):
    if DATABASE_URL:
        with pg_conn() as c:c.execute('UPDATE users SET reset_token_hash=%s,reset_expires=%s WHERE id=%s',(h,expires,uid))
    else:
        c=sqlite3.connect(SQLITE_PATH);c.execute('UPDATE users SET reset_token_hash=?,reset_expires=? WHERE id=?',(h,expires.isoformat(),uid));c.commit();c.close()

def clear_reset(uid):update_reset(uid,None,None)

def send_reset_email(to,name,link):
    host=os.environ.get('SMTP_HOST','').strip();port=int(os.environ.get('SMTP_PORT','587'));user=os.environ.get('SMTP_USER','').strip();pw=os.environ.get('SMTP_PASSWORD','').strip();sender=os.environ.get('SMTP_FROM',user).strip()
    if not all([host,user,pw,sender]):raise RuntimeError('Email service is not configured on the server.')
    msg=EmailMessage();msg['Subject']='Vyapara Track - Reset your password';msg['From']=sender;msg['To']=to;msg.set_content(f'''Hello {name},\n\nUse this link to create a new Vyapara Track password:\n{link}\n\nThis link expires in 30 minutes and can be used once.\n\nIf you did not request this, you can ignore this email.\n''')
    with smtplib.SMTP(host,port,timeout=20) as s:
        s.starttls();s.login(user,pw);s.send_message(msg)

def require_user():
    uid=session.get('uid')
    if not uid:return None
    return user_row(uid)

@app.get('/')
def index():return send_from_directory(BASE,'index.html')
@app.get('/api/auth/me')
def auth_me():
    r=require_user()
    if not r:return jsonify({'error':'Not logged in'}),401
    return jsonify({'user':public_user(r)})
@app.post('/api/auth/signup')
def auth_signup():
    x=request.get_json(silent=True) or {};name=str(x.get('name','')).strip();phone=str(x.get('phone','')).strip();email=str(x.get('email','')).strip().lower();pw=str(x.get('password',''))
    if not all([name,phone,email,pw]):return jsonify(error='All fields are required.'),400
    if len(pw)<6:return jsonify(error='Password must be at least 6 characters.'),400
    try:create_user(name,phone,email,pw)
    except ValueError as e:return jsonify(error=str(e)),400
    return jsonify(ok=True)
@app.post('/api/auth/login')
def auth_login():
    x=request.get_json(silent=True) or {};login=str(x.get('login','')).strip();pw=str(x.get('password',''))
    r=find_login(login) if login else None
    if not r or not check_password_hash(r[5],pw):return jsonify(error='Invalid email/mobile or password.'),401
    session.clear();session['uid']=r[0];return jsonify(user=public_user(r))
@app.post('/api/auth/logout')
def auth_logout():session.clear();return jsonify(ok=True)
@app.put('/api/auth/profile')
def auth_profile():
    r=require_user()
    if not r:return jsonify(error='Not logged in'),401
    x=request.get_json(silent=True) or {};name=str(x.get('name','')).strip();phone=str(x.get('phone','')).strip()
    if not name or not phone:return jsonify(error='Name and mobile are required.'),400
    if DATABASE_URL:
        with pg_conn() as c:c.execute('UPDATE users SET name=%s,phone=%s WHERE id=%s',(name,phone,r[0]))
    else:
        c=sqlite3.connect(SQLITE_PATH);c.execute('UPDATE users SET name=?,phone=? WHERE id=?',(name,phone,r[0]));c.commit();c.close()
    return jsonify(user=public_user(user_row(r[0])))
@app.post('/api/auth/change-password')
def auth_change_password():
    r=require_user();x=request.get_json(silent=True) or {};pw=str(x.get('password',''))
    if not r:return jsonify(error='Not logged in'),401
    if len(pw)<6:return jsonify(error='Password must be at least 6 characters.'),400
    if DATABASE_URL:
        with pg_conn() as c:c.execute('UPDATE users SET password_hash=%s WHERE id=%s',(generate_password_hash(pw),r[0]))
    else:
        c=sqlite3.connect(SQLITE_PATH);c.execute('UPDATE users SET password_hash=? WHERE id=?',(generate_password_hash(pw),r[0]));c.commit();c.close()
    return jsonify(ok=True)
@app.post('/api/auth/forgot')
def auth_forgot():
    x=request.get_json(silent=True) or {};email=str(x.get('email','')).strip().lower()
    r=find_login(email) if email else None
    # Same response whether account exists or not.
    if not r or '@vyaparatrack.local' in r[3]:return jsonify(message='If the email is registered, a reset link has been sent.')
    token=secrets.token_urlsafe(48);h=hashlib.sha256(token.encode()).hexdigest();expires=datetime.now(timezone.utc)+timedelta(minutes=30);update_reset(r[0],h,expires)
    base=request.host_url.rstrip('/');link=f'{base}/?reset={token}'
    try:send_reset_email(r[3],r[1],link)
    except Exception as e:
        clear_reset(r[0]);app.logger.exception('reset email failed');return jsonify(error='Email could not be sent. Please configure SMTP settings in Render.'),500
    return jsonify(message='If the email is registered, a reset link has been sent.')
@app.post('/api/auth/reset')
def auth_reset():
    x=request.get_json(silent=True) or {};token=str(x.get('token',''));pw=str(x.get('password',''))
    if len(pw)<6:return jsonify(error='Password must be at least 6 characters.'),400
    if not token:return jsonify(error='Invalid reset link.'),400
    h=hashlib.sha256(token.encode()).hexdigest()
    if DATABASE_URL:
        with pg_conn() as c:r=c.execute('SELECT id,reset_expires FROM users WHERE reset_token_hash=%s',(h,)).fetchone()
    else:
        c=sqlite3.connect(SQLITE_PATH);r=c.execute('SELECT id,reset_expires FROM users WHERE reset_token_hash=?',(h,)).fetchone();c.close()
    if not r:return jsonify(error='Invalid or already used reset link.'),400
    exp=r[1] if isinstance(r[1],datetime) else datetime.fromisoformat(r[1])
    if exp.tzinfo is None:exp=exp.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc)>exp:return jsonify(error='Reset link expired. Please request a new one.'),400
    if DATABASE_URL:
        with pg_conn() as c:c.execute('UPDATE users SET password_hash=%s,reset_token_hash=NULL,reset_expires=NULL WHERE id=%s',(generate_password_hash(pw),r[0]))
    else:
        c=sqlite3.connect(SQLITE_PATH);c.execute('UPDATE users SET password_hash=?,reset_token_hash=NULL,reset_expires=NULL WHERE id=?',(generate_password_hash(pw),r[0]));c.commit();c.close()
    return jsonify(ok=True)
@app.get('/api/state')
def state_get():
    r=require_user()
    if not r:return jsonify(error='Not logged in'),401
    return jsonify(data=get_state(r[0]))
@app.post('/api/state')
def state_post():
    r=require_user();data=request.get_json(silent=True)
    if not r:return jsonify(error='Not logged in'),401
    if not isinstance(data,dict):return jsonify(error='Invalid state'),400
    save_state(r[0],data);return jsonify(ok=True)
@app.get('/health')
def health():
    try:db_init();return jsonify(ok=True,app='Vyapara Track',database='supabase-postgresql' if DATABASE_URL else 'local-sqlite')
    except Exception as e:return jsonify(ok=False,error=str(e)),500

with app.app_context():db_init()
if __name__=='__main__':app.run(host='0.0.0.0',port=int(os.environ.get('PORT',5000)),debug=False)
