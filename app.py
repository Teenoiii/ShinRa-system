import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import hashlib

# Database setup
def init_db():
    conn = sqlite3.connect('storebot.db', check_same_thread=False)
    conn.execute('PRAGMA foreign_keys = ON')
    
    # Create tables
    conn.executescript('''
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
      created_at TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS users (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password TEXT NOT NULL,
      role TEXT NOT NULL DEFAULT 'user'
    );
    ''')
    
    # Create default admin user if not exists
    try:
        admin_password = hashlib.sha256('admin123'.encode()).hexdigest()
        conn.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
                    ('admin', admin_password, 'manager'))
        conn.commit()
    except:
        pass  # Admin user already exists
    
    conn.close()

# Helper functions
def get_db_connection():
    return sqlite3.connect('storebot.db', check_same_thread=False)

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
        conn.execute('INSERT INTO users (username, password, role) VALUES (?, ?, ?)',
                    (username, hashed_password, role))
        conn.commit()
        conn.close()
        return True
    except:
        conn.close()
        return False

# Status mapping
TH_STATUS = {'pending': 'รออนุมัติ', 'approved': 'อนุมัติแล้ว', 'rejected': 'ปฏิเสธแล้ว'}

# Initialize database
init_db()

# Streamlit app configuration
st.set_page_config(
    page_title="ระบบจัดการคลังสินค้า",
    page_icon="📦",
    layout="wide"
)

# Session state initialization
if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False
if 'user_id' not in st.session_state:
    st.session_state.user_id = None
if 'username' not in st.session_state:
    st.session_state.username = None
if 'role' not in st.session_state:
    st.session_state.role = None

# Login/Register page
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

    # Demo account info
    # st.info("บัญชีทดสอบ: admin / admin123 (ผู้จัดการ)")

# User dashboard
def user_dashboard():
    st.title("📦 ระบบเบิก-คืนสินค้า")
    st.write(f"สวัสดี **{st.session_state.username}**!")
    
    tab1, tab2, tab3, tab4 = st.tabs(["🔥 ขอเบิก", "🔄 คืนของ", "💸 ส่งเงินแก๊ง", "📊 สถานะ"])
    
    with tab1:
        request_item_tab()
    
    with tab2:
        return_item_tab()
    
    with tab3:
        donate_item_tab()
    
    with tab4:
        status_tab()

# Request item tab
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
            
            # Check stock
            conn = get_db_connection()
            item = conn.execute('SELECT name, stock FROM items WHERE id = ?', (item_id,)).fetchone()
            
            if qty > item[1]:
                st.error(f"ขอได้สูงสุด {item[1]} สำหรับ {item[0]}")
            else:
                conn.execute('INSERT INTO requests (user_id, item_id, qty, reason, status) VALUES (?,?,?,?,?)',
                           (st.session_state.user_id, item_id, qty, reason, 'pending'))
                conn.commit()
                last_id = conn.lastrowid
                conn.close()
                
                st.success(f"📝 สร้างคำขอ #{last_id} ขอเบิก **{item[0]}** จำนวน {qty} (รออนุมัติ)")

# Return item tab
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
            else:
                conn.execute('UPDATE items SET stock = stock + ? WHERE id = ?', (return_qty, req[2]))
                conn.execute('UPDATE requests SET returned_qty = returned_qty + ? WHERE id = ?', (return_qty, request_id))
                conn.commit()
                conn.close()
                
                new_remaining = remaining - return_qty
                st.success(f"↩️ คืนสินค้าคำขอ #{request_id} • {req[8]} จำนวน {return_qty} {'(คืนครบแล้ว)' if new_remaining == 0 else ''}")

# Donate item tab
def donate_item_tab():
    st.header("💸 ส่งเงินแก๊ง")
    
    conn = get_db_connection()
    items = conn.execute('SELECT id, name, stock FROM items ORDER BY name').fetchall()
    conn.close()
    
    if not items:
        st.warning("ยังไม่มีรายการสินค้า")
        return
    
    with st.form("donate_form"):
        item_options = {f"{item[1]} (คงเหลือ {item[2]})": item[0] for item in items}
        selected_item = st.selectbox("เลือกสินค้าที่จะเพิ่มเข้าสต๊อก", options=list(item_options.keys()))
        qty = st.number_input("จำนวนที่จะส่ง", min_value=1, value=1)
        note = st.text_area("บันทึก/หมายเหตุ (ไม่บังคับ)")
        
        submit = st.form_submit_button("ส่งเงินแก๊ง")
        
        if submit:
            item_id = item_options[selected_item]
            
            conn = get_db_connection()
            item_name = conn.execute('SELECT name FROM items WHERE id = ?', (item_id,)).fetchone()[0]
            
            conn.execute('UPDATE items SET stock = stock + ? WHERE id = ?', (qty, item_id))
            conn.execute('INSERT INTO donations (user_id, item_id, qty, note) VALUES (?,?,?,?)',
                        (st.session_state.user_id, item_id, qty, note))
            conn.commit()
            conn.close()
            
            st.success(f"✅ ส่งเข้าแก๊งเรียบร้อย • **{item_name}** จำนวน **{qty}**")

# Status tab
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

# Admin dashboard
def admin_dashboard():
    st.title("🛠 แผงผู้ดูแล (StoreManager)")
    st.write(f"สวัสดี **{st.session_state.username}** (ผู้จัดการ)")
    
    tab1, tab2, tab3, tab4 = st.tabs(["➕ เพิ่มสินค้า", "🔍 ตรวจคำขอ", "📦 สต๊อก", "⚙️ จัดการ"])
    
    with tab1:
        add_item_tab()
    
    with tab2:
        review_requests_tab()
    
    with tab3:
        stock_tab()
    
    with tab4:
        manage_items_tab()

# Add item tab (admin)
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
                except sqlite3.IntegrityError:
                    conn.close()
                    st.error("มีชื่อสินค้านี้อยู่แล้ว")

# Review requests tab (admin)
def review_requests_tab():
    st.header("🔍 ตรวจสอบคำขอ")
    
    conn = get_db_connection()
    pending_requests = conn.execute('''
        SELECT r.id, r.qty, r.user_id, r.reason, it.name, it.stock, u.username
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
        req_id, qty, user_id, reason, item_name, item_stock, username = req
        
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
                        conn.execute('UPDATE items SET stock = stock - ? WHERE name = ?', (qty, item_name))
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

# Stock tab (admin)
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
    
    # Stock summary
    total_items = len(items)
    low_stock = sum(1 for item in items if item[2] <= 5)
    
    col1, col2 = st.columns(2)
    with col1:
        st.metric("รายการสินค้าทั้งหมด", total_items)
    with col2:
        st.metric("สินค้าใกล้หมด (≤5)", low_stock)

# Manage items tab (admin)
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
                
                # Check if item has related requests
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

# Main app
def main():
    if not st.session_state.logged_in:
        login_page()
    else:
        # Sidebar with user info and logout
        with st.sidebar:
            st.write(f"👤 {st.session_state.username}")
            st.write(f"🎭 {st.session_state.role}")
            
            if st.button("🚪 ออกจากระบบ"):
                st.session_state.logged_in = False
                st.session_state.user_id = None
                st.session_state.username = None
                st.session_state.role = None
                st.rerun()
        
        # Main content based on user role
        if st.session_state.role == 'manager':
            admin_dashboard()
        else:
            user_dashboard()

if __name__ == "__main__":
    main()