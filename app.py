import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import hashlib
import os, random, time
from contextlib import closing
import io, csv
from pathlib import Path


# ---------------- Database setup ----------------
def init_db():
    conn = sqlite3.connect('storebot.db', check_same_thread=False)
    conn.execute('PRAGMA foreign_keys = ON')
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS wheel_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_id INTEGER NOT NULL,
        label TEXT,
        image_url TEXT,
        weight REAL NOT NULL CHECK(weight > 0),
        qty_per_spin INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (item_id) REFERENCES items(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS user_spins (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        wheel_item_id INTEGER NOT NULL,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (wheel_item_id) REFERENCES wheel_items(id)
    );

    CREATE TABLE IF NOT EXISTS spin_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        tokens INTEGER NOT NULL DEFAULT 0
    );
                       
    CREATE TABLE IF NOT EXISTS wheel (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL DEFAULT 'Default Wheel',
        is_active INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS wheel_item (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        wheel_id INTEGER NOT NULL,
        item_id INTEGER NOT NULL,
        label TEXT,
        weight REAL NOT NULL,
        reward_qty INTEGER NOT NULL DEFAULT 1,
        image_path TEXT,
        is_enabled INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (wheel_id) REFERENCES wheel(id),
        FOREIGN KEY (item_id) REFERENCES items(id)
    );

    CREATE TABLE IF NOT EXISTS user_spin_credit (
        user_id INTEGER NOT NULL,
        wheel_id INTEGER NOT NULL,
        credit INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (user_id, wheel_id),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (wheel_id) REFERENCES wheel(id)
    );

    CREATE TABLE IF NOT EXISTS wheel_spin_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        wheel_id INTEGER NOT NULL,
        item_id INTEGER,
        reward_qty INTEGER,
        before_stock INTEGER,
        after_stock INTEGER,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (wheel_id) REFERENCES wheel(id),
        FOREIGN KEY (item_id) REFERENCES items(id)
    );


    CREATE TABLE IF NOT EXISTS items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        stock INTEGER NOT NULL CHECK (stock >= 0)
    );

    CREATE TABLE IF NOT EXISTS requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        qty INTEGER NOT NULL CHECK (qty > 0),
        reason TEXT,
        status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected')),
        returned_qty INTEGER NOT NULL DEFAULT 0,
        approved_by TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS donations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT NOT NULL,
        item_id INTEGER NOT NULL,
        qty INTEGER NOT NULL CHECK (qty > 0),
        note TEXT,
        status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
        approved_by TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- ✅ เพิ่ม sessions table ให้แน่ใจว่าสร้างอัตโนมัติ
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        expires_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'user'
    );
    ''')
    # seed default wheel
    if conn.execute("SELECT COUNT(*) FROM wheel").fetchone()[0] == 0:
        conn.execute("INSERT INTO wheel(name, is_active) VALUES(?,1)", ("Default Wheel",))
        conn.commit()

    # migrate กันพัง (กรณี donations เก่า)
    try:
        conn.execute("ALTER TABLE donations ADD COLUMN status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected'))")
    except:
        pass
    try:
        conn.execute("ALTER TABLE donations ADD COLUMN approved_by TEXT")
    except:
        pass

    # admin default
    try:
        admin_password = hashlib.sha256('admin123'.encode()).hexdigest()
        conn.execute('INSERT INTO users (username, password, role) VALUES (?,?,?)',
                     ('admin', admin_password, 'manager'))
        conn.commit()
    except:
        pass


    conn.close()


def show_image_safe(img_path: str, width: int = 120, caption: str | None = None):
    """แสดงรูปจาก URL หรือไฟล์โลคัล; ถ้าไฟล์ไม่มีจะไม่พัง"""
    if not img_path:
        return
    p = str(img_path).strip()
    try:
        # URL/ data-URI
        if p.startswith(("http://", "https://", "data:image")):
            st.image(p, width=width, caption=caption)
            return
        # ไฟล์โลคัล (ลองหลาย relative paths)
        candidates = [p, os.path.join(".", p), str((Path(__file__).parent / p))]
        for cand in candidates:
            if os.path.exists(cand):
                st.image(cand, width=width, caption=caption)
                return
        st.caption(f"ไม่พบรูป: {p}")
    except Exception:
        st.caption("แสดงรูปไม่สำเร็จ")


# ---------------- Session token (persist after F5) ----------------
import uuid
from datetime import timedelta

def get_db_connection():
    return sqlite3.connect('storebot.db', check_same_thread=False)

def create_session(user_id, ttl_minutes=1440):
    """Create a persistent session token and store in DB."""
    token = uuid.uuid4().hex
    expires_at = (datetime.utcnow() + timedelta(minutes=ttl_minutes)).isoformat(timespec='seconds')
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            expires_at TEXT NOT NULL
        )
    ''')
    conn.execute('INSERT OR REPLACE INTO sessions (token, user_id, expires_at) VALUES (?,?,?)',
                 (token, str(user_id), expires_at))
    conn.commit()
    conn.close()
    return token

def get_user_by_token(token: str):
    if not token:
        return None
    conn = get_db_connection()
    row = conn.execute('''
        SELECT s.user_id, u.username, u.role, s.expires_at
        FROM sessions s JOIN users u ON u.id = s.user_id
        WHERE s.token = ?
    ''', (token,)).fetchone()
    conn.close()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row[3]) < datetime.utcnow():
            return None
    except:
        return None
    return (str(row[0]), row[1], row[2])  # (user_id, username, role)

def clear_session(token: str):
    if not token:
        return
    conn = get_db_connection()
    conn.execute('DELETE FROM sessions WHERE token = ?', (token,))
    conn.commit()
    conn.close()

# ---------------- Helpers ----------------
def weighted_choice(rows):
    """rows: list of dicts with keys: item_id, weight, reward_qty, stock"""
    total = sum(max(0.0, r["weight"]) for r in rows)
    if total <= 0:
        return None
    r = random.random() * total
    acc = 0.0
    for row in rows:
        acc += max(0.0, row["weight"])
        if acc >= r:
            return row
    return rows[-1]

def ensure_assets_dir():
    os.makedirs("wheel_assets", exist_ok=True)
    return "wheel_assets"

def add_credit(user_id:int, wheel_id:int, delta:int):
    with closing(get_db_connection()) as con:
        cur = con.cursor()
        cur.execute("""
            INSERT INTO user_spin_credit(user_id, wheel_id, credit)
            VALUES(?,?,?)
            ON CONFLICT(user_id, wheel_id) DO UPDATE SET credit = credit + ?
        """, (user_id, wheel_id, max(0,delta), delta))
        con.commit()

def get_credit(user_id:int, wheel_id:int)->int:
    with closing(get_db_connection()) as con:
        row = con.execute("SELECT credit FROM user_spin_credit WHERE user_id=? AND wheel_id=?",
                          (user_id, wheel_id)).fetchone()
        return row[0] if row else 0

def spin_once(user_id:int, wheel_id:int=1):
    """ทำธุรกรรมหมุนแบบ atomic เบื้องต้น"""
    con = get_db_connection()
    con.isolation_level = "EXCLUSIVE"
    cur = con.cursor()
    try:
        cur.execute("BEGIN")
        # เครดิต
        row = cur.execute("SELECT credit FROM user_spin_credit WHERE user_id=? AND wheel_id=?",
                          (user_id, wheel_id)).fetchone()
        credit = row[0] if row else 0
        if credit <= 0:
            con.rollback()
            return {"ok": False, "msg": "เครดิตหมุนไม่พอ"}

        # รายการที่สต๊อกพอและเปิดใช้งาน
        rows = cur.execute("""
            SELECT wi.item_id, wi.weight, wi.reward_qty, it.stock
            FROM wheel_item wi
            JOIN items it ON it.id = wi.item_id
            WHERE wi.wheel_id=? AND wi.is_enabled=1 AND it.stock >= wi.reward_qty
        """, (wheel_id,)).fetchall()
        items_avail = [{"item_id":r[0], "weight":r[1], "reward_qty":r[2], "stock":r[3]} for r in rows]

        if not items_avail:
            con.rollback()
            return {"ok": False, "msg": "ของในวงล้อไม่พอในสต๊อก"}

        chosen = weighted_choice(items_avail)
        if not chosen:
            con.rollback()
            return {"ok": False, "msg": "ไม่สามารถสุ่มได้"}

        item_id = chosen["item_id"]
        qty = int(chosen["reward_qty"])

        # หักเครดิต (ป้องกันแข่งกันอัปเดต)
        cur.execute("""
            UPDATE user_spin_credit SET credit = credit - 1
            WHERE user_id=? AND wheel_id=? AND credit > 0
        """, (user_id, wheel_id))
        if cur.rowcount == 0:
            con.rollback()
            return {"ok": False, "msg": "เครดิตหมดแล้ว"}

        # ตัดสต๊อก
        before = cur.execute("SELECT stock FROM items WHERE id=?", (item_id,)).fetchone()[0]
        if before < qty:
            con.rollback()
            return {"ok": False, "msg": "สต๊อกไม่พอขณะทำรายการ"}
        cur.execute("UPDATE items SET stock = stock - ? WHERE id=?", (qty, item_id))
        after = before - qty

        # บันทึกประวัติ
        cur.execute("""
            INSERT INTO wheel_spin_history(user_id, wheel_id, item_id, reward_qty, before_stock, after_stock)
            VALUES(?,?,?,?,?,?)
        """, (user_id, wheel_id, item_id, qty, before, after))

        con.commit()
        return {"ok": True, "item_id": item_id, "qty": qty, "stock_after": after}
    except Exception as e:
        try: con.rollback()
        except: pass
        return {"ok": False, "msg": f"error: {e}"}
    finally:
        con.close()

def format_thai_datetime(datetime_str):
    try:
        dt = datetime.fromisoformat(datetime_str.replace(' ', 'T'))
        return dt.strftime('%d/%m/%Y %H:%M:%S')
    except:
        return datetime_str

def authenticate_user(username, password):
    conn = get_db_connection()
    hashed_password = hashlib.sha256(password.encode()).hexdigest()
    user = conn.execute('SELECT * FROM users WHERE username = ? AND password = ?',
                        (username, hashed_password)).fetchone()
    conn.close()
    return user

def create_user(username, password, role='user'):
    conn = get_db_connection()
    try:
        hashed_password = hashlib.sha256(password.encode()).hexdigest()
        conn.execute('INSERT INTO users (username, password, role) VALUES (?,?,?)',
                     (username, hashed_password, role))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False

TH_STATUS = {'pending': 'รออนุมัติ', 'approved': 'อนุมัติแล้ว', 'rejected': 'ปฏิเสธแล้ว'}

# ---------------- Init ----------------
init_db()
st.set_page_config(page_title="ระบบจัดการคลังสินค้า", page_icon="📦", layout="wide")

# Session state init
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'user_id' not in st.session_state:
    st.session_state.user_id = None
if 'username' not in st.session_state:
    st.session_state.username = None
if 'role' not in st.session_state:
    st.session_state.role = None

# ---------------- UI: Login/Register ----------------
def login_page():
    st.title("🔐 เข้าสู่ระบบ")

    tab1, tab2 = st.tabs(["เข้าสู่ระบบ", "สมัครสมาชิก"])

    with tab1:
        with st.form("login_form"):
            username = st.text_input("ชื่อผู้ใช้")
            password = st.text_input("รหัสผ่าน", type="password")
            submit = st.form_submit_button("เข้าสู่ระบบ")

            if submit:
                user = authenticate_user(username, password)
                if user:
                    st.session_state.logged_in = True
                    st.session_state.user_id = str(user[0])
                    st.session_state.username = user[1]
                    st.session_state.role = user[3]

                    # ✅ สร้างโทเคนและใส่ลง URL เพื่อทนต่อ F5/cold start
                    token = create_session(st.session_state.user_id)
                    try:
                        st.query_params.update({"auth": token})
                    except Exception:
                        st.experimental_set_query_params(auth=token)

                    st.success("เข้าสู่ระบบสำเร็จ!")
                    st.rerun()
                else:
                    st.error("ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")

    with tab2:
        with st.form("register_form"):
            new_username = st.text_input("ชื่อผู้ใช้ใหม่")
            new_password = st.text_input("รหัสผ่าน", type="password")
            confirm_password = st.text_input("ยืนยันรหัสผ่าน", type="password")
            register_submit = st.form_submit_button("สมัครสมาชิก")

            if register_submit:
                if new_password != confirm_password:
                    st.error("รหัสผ่านไม่ตรงกัน")
                elif len(new_password) < 4:
                    st.error("รหัสผ่านต้องมีอย่างน้อย 4 ตัวอักษร")
                elif create_user(new_username, new_password):
                    st.success("สมัครสมาชิกสำเร็จ! กรุณาเข้าสู่ระบบ")
                else:
                    st.error("ชื่อผู้ใช้นี้มีอยู่แล้ว")

# ---------------- UI: User dashboard ----------------
def user_dashboard():
    st.title("📦 ระบบเบิก-คืนสินค้า")
    st.write(f"สวัสดี **{st.session_state.username}**!")

    tab1, tab2, tab3, tab4, tab5 = st.tabs(["🔥 ขอเบิก", "🔄 คืนของ", "💸 ส่งเงินแก๊ง", "🎡 สุ่มไอเทม", "📊 สถานะ"])
    with tab1: request_item_tab()
    with tab2: return_item_tab()
    with tab3: donate_item_tab()
    with tab4: spin_wheel_tab()     
    with tab5: status_tab()


def request_item_tab():
    st.header("🔥 ขอเบิกสินค้า")
    conn = get_db_connection()
    items = conn.execute('SELECT id, name, stock FROM items ORDER BY stock DESC').fetchall()
    conn.close()

    if not items:
        st.warning("ยังไม่มีรายการสินค้า")
        return

    with st.form("request_form"):
        item_options = {f"{item[1]} (คงเหลือ {item[2]})": item[0] for item in items}
        selected_item = st.selectbox("เลือกสินค้าที่ต้องการเบิก", options=list(item_options.keys()))
        qty = st.number_input("จำนวนที่ต้องการ", min_value=1, value=1)
        reason = st.text_area("เหตุผล (ไม่บังคับ)")
        submit = st.form_submit_button("ส่งคำขอ")

        if submit:
            item_id = item_options[selected_item]
            conn = get_db_connection()
            item = conn.execute('SELECT name, stock FROM items WHERE id = ?', (item_id,)).fetchone()
            if qty > item[1]:
                st.error(f"ขอได้สูงสุด {item[1]} สำหรับ {item[0]}")
                conn.close()
            else:
                cur = conn.execute(
                    'INSERT INTO requests (user_id, item_id, qty, reason, status) VALUES (?,?,?,?,?)',
                    (st.session_state.user_id, item_id, qty, reason, 'pending')
                )
                conn.commit()
                last_id = cur.lastrowid
                conn.close()
                st.success(f"📝 สร้างคำขอ #{last_id} ขอเบิก **{item[0]}** จำนวน {qty} (รออนุมัติ)")

def return_item_tab():
    st.header("🔄 คืนสินค้า")
    conn = get_db_connection()
    requests = conn.execute('''
        SELECT r.id, it.name, r.qty, r.returned_qty
        FROM requests r JOIN items it ON it.id = r.item_id
        WHERE r.user_id = ? AND r.status = 'approved' AND r.returned_qty < r.qty
        ORDER BY r.id DESC
    ''', (st.session_state.user_id,)).fetchall()
    conn.close()

    if not requests:
        st.info("คุณไม่มีคำขอที่ค้างอยู่สำหรับคืนสินค้า")
        return

    with st.form("return_form"):
        request_options = {}
        for req in requests:
            remaining = req[2] - req[3]
            request_options[f"#{req[0]} • {req[1]} (คืนได้อีก {remaining})"] = req[0]

        selected_request = st.selectbox("เลือกคำขอที่จะคืนสินค้า", options=list(request_options.keys()))
        return_qty = st.number_input("จำนวนที่จะคืน", min_value=1, value=1)
        submit = st.form_submit_button("คืนสินค้า")

        if submit:
            request_id = request_options[selected_request]
            conn = get_db_connection()
            req = conn.execute('''
                SELECT r.*, it.name FROM requests r JOIN items it ON it.id = r.item_id
                WHERE r.id = ? AND r.user_id = ?
            ''', (request_id, st.session_state.user_id)).fetchone()

            remaining = req[3] - req[6]  # qty - returned_qty
            if return_qty > remaining:
                st.error(f"คืนได้สูงสุด {remaining}")
                conn.close()
            else:
                conn.execute('UPDATE items SET stock = stock + ? WHERE id = ?', (return_qty, req[2]))
                conn.execute('UPDATE requests SET returned_qty = returned_qty + ? WHERE id = ?', (return_qty, request_id))
                conn.commit()
                conn.close()
                new_remaining = remaining - return_qty
                st.success(f"↩️ คืนสินค้าคำขอ #{request_id} • {req[9]} จำนวน {return_qty} {'(คืนครบแล้ว)' if new_remaining == 0 else ''}")
                st.rerun()

def donate_item_tab():
    st.header("💸 ส่งเงินแก๊ง (รออนุมัติ)")

    conn = get_db_connection()
    items = conn.execute('SELECT id, name, stock FROM items ORDER BY name').fetchall()
    conn.close()

    if not items:
        st.warning("ยังไม่มีรายการสินค้า")
        return

    with st.form("donate_form"):
        item_options = {f"{item[1]} (คงเหลือ {item[2]})": item[0] for item in items}
        selected_item = st.selectbox("เลือกสินค้าที่จะส่งเข้าแก๊ง", options=list(item_options.keys()))
        qty = st.number_input("จำนวนที่จะส่ง", min_value=1, value=1)
        note = st.text_area("บันทึก/หมายเหตุ (ไม่บังคับ)")

        submit = st.form_submit_button("ส่งคำขอเติมสต๊อก")

        if submit:
            item_id = item_options[selected_item]
            conn = get_db_connection()
            # บันทึกเป็น pending รอแอดมินอนุมัติ
            cur = conn.execute(
                'INSERT INTO donations (user_id, item_id, qty, note, status) VALUES (?,?,?,?,?)',
                (st.session_state.user_id, item_id, qty, note, 'pending')
            )
            conn.commit()
            rid = cur.lastrowid
            item_name = conn.execute('SELECT name FROM items WHERE id = ?', (item_id,)).fetchone()[0]
            conn.close()

            st.success(f"📝 สร้างคำขอเติมสต๊อก #{rid} • {item_name} × {qty} (รออนุมัติ)")
            st.rerun()


def status_tab():
    st.header("📊 สถานะคำขอของคุณ")
    conn = get_db_connection()
    requests = conn.execute('''
        SELECT r.id, it.name, r.qty, r.returned_qty, r.status, r.created_at
        FROM requests r JOIN items it ON it.id = r.item_id
        WHERE r.user_id = ?
        ORDER BY r.id DESC
        LIMIT 20
    ''', (st.session_state.user_id,)).fetchall()
    conn.close()

    if not requests:
        st.info("คุณยังไม่มีประวัติการขอเบิก")
        return

    df = pd.DataFrame(requests, columns=['ID', 'สินค้า', 'จำนวน', 'คืนแล้ว', 'สถานะ', 'วันที่สร้าง'])
    df['สถานะ'] = df['สถานะ'].map(TH_STATUS)
    df['วันที่สร้าง'] = df['วันที่สร้าง'].apply(format_thai_datetime)
    st.dataframe(df, use_container_width=True)

    st.markdown("---")
    st.subheader("ประวัติส่งเงินแก๊ง/เติมสต๊อกของฉัน")
    conn = get_db_connection()
    dons = conn.execute('''
        SELECT d.id, it.name, d.qty, d.status, d.created_at
        FROM donations d JOIN items it ON it.id = d.item_id
        WHERE d.user_id = ?
        ORDER BY d.id DESC
        LIMIT 20
    ''', (st.session_state.user_id,)).fetchall()
    conn.close()

    if dons:
        df2 = pd.DataFrame(dons, columns=['ID','สินค้า','จำนวน','สถานะ','วันที่สร้าง'])
        df2['สถานะ'] = df2['สถานะ'].map({'pending':'รออนุมัติ','approved':'อนุมัติแล้ว','rejected':'ปฏิเสธแล้ว'})
        df2['วันที่สร้าง'] = df2['วันที่สร้าง'].apply(format_thai_datetime)
        st.dataframe(df2, use_container_width=True)
    else:
        st.info("ยังไม่มีประวัติส่งเงินแก๊ง")

IMG_SIZE = 120   # <<< กำหนดขนาดรูปมาตรฐานตรงนี้

def spin_wheel_tab():
    st.header("🎡 สุ่มไอเทม")
    wheel_id = 1

    # === Flash จากรอบก่อน (ข้อความ + รูป) ===
    flash     = st.session_state.pop("spin_flash", None)
    flash_ty  = st.session_state.pop("spin_flash_type", "success")
    flash_img = st.session_state.pop("spin_flash_img", None)
    if flash:
        getattr(st, flash_ty)(flash)
    if flash_img:
        show_image_safe(img_path, width=IMG_SIZE, caption="รางวัลรอบล่าสุด")

    # เครดิต
    credit = get_credit(int(st.session_state.user_id), wheel_id)
    c1, c2 = st.columns([1, 1])
    with c1: st.metric("สิทธิ์หมุนคงเหลือ", credit)
    # with c2: st.caption("ระบบจะสุ่มเฉพาะรายการที่ **สต๊อกเพียงพอ** เท่านั้น")

    # โหลดรายการบนวงล้อ
    with closing(get_db_connection()) as con:
        rows = con.execute("""
            SELECT wi.id, it.name, wi.weight, wi.reward_qty, it.stock,
                   COALESCE(wi.label,''), COALESCE(wi.image_path,''), wi.item_id
            FROM wheel_item wi
            JOIN items it ON it.id = wi.item_id
            WHERE wi.wheel_id=? AND wi.is_enabled=1
            ORDER BY it.name
        """, (wheel_id,)).fetchall()

        ok_rows = con.execute("""
            SELECT wi.weight, wi.reward_qty, it.stock, COALESCE(wi.label,''), it.name, COALESCE(wi.image_path,'')
            FROM wheel_item wi JOIN items it ON it.id=wi.item_id
            WHERE wi.wheel_id=? AND wi.is_enabled=1 AND it.stock >= wi.reward_qty
        """, (wheel_id,)).fetchall()

    # === การ์ดไอเทม ===
    st.markdown("### รายการในวงล้อ")
    if rows:
        total_w = sum(max(0.0, r[0]) for r in ok_rows) or 1.0
        grid = st.columns(3)
        for i, r in enumerate(rows):
            _, name, w, qty, stock, label, img, _ = r
            pct = (w / total_w) * 100.0 if stock >= qty and w > 0 and total_w > 0 else 0
            with grid[i % 3]:
                with st.container(border=True):
                    show_image_safe(img, width=IMG_SIZE)
                    st.markdown(f"**{label or name}**")
                    st.caption(f"โอกาศอยู่ในมือคุณ ผีพนันทั้งหลาย")
                    # st.caption(f"โอกาศได้: {w:g} %")
                    # st.progress(min(int(pct), 100), text=f"โอกาศได้ : {pct:.2f}%")
    else:
        st.info("ยังไม่มีรายการบนวงล้อ")

    st.divider()

        # === ปุ่มหมุน ===
    ph_img, ph_text, ph_prog = st.empty(), st.empty(), st.empty()
    ph_flash = st.empty()  # ✅ placeholder สำหรับ flash ข้อความ/รูป

    candidates = [{"label": r[5] or r[1], "image": r[6] or ""} for r in rows]

    if st.button("🎰 หมุนเลย", type="primary", disabled=(credit <= 0), use_container_width=True):
        if not candidates:
            ph_flash.warning("ไม่มีรายการให้สุ่ม หรือสต๊อกไม่เพียงพอ")
        else:
            spins, base, step = 28, 0.05, 0.015
            for i in range(spins):
                pick = random.choice(candidates)
                # if pick["image"]:
                #     try: ph_img.image(pick["image"], width=IMG_SIZE)
                #     except: ph_img.empty()
                ph_text.markdown(f"## 🎲 {pick['label']}")
                ph_prog.progress(int((i+1)/spins*100), text="กำลังหมุน…")
                time.sleep(base + step*i)

            ph_text.markdown("## ✅ กำลังตัดสต๊อก…")

            # ทำธุรกรรมสุ่มจริง
            res = spin_once(int(st.session_state.user_id), wheel_id)
            if res.get("ok"):
                with closing(get_db_connection()) as con:
                    it  = con.execute("SELECT name FROM items WHERE id=?", (res["item_id"],)).fetchone()
                    img = con.execute("SELECT COALESCE(image_path,'') FROM wheel_item WHERE wheel_id=? AND item_id=? AND is_enabled=1 ORDER BY id DESC LIMIT 1",
                                      (wheel_id, res["item_id"])).fetchone()
                won = it[0] if it else f"ID {res['item_id']}"
                img_path = (img[0] if img else "") or ""

                # ✅ แสดงผลลัพธ์ตรงนี้เลย ไม่ต้อง rerun
                ph_flash.success(f"คุณได้ **{won}** × {res['qty']} (สต๊อกคงเหลือ {res['stock_after']})")
                if img_path:
                    show_image_safe(img_path, width=IMG_SIZE, caption="รางวัลรอบล่าสุด")

                st.balloons()
            else:
                ph_flash.error(res.get("msg", "สุ่มไม่สำเร็จ"))


    st.divider()
    
    # === ประวัติ ===
    # === ปุ่มลบประวัติของฉัน ===
    with st.expander("🗑️ ล้างประวัติการหมุนของฉัน", expanded=False):
        st.caption("เหมาะสำหรับล้างข้อมูลตอนเทส ฟีเจอร์นี้ลบเฉพาะประวัติของบัญชีที่กำลังล็อกอินอยู่")
        colx, coly = st.columns([1,1])
        with colx:
            confirm = st.checkbox("ฉันยืนยันว่าจะลบประวัติเหล่านี้ทั้งหมด", key="clear_my_spin_confirm")
        with coly:
            really = st.selectbox("พิมพ์/เลือกคำว่า YES เพื่อยืนยัน", ["", "YES"], key="clear_my_spin_yes")

        if st.button("ลบประวัติของฉันทันที", type="secondary", use_container_width=True,
                    disabled=not(confirm and really == "YES")):
            with closing(get_db_connection()) as con:
                con.execute(
                    "DELETE FROM wheel_spin_history WHERE user_id=? AND wheel_id=?",
                    (int(st.session_state.user_id), 1)  # 1 = wheel_id ของคุณ
                )
                con.commit()
            st.success("ลบประวัติการหมุนของคุณเรียบร้อยแล้ว")
            st.rerun()

    st.markdown("### 🗂 ประวัติการหมุนของฉัน")
    limit = st.slider("จำนวนแถวที่แสดง", 10, 200, 50, 10)
    with closing(get_db_connection()) as con:
        hist = con.execute("""
            SELECT h.created_at, i.name, h.reward_qty, h.before_stock, h.after_stock, COALESCE(wi.image_path,'')
            FROM wheel_spin_history h
            LEFT JOIN items i ON i.id=h.item_id
            LEFT JOIN wheel_item wi ON wi.item_id=h.item_id AND wi.wheel_id=h.wheel_id
            WHERE h.user_id=? AND h.wheel_id=?
            ORDER BY h.id DESC LIMIT ?
        """, (int(st.session_state.user_id), wheel_id, int(limit))).fetchall()

    if hist:
        data = [{
            "เวลา": format_thai_datetime(r[0]) if 'format_thai_datetime' in globals() else r[0],
            "ไอเทม": r[1] or "-",
            "จำนวน": r[2],
            "สต๊อกก่อน": r[3],
            "สต๊อกหลัง": r[4],
            "รูป": r[5] or "",
        } for r in hist]
        st.dataframe(data, use_container_width=True,
            column_config={"รูป": st.column_config.ImageColumn("รูป", width=IMG_SIZE)})
    else:
        st.info("ยังไม่มีประวัติการหมุน")
    







# ---------------- UI: Admin ----------------
def admin_dashboard():
    st.title("🛠 แผงผู้ดูแล (StoreManager)")
    st.write(f"สวัสดี **{st.session_state.username}** (ผู้จัดการ)")

    tab1, tab2, tab3, tab4, tab5, tab6, tab7 = st.tabs([
    "➕ เพิ่มสินค้า","🔍 ตรวจคำขอ","🧾 ตรวจคำขอเติมสต๊อก","📦 สต๊อก","⚙️ จัดการ","🗂 ประวัติทั้งหมด","🎡 วงล้อสุ่ม"
    ])
    with tab1: add_item_tab()
    with tab2: review_requests_tab()
    with tab3: review_donations_tab()
    with tab4: stock_tab()
    with tab5: manage_items_tab()
    with tab6: all_logs_tab()
    with tab7: wheel_admin_tab()
        
def wheel_admin_tab():
    st.subheader("🎡 จัดการวงล้อสุ่ม")
    wheel_id = 1

    # 1) จัดรายการบนวงล้อ
    st.markdown("#### รายการบนวงล้อ")
    with closing(get_db_connection()) as con:
        items = con.execute("SELECT id, name, stock FROM items ORDER BY name").fetchall()
        wheel_rows = con.execute("""
            SELECT wi.id, it.name, wi.weight, wi.reward_qty, wi.is_enabled,
                   COALESCE(wi.label,''), COALESCE(wi.image_path,'')
            FROM wheel_item wi JOIN items it ON it.id=wi.item_id
            WHERE wi.wheel_id=? ORDER BY it.name
        """, (wheel_id,)).fetchall()

        # ใช้สำหรับคำนวณ % จาก weight เฉพาะตัวที่สต๊อกพอและเปิดใช้งาน
        ok_rows = con.execute("""
            SELECT wi.weight, wi.reward_qty, it.stock, COALESCE(wi.label,''), it.name
            FROM wheel_item wi JOIN items it ON it.id=wi.item_id
            WHERE wi.wheel_id=? AND wi.is_enabled=1 AND it.stock >= wi.reward_qty
        """, (wheel_id,)).fetchall()

    # แสดง % โดยประมาณจาก weight
    if ok_rows:
        total_w = sum(max(0.0, r[0]) for r in ok_rows) or 1.0
        st.caption("เปอร์เซ็นต์โดยประมาณ (คำนวณจากน้ำหนักของรายการที่ **สต๊อกพอ** ในตอนนี้):")
        for w, rq, stock, lbl, nm in ok_rows:
            pct = 100.0 * max(0.0, w) / total_w
            st.write(f"- {lbl or nm}: ~{pct:.2f}% (ได้ {rq} ชิ้น · คงเหลือ {stock})")

    # แสดงรายการที่มีให้แก้ไข
    if wheel_rows:
        for wid, name, w, qty, on, label, img in wheel_rows:
            with st.expander(f"• {label or name} (weight={w:g}, ได้ {qty}, {'เปิด' if on else 'ปิด'})"):
                col1, col2 = st.columns([2,1])

                with col1:
                    new_label = st.text_input("Label (โชว์บนวงล้อ)", value=label, key=f"wlbl_{wid}")
                    new_weight = st.number_input("น้ำหนัก (ยิ่งมากยิ่งออกง่าย)", value=float(w),
                                                 min_value=0.0, step=0.1, key=f"ww_{wid}")
                    new_qty = st.number_input("จำนวนที่ได้ต่อครั้ง", value=int(qty), min_value=1, step=1, key=f"wq_{wid}")
                    new_on  = st.checkbox("เปิดใช้งาน", value=bool(on), key=f"won_{wid}")

                with col2:
                    if img:
                        show_image_safe(img, width=120)
                    st.caption("อัปโหลดไฟล์หรือใส่ลิงก์รูปก็ได้")
                    up = st.file_uploader("อัปโหลดรูป", type=["png","jpg","jpeg","webp"], key=f"up_{wid}")
                    new_img = st.text_input("หรือวาง URL/path รูป", value=img, key=f"wimg_{wid}")

                    # ถ้ามีไฟล์อัปโหลด ให้เซฟลงโฟลเดอร์และใช้ path นั้น
                    if up is not None:
                        assets = ensure_assets_dir()  # มีฟังก์ชันนี้อยู่แล้วในไฟล์ของคุณ
                        safe_name = f"{wid}_{int(time.time())}_{up.name}".replace(" ", "_")
                        save_path = os.path.join(assets, safe_name)
                        with open(save_path, "wb") as f:
                            f.write(up.read())
                        new_img = save_path
                        st.success("บันทึกรูปแล้ว")
                        show_image_safe()(new_img, width=120)

                c = st.columns(3)
                if c[0].button("💾 บันทึก", key=f"wsv_{wid}"):
                    with closing(get_db_connection()) as con:
                        con.execute("""
                            UPDATE wheel_item
                            SET label=?, weight=?, reward_qty=?, image_path=?, is_enabled=?
                            WHERE id=?
                        """, (new_label.strip(), float(new_weight), int(new_qty),
                              new_img.strip(), 1 if new_on else 0, wid))
                        con.commit()
                    st.success("บันทึกแล้ว")
                    st.rerun()

                if c[1].button("🗑 ลบ", key=f"wdel_{wid}"):
                    with closing(get_db_connection()) as con:
                        con.execute("DELETE FROM wheel_item WHERE id=?", (wid,))
                        con.commit()
                    st.success("ลบรายการแล้ว")
                    st.rerun()

    # เพิ่มรายการใหม่
    st.markdown("#### ➕ เพิ่มรายการใหม่ลงวงล้อ")
    if not items:
        st.info("ยังไม่มีสินค้าในคลัง — ไปเพิ่มในแท็บ '➕ เพิ่มสินค้า' ก่อน")
    else:
        colA, colB, colC = st.columns([2,1,1])
        with colA:
            sel   = st.selectbox("เลือกสินค้า", options=items,
                                 format_func=lambda r: f"{r[1]} (คงเหลือ {r[2]})")
            label = st.text_input("Label (ไม่ใส่ = ใช้ชื่อสินค้า)")
            st.caption("อัปโหลดไฟล์หรือใส่ลิงก์รูปก็ได้")
            up_new = st.file_uploader("อัปโหลดรูป", type=["png","jpg","jpeg","webp"], key="up_new_wheel")
            img    = st.text_input("หรือวาง URL/path รูป")
        with colB:
            weight = st.number_input("น้ำหนัก", min_value=0.0, value=10.0, step=0.5)
        with colC:
            qty    = st.number_input("จำนวนที่ได้ต่อครั้ง", min_value=1, value=1, step=1)

        if st.button("เพิ่มลงวงล้อ", use_container_width=True):
            img_path = img.strip()
            if up_new is not None:
                assets = ensure_assets_dir()
                safe_name = f"new_{int(time.time())}_{up_new.name}".replace(" ", "_")
                save_path = os.path.join(assets, safe_name)
                with open(save_path, "wb") as f:
                    f.write(up_new.read())
                img_path = save_path

            with closing(get_db_connection()) as con:
                con.execute("""
                    INSERT INTO wheel_item(wheel_id, item_id, label, weight, reward_qty, image_path, is_enabled)
                    VALUES(?,?,?,?,?,?,1)
                """, (wheel_id, sel[0], label.strip(), float(weight), int(qty), img_path))
                con.commit()
            st.success("เพิ่มลงวงล้อแล้ว")
            st.rerun()

    st.divider()

    # 2) เติมเครดิตให้ผู้ใช้
    st.markdown("#### 🎟 เครดิตการหมุน")
    with closing(get_db_connection()) as con:
        users = con.execute("SELECT id, username, role FROM users ORDER BY username").fetchall()
    if users:
        u = st.selectbox("ผู้ใช้", options=users, format_func=lambda r: f"{r[1]} ({r[2]})")
        delta = st.number_input("จำนวนเครดิต (+ เพิ่ม / - ลด)", value=1, step=1)
        if st.button("อัปเดตเครดิต", use_container_width=True):
            add_credit(int(u[0]), wheel_id, int(delta))
            st.success("อัปเดตเครดิตสำเร็จ")

    # ดูยอดเครดิตทั้งหมด
    with closing(get_db_connection()) as con:
        rows = con.execute("""
            SELECT u.username, c.credit
            FROM user_spin_credit c JOIN users u ON u.id=c.user_id
            WHERE c.wheel_id=?
            ORDER BY u.username
        """, (wheel_id,)).fetchall()
    if rows:
        st.table([{"ผู้ใช้":r[0], "เครดิต":r[1]} for r in rows])

    st.divider()

    # 3) ประวัติการหมุน
    st.markdown("#### 🗂 ประวัติการหมุนล่าสุด")
    with closing(get_db_connection()) as con:
        hist = con.execute("""
            SELECT h.id, u.username, i.name, h.reward_qty,
                   h.before_stock, h.after_stock, h.created_at
            FROM wheel_spin_history h
            LEFT JOIN users u ON u.id=h.user_id
            LEFT JOIN items i ON i.id=h.item_id
            WHERE h.wheel_id=? ORDER BY h.id DESC LIMIT 200
        """, (wheel_id,)).fetchall()
    if hist:
        st.dataframe([{
            "ID":x[0], "ผู้ใช้":x[1], "ไอเทม":x[2], "จำนวน":x[3],
            "ก่อน":x[4], "หลัง":x[5], "เวลา":format_thai_datetime(x[6])
        } for x in hist], use_container_width=True)
    else:
        st.info("ยังไม่มีประวัติการหมุน")



def add_item_tab():
    st.header("➕ เพิ่มสินค้าใหม่")
    with st.form("add_item_form"):
        name = st.text_input("ชื่อสินค้า")
        stock = st.number_input("สต๊อกเริ่มต้น", min_value=0, value=0)
        submit = st.form_submit_button("เพิ่มสินค้า")
        if submit:
            if not name.strip():
                st.error("กรุณาใส่ชื่อสินค้า")
            else:
                conn = get_db_connection()
                try:
                    conn.execute('INSERT INTO items (name, stock) VALUES (?,?)', (name.strip(), stock))
                    conn.commit()
                    conn.close()
                    st.success(f"✅ เพิ่มสินค้า **{name}** (สต๊อก {stock})")
                    st.rerun()
                except sqlite3.IntegrityError:
                    conn.close()
                    st.error("มีชื่อสินค้านี้อยู่แล้ว")

# optional auto-refresh for admin request queue
def review_requests_tab():
    st.header("🔍 ตรวจสอบคำขอ")

    # ทางเลือก: เปิดใช้ auto-refresh ทุก 5 วิ (ต้องติดตั้ง streamlit-extras)
    try:
        from streamlit_extras.app_refresh import st_autorefresh
        st_autorefresh(interval=5000, key="auto_refresh_requests")
    except Exception:
        pass  # ถ้าไม่ได้ติดตั้ง จะข้ามไป

    conn = get_db_connection()
    pending_requests = conn.execute('''
        SELECT r.id, r.qty, r.user_id, r.reason, it.name, it.stock, u.username, it.id
        FROM requests r 
        JOIN items it ON it.id = r.item_id
        JOIN users u ON u.id = r.user_id
        WHERE r.status = 'pending'
        ORDER BY r.id ASC
    ''').fetchall()

    if not pending_requests:
        st.info("ไม่มีคำขอค้าง")
        conn.close()
        return

    for req in pending_requests:
        req_id, qty, user_id, reason, item_name, item_stock, username, item_id = req
        with st.container():
            st.write(f"**คำขอ #{req_id}**")
            col1, col2, col3 = st.columns([2, 1, 1])

            with col1:
                st.write(f"• ผู้ขอ: {username}")
                st.write(f"• สินค้า: **{item_name}** x{qty}")
                st.write(f"• เหตุผล: {reason or '-'}")
                st.write(f"• สต๊อกปัจจุบัน: {item_stock}")

            with col2:
                if st.button("✅ อนุมัติ", key=f"approve_{req_id}"):
                    if item_stock >= qty:
                        # ใช้ id แทน name เพื่อความปลอดภัย
                        conn.execute('UPDATE items SET stock = stock - ? WHERE id = ?', (qty, item_id))
                        conn.execute('UPDATE requests SET status = ?, approved_by = ? WHERE id = ?',
                                     ('approved', st.session_state.user_id, req_id))
                        conn.commit()
                        st.success(f"อนุมัติคำขอ #{req_id}")
                        st.rerun()
                    else:
                        st.error("สต๊อกไม่พอ")

            with col3:
                if st.button("❌ ปฏิเสธ", key=f"reject_{req_id}"):
                    conn.execute('UPDATE requests SET status = ? WHERE id = ?', ('rejected', req_id))
                    conn.commit()
                    st.success(f"ปฏิเสธคำขอ #{req_id}")
                    st.rerun()

            st.divider()
    conn.close()

def stock_tab():
    st.header("📦 สต๊อกปัจจุบัน")
    conn = get_db_connection()
    items = conn.execute('SELECT id, name, stock FROM items ORDER BY stock DESC, name ASC').fetchall()
    conn.close()

    if not items:
        st.warning("ยังไม่มีรายการสินค้า")
        return

    df = pd.DataFrame(items, columns=['ID', 'ชื่อสินค้า', 'คงเหลือ'])
    st.dataframe(df, use_container_width=True)

    total_items = len(items)
    low_stock = sum(1 for item in items if item[2] <= 5)
    col1, col2 = st.columns(2)
    with col1: st.metric("รายการสินค้าทั้งหมด", total_items)
    with col2: st.metric("สินค้าใกล้หมด (≤5)", low_stock)

def manage_items_tab():
    st.header("⚙️ จัดการสินค้า")
    conn = get_db_connection()
    items = conn.execute('SELECT id, name, stock FROM items ORDER BY name ASC').fetchall()
    if not items:
        st.warning("ยังไม่มีรายการสินค้า")
        conn.close()
        return

    for item in items:
        item_id, name, stock = item
        with st.expander(f"📦 {name} (คงเหลือ {stock})"):
            col1, col2 = st.columns(2)
            with col1:
                new_name = st.text_input("ชื่อใหม่", value=name, key=f"name_{item_id}")
                new_stock = st.number_input("สต๊อกใหม่", value=stock, min_value=0, key=f"stock_{item_id}")
                if st.button("💾 บันทึก", key=f"save_{item_id}"):
                    if new_name.strip():
                        try:
                            conn.execute('UPDATE items SET name = ?, stock = ? WHERE id = ?',
                                         (new_name.strip(), new_stock, item_id))
                            conn.commit()
                            st.success("บันทึกแล้ว")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("ชื่อสินค้าซ้ำ")
                    else:
                        st.error("ชื่อสินค้าห้ามว่าง")
            with col2:
                st.write(f"ID: {item_id}")
                related_requests = conn.execute('SELECT COUNT(*) FROM requests WHERE item_id = ?', (item_id,)).fetchone()[0]
                if related_requests > 0:
                    st.warning(f"มีประวัติคำขอ {related_requests} รายการ")
                else:
                    if st.button("🗑 ลบสินค้า", key=f"delete_{item_id}"):
                        conn.execute('DELETE FROM items WHERE id = ?', (item_id,))
                        conn.commit()
                        st.success(f"ลบ {name} แล้ว")
                        st.rerun()
    conn.close()

def review_donations_tab():
    st.header("🧾 ตรวจคำขอส่งเงินแก๊ง")
    # auto-refresh (มีได้/ไม่มีได้)
    try:
        from streamlit_extras.app_refresh import st_autorefresh
        st_autorefresh(interval=5000, key="auto_refresh_donations")
    except Exception:
        pass

    conn = get_db_connection()
    rows = conn.execute('''
        SELECT d.id, u.username, it.name, d.qty, d.note, d.created_at, it.id
        FROM donations d
        JOIN users u ON u.id = d.user_id
        JOIN items it ON it.id = d.item_id
        WHERE d.status = 'pending'
        ORDER BY d.id ASC
    ''').fetchall()

    if not rows:
        st.info("ไม่มีคำขอเติมสต๊อกค้าง")
        conn.close()
        return

    for r in rows:
        did, username, item_name, qty, note, created_at, item_id = r
        with st.container():
            st.write(f"**คำขอเติมสต๊อก #{did}**")
            st.write(f"• ผู้ส่ง: {username}")
            st.write(f"• สินค้า: **{item_name}** × {qty}")
            st.write(f"• เหตุผล/หมายเหตุ: {note or '-'}")
            st.write(f"• เวลา: {format_thai_datetime(created_at)}")

            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ อนุมัติ", key=f"approve_donation_{did}"):
                    # อนุมัติแล้วค่อยเพิ่มสต๊อก
                    conn.execute('UPDATE items SET stock = stock + ? WHERE id = ?', (qty, item_id))
                    conn.execute("UPDATE donations SET status = 'approved', approved_by = ? WHERE id = ?",
                                 (st.session_state.user_id, did))
                    conn.commit()
                    st.success(f"อนุมัติคำขอเติมสต๊อก #{did} (+{qty})")
                    st.rerun()
            with c2:
                if st.button("❌ ปฏิเสธ", key=f"reject_donation_{did}"):
                    conn.execute("UPDATE donations SET status = 'rejected', approved_by = ? WHERE id = ?",
                                 (st.session_state.user_id, did))
                    conn.commit()
                    st.success(f"ปฏิเสธคำขอเติมสต๊อก #{did}")
                    st.rerun()

            st.divider()

    conn.close()

def all_logs_tab():
    import sqlite3
    st.header("🗂 ประวัติทั้งหมด (Transaction Logs)")

    # ---------- Filters ----------
    col1, col2, col3 = st.columns([1,1,1.2])
    with col1:
        date_range = st.date_input("ช่วงวันที่", value=None)
    with col2:
        types = st.multiselect("ประเภท", ["เบิกสินค้า", "เติมสต๊อก"], default=["เบิกสินค้า", "เติมสต๊อก"])
    with col3:
        statuses = st.multiselect("สถานะ", ["รออนุมัติ", "อนุมัติแล้ว", "ปฏิเสธแล้ว"], default=[])

    q = st.text_input("ค้นหา (ชื่อผู้ใช้/ชื่อสินค้า/หมายเหตุ)", value="").strip()

    # ---------- Load ----------
    conn = get_db_connection()

    # requests (เบิก)
    req_rows = conn.execute('''
        SELECT r.id, r.created_at, u.username, it.name, r.qty, r.status, r.approved_by,
               'เบิกสินค้า' AS type, r.reason AS note
        FROM requests r
        JOIN users u ON u.id = r.user_id
        JOIN items it ON it.id = r.item_id
        ORDER BY r.id DESC
    ''').fetchall()

    # donations (เติม)
    try:
        don_rows = conn.execute('''
            SELECT d.id, d.created_at, u.username, it.name, d.qty, d.status, d.approved_by,
                   'เติมสต๊อก' AS type, d.note AS note
            FROM donations d
            JOIN users u ON u.id = d.user_id
            JOIN items it ON it.id = d.item_id
            ORDER BY d.id DESC
        ''').fetchall()
    except sqlite3.OperationalError:
        # fallback ถ้าตาราง donations เก่ายังไม่มี status/approved_by
        don_rows = conn.execute('''
            SELECT d.id, d.created_at, u.username, it.name, d.qty,
                   'approved' as status, NULL as approved_by,
                   'เติมสต๊อก' AS type, d.note AS note
            FROM donations d
            JOIN users u ON u.id = d.user_id
            JOIN items it ON it.id = d.item_id
            ORDER BY d.id DESC
        ''').fetchall()

    conn.close()

    import pandas as pd
    df_req = pd.DataFrame(req_rows, columns=["ID","วันที่","ผู้ใช้","สินค้า","จำนวน","สถานะ","ผู้อนุมัติ","ประเภท","หมายเหตุ"])
    df_don = pd.DataFrame(don_rows, columns=["ID","วันที่","ผู้ใช้","สินค้า","จำนวน","สถานะ","ผู้อนุมัติ","ประเภท","หมายเหตุ"])

    df = pd.concat([df_req, df_don], ignore_index=True)
    if df.empty:
        st.info("ยังไม่มีประวัติรายการ")
        return

    # แปลงสถานะภาษาไทยสำหรับแสดงผล/กรอง
    map_th = {"pending":"รออนุมัติ","approved":"อนุมัติแล้ว","rejected":"ปฏิเสธแล้ว"}
    df["สถานะ(TH)"] = df["สถานะ"].map(map_th).fillna(df["สถานะ"])
    # แปลงเวลา
    df["วันที่"] = df["วันที่"].apply(format_thai_datetime)

    # ---------- Apply filters ----------
    # ประเภท
    if types:
        df = df[df["ประเภท"].isin(types)]
    # สถานะ
    if statuses:
        df = df[df["สถานะ(TH)"].isin(statuses)]
    # ช่วงวัน (ลดรูป: เทียบแค่ date ส่วนหน้า)
    if date_range:
        if isinstance(date_range, tuple) or isinstance(date_range, list):
            start = date_range[0]
            end = date_range[-1]
        else:
            start = end = date_range
        # แปลงคอลัมน์ "วันที่" ที่เป็น text ไทยกลับเป็นวันที่สำหรับเทียบช่วง
        def to_date(s):
            try:
                # s รูปแบบ dd/mm/YYYY HH:MM:SS
                return datetime.strptime(s, "%d/%m/%Y %H:%M:%S").date()
            except:
                return None
        df_dates = df["วันที่"].apply(to_date)
        mask = (df_dates >= start) & (df_dates <= end)
        df = df[mask]

    # คีย์เวิร์ด
    if q:
        q_lower = q.lower()
        df = df[df.apply(lambda r:
                         q_lower in str(r["ผู้ใช้"]).lower() or
                         q_lower in str(r["สินค้า"]).lower() or
                         q_lower in str(r["หมายเหตุ"]).lower() or
                         q_lower in str(r["ID"]).lower()
                         , axis=1)]

    # ---------- Show ----------
    show_df = df[["ID","ประเภท","ผู้ใช้","สินค้า","จำนวน","สถานะ(TH)","ผู้อนุมัติ","วันที่","หมายเหตุ"]].copy()
    show_df = show_df.sort_values(by=["วันที่","ID"], ascending=[False, False]).reset_index(drop=True)
    st.dataframe(show_df, use_container_width=True, hide_index=True)

    # ดาวน์โหลด CSV
    csv = show_df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ ดาวน์โหลด CSV (ประวัติทั้งหมด)", data=csv,
                       file_name="all_transactions.csv", mime="text/csv", use_container_width=True)



# ---------------- Main ----------------
def main():
    # 1) Auto-login จาก query param ?auth=
    try:
        qp = st.query_params
        token = qp.get("auth", None)
        if isinstance(token, list):
            token = token[0] if token else None
    except Exception:
        token = st.experimental_get_query_params().get("auth", [None])[0]

    if not st.session_state.get("logged_in") and token:
        u = get_user_by_token(token)
        if u:
            st.session_state.logged_in = True
            st.session_state.user_id, st.session_state.username, st.session_state.role = u

    # 2) Route
    if not st.session_state.logged_in:
        login_page()
    else:
        with st.sidebar:
            st.write(f"👤 {st.session_state.username}")
            st.write(f"🎭 {st.session_state.role}")
            if st.button("🚪 ออกจากระบบ"):
                # ลบโทเคนและล้างพารามิเตอร์ใน URL
                try:
                    qp = st.query_params
                    token = qp.get("auth", None)
                except Exception:
                    token = st.experimental_get_query_params().get("auth", [None])[0]
                clear_session(token)
                try:
                    st.query_params.clear()
                except Exception:
                    st.experimental_set_query_params()  # เคลียร์ทั้งหมด

                st.session_state.logged_in = False
                st.session_state.user_id = None
                st.session_state.username = None
                st.session_state.role = None
                st.rerun()

        if st.session_state.role == 'manager':
            admin_dashboard()
        else:
            user_dashboard()

if __name__ == "__main__":
    main()