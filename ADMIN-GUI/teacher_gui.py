# coding: utf-8
"""
╔══════════════════════════════════════════════════════════════════════════════╗
║  teacher_gui.py  —  Панель управления компьютерным классом                  ║
║  Требования: pip install PyQt6 flask requests                                ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

import sys, os, json, math, base64, random, sqlite3, threading, datetime, shutil
from pathlib import Path
from typing import Optional, List, Dict

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QStackedWidget, QListWidget, QListWidgetItem, QLabel,
    QLineEdit, QPushButton, QFrame, QSizePolicy, QFileDialog,
    QColorDialog, QComboBox, QInputDialog, QScrollArea, QDialog,
    QGraphicsOpacityEffect, QTableWidget, QTableWidgetItem, QHeaderView,
    QTabWidget, QCheckBox, QSpinBox, QAbstractItemView, QSplitter,
    QTextEdit, QProgressBar, QMessageBox,
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QObject, QTimer, QSize, QRectF, QPointF,
    QPropertyAnimation, QParallelAnimationGroup,
    QEasingCurve, pyqtProperty, QEvent, QRect, QThread, QRunnable,
    QThreadPool, pyqtSlot,
)
from PyQt6.QtGui import (
    QFont, QFontMetrics, QColor, QPixmap, QPainter, QBrush,
    QLinearGradient, QRadialGradient, QPen, QPainterPath, QPalette, QAction,
    QImage,
)

try:
    from flask import Flask, request, jsonify, send_file
    FLASK_OK = True
except ImportError:
    FLASK_OK = False
    print("[ОШИБКА] Flask не установлен: pip install flask")

try:
    import requests as _req
    REQUESTS_OK = True
except ImportError:
    REQUESTS_OK = False

try:
    from config import TEACHER_IP, FLASK_PORT, ADMIN_ID
except ImportError:
    TEACHER_IP = "0.0.0.0"
    FLASK_PORT = 5000
    ADMIN_ID   = 0

import collections

# ─────────────────────────────────────────────────────────────────────────────
# §1  CONSTANTS & PATHS
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR      = Path(__file__).parent
DB_PATH       = BASE_DIR / "classroom.db"
WORKS_DIR     = BASE_DIR / "received_works"
WORKS_DIR.mkdir(exist_ok=True)

DEFAULT_ACCENT = "#5E9EFF"

# ─────────────────────────────────────────────────────────────────────────────
# §2  DATABASE
# ─────────────────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    con = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with _db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS agents (
            hostname TEXT PRIMARY KEY,
            ip       TEXT,
            last_seen TEXT,
            status   TEXT DEFAULT 'offline'
        );
        CREATE TABLE IF NOT EXISTS command_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            hostname  TEXT,
            command   TEXT,
            result    TEXT
        );
        CREATE TABLE IF NOT EXISTS file_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            hostname  TEXT,
            filename  TEXT,
            direction TEXT,
            size      INTEGER
        );
        CREATE TABLE IF NOT EXISTS observation_log (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            start_time TEXT,
            end_time   TEXT,
            hostname   TEXT,
            duration_sec INTEGER
        );
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS groups (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        );
        CREATE TABLE IF NOT EXISTS group_members (
            group_id INTEGER,
            hostname TEXT,
            PRIMARY KEY (group_id, hostname)
        );
        """)

def db_setting(key: str, default: str = "") -> str:
    with _db() as con:
        row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def db_set_setting(key: str, value: str):
    with _db() as con:
        con.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, value))

def db_log_command(hostname: str, command: str, result: str = ""):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with _db() as con:
        con.execute("INSERT INTO command_log(timestamp,hostname,command,result) VALUES(?,?,?,?)",
                    (ts, hostname, command, result))

def db_log_file(hostname: str, filename: str, direction: str, size: int):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with _db() as con:
        con.execute("INSERT INTO file_log(timestamp,hostname,filename,direction,size) VALUES(?,?,?,?,?)",
                    (ts, hostname, filename, direction, size))

def db_upsert_agent(hostname: str, ip: str, status: str = "online"):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with _db() as con:
        con.execute("""INSERT INTO agents(hostname,ip,last_seen,status) VALUES(?,?,?,?)
                       ON CONFLICT(hostname) DO UPDATE SET ip=excluded.ip,
                       last_seen=excluded.last_seen, status=excluded.status""",
                    (hostname, ip, ts, status))

def db_all_agents() -> List[Dict]:
    with _db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM agents ORDER BY hostname").fetchall()]

