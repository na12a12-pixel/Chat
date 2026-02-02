import os
import random
from datetime import datetime, timezone
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate, upgrade

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'dev-key-change-in-production')
db_url = os.environ.get('DATABASE_URL')
if not db_url or not db_url.startswith('postgresql'):
    if os.environ.get('ENVIRONMENT') == 'production':
        raise ValueError("DATABASE_URL must be set to a PostgreSQL URL in production")
    else:
        default_db = os.path.join(os.getcwd(), 'instance', 'chat.db')
        db_url = f'sqlite:///{default_db}'
# Convert legacy postgres URL scheme to SQLAlchemy-compatible scheme
if isinstance(db_url, str) and db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)
socketio = SocketIO(app, cors_allowed_origins="*")

# ฐานข้อมูล

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender_sid = db.Column(db.String(100))
    receiver_sid = db.Column(db.String(100))
    sender_name = db.Column(db.String(100))
    text = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    user_deleted = db.Column(db.Boolean, default=False)


class AdminToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(64), unique=True, index=True, nullable=False)
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    last_sid = db.Column(db.String(100), nullable=True)

with app.app_context():
    upgrade()

#--- ระบบจัดการแชท ---
users = {}
admins = set()  # set of admin sids
admin_names = {}  # map of sid -> admin_name
ADMIN_PASS = os.environ.get('ADMIN_PASS', 'adminworakanjajakub')

# Weapon/equipment names for admin display
ADMIN_WEAPONS = [
    'Shield', 'Sword', 'Spear', 'Bow', 'Axe', 'Mace', 'Hammer', 'Crossbow', 'Dagger', 'Lance',
    'Flail', 'Pike', 'Halberd', 'Sabre', 'Katana', 'Claymore', 'Longsword', 'Falchion', 'Warhammer', 'Trident',
    'Rapier', 'Scimitar', 'Maul', 'Greatsword', 'Arbalest', 'Ballista', 'Catapult', 'Trebuchet', 'Cannon', 'Musket',
    'Blunderbuss', 'Flintlock', 'Rifle', 'Carbine', 'Revolver', 'Pistol', 'SMG', 'Shotgun', 'Sniper', 'Grenade'
]
used_admin_names = set()  # track used admin names to prevent duplicates

def get_unique_admin_name():
    """Generate a unique admin name from ADMIN_WEAPONS, no duplicates."""
    available = [w for w in ADMIN_WEAPONS if w not in used_admin_names]
    if not available:
        used_admin_names.clear()
        available = ADMIN_WEAPONS
    name = random.choice(available)
    used_admin_names.add(name)
    return name

def get_admins_list():
    """Return list of admins with their names for broadcasting."""
    return [{'sid': sid, 'name': admin_names.get(sid, 'Admin')} for sid in admins]

def get_other_admins_list(exclude_sid=None):
    """Return list of OTHER admins (excluding the specified sid)."""
    return [{'sid': sid, 'name': admin_names.get(sid, 'Admin')} for sid in admins if sid != exclude_sid]

def get_all_users_for_admin(exclude_sid=None):
    """Return all users (regular users + other admins) for admin's left panel."""
    user_list = []
    for sid, name in users.items():
        if exclude_sid and sid == exclude_sid:
            continue
        # Only include if not an admin, or if it's another admin
        if sid not in admins or sid != exclude_sid:
            user_list.append({'sid': sid, 'name': name})
    return user_list

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/health')
def health():
    return {'status': 'ok'}

@socketio.on('join')
def handle_join(data=None):
    # Allow client to provide a preferred display name
    preferred = None
    if isinstance(data, dict):
        preferred = data.get('name')
    if preferred:
        nick = preferred
    else:
        nick = f"User-{random.randint(1000, 9999)}"
    users[request.sid] = nick
    emit('set_identity', {'name': nick, 'id': request.sid})
    print(f"[DEBUG] JOIN: sid={request.sid} nick={nick}")

    # If client provided an admin token, validate and re-authenticate admin
    if isinstance(data, dict):
        token = data.get('admin_token')
        if token:
            at = AdminToken.query.filter_by(token=token).first()
            if at:
                admins.add(request.sid)
                admin_names[request.sid] = at.name
                users[request.sid] = at.name
                at.last_sid = request.sid
                db.session.add(at)
                db.session.commit()
                emit('admin_status', {'is_admin': True, 'name': at.name})
                # send all users to this admin (exclude themselves)
                emit('user_list', get_all_users_for_admin(request.sid))
                # notify all other admins of new admin
                socketio.emit('user_list', get_admins_list(), skip_sid=request.sid)

    # broadcast updated user list ONLY to all admins (not to regular users)
    if request.sid in admins:
        socketio.emit('user_list', get_all_users_for_admin(request.sid))

    # load history for this user (only messages not soft-deleted by the user)
    history = Message.query.filter(
        ((Message.sender_sid == request.sid) | (Message.receiver_sid == request.sid)),
        (Message.user_deleted == False)
    ).order_by(Message.timestamp.asc()).all()

    for msg in history:
        emit('new_msg', {'user': msg.sender_name, 'text': msg.text})


@socketio.on('admin_login')
def handle_admin_login(data):
    code = ''
    if isinstance(data, dict):
        code = data.get('code', '')
    if code == ADMIN_PASS:
        admins.add(request.sid)
        admin_name = get_unique_admin_name()
        admin_names[request.sid] = admin_name
        users[request.sid] = admin_name
        # create a persistent token for this admin so they remain admin across refreshes
        import uuid
        token = uuid.uuid4().hex
        at = AdminToken(token=token, name=admin_name, last_sid=request.sid)
        db.session.add(at)
        db.session.commit()
        emit('admin_status', {'is_admin': True, 'name': admin_name})
        emit('admin_token', {'token': token})
        emit('sys_msg', {'msg': "คุณเข้าสู่ระบบแอดมินแล้ว"})
        # send all users to this admin (exclude themselves)
        emit('user_list', get_all_users_for_admin(request.sid))
        # notify all other admins (they see all users including new admin)
        socketio.emit('user_list', get_admins_list(), skip_sid=request.sid)
        print(f"[DEBUG] ADMIN LOGIN: sid={request.sid} name={admin_name} token={token}")
    else:
        emit('sys_msg', {'msg': "รหัสแอดมินไม่ถูกต้อง"})

@socketio.on('message')
def handle_message(data):
    msg_text = data.get('text', '').strip()
    target_sid = data.get('target_sid')
    print(f"[DEBUG] MESSAGE from={request.sid} data={data}")
    if not msg_text: return

    #ล็อกอินแอดมิน
    if msg_text == f"/login {ADMIN_PASS}":
        admins.add(request.sid)
        # assign a nicer admin name and record it
        admin_name = get_unique_admin_name()
        admin_names[request.sid] = admin_name
        users[request.sid] = admin_name
        emit('admin_status', {'is_admin' : True, 'name': admin_name})
        emit('sys_msg', {'msg': "คุณเข้าสู่ระบบแอดมินแล้ว"})
        return
    new_msg = None
    if request.sid not in admins:
        new_msg = Message(sender_sid=request.sid, receiver_sid="ADMINS", sender_name=users[request.sid], text=msg_text)
        if not admins:
            emit('sys_msg', {'msg': "ขณะนี้ไม่มีแอดมินออนไลน์ กรุณารอสักครู่"})
        for a_sid in admins:
            emit('new_msg', {'user': users[request.sid], 'text': msg_text, 'from_sid': request.sid}, room=a_sid)
        emit('new_msg', {'user': "คุณ", 'text': msg_text}, room=request.sid)
        # acknowledge to sender that message was received/saved
        # (will be sent after DB commit below)
    else: # แอดมินตอบกลับ
        if target_sid:
            new_msg = Message(sender_sid=request.sid, receiver_sid=target_sid, sender_name="ADMIN", text=msg_text)
            emit('new_msg', {'user': "ADMIN", 'text': msg_text}, room=target_sid)
            for a_sid in admins: # แจ้งแอดมินทุกคนว่าตอบแล้ว
                emit('new_msg', {'user': f"ตอบถึง {users.get(target_sid)}", 'text': msg_text, 'from_sid': target_sid}, room=a_sid)
    if new_msg:
        db.session.add(new_msg)
        db.session.commit()
        print(f"[DEBUG] SAVED msg id={new_msg.id} from={new_msg.sender_sid} to={new_msg.receiver_sid} text={new_msg.text}")
        try:
            emit('message_ack', {'status': 'saved', 'id': new_msg.id}, room=request.sid)
        except Exception as _:
            print('[DEBUG] failed to emit ack to', request.sid)
        # notify all admins of updated user list (in case new users joined)
        for a_sid in admins:
            socketio.emit('user_list', get_all_users_for_admin(a_sid), room=a_sid)

@socketio.on('clear_my_chat')
def clear_chat():
    # ลบเฉพาะฝั่งผู้ใช้(ใน DB และฝั่งแอดมินอยู่ครบ)
    Message.query.filter(
        (Message.sender_sid == request.sid)
    ).update({Message.user_deleted: True})
    db.session.commit()
    emit('clear_screen')


@socketio.on('admin_logout')
def handle_admin_logout():
    # remove this session from admin list but keep token valid for re-login
    if request.sid in admins:
        try:
            admins.remove(request.sid)
            admin_name = admin_names.pop(request.sid, None)
            if admin_name:
                used_admin_names.discard(admin_name)  # free up the name
        except KeyError:
            pass
    emit('admin_status', {'is_admin': False})
    # notify remaining admins of the logout
    socketio.emit('user_list', get_admins_list())


@socketio.on('disconnect')
def handle_disconnect():
    if request.sid in admins:
        try:
            admins.remove(request.sid)
            admin_name = admin_names.pop(request.sid, None)
            if admin_name:
                used_admin_names.discard(admin_name)  # free up the name
        except KeyError:
            pass
    users.pop(request.sid, None)
    # notify remaining admins of the disconnect (updated user list)
    for a_sid in admins:
        socketio.emit('user_list', get_all_users_for_admin(a_sid), room=a_sid)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    is_production = os.environ.get('ENVIRONMENT', 'development') == 'production'
    # Disable the reloader to avoid the server starting twice (which can cause WinError 10048)
    # Allow the Werkzeug dev server only when not in production to avoid RuntimeError
    socketio.run(
        app,
        host='0.0.0.0',
        port=port,
        debug=not is_production,
        use_reloader=False,
        allow_unsafe_werkzeug=not is_production,
    )