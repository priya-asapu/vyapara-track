from flask import Flask, jsonify, request, send_from_directory
import sqlite3, json, os

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, 'vyapara_track.db')
app = Flask(__name__, static_folder=BASE, static_url_path='')

DEFAULT = {"products":[],"sales":[],"pending":[],"expenses":[],"cash":[],"drafts":{},"account":{"username":"admin","password":"1234"}}

def conn():
    c = sqlite3.connect(DB_PATH)
    c.execute('CREATE TABLE IF NOT EXISTS app_state (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)')
    return c

def get_state():
    c=conn(); row=c.execute('SELECT data FROM app_state WHERE id=1').fetchone()
    if not row:
        c.execute('INSERT INTO app_state(id,data) VALUES(1,?)',(json.dumps(DEFAULT),)); c.commit(); data=DEFAULT
    else: data=json.loads(row[0])
    c.close(); return data

@app.get('/')
def index(): return send_from_directory(BASE, 'index.html')

@app.get('/api/state')
def state_get(): return jsonify(get_state())

@app.post('/api/state')
def state_post():
    data=request.get_json(silent=True)
    if not isinstance(data, dict): return jsonify({'error':'Invalid state'}),400
    # Keep the expected application shape and avoid storing arbitrary payloads.
    state={k:data.get(k, DEFAULT[k]) for k in DEFAULT}
    c=conn(); c.execute('UPDATE app_state SET data=? WHERE id=1',(json.dumps(state,separators=(',',':')),)); c.commit(); c.close()
    return jsonify({'ok':True})

@app.get('/health')
def health(): return jsonify({'ok':True,'app':'Vyapara Track'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT',5000)), debug=False)