def db_command_log(hostname_filter: str = "", limit: int = 200) -> List[Dict]:
    with _db() as con:
        if hostname_filter:
            rows = con.execute("SELECT * FROM command_log WHERE hostname=? ORDER BY id DESC LIMIT ?",
                               (hostname_filter, limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM command_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

def db_file_log(limit: int = 200) -> List[Dict]:
    with _db() as con:
        return [dict(r) for r in
                con.execute("SELECT * FROM file_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]

def db_obs_log_start(hostname: str) -> int:
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with _db() as con:
        cur = con.execute("INSERT INTO observation_log(start_time,hostname) VALUES(?,?)",
                          (ts, hostname))
        return cur.lastrowid

def db_obs_log_end(obs_id: int, duration_sec: int):
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with _db() as con:
        con.execute("UPDATE observation_log SET end_time=?,duration_sec=? WHERE id=?",
                    (ts, duration_sec, obs_id))

# ─────────────────────────────────────────────────────────────────────────────
# §3  COMMAND QUEUES & FILE STORE (same as teacher_bot.py)
# ─────────────────────────────────────────────────────────────────────────────

command_queues: Dict[str, collections.deque] = {}
command_queues_lock = threading.Lock()

pending_files: Dict[str, dict] = {}
pending_files_lock = threading.Lock()

# Latest sysinfo per agent
agent_sysinfo: Dict[str, str] = {}

# Latest frame per agent (base64 JPEG)
agent_frames: Dict[str, str] = {}
agent_frames_lock = threading.Lock()

# Signals bridge: Flask thread → Qt main thread
class _Bridge(QObject):
    agent_registered   = pyqtSignal(str, str)   # hostname, ip
    log_received       = pyqtSignal(str, str, str)  # type, hostname, data
    frame_received     = pyqtSignal(str, str)    # hostname, base64
    works_received     = pyqtSignal(str, str)    # hostname, filename

BRIDGE = _Bridge()

def _enqueue(targets: List[str], command: str):
    with command_queues_lock:
        for h in targets:
            command_queues.setdefault(h, collections.deque()).append(command)

# ─────────────────────────────────────────────────────────────────────────────
# §4  FLASK SERVER
# ─────────────────────────────────────────────────────────────────────────────

flask_app = Flask("ClassroomTeacher")

import logging as _logging
_logging.getLogger("werkzeug").setLevel(_logging.WARNING)

@flask_app.route("/register", methods=["POST"])
def flask_register():
    data = request.get_json(silent=True) or {}
    hostname = (data.get("hostname") or "").strip()
    ip       = (data.get("ip") or "").strip()
    if not hostname:
        return jsonify({"status": "error", "msg": "hostname required"}), 400
    db_upsert_agent(hostname, ip, "online")
    with command_queues_lock:
        command_queues.setdefault(hostname, collections.deque())
    BRIDGE.agent_registered.emit(hostname, ip)
    return jsonify({"status": "ok"})

@flask_app.route("/get_command/<hostname>", methods=["GET"])
def flask_get_command(hostname: str):
    db_upsert_agent(hostname, "", "online")
    with command_queues_lock:
        q = command_queues.get(hostname)
        if q:
            try:
                cmd = q.popleft()
                db_log_command(hostname, cmd, "sent")
                return jsonify({"status": "ok", "command": cmd})
            except IndexError:
                pass
    return jsonify({"status": "ok", "command": None})

@flask_app.route("/post_log", methods=["POST"])
def flask_post_log():
    data     = request.get_json(silent=True) or {}
    msg_type = (data.get("type") or "LOG").strip()
    hostname = (data.get("hostname") or "неизвестно").strip()
    payload  = str(data.get("data") or "")

    if msg_type == "FRAME":
        with agent_frames_lock:
            agent_frames[hostname] = payload
        BRIDGE.frame_received.emit(hostname, payload)
        return jsonify({"status": "ok"})

    if msg_type == "SYSINFO":
        agent_sysinfo[hostname] = payload

    db_log_command(hostname, f"RECV:{msg_type}", payload[:200])
    BRIDGE.log_received.emit(msg_type, hostname, payload)
    return jsonify({"status": "ok"})

@flask_app.route("/get_file/<hostname>", methods=["GET"])
def flask_get_file(hostname: str):
    import io
    with pending_files_lock:
        info = pending_files.pop(hostname, None)
    if not info:
        return jsonify({"status": "error", "msg": "Нет файла"}), 404
    db_log_file(hostname, info["filename"], "out", len(info["data"]))
    return send_file(io.BytesIO(info["data"]), download_name=info["filename"], as_attachment=True)

@flask_app.route("/upload_works", methods=["POST"])
def flask_upload_works():
    hostname = (request.form.get("hostname") or "unknown").strip()
    f = request.files.get("file")
    if not f:
        return jsonify({"status": "error", "msg": "no file"}), 400
    fname = f.filename or f"{hostname}_works.zip"
    safe  = "".join(c for c in fname if c.isalnum() or c in "._- ")
    dest  = WORKS_DIR / f"{datetime.datetime.now():%Y%m%d_%H%M%S}_{hostname}_{safe}"
    f.save(str(dest))
    db_log_file(hostname, str(dest.name), "in", dest.stat().st_size)
    BRIDGE.works_received.emit(hostname, str(dest.name))
    return jsonify({"status": "ok"})

def run_flask():
    flask_app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, use_reloader=False)

import select

DISCOVERY_PORT = 54321          # порт для автообнаружения

def _discovery_loop():
    """Слушает UDP‑запросы от агентов и отвечает IP‑адресом учителя."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", DISCOVERY_PORT))
    sock.settimeout(3)
    log_main = logging.getLogger("УчительБот")
    log_main.info("[ОБНАРУЖЕНИЕ] Слушаю UDP порт %d", DISCOVERY_PORT)

    while True:
        try:
            data, addr = sock.recvfrom(1024)
            if data.strip() == b"CLASSROOM_DISCOVER":
                response = TEACHER_IP.encode()   # TEACHER_IP определён выше
                sock.sendto(response, addr)
                log_main.debug("[ОБНАРУЖЕНИЕ] Ответил %s", addr[0])
        except socket.timeout:
            continue
        except Exception as e:
            log_main.warning("[ОБНАРУЖЕНИЕ] Ошибка: %s", e)
            
# ─────────────────────────────────────────────────────────────────────────────
# §5  STYLE SYSTEM  (exact copy + adapt from clod.py)
# ─────────────────────────────────────────────────────────────────────────────

_K = 0.552284749831

def squircle(rect: QRectF, r: float) -> QPainterPath:
    r = min(r, rect.width()/2, rect.height()/2); c = r*_K
    x,y,w,h = rect.x(),rect.y(),rect.width(),rect.height()
    p = QPainterPath(); p.moveTo(x+r,y)
    p.lineTo(x+w-r,y);   p.cubicTo(x+w-r+c,y,  x+w,y+r-c,  x+w,y+r)
    p.lineTo(x+w,y+h-r); p.cubicTo(x+w,y+h-r+c,x+w-r+c,y+h,x+w-r,y+h)
    p.lineTo(x+r,y+h);   p.cubicTo(x+r-c,y+h,  x,y+h-r+c,  x,y+h-r)
    p.lineTo(x,y+r);     p.cubicTo(x,y+r-c,     x+r-c,y,    x+r,y)
    p.closeSubpath(); return p

def _fill(c: QColor, a: float) -> QColor:
    q = QColor(c); q.setAlphaF(max(0., min(1., a))); return q

def _gb(c: QColor, a: float) -> QBrush:
    return QBrush(_fill(c, a))

def _c(color: QColor, alpha: float) -> str:
    return f"rgba({color.red()},{color.green()},{color.blue()},{int(alpha*255)})"

def build_qss(accent: QColor, has_bg: bool = False) -> str:
    a   = accent.name()
    a06 = _c(accent,.06); a10 = _c(accent,.10); a16 = _c(accent,.16)
    a26 = _c(accent,.26); a42 = _c(accent,.42); a60 = _c(accent,.60)
    card  = "rgba(12,12,22,0.86)" if has_bg else "rgba(12,12,22,0.98)"
    root  = "rgba(6,6,13,0.55)"   if has_bg else "#060610"
    side  = "rgba(10,10,20,0.82)" if has_bg else "rgba(10,10,20,0.98)"
    return f"""
QMainWindow  {{ background:{root}; }}
QWidget      {{ color:rgba(225,225,248,.92); font-size:13px;
                font-family:"Segoe UI","SF Pro Display","DejaVu Sans",sans-serif; }}
QToolTip     {{ background:rgba(14,14,28,.97); color:#fff;
                border:1px solid {a42}; border-radius:9px; padding:5px 10px; }}
#card        {{ background:{card}; border:1px solid rgba(255,255,255,.08); border-radius:22px; }}
#sidebar     {{ background:{side}; border-right:1px solid rgba(255,255,255,.06); }}
#panel       {{ background:rgba(11,11,21,.90); border:1px solid rgba(255,255,255,.07); border-radius:18px; }}
#nav_btn     {{ background:transparent; border:none; border-radius:14px;
                color:rgba(225,225,248,.42); font-weight:600; font-size:13px;
                padding:11px 16px; text-align:left; min-height:44px; }}
#nav_btn:hover {{ background:{a10}; color:rgba(225,225,248,.82); }}
#nav_btn_active {{ background:{a16}; border:1px solid {a26}; color:#ffffff;
                   font-weight:700; border-radius:14px; font-size:13px;
                   padding:11px 16px; text-align:left; min-height:44px; }}
QPushButton        {{ background:{a10}; border:1px solid {a42}; border-radius:14px;
                       color:rgba(225,225,248,.92); font-weight:600; font-size:13px;
                       padding:10px 20px; min-height:36px; }}
QPushButton:hover  {{ background:{a16}; border-color:{a60}; color:#fff; }}
QPushButton:pressed{{ background:{a06}; }}
QPushButton:disabled{{ background:rgba(255,255,255,.04); color:rgba(225,225,248,.22);
                        border-color:rgba(255,255,255,.06); }}
QComboBox {{ background:rgba(255,255,255,.07); border:1px solid {a42};
              border-radius:12px; color:rgba(225,225,248,.92);
              padding:8px 14px; font-size:13px; min-height:34px; }}
QComboBox:hover {{ border-color:{a60}; }}
QComboBox::drop-down {{ border:none; width:28px; }}
QComboBox QAbstractItemView {{ background:rgba(14,14,28,.97); border:1px solid {a42};
    border-radius:10px; color:rgba(225,225,248,.92);
    selection-background-color:{a26}; outline:none; padding:4px; }}
QLineEdit {{ background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11);
              border-radius:14px; color:rgba(225,225,248,.95);
              padding:10px 16px; font-size:13px;
              selection-background-color:{a42}; }}
QLineEdit:focus {{ border-color:{a60}; background:rgba(255,255,255,.10); }}
QListWidget {{ background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.07);
                border-radius:16px; outline:none; padding:4px; }}
QListWidget::item {{ background:transparent; border-radius:9px;
                      padding:7px 12px; color:rgba(210,210,235,.85); font-size:12px; }}
QListWidget::item:hover    {{ background:rgba(255,255,255,.06); color:#fff; }}
QListWidget::item:selected {{ background:{a26}; color:#fff; border:1px solid {a42}; }}
QTableWidget {{ background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.07);
                 border-radius:14px; outline:none; gridline-color:rgba(255,255,255,.05); }}
QTableWidget::item {{ background:transparent; padding:6px 10px; color:rgba(210,210,235,.85); }}
QTableWidget::item:hover    {{ background:rgba(255,255,255,.06); color:#fff; }}
QTableWidget::item:selected {{ background:{a26}; color:#fff; }}
QHeaderView::section {{ background:rgba(255,255,255,.04); color:rgba(225,225,248,.55);
                         border:none; border-bottom:1px solid rgba(255,255,255,.07);
                         padding:8px 12px; font-size:11px; font-weight:700;
                         letter-spacing:1.4px; }}
QScrollBar:vertical {{ background:transparent; width:5px; margin:3px 1px; }}
QScrollBar::handle:vertical {{ background:rgba(255,255,255,.14); border-radius:2px; min-height:20px; }}
QScrollBar::handle:vertical:hover {{ background:{a42}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
QFrame#sep {{ background:rgba(255,255,255,.07); max-height:1px; border:none; }}
QTabWidget::pane {{ border:1px solid rgba(255,255,255,.08); border-radius:14px;
                     background:rgba(12,12,22,.90); }}
QTabBar::tab {{ background:rgba(255,255,255,.05); border:none; border-radius:10px;
                 color:rgba(225,225,248,.55); font-size:12px; font-weight:600;
                 padding:8px 18px; margin:2px; }}
QTabBar::tab:selected {{ background:{a16}; color:#fff; border:1px solid {a26}; }}
QTabBar::tab:hover {{ background:{a10}; color:rgba(225,225,248,.80); }}
QCheckBox {{ spacing:8px; color:rgba(225,225,248,.80); }}
QCheckBox::indicator {{ width:18px; height:18px; border-radius:5px;
                          border:1px solid rgba(255,255,255,.20); background:rgba(255,255,255,.05); }}
QCheckBox::indicator:checked {{ background:{a}; border-color:{a60}; }}
QSpinBox {{ background:rgba(255,255,255,.07); border:1px solid rgba(255,255,255,.11);
             border-radius:10px; color:rgba(225,225,248,.92);
             padding:6px 12px; font-size:13px; }}
QTextEdit {{ background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.07);
              border-radius:14px; color:rgba(210,210,235,.85);
              padding:10px; font-size:12px; }}
QProgressBar {{ background:rgba(255,255,255,.08); border:none; border-radius:4px; height:6px; }}
QProgressBar::chunk {{ background:{a}; border-radius:4px; }}
"""

# ─────────────────────────────────────────────────────────────────────────────
# §6  ICON PAINTERS  (adapted from clod.py + new classroom icons)
# ─────────────────────────────────────────────────────────────────────────────

def icon_monitor(p, r, c):
    """Dashboard / monitor icon"""
    cx,cy = r.center().x(), r.center().y(); m = min(r.width(), r.height())
    sw,sh = m*.50, m*.36; sx = cx-sw/2; sy = cy-sh/2
    scr = QRectF(sx, sy, sw, sh)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_gb(c,.22)); p.drawRoundedRect(scr.adjusted(-3,-3,3,3), 5,5)
    p.setBrush(_gb(c,.85)); p.drawRoundedRect(scr, 4,4)
    # Stand
    stand = QPainterPath()
    stand.moveTo(cx-sw*.18, cy+sh/2); stand.lineTo(cx+sw*.18, cy+sh/2)
    stand.lineTo(cx+sw*.10, cy+sh/2+m*.10); stand.lineTo(cx-sw*.10, cy+sh/2+m*.10)
    stand.closeSubpath()
    p.setBrush(_gb(c,.70)); p.drawPath(stand)
    # Screen inner
    p.setBrush(_gb(c,.18)); p.drawRoundedRect(scr.adjusted(3,3,-3,-3),2,2)

def icon_computer(p, r, c):
    """Computers list icon"""
    cx,cy = r.center().x(), r.center().y(); m = min(r.width(), r.height())
    # Multiple overlapping screens
    for dx,dy,a in ((-m*.06,-m*.04,.35),(0,0,.85)):
        sw,sh = m*.44, m*.30
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(_gb(c,a))
        p.drawRoundedRect(QRectF(cx-sw/2+dx, cy-sh/2+dy, sw, sh), 3,3)

def icon_eye(p, r, c):
    """Observation icon"""
    cx,cy = r.center().x(), r.center().y(); m = min(r.width(), r.height()); s = m*.28
    eye = QPainterPath()
    eye.moveTo(cx-s, cy)
    eye.cubicTo(cx-s, cy-s*.55, cx+s, cy-s*.55, cx+s, cy)
    eye.cubicTo(cx+s, cy+s*.55, cx-s, cy+s*.55, cx-s, cy)
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(_gb(c,.28)); p.drawPath(eye)
    p.setBrush(_gb(c,.88)); p.drawPath(eye)
    p.setBrush(_gb(c,1.)); p.drawEllipse(QPointF(cx, cy), s*.36, s*.36)
    p.setBrush(_gb(QColor(0,0,0), .70)); p.drawEllipse(QPointF(cx, cy), s*.18, s*.18)

def icon_folder(p, r, c):
    cx,cy = r.center().x(), r.center().y(); m = min(r.width(), r.height())
    fw,fh = m*.55,m*.36; tw,th = fw*.36,fh*.22
    path = QPainterPath()
    path.moveTo(cx-fw/2,cy-fh/2+th); path.lineTo(cx-fw/2+tw,cy-fh/2+th)
    path.lineTo(cx-fw/2+tw+th,cy-fh/2); path.lineTo(cx+fw/2,cy-fh/2)
    path.lineTo(cx+fw/2,cy+fh/2); path.lineTo(cx-fw/2,cy+fh/2); path.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_gb(c,.22)); p.drawPath(path); p.setBrush(_gb(c,.82)); p.drawPath(path)

def icon_list(p, r, c):
    """Journal / list icon"""
    cx,cy = r.center().x(), r.center().y(); m = min(r.width(), r.height()); s = m*.28
    for w2,a in ((4.,.22),(2.,.88)):
        pen = QPen(_fill(c,a), w2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        p.setPen(pen); p.setBrush(Qt.BrushStyle.NoBrush)
        for dy in (-s*.70, 0., s*.70):
            p.drawLine(QPointF(cx-s*.10, cy+dy), QPointF(cx+s, cy+dy))
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(_gb(c,.88))
    for dy in (-s*.70, 0., s*.70):
        p.drawEllipse(QPointF(cx-s*.50, cy+dy), s*.14, s*.14)

def icon_settings(p, r, c):
    cx,cy = r.center().x(), r.center().y(); m = min(r.width(), r.height())
    ro,ri = m*.28,m*.14; teeth = 8
    gear = QPainterPath()
    for i in range(teeth*2):
        ang = math.radians(i*180/teeth-90); rad = ro if i%2==0 else ro*.76
        pt = QPointF(cx+rad*math.cos(ang), cy+rad*math.sin(ang))
        gear.moveTo(pt) if i==0 else gear.lineTo(pt)
    gear.closeSubpath(); hole = QPainterPath(); hole.addEllipse(QPointF(cx,cy), ri, ri)
    gear = gear.subtracted(hole); p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_gb(c,.28)); p.drawPath(gear); p.setBrush(_gb(c,.86)); p.drawPath(gear)

NAV_ICONS = {
    "monitor": icon_monitor,
    "computer": icon_computer,
    "eye": icon_eye,
    "folder": icon_folder,
    "list": icon_list,
    "settings": icon_settings,
}

# ─────────────────────────────────────────────────────────────────────────────
# §7  SQUIRCLE BUTTON  (exact copy from clod.py)
# ─────────────────────────────────────────────────────────────────────────────

class SBtn(QPushButton):
    def _g(self): return self.__sc
    def _s(self,v): self.__sc=v; self.update()
    _scale = pyqtProperty(float,_g,_s)

    def __init__(self, text="", icon="", sz=None, radius=15, variant="default", parent=None):
        super().__init__(parent)
        self.__sc=1.0; self._glow=0.0; self._glow_t=0.0
        self._accent=QColor(DEFAULT_ACCENT); self._icon=icon; self._lbl=text
        self._radius=radius; self._variant=variant
        super().setText(""); self.setFlat(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        if sz: self.setFixedSize(QSize(*sz))
        else:  self._autosize()
        self._gt = QTimer(self); self._gt.setInterval(6)
        self._gt.timeout.connect(self._tick); self._gt.start()
        self._sa = QPropertyAnimation(self, b"_scale", self)
        self.installEventFilter(self)

    def set_accent(self, c: QColor): self._accent=c; self.update()
    def set_label(self, t: str):
        self._lbl=t
        if self.width() != self.height(): self._autosize()
        self.update()

    def _autosize(self):
        f=self.font(); f.setPixelSize(14 if self._variant=="primary" else 13)
        f.setWeight(QFont.Weight.Bold if self._variant=="primary" else QFont.Weight.DemiBold)
        fm=QFontMetrics(f); tw=fm.horizontalAdvance(self._lbl) if self._lbl else 0
        self.setFixedSize(QSize(max(76, tw+44), max(40, fm.height()+22)))

    def eventFilter(self, obj, ev):
        if obj is self:
            t=ev.type()
            if   t==QEvent.Type.Enter:              self._glow_t=0.62
            elif t==QEvent.Type.Leave:               self._glow_t=0.0
            elif t==QEvent.Type.MouseButtonPress:   self._glow_t=1.0; self._anim(0.96)
            elif t==QEvent.Type.MouseButtonRelease: self._glow_t=0.44; self._anim(1.0, True)
        return super().eventFilter(obj, ev)

    def _anim(self, target, spring=False):
        self._sa.stop(); self._sa.setStartValue(self.__sc); self._sa.setEndValue(target)
        self._sa.setEasingCurve(QEasingCurve.Type.OutBack if spring else QEasingCurve.Type.OutQuad)
        self._sa.setDuration(320 if spring else 65); self._sa.start()

    def _tick(self):
        diff=self._glow_t-self._glow
        if abs(diff)>0.002: self._glow+=diff*.22; self.update()

    def paintEvent(self, _):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w,h=float(self.width()),float(self.height()); cx,cy=w/2,h/2
        if abs(self.__sc-1.)>0.001:
            p.translate(cx,cy); p.scale(self.__sc,self.__sc); p.translate(-cx,-cy)
        rect=QRectF(1,1,w-2,h-2); path=squircle(rect,self._radius); a=self._accent
        if self._variant=="primary":
            bg=QLinearGradient(0,0,w,h)
            bg.setColorAt(0,_fill(a,.44+self._glow*.22)); bg.setColorAt(1,_fill(a,.26+self._glow*.12))
            p.setBrush(QBrush(bg))
        elif self._variant=="ghost": p.setBrush(QBrush(_fill(a,.07+self._glow*.13)))
        else: p.setBrush(QBrush(_fill(a,.11+self._glow*.17)))
        p.setPen(Qt.PenStyle.NoPen); p.drawPath(path)
        if self._glow>0.015:
            rg=QRadialGradient(QPointF(cx,cy), max(cx,cy)*.92)
            rg.setColorAt(0,_fill(a,self._glow*.30)); rg.setColorAt(1,QColor(0,0,0,0))
            p.setBrush(QBrush(rg)); p.drawPath(path)
        bc=_fill(a,.62+self._glow*.34) if self._variant=="primary" \
           else QColor(255,255,255,int((.10+self._glow*.12)*255))
        p.setPen(QPen(bc,1.)); p.setBrush(Qt.BrushStyle.NoBrush); p.drawPath(path)
        sg=QLinearGradient(w*.18,1,w*.82,1); sa=int((.17+self._glow*.17)*255)
        sg.setColorAt(0,QColor(255,255,255,0)); sg.setColorAt(.5,QColor(255,255,255,sa)); sg.setColorAt(1,QColor(255,255,255,0))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QBrush(sg)); p.drawRect(QRectF(1,1,w-2,1.8))
        if self._lbl: self._paint_text(p,w,h)
        p.end()

    def _paint_text(self, p, w, h):
        f=QFont(self.font())
        if self._variant=="primary": f.setPixelSize(14); f.setWeight(QFont.Weight.Bold)
        else: f.setPixelSize(13); f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f); fm=QFontMetrics(f)
        text=fm.elidedText(self._lbl, Qt.TextElideMode.ElideRight, int(w-28))
        tw=fm.horizontalAdvance(text); tx=(w-tw)/2.; ty=(h+fm.ascent()-fm.descent())/2.
        if self._variant=="primary":
            p.setPen(_fill(self._accent,.44)); p.drawText(QPointF(tx+.6,ty+.9),text)
        p.setPen(QColor(255,255,255,240 if self._variant=="primary" else 210))
        p.drawText(QPointF(tx,ty),text)

# ─────────────────────────────────────────────────────────────────────────────
# §8  NAV BUTTON  (exact copy from clod.py)
# ─────────────────────────────────────────────────────────────────────────────

class NavBtn(QPushButton):
    def __init__(self, icon_name: str, label: str, parent=None):
        super().__init__(parent)
        self._icon_name=icon_name; self._label=label
        self._accent=QColor(DEFAULT_ACCENT); self._active=False
        self._glow=0.0; self._glow_t=0.0; self._compact=False
        self.setObjectName("nav_btn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedHeight(44)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._gt=QTimer(self); self._gt.setInterval(6)
        self._gt.timeout.connect(self._tick); self._gt.start()
        self.installEventFilter(self)

    def set_accent(self, c: QColor): self._accent=c; self.update()
    def set_active(self, v: bool):
        self._active=v; self._glow_t=0.80 if v else 0.0
        self.setObjectName("nav_btn_active" if v else "nav_btn")
        self.setStyleSheet(""); self.update()
    def set_compact(self, v: bool):
        self._compact=v
        self.setFixedWidth(44 if v else 16777215)
        self.setSizePolicy(QSizePolicy.Policy.Fixed if v else QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Fixed)
        self.update()

    def eventFilter(self, obj, ev):
        if obj is self:
            t=ev.type()
            if   t==QEvent.Type.Enter: self._glow_t=max(self._glow_t,.40)
            elif t==QEvent.Type.Leave: self._glow_t=0.80 if self._active else 0.0
        return super().eventFilter(obj, ev)

    def _tick(self):
        diff=self._glow_t-self._glow
        if abs(diff)>0.002: self._glow+=diff*.24; self.update()

    def paintEvent(self, _):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w,h=float(self.width()),float(self.height()); a=self._accent
        rect=QRectF(2,2,w-4,h-4)
        if self._glow>0.02:
            bg_alpha=0.16 if self._active else (self._glow*.10)
            p.setBrush(QBrush(_fill(a,bg_alpha))); p.setPen(Qt.PenStyle.NoPen)
            if self._active: p.setPen(QPen(_fill(a,.28),1.))
            p.drawRoundedRect(rect,12,12)
        icon_sz=22.0; iy=(h-icon_sz)/2.
        ix=((w-icon_sz)/2.) if self._compact else 14.
        icon_fn = NAV_ICONS.get(self._icon_name)
        if icon_fn:
            icon_fn(p, QRectF(ix,iy,icon_sz,icon_sz),
                    a if self._active else _fill(a,.55+self._glow*.44))
        if not self._compact:
            f=QFont(self.font()); f.setPixelSize(13)
            f.setWeight(QFont.Weight.Bold if self._active else QFont.Weight.DemiBold)
            p.setFont(f)
            alpha=0.95 if self._active else (.55+self._glow*.38)
            p.setPen(QColor(225,225,248,int(alpha*255)))
            p.drawText(QRectF(ix+icon_sz+10,0,w-ix-icon_sz-20,h),
                       Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft, self._label)
        p.end()

# ─────────────────────────────────────────────────────────────────────────────
# §9  ANIMATED STACK  (exact copy from clod.py)
# ─────────────────────────────────────────────────────────────────────────────

class AnimStack(QStackedWidget):
    def __init__(self, parent=None):
        super().__init__(parent); self._animating=False

    def slide_to(self, index: int, direction: int=1):
        if index==self.currentIndex() or self._animating:
            self.setCurrentIndex(index); return
        old_w=self.currentWidget(); self.setCurrentIndex(index); new_w=self.currentWidget()
        if not old_w or not new_w: return
        self._animating=True
        eff_old=QGraphicsOpacityEffect(old_w); eff_new=QGraphicsOpacityEffect(new_w)
        old_w.setGraphicsEffect(eff_old); new_w.setGraphicsEffect(eff_new)
        a_out=QPropertyAnimation(eff_old,b"opacity",self)
        a_out.setDuration(150); a_out.setStartValue(1.0); a_out.setEndValue(0.0)
        a_out.setEasingCurve(QEasingCurve.Type.OutCubic)
        a_in=QPropertyAnimation(eff_new,b"opacity",self)
        a_in.setDuration(200); a_in.setStartValue(0.0); a_in.setEndValue(1.0)
        a_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        grp=QParallelAnimationGroup(self); grp.addAnimation(a_out); grp.addAnimation(a_in)
        def _done():
            old_w.setGraphicsEffect(None); new_w.setGraphicsEffect(None); self._animating=False
        grp.finished.connect(_done)
        grp.start(QParallelAnimationGroup.DeletionPolicy.DeleteWhenStopped)

# ─────────────────────────────────────────────────────────────────────────────
# §10  WAVEFORM  (exact copy from clod.py, adapted for agent status)
# ─────────────────────────────────────────────────────────────────────────────

class Waveform(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._acc = QColor(DEFAULT_ACCENT)
        self._play = False
        self._ph = 0.0
        n = 32
        self._h = [random.uniform(.10, .60) for _ in range(n)]
        self._t = list(self._h)
        self.setMinimumHeight(22)
        self.setMaximumHeight(28)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        tm = QTimer(self)
        tm.setInterval(8)
        tm.timeout.connect(self._step)
        tm.start()

    def set_accent(self, c): self._acc=c
    def set_active(self, v): self._play=v

    def _step(self):
        if self._play:
            self._ph+=0.06
            for i in range(len(self._h)):
                t=.28+.58*abs(math.sin(i*.37+self._ph))+.06*random.gauss(0,.2)
                self._t[i]=max(.06,min(1.,t)); self._h[i]+=(self._t[i]-self._h[i])*.22
        else:
            for i in range(len(self._h)):
                self._h[i]+=((.08+.04*math.sin(i*.5))-self._h[i])*.04
        self.update()

    def paintEvent(self, _):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w,h=self.width(),self.height(); n=len(self._h)
        bw=max(1.5,(w-n*1.2)/n); step=bw+1.2
        for i,ht in enumerate(self._h):
            bh=max(2.,ht*(h-4)); x=i*step; y=(h-bh)/2.
            col=_fill(self._acc,.85 if self._play else .35)
            p.setBrush(QBrush(col)); p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(QRectF(x,y,bw,bh),1.,1.)
        p.end()

# ─────────────────────────────────────────────────────────────────────────────
# §11  TOAST NOTIFICATION
# ─────────────────────────────────────────────────────────────────────────────

class Toast(QFrame):
    def __init__(self, message: str, accent: QColor, parent=None):
        super().__init__(parent)
        self._accent = accent
        self.setWindowFlags(Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QHBoxLayout(self); lay.setContentsMargins(14,10,14,10); lay.setSpacing(10)
        dot = QLabel("●"); dot.setStyleSheet(f"color:{accent.name()};font-size:10px;")
        lay.addWidget(dot)
        lbl = QLabel(message); lbl.setStyleSheet("color:rgba(225,225,248,.95);font-size:13px;")
        lay.addWidget(lbl); self.adjustSize()
        eff = QGraphicsOpacityEffect(self); self.setGraphicsEffect(eff)
        self._ani = QPropertyAnimation(eff, b"opacity", self)
        self._ani.setDuration(300); self._ani.setStartValue(0.0); self._ani.setEndValue(1.0)
        self._ani.setEasingCurve(QEasingCurve.Type.OutCubic); self._ani.start()
        QTimer.singleShot(3000, self._fade_out)

    def _fade_out(self):
        eff = self.graphicsEffect()
        if not eff: self.close(); return
        a2 = QPropertyAnimation(eff, b"opacity", self)
        a2.setDuration(400); a2.setStartValue(1.0); a2.setEndValue(0.0)
        a2.finished.connect(self.close); a2.start()

    def paintEvent(self, _):
        p=QPainter(self); p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(0,0,self.width(),self.height())
        path=squircle(rect, 14)
        bg=QColor(14,14,28,240); p.setBrush(QBrush(bg)); p.setPen(Qt.PenStyle.NoPen)
        p.drawPath(path)
        p.setPen(QPen(QColor(255,255,255,30),1.)); p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(path); p.end()

# ─────────────────────────────────────────────────────────────────────────────
# §12  AGENT CARD WIDGET  (for dashboard)
# ─────────────────────────────────────────────────────────────────────────────

class AgentCard(QFrame):
    action_sig = pyqtSignal(str, str)  # hostname, action

    def __init__(self, hostname: str, info: dict, accent: QColor, parent=None):
        super().__init__(parent)
        self._hostname = hostname
        self._accent   = accent
        self.setObjectName("card")
        self.setMinimumWidth(260); self.setMaximumWidth(340)
        lay = QVBoxLayout(self); lay.setContentsMargins(16,14,16,14); lay.setSpacing(8)

        # Header row
        hdr = QHBoxLayout(); hdr.setSpacing(10)
        self._wave = Waveform()
        self._wave.set_accent(accent)
        hdr.addWidget(self._wave)
        info_col = QVBoxLayout(); info_col.setSpacing(2)
        self._name_lbl = QLabel(hostname)
        self._name_lbl.setStyleSheet("font-size:14px;font-weight:700;color:rgba(255,255,255,.94);")
        info_col.addWidget(self._name_lbl)
        self._ip_lbl = QLabel(info.get("ip",""))
        self._ip_lbl.setStyleSheet("font-size:11px;color:rgba(225,225,248,.42);")
        info_col.addWidget(self._ip_lbl)
        hdr.addLayout(info_col,1)
        self._status_dot = QLabel("●")
        hdr.addWidget(self._status_dot)
        lay.addLayout(hdr)

        # CPU/RAM mini bars
        bars_w = QWidget(); bl = QHBoxLayout(bars_w); bl.setContentsMargins(0,0,0,0); bl.setSpacing(8)
        self._cpu_bar = QProgressBar(); self._cpu_bar.setRange(0,100); self._cpu_bar.setValue(0)
        self._cpu_bar.setFixedHeight(5); self._cpu_bar.setTextVisible(False)
        self._ram_bar = QProgressBar(); self._ram_bar.setRange(0,100); self._ram_bar.setValue(0)
        self._ram_bar.setFixedHeight(5); self._ram_bar.setTextVisible(False)
        self._cpu_lbl = QLabel("CPU"); self._cpu_lbl.setStyleSheet("font-size:10px;color:rgba(225,225,248,.38);")
        self._ram_lbl = QLabel("RAM"); self._ram_lbl.setStyleSheet("font-size:10px;color:rgba(225,225,248,.38);")
        bl.addWidget(self._cpu_lbl); bl.addWidget(self._cpu_bar,1)
        bl.addWidget(self._ram_lbl); bl.addWidget(self._ram_bar,1)
        lay.addWidget(bars_w)

        sep = QFrame(); sep.setObjectName("sep"); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1); lay.addWidget(sep)
        self._wave = Waveform()
        self._wave.set_accent(accent)
        lay.addWidget(self._wave)
        
        # Action buttons – сетка 2x2
        btn_grid = QGridLayout()
        btn_grid.setSpacing(6)
        actions = [
            ("Инфо","info"), ("Наблюдение","observe"),
            ("Работы","collect"), ("Процессы","kill"),
        ]
        for i, (label, act) in enumerate(actions):
            b = SBtn(label, radius=11, parent=self)
            b.set_accent(accent)
            b.clicked.connect(lambda checked=False, a=act: self.action_sig.emit(self._hostname, a))
            btn_grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(btn_grid)

    def set_status(self, status: str):
        online = status == "online"
        self._wave.set_active(online)
        col = "#44FF88" if online else "#FF4455"
        self._status_dot.setStyleSheet(f"color:{col};font-size:12px;")
        self._status_dot.setToolTip("Онлайн" if online else "Оффлайн")

    def update_sysinfo(self, text: str):
        import re
        cpu_m = re.search(r"CPU.*?([\d.]+)%", text)
        ram_m = re.search(r"ОЗУ.*?([\d.]+)\s*/\s*([\d.]+)", text)
        if cpu_m:
            self._cpu_bar.setValue(int(float(cpu_m.group(1))))
        if ram_m:
            used, total = float(ram_m.group(1)), float(ram_m.group(2))
            self._ram_bar.setValue(int(used/total*100) if total>0 else 0)

    def set_accent(self, c: QColor):
        self._accent = c
        self._wave.set_accent(c)

# ─────────────────────────────────────────────────────────────────────────────
# §13  PAGE: DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

class DashboardPage(QWidget):
    action_sig = pyqtSignal(str, str)  # hostname, action

    def __init__(self, accent: QColor, parent=None):
        super().__init__(parent)
        self._accent = accent
        self._cards: Dict[str, AgentCard] = {}
        lay = QVBoxLayout(self); lay.setContentsMargins(20,16,20,16); lay.setSpacing(16)

        hdr = QHBoxLayout()
        title = QLabel("Панель управления"); title.setStyleSheet("font-size:22px;font-weight:700;color:rgba(255,255,255,.94);")
        hdr.addWidget(title); hdr.addStretch()
        self._refresh_btn = SBtn("Обновить всех", radius=12, parent=self)
        self._refresh_btn.set_accent(accent)
        hdr.addWidget(self._refresh_btn); lay.addLayout(hdr)

        sep = QFrame(); sep.setObjectName("sep"); sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFixedHeight(1); lay.addWidget(sep)

        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget(); self._grid = QGridLayout(inner)
        self._grid.setContentsMargins(0,0,0,0); self._grid.setSpacing(14)
        scroll.setWidget(inner); lay.addWidget(scroll, 1)

        self._no_agents_lbl = QLabel("Нет подключённых рабочих станций.\nЗапустите student_agent_v2.py на ПК студентов.")
        self._no_agents_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._no_agents_lbl.setStyleSheet("color:rgba(225,225,248,.30);font-size:15px;")
        self._grid.addWidget(self._no_agents_lbl, 0, 0, 1, 3)

    def refresh_agents(self):
        agents = db_all_agents()
        # Remove stale cards
        for h in list(self._cards):
            if not any(a["hostname"]==h for a in agents):
                card = self._cards.pop(h)
                card.setParent(None)
        # Add / update
        for ag in agents:
            h = ag["hostname"]
            if h not in self._cards:
                card = AgentCard(h, ag, self._accent)
                card.action_sig.connect(self.action_sig)
                self._cards[h] = card
            else:
                self._cards[h].set_status(ag.get("status","offline"))
        self._relayout()
        self._no_agents_lbl.setVisible(not bool(self._cards))

    def _relayout(self):
        # Clear grid (except no_agents label)
        while self._grid.count() > 0:
            item = self._grid.takeAt(0)
            if item and item.widget() and item.widget() is not self._no_agents_lbl:
                item.widget().setParent(None)
        cols = max(1, min(4, (self.width()-40)//270))
        for i, (h, card) in enumerate(self._cards.items()):
            self._grid.addWidget(card, i//cols, i%cols)

    def update_sysinfo(self, hostname: str, text: str):
        if hostname in self._cards:
            self._cards[hostname].update_sysinfo(text)

    def mark_online(self, hostname: str):
        if hostname in self._cards:
            self._cards[hostname].set_status("online")

    def set_accent(self, c: QColor):
        self._accent = c
        self._refresh_btn.set_accent(c)
        for card in self._cards.values():
            card.set_accent(c)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

# ─────────────────────────────────────────────────────────────────────────────
# §14  PAGE: COMPUTERS (table view + multi-select actions)
# ─────────────────────────────────────────────────────────────────────────────

class ComputersPage(QWidget):
    action_sig = pyqtSignal(list, str)  # [hostnames], action

    def __init__(self, accent: QColor, parent=None):
        super().__init__(parent)
        self._accent = accent
        lay = QVBoxLayout(self); lay.setContentsMargins(20,16,20,16); lay.setSpacing(12)

        title = QLabel("Компьютеры"); title.setStyleSheet("font-size:22px;font-weight:700;color:rgba(255,255,255,.94);")
        lay.addWidget(title)

        self._tbl = QTableWidget(0, 5)
        self._tbl.setHorizontalHeaderLabels(["Имя компьютера","IP-адрес","Последний сигнал","Статус",""])
        self._tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl.verticalHeader().hide()
        lay.addWidget(self._tbl, 1)

        # Multi-action toolbar
        act_row = QHBoxLayout(); act_row.setSpacing(8)
        actions = [("Инфо","info"),("Наблюдение","observe"),("Собрать работы","collect"),
                   ("Раздать файл","sendfile"),("Завершить процесс","kill")]
        self._all_btns = []
        for label,act in actions:
            b = SBtn(label, radius=12, parent=self); b.set_accent(accent)
            b.clicked.connect(lambda checked=False, a=act: self._on_action(a))
            act_row.addWidget(b); self._all_btns.append(b)
        act_row.addStretch(); lay.addLayout(act_row)

    def refresh_agents(self):
        agents = db_all_agents()
        self._tbl.setRowCount(len(agents))
        for r, ag in enumerate(agents):
            self._tbl.setItem(r,0, QTableWidgetItem(ag["hostname"]))
            self._tbl.setItem(r,1, QTableWidgetItem(ag.get("ip","")))
            self._tbl.setItem(r,2, QTableWidgetItem(ag.get("last_seen","")[:19]))
            status = ag.get("status","offline")
            st_item = QTableWidgetItem("🟢 Онлайн" if status=="online" else "🔴 Оффлайн")
            self._tbl.setItem(r,3, st_item)

    def _selected_hosts(self) -> List[str]:
        rows = set(i.row() for i in self._tbl.selectedItems())
        result = []
        for r in rows:
            item = self._tbl.item(r, 0)
            if item: result.append(item.text())
        if not result:
            # If none selected, use all
            for r in range(self._tbl.rowCount()):
                item = self._tbl.item(r, 0)
                if item: result.append(item.text())
        return result

    def _on_action(self, act: str):
        hosts = self._selected_hosts()
        if hosts:
            self.action_sig.emit(hosts, act)

    def set_accent(self, c: QColor):
        self._accent = c
        for b in self._all_btns: b.set_accent(c)

# ─────────────────────────────────────────────────────────────────────────────
# §15  PAGE: OBSERVATION
# ─────────────────────────────────────────────────────────────────────────────

class ObservePage(QWidget):
    def __init__(self, accent: QColor, parent=None):
        super().__init__(parent)
        self._accent   = accent
        self._obs_host  = None
        self._obs_start = None
        self._obs_id    = None
        self._frame_n   = 0
        lay = QVBoxLayout(self); lay.setContentsMargins(20,16,20,16); lay.setSpacing(12)

        title = QLabel("Наблюдение за рабочим столом")
        title.setStyleSheet("font-size:22px;font-weight:700;color:rgba(255,255,255,.94);")
        lay.addWidget(title)

        ctrl_row = QHBoxLayout(); ctrl_row.setSpacing(10)
        ctrl_row.addWidget(QLabel("Станция:"))
        self._host_combo = QComboBox(); self._host_combo.setMinimumWidth(200)
        ctrl_row.addWidget(self._host_combo)
        self._start_btn = SBtn("▶ Начать наблюдение", radius=12, variant="primary", parent=self)
        self._start_btn.set_accent(accent); self._start_btn.clicked.connect(self._start_obs)
        ctrl_row.addWidget(self._start_btn)
        self._stop_btn = SBtn("■ Остановить", radius=12, parent=self)
        self._stop_btn.set_accent(accent); self._stop_btn.clicked.connect(self._stop_obs)
        self._stop_btn.setEnabled(False); ctrl_row.addWidget(self._stop_btn)
        ctrl_row.addStretch()
        self._fps_lbl = QLabel("FPS: —")
        self._fps_lbl.setStyleSheet("font-size:11px;color:rgba(225,225,248,.45);")
        ctrl_row.addWidget(self._fps_lbl)
        self._dur_lbl = QLabel("00:00")
        self._dur_lbl.setStyleSheet("font-size:11px;color:rgba(225,225,248,.45);")
        ctrl_row.addWidget(self._dur_lbl)
        lay.addLayout(ctrl_row)

        # Frame display
        self._frame_lbl = QLabel()
        self._frame_lbl.setObjectName("card")
        self._frame_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._frame_lbl.setMinimumHeight(400)
        self._frame_lbl.setStyleSheet("#card{border-radius:18px;background:rgba(8,8,18,.95);}")
        self._frame_lbl.setText("Выберите станцию и нажмите «Начать наблюдение»")
        lay.addWidget(self._frame_lbl, 1)

        # FPS/duration timer
        self._timer = QTimer(self); self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick_timer)
        self._last_frame_time = None
        self._fps_count = 0

    def refresh_agents(self):
        agents = db_all_agents()
        cur = self._host_combo.currentText()
        self._host_combo.clear()
        for ag in agents:
            self._host_combo.addItem(ag["hostname"])
        idx = self._host_combo.findText(cur)
        if idx>=0: self._host_combo.setCurrentIndex(idx)

    def _start_obs(self):
        host = self._host_combo.currentText()
        if not host: return
        self._obs_host  = host
        self._obs_start = datetime.datetime.now()
        self._obs_id    = db_obs_log_start(host)
        self._frame_n   = 0; self._fps_count = 0
        _enqueue([host], "CMD_OBSERVE_START")
        db_log_command(host, "CMD_OBSERVE_START")
        self._start_btn.setEnabled(False); self._stop_btn.setEnabled(True)
        self._timer.start()
        self._frame_lbl.setText("Ожидание кадров…")

    def _stop_obs(self):
        if self._obs_host:
            _enqueue([self._obs_host], "CMD_OBSERVE_STOP")
            db_log_command(self._obs_host, "CMD_OBSERVE_STOP")
            if self._obs_id and self._obs_start:
                dur = int((datetime.datetime.now()-self._obs_start).total_seconds())
                db_obs_log_end(self._obs_id, dur)
            self._obs_host = None; self._obs_start = None; self._obs_id = None
        self._start_btn.setEnabled(True); self._stop_btn.setEnabled(False)
        self._timer.stop()

    def on_frame(self, hostname: str, b64: str):
        if hostname != self._obs_host: return
        import time
        self._frame_n += 1; self._fps_count += 1
        try:
            img_data = base64.b64decode(b64)
            pix = QPixmap(); pix.loadFromData(img_data)
            if not pix.isNull():
                scaled = pix.scaled(self._frame_lbl.size(),
                                    Qt.AspectRatioMode.KeepAspectRatio,
                                    Qt.TransformationMode.SmoothTransformation)
                # Overlay timestamp + frame count
                painter = QPainter(scaled)
                painter.setFont(QFont("Courier New", 10))
                painter.setPen(QColor(0,255,100,200))
                now_s = datetime.datetime.now().strftime("%H:%M:%S")
                painter.drawText(8, 20, f"🔴 {hostname}  {now_s}  #{self._frame_n}")
                painter.end()
                self._frame_lbl.setPixmap(scaled)
        except Exception as e:
            print(f"[КАДР] Ошибка декодирования: {e}")

    def _tick_timer(self):
        self._fps_lbl.setText(f"FPS: {self._fps_count}")
        self._fps_count = 0
        if self._obs_start:
            dur = int((datetime.datetime.now()-self._obs_start).total_seconds())
            m,s = divmod(dur, 60)
            self._dur_lbl.setText(f"{m:02d}:{s:02d}")

    def set_accent(self, c: QColor):
        self._accent = c
        self._start_btn.set_accent(c); self._stop_btn.set_accent(c)

# ─────────────────────────────────────────────────────────────────────────────
# §16  PAGE: FILES
# ─────────────────────────────────────────────────────────────────────────────

class FilesPage(QWidget):
    def __init__(self, accent: QColor, parent=None):
        super().__init__(parent)
        self._accent = accent
        self._selected_file: Optional[str] = None
        lay = QVBoxLayout(self); lay.setContentsMargins(20,16,20,16); lay.setSpacing(12)

        title = QLabel("Файлы"); title.setStyleSheet("font-size:22px;font-weight:700;color:rgba(255,255,255,.94);")
        lay.addWidget(title)

        tabs = QTabWidget(); lay.addWidget(tabs, 1)

        # ── Tab 1: Distribute ──────────────────────────────────────────────
        dist_w = QWidget(); dt = QVBoxLayout(dist_w); dt.setContentsMargins(16,16,16,16); dt.setSpacing(12)
        file_row = QHBoxLayout()
        self._file_lbl = QLabel("Файл не выбран"); self._file_lbl.setStyleSheet("color:rgba(225,225,248,.55);")
        file_row.addWidget(self._file_lbl,1)
        self._pick_btn = SBtn("Выбрать файл", radius=12, parent=self); self._pick_btn.set_accent(accent)
        self._pick_btn.clicked.connect(self._pick_file); file_row.addWidget(self._pick_btn)
        dt.addLayout(file_row)
        dt.addWidget(QLabel("Выберите получателей (пусто = все):"))
        self._recv_list = QListWidget(); self._recv_list.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        self._recv_list.setMaximumHeight(200); dt.addWidget(self._recv_list)
        self._send_btn = SBtn("Разослать файл", radius=12, variant="primary", parent=self)
        self._send_btn.set_accent(accent); self._send_btn.clicked.connect(self._send_file)
        dt.addWidget(self._send_btn); dt.addStretch()
        tabs.addTab(dist_w, "Раздача файлов")

        # ── Tab 2: Received works ──────────────────────────────────────────
        recv_w = QWidget(); rt = QVBoxLayout(recv_w); rt.setContentsMargins(16,16,16,16); rt.setSpacing(12)
        self._works_tbl = QTableWidget(0, 4)
        self._works_tbl.setHorizontalHeaderLabels(["Файл","Станция","Время","Размер"])
        self._works_tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._works_tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._works_tbl.verticalHeader().hide()
        rt.addWidget(self._works_tbl,1)
        open_btn = SBtn("Открыть папку works", radius=12, parent=self); open_btn.set_accent(accent)
        open_btn.clicked.connect(lambda: os.startfile(str(WORKS_DIR)) if sys.platform=="win32"
                                         else subprocess.Popen(["xdg-open", str(WORKS_DIR)]))
        rt.addWidget(open_btn)
        tabs.addTab(recv_w, "Принятые работы")

    def refresh_agents(self):
        agents = db_all_agents()
        cur_sel = [self._recv_list.item(i).text() for i in range(self._recv_list.count())
                   if self._recv_list.item(i).isSelected()]
        self._recv_list.clear()
        for ag in agents:
            item = QListWidgetItem(ag["hostname"])
            self._recv_list.addItem(item)
            if ag["hostname"] in cur_sel:
                item.setSelected(True)

    def refresh_file_log(self):
        rows = [r for r in db_file_log() if r["direction"]=="in"]
        self._works_tbl.setRowCount(len(rows))
        for i, r in enumerate(rows):
            self._works_tbl.setItem(i,0, QTableWidgetItem(r.get("filename","")))
            self._works_tbl.setItem(i,1, QTableWidgetItem(r.get("hostname","")))
            self._works_tbl.setItem(i,2, QTableWidgetItem(r.get("timestamp","")[:19]))
            self._works_tbl.setItem(i,3, QTableWidgetItem(f"{r.get('size',0)//1024} КБ"))

    def add_received(self, hostname: str, filename: str):
        self.refresh_file_log()

    def _pick_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Выберите файл для рассылки")
        if path:
            self._selected_file = path
            self._file_lbl.setText(Path(path).name)

    def _send_file(self):
        if not self._selected_file:
            return
        selected = [self._recv_list.item(i).text()
                    for i in range(self._recv_list.count())
                    if self._recv_list.item(i).isSelected()]
        if not selected:
            selected = [self._recv_list.item(i).text() for i in range(self._recv_list.count())]
        if not selected:
            return
        try:
            data = Path(self._selected_file).read_bytes()
            fname = Path(self._selected_file).name
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e)); return
        with pending_files_lock:
            for h in selected:
                pending_files[h] = {"filename": fname, "data": data}
        _enqueue(selected, f"CMD_SEND_FILE|{fname}")
        for h in selected:
            db_log_command(h, f"CMD_SEND_FILE|{fname}")
        QMessageBox.information(self, "Готово", f"Файл «{fname}» поставлен в очередь для {len(selected)} станций.")

    def set_accent(self, c: QColor):
        self._accent = c
        self._pick_btn.set_accent(c); self._send_btn.set_accent(c)

# ─────────────────────────────────────────────────────────────────────────────
# §17  PAGE: JOURNAL
# ─────────────────────────────────────────────────────────────────────────────

class JournalPage(QWidget):
    def __init__(self, accent: QColor, parent=None):
        super().__init__(parent)
        self._accent = accent
        lay = QVBoxLayout(self); lay.setContentsMargins(20,16,20,16); lay.setSpacing(12)
        title = QLabel("Журнал событий"); title.setStyleSheet("font-size:22px;font-weight:700;color:rgba(255,255,255,.94);")
        lay.addWidget(title)

        filter_row = QHBoxLayout(); filter_row.setSpacing(10)
        filter_row.addWidget(QLabel("Станция:"))
        self._host_filter = QComboBox(); self._host_filter.setMinimumWidth(180)
        self._host_filter.addItem("Все"); filter_row.addWidget(self._host_filter)
        refresh_btn = SBtn("Обновить", radius=12, parent=self); refresh_btn.set_accent(accent)
        refresh_btn.clicked.connect(self.refresh); filter_row.addWidget(refresh_btn)
        filter_row.addStretch(); lay.addLayout(filter_row)

        self._tbl = QTableWidget(0, 4)
        self._tbl.setHorizontalHeaderLabels(["Время","Станция","Команда","Результат"])
        self._tbl.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._tbl.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._tbl.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._tbl.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._tbl.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._tbl.verticalHeader().hide(); lay.addWidget(self._tbl,1)

    def refresh_agents(self):
        agents = db_all_agents()
        cur = self._host_filter.currentText()
        self._host_filter.clear(); self._host_filter.addItem("Все")
        for ag in agents: self._host_filter.addItem(ag["hostname"])
        idx = self._host_filter.findText(cur)
        if idx>=0: self._host_filter.setCurrentIndex(idx)

    def refresh(self):
        hf = self._host_filter.currentText()
        rows = db_command_log(hostname_filter="" if hf=="Все" else hf)
        self._tbl.setRowCount(len(rows))
        for i,r in enumerate(rows):
            self._tbl.setItem(i,0, QTableWidgetItem(r.get("timestamp","")[:19]))
            self._tbl.setItem(i,1, QTableWidgetItem(r.get("hostname","")))
            self._tbl.setItem(i,2, QTableWidgetItem(r.get("command","")[:80]))
            self._tbl.setItem(i,3, QTableWidgetItem(r.get("result","")[:120]))

    def add_entry(self, type_: str, hostname: str, data: str):
        self.refresh()

    def set_accent(self, c: QColor):
        self._accent = c

# ─────────────────────────────────────────────────────────────────────────────
# §18  PAGE: SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

class SettingsPage(QWidget):
    accent_changed = pyqtSignal(str)

    def __init__(self, accent: QColor, parent=None):
        super().__init__(parent)
        self._accent = accent
        lay = QVBoxLayout(self); lay.setContentsMargins(20,16,20,16); lay.setSpacing(16)
        title = QLabel("Настройки"); title.setStyleSheet("font-size:22px;font-weight:700;color:rgba(255,255,255,.94);")
        lay.addWidget(title)

        panel = QFrame(); panel.setObjectName("panel")
        pl = QGridLayout(panel); pl.setSpacing(14); pl.setContentsMargins(20,20,20,20)

        # Accent color
        pl.addWidget(QLabel("Цвет акцента:"), 0,0)
        self._acc_preview = QPushButton(); self._acc_preview.setFixedSize(80,32)
        self._acc_preview.setStyleSheet(f"background:{accent.name()};border-radius:10px;border:none;")
        self._acc_preview.clicked.connect(self._pick_accent); pl.addWidget(self._acc_preview,0,1)

        # Flask port
        pl.addWidget(QLabel("Flask-порт:"), 1,0)
        self._port_spin = QSpinBox(); self._port_spin.setRange(1024,65535)
        self._port_spin.setValue(FLASK_PORT); pl.addWidget(self._port_spin,1,1)

        # FPS limit for observation
        pl.addWidget(QLabel("Макс. FPS наблюдения (1-5):"), 2,0)
        self._fps_spin = QSpinBox(); self._fps_spin.setRange(1,5)
        self._fps_spin.setValue(int(db_setting("obs_fps","2") or "2"))
        pl.addWidget(self._fps_spin,2,1)

        # Server IP display
        pl.addWidget(QLabel("IP сервера (только чтение):"), 3,0)
        ip_lbl = QLabel(TEACHER_IP); ip_lbl.setStyleSheet("color:rgba(225,225,248,.55);")
        pl.addWidget(ip_lbl,3,1)

        save_btn = SBtn("Сохранить настройки", radius=12, variant="primary", parent=self)
        save_btn.set_accent(accent); save_btn.clicked.connect(self._save)
        pl.addWidget(save_btn, 4, 0, 1, 2)

        lay.addWidget(panel); lay.addStretch()

    def _pick_accent(self):
        c = QColorDialog.getColor(self._accent, self, "Выберите цвет акцента")
        if c.isValid():
            self._accent = c
            self._acc_preview.setStyleSheet(f"background:{c.name()};border-radius:10px;border:none;")

    def _save(self):
        db_set_setting("accent_color", self._accent.name())
        db_set_setting("flask_port", str(self._port_spin.value()))
        db_set_setting("obs_fps", str(self._fps_spin.value()))
        self.accent_changed.emit(self._accent.name())

    def set_accent(self, c: QColor):
        self._accent = c
        self._acc_preview.setStyleSheet(f"background:{c.name()};border-radius:10px;border:none;")

# ─────────────────────────────────────────────────────────────────────────────
# §19  PROCESS KILL DIALOG
# ─────────────────────────────────────────────────────────────────────────────

class KillDialog(QDialog):
    def __init__(self, hosts: List[str], accent: QColor, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Завершить процесс")
        self.setMinimumWidth(360)
        lay = QVBoxLayout(self); lay.setSpacing(12); lay.setContentsMargins(20,20,20,20)
        lay.addWidget(QLabel(f"Станции: {', '.join(hosts[:5])}{'…' if len(hosts)>5 else ''}"))
        lay.addWidget(QLabel("Имя процесса (например chrome.exe):"))
        self._edit = QLineEdit(); self._edit.setPlaceholderText("chrome.exe")
        lay.addWidget(self._edit)
        row = QHBoxLayout()
        ok_btn = SBtn("Завершить", radius=12, variant="primary", parent=self)
        ok_btn.set_accent(accent); ok_btn.clicked.connect(self.accept)
        cancel_btn = SBtn("Отмена", radius=12, parent=self); cancel_btn.set_accent(accent)
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(ok_btn); row.addWidget(cancel_btn); lay.addLayout(row)

    def process_name(self) -> str:
        return self._edit.text().strip()

# ─────────────────────────────────────────────────────────────────────────────
# §20  MAIN WINDOW
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    _toast_y = 80  # stack position

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Управление компьютерным классом")
        self.resize(1280, 800)
        self._accent = QColor(db_setting("accent_color", DEFAULT_ACCENT) or DEFAULT_ACCENT)
        if not self._accent.isValid(): self._accent = QColor(DEFAULT_ACCENT)

        # Central widget
        central = QWidget(); self.setCentralWidget(central)
        root = QHBoxLayout(central); root.setContentsMargins(0,0,0,0); root.setSpacing(0)

        # ── Sidebar ─────────────────────────────────────────────────────────
        self._sidebar = QFrame(); self._sidebar.setObjectName("sidebar")
        self._sidebar.setFixedWidth(220)
        sb_lay = QVBoxLayout(self._sidebar); sb_lay.setContentsMargins(10,16,10,16); sb_lay.setSpacing(4)

        logo = QLabel("🖥  Класс"); logo.setStyleSheet("font-size:18px;font-weight:800;color:#fff;padding:8px 6px 16px 6px;")
        sb_lay.addWidget(logo)

        nav_items = [
            ("monitor",  "Панель управления",0),
            ("computer", "Компьютеры",       1),
            ("eye",      "Наблюдение",        2),
            ("folder",   "Файлы",             3),
            ("list",     "Журнал",            4),
            ("settings", "Настройки",         5),
        ]
        self._nav_btns: Dict[int,NavBtn] = {}
        for icon, label, idx in nav_items:
            btn = NavBtn(icon, label)
            btn.set_accent(self._accent)
            btn.clicked.connect(lambda checked=False, i=idx: self._switch_page(i))
            sb_lay.addWidget(btn); self._nav_btns[idx] = btn

        sb_lay.addStretch()
        self._srv_lbl = QLabel(f"Flask :{FLASK_PORT}"); self._srv_lbl.setStyleSheet("font-size:10px;color:rgba(225,225,248,.30);padding:4px 8px;")
        sb_lay.addWidget(self._srv_lbl)

        root.addWidget(self._sidebar)

        # ── Page stack ───────────────────────────────────────────────────────
        self._stack = AnimStack()
        self._dash   = DashboardPage(self._accent)
        self._comps  = ComputersPage(self._accent)
        self._obs    = ObservePage(self._accent)
        self._files  = FilesPage(self._accent)
        self._journal= JournalPage(self._accent)
        self._sett   = SettingsPage(self._accent)

        for page in (self._dash, self._comps, self._obs, self._files, self._journal, self._sett):
            self._stack.addWidget(page)
        root.addWidget(self._stack, 1)

        # ── Signals ──────────────────────────────────────────────────────────
        BRIDGE.agent_registered.connect(self._on_agent_registered)
        BRIDGE.log_received.connect(self._on_log_received)
        BRIDGE.frame_received.connect(self._obs.on_frame)
        BRIDGE.works_received.connect(self._files.add_received)
        self._dash.action_sig.connect(self._on_action)
        self._comps.action_sig.connect(lambda hosts,act: self._on_action_multi(hosts, act))
        self._sett.accent_changed.connect(self._apply_accent)

        # ── Refresh timer ─────────────────────────────────────────────────────
        self._refresh_timer = QTimer(self); self._refresh_timer.setInterval(5000)
        self._refresh_timer.timeout.connect(self._full_refresh); self._refresh_timer.start()

        self._switch_page(0)
        self._apply_accent(self._accent.name())
        self._full_refresh()

    # ── Navigation ───────────────────────────────────────────────────────────

    def _switch_page(self, idx: int):
        self._stack.slide_to(idx)
        for i, btn in self._nav_btns.items():
            btn.set_active(i == idx)
        if idx == 4: self._journal.refresh()
        elif idx == 3: self._files.refresh_file_log()

    # ── Theme ─────────────────────────────────────────────────────────────────

    def _apply_accent(self, hex_color: str):
        c = QColor(hex_color)
        if not c.isValid(): return
        self._accent = c
        self.setStyleSheet(build_qss(c))
        for btn in self._nav_btns.values(): btn.set_accent(c)
        for page in (self._dash, self._comps, self._obs, self._files, self._journal, self._sett):
            page.set_accent(c)

    # ── Refresh ───────────────────────────────────────────────────────────────

    def _full_refresh(self):
        for page in (self._dash, self._comps, self._obs, self._files, self._journal):
            page.refresh_agents()
        self._check_stale_agents()

    def _check_stale_agents(self):
        agents = db_all_agents()
        now = datetime.datetime.now()
        for ag in agents:
            if ag.get("status") != "online": continue
            try:
                last = datetime.datetime.fromisoformat(ag.get("last_seen",""))
                if (now - last).total_seconds() > 60:
                    db_upsert_agent(ag["hostname"], ag.get("ip",""), "offline")
            except Exception:
                pass

    # ── Signal handlers ───────────────────────────────────────────────────────

    def _on_agent_registered(self, hostname: str, ip: str):
        self._full_refresh()
        self._dash.mark_online(hostname)
        self._toast(f"Подключена станция: {hostname} ({ip})")

    def _on_log_received(self, type_: str, hostname: str, data: str):
        self._journal.add_entry(type_, hostname, data)
        if type_ == "SYSINFO":
            self._dash.update_sysinfo(hostname, data)

    # ── Actions ───────────────────────────────────────────────────────────────

    def _on_action(self, hostname: str, action: str):
        self._on_action_multi([hostname], action)

    def _on_action_multi(self, hosts: List[str], action: str):
        if action == "info":
            _enqueue(hosts, "CMD_SYSINFO")
            for h in hosts: db_log_command(h, "CMD_SYSINFO")
            self._toast(f"Запрос инфо → {len(hosts)} станций")
        elif action == "observe":
            if hosts:
                self._switch_page(2)
                idx = self._obs._host_combo.findText(hosts[0])
                if idx>=0: self._obs._host_combo.setCurrentIndex(idx)
        elif action == "collect":
            _enqueue(hosts, "CMD_COLLECT")
            for h in hosts: db_log_command(h, "CMD_COLLECT")
            self._toast(f"Сбор работ → {len(hosts)} станций")
        elif action == "kill":
            dlg = KillDialog(hosts, self._accent, self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                proc = dlg.process_name()
                if proc:
                    cmd = f"CMD_KILL|{proc}"
                    _enqueue(hosts, cmd)
                    for h in hosts: db_log_command(h, cmd)
                    self._toast(f"Завершение {proc} → {len(hosts)} станций")
        elif action == "sendfile":
            self._switch_page(3)

    # ── Toast ─────────────────────────────────────────────────────────────────

    def _toast(self, message: str):
        t = Toast(message, self._accent, self)
        t.adjustSize()
        x = self.width() - t.width() - 20
        t.move(x, self._toast_y)
        t.show()
        self._toast_y += t.height() + 8
        QTimer.singleShot(3800, lambda: setattr(self, '_toast_y', max(80, self._toast_y - t.height() - 8)))

# ─────────────────────────────────────────────────────────────────────────────
# §21  ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def main():
    if hasattr(sys.stdout, "reconfigure"): sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"): sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING","1")

    init_db()

    # Start Flask in daemon thread
    if FLASK_OK:
        t = threading.Thread(target=run_flask, daemon=True, name="Flask")
        t.start()
        print(f"[СЕРВЕР] Flask запущен на порту {FLASK_PORT}")
    else:
        print("[ОШИБКА] Flask не установлен, HTTP-сервер недоступен")

    app = QApplication(sys.argv)
    app.setApplicationName("Управление классом")
    app.setStyle("Fusion")
    f = QFont(); f.setFamilies(["Segoe UI","SF Pro Display","DejaVu Sans","Arial Unicode MS","sans-serif"])
    f.setPixelSize(13); app.setFont(f)
    pal = app.palette()
    for role,col in (
        (QPalette.ColorRole.Window,     QColor(6,6,13)),
        (QPalette.ColorRole.WindowText, QColor(225,225,248)),
        (QPalette.ColorRole.Base,       QColor(10,10,20)),
        (QPalette.ColorRole.Text,       QColor(225,225,248)),
        (QPalette.ColorRole.Button,     QColor(13,13,26)),
        (QPalette.ColorRole.ButtonText, QColor(225,225,248)),
    ): pal.setColor(role,col)
    app.setPalette(pal)

    win = MainWindow(); win.show()
    threading.Thread(target=_discovery_loop, daemon=True, name="Discovery").start()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
