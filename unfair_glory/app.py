# -*- coding: utf-8 -*-
# ============================================================
#   CGU — FREE FIRE 4-BOT TEAM + AUTO CREATE + MOVEMENT
#   + Auto-load accounts + TCP-idle-on-match + All-done restart
#   + FIXED: match-end detection + friend-request accept
#   + AUTO-RESTART: no match 90s (HANYA saat ONLINE/SEARCHING)
# ============================================================
import sys, os, json, time, uuid, random, asyncio, base64, struct, socket
import itertools, traceback, ssl, hmac, hashlib, string, re, subprocess
import httpx
import aiohttp
import aiohttp.web
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any

if sys.platform == "win32":
    try:
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception: pass

from google_play_scraper import app as play_scraper
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from protobuf_decoder.protobuf_decoder import Parser
from message_ids import MESSAGE_ID_TO_NAME
import thunderFF_pb2

try:
    import StartMatch_pb2
except Exception:
    StartMatch_pb2 = None

try:
    import reqClan_pb2
    _CLAN_PB_AVAILABLE = True
except Exception:
    _CLAN_PB_AVAILABLE = False

# ==================== CONFIG ====================
WEB_HOST = "0.0.0.0"
WEB_PORT = 25567
ACCOUNTS_FILE = "accounts.json"
TOKEN_CACHE_FILE = "token_cache.json"
DEVICES_FILE = "devices.json"
TOKEN_CACHE_TTL = 1200
INDEX_HTML_PATH = "templates/index.html"

START_MATCH_INTERVAL = 3.0
NEW_MATCH_DELAY = 2.0
MAX_MATCH_DURATION = 1500      # 25 min (real BR is 15-20 min)

# --- Match idle: nilai di atas 10-20s agar tidak salah deteksi match end ---
MATCH_IDLE_TIMEOUT  = 90.0     # tunggu 90s paket UDP; 35s terlalu cepat (match asli 15-19 mnt)

# --- Auto-restart kalau tidak dapat match ---
NO_MATCH_RESTART_ENABLED  = True       # set False untuk mematikan
NO_MATCH_RESTART_TIMEOUT  = 90.0       # 90 detik tanpa match → restart otomatis
NO_MATCH_RESTART_GRACE    = 60.0       # beri waktu bot login & cari match pertama

_accept_friend_request = True
DEBUG_ALL_PACKETS = False

TEAM_MODE = True
TEAM_SIZE = 4
TEAM_READY_DELAY = 5.0
TEAM_INVITE_WAIT = 2.5
CLAN_ID_DEFAULT = "3105331459"

AES_KEY = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
AES_IV  = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])
KEYSTREAM = bytes([0x30,0x30,0x30,0x32,0x30,0x31,0x37,0x30,0x30,0x30,0x30,0x30,0x32,0x30,0x31,0x37,0x30,0x30,0x30,0x30,0x30,0x32,0x30,0x31,0x37,0x30,0x30,0x30,0x30,0x30,0x32,0x30])
HEX_KEY = "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3"
API_KEY = HEX_KEY.encode()

REGION_LANG = {"ME":"ar","IND":"hi","ID":"id","VN":"vi","TH":"th","BD":"bn",
               "PK":"ur","TW":"zh","RU":"ru","SAC":"es","BR":"pt","NA":"pt",
               "SG":"pt","US":"pt"}

# ==================== STATE ====================
class BotState:
    def __init__(self):
        self.accounts: Dict[str, dict] = {}
        self.account_credentials: Dict[str, dict] = {}
        self.account_workers: Dict[str, asyncio.Task] = {}
        self.auth_to_game_id: Dict[str, str] = {}
        self.game_to_auth_id: Dict[str, str] = {}
        self.account_token_map: Dict[str, str] = {}
        self.paused: set = set()
        self.writers: Dict[str, list] = {}
        self.logs: List[dict] = []
        self.total_matches = 0
        self.matches_started = 0
        self.total_exp_gained = 0
        self.create_progress: List[str] = []
        self.creating = False
        self.start_time = time.time()
        self._writer_lock = asyncio.Lock()

    def log(self, msg, level="info"):
        try:
            t = datetime.now().strftime("%H:%M:%S")
            self.logs.append({"t": time.time(), "time": t, "message": str(msg), "level": level})
            if len(self.logs) > 500:
                self.logs = self.logs[-500:]
        except Exception:
            pass

    def register_account(self, uid, nickname, region="ME", level=1, exp=0,
                         likes=0, token=None, auth_uid=None):
        uid = str(uid)
        existing = self.accounts.get(uid, {})
        self.accounts[uid] = {
            "uid": uid, "nickname": nickname, "region": region,
            "level": level, "exp": exp, "likes": likes,
            "token": token, "auth_uid": auth_uid,
            "status": "ONLINE",
            "current_exp": exp,
            "initial_exp": existing.get("initial_exp", exp),
            "gained_exp": existing.get("gained_exp", 0),
            "matches": existing.get("matches", 0),
            "active_matches": 0,
            "uptime_seconds": existing.get("uptime_seconds", 0),
            "last_match_time": existing.get("last_match_time", "Running..."),
            "is_paused": False,
        }

    def update_status(self, uid, status, matches=0):
        uid = str(uid)
        if uid in self.accounts:
            self.accounts[uid]["status"] = status
            self.accounts[uid]["active_matches"] = matches
            self.accounts[uid]["is_paused"] = (status == "PAUSED")

    def update_exp(self, uid, exp, level=None):
        uid = str(uid)
        if uid in self.accounts:
            a = self.accounts[uid]
            old = a.get("current_exp", 0)
            if exp > old:
                a["gained_exp"] = a.get("gained_exp", 0) + (exp - old)
                self.total_exp_gained += (exp - old)
            a["current_exp"] = exp
            a["exp"] = exp
            if level: a["level"] = level

    def increment_match(self, uid):
        self.total_matches += 1
        uid = str(uid)
        if uid in self.accounts:
            self.accounts[uid]["matches"] = self.accounts[uid].get("matches", 0) + 1
            self.accounts[uid]["last_match_time"] = datetime.now().strftime("%H:%M:%S")

    def increment_match_started(self):
        self.matches_started += 1

    def register_writer(self, uid, w):
        self.writers.setdefault(str(uid), []).append(w)

    def unregister_writer(self, uid, w):
        try: self.writers.get(str(uid), []).remove(w)
        except Exception: pass

    def close_writers_for_account(self, uid):
        for w in list(self.writers.get(str(uid), [])):
            try: w.close()
            except Exception: pass
        self.writers[str(uid)] = []

    def is_paused(self, uid):
        return str(uid) in self.paused

    def toggle_pause(self, uid, state):
        uid = str(uid)
        if state:
            self.paused.add(uid)
            if uid in self.accounts:
                self.accounts[uid]["is_paused"] = True
                self.accounts[uid]["status"] = "PAUSED"
        else:
            self.paused.discard(uid)
            if uid in self.accounts:
                self.accounts[uid]["is_paused"] = False
                if self.accounts[uid].get("status") == "PAUSED":
                    self.accounts[uid]["status"] = "ONLINE"

bot_state = BotState()

_team_state = {
    "peers": {},
    "leader_uid": None,
    "team_code": None,
    "lock": asyncio.Lock(),
}

# ---- restart tracking (all-bots-finished → full process restart) ----
_restart_state = {
    "lock": asyncio.Lock(),
    "finished_bots": set(),
    "target": None,
    "restarting": False,
}

async def _mark_bot_match_finished(account_id):
    async with _restart_state["lock"]:
        if _restart_state["restarting"]:
            return
        _restart_state["finished_bots"].add(str(account_id))
        n_done = len(_restart_state["finished_bots"])

        if _restart_state["target"] is None:
            active_ids = {uid for uid, a in bot_state.accounts.items()
                          if a.get("status") not in ("OFFLINE",)}
            _restart_state["target"] = max(1, min(TEAM_SIZE, len(active_ids))) if active_ids else TEAM_SIZE
            print_info(f"[RESTART] Target set to {_restart_state['target']} bots")

        target = _restart_state["target"]
        print_success(f"[RESTART] {account_id} match done → {n_done}/{target}")

        if n_done >= target:
            _restart_state["restarting"] = True
            print_warning(f"[RESTART] ✅ All {target} bots finished! Full process restart in 3s...")
            asyncio.create_task(_schedule_restart())

# ---- NO-MATCH watchdog state ----
_no_match_state = {
    "lock": asyncio.Lock(),
    "global_last_match": None,     # timestamp match terakhir ditemukan
    "restart_triggered": False,
}

async def _mark_match_found(account_id=None):
    """Dipanggil tiap kali match ditemukan (paket 0300 ≥300 byte)."""
    async with _no_match_state["lock"]:
        _no_match_state["global_last_match"] = time.time()

# Status yang DIIZINKAN untuk trigger auto-restart
_ALLOWED_RESTART_STATUSES = {"ONLINE", "SEARCHING"}

def _all_bots_are_online_or_searching() -> bool:
    """
    Return True HANYA jika:
      - Ada minimal 1 bot aktif (tidak OFFLINE)
      - SEMUA bot aktif berstatus ONLINE atau SEARCHING
    Return False kalau ada bot dengan status lain (IN_MATCH, PAUSED, dll)
    atau tidak ada bot aktif sama sekali.
    """
    active_statuses = []
    for uid, acc in bot_state.accounts.items():
        status = str(acc.get("status", "")).upper().strip()
        if status in ("", "OFFLINE"):
            continue
        active_statuses.append(status)

    if not active_statuses:
        return False  # tidak ada bot aktif → jangan trigger

    # Semua status aktif harus ONLINE atau SEARCHING
    for st in active_statuses:
        if st not in _ALLOWED_RESTART_STATUSES:
            return False

    return True


async def _no_match_watchdog():
    """
    Loop pengawas: restart HANYA jika tidak ada match selama
    NO_MATCH_RESTART_TIMEOUT DAN semua bot aktif berstatus
    ONLINE / SEARCHING.

    Skip (reset timer) kalau:
      - Ada bot IN_MATCH / IN_MATCH (TEAM) / PAUSED / CONNECTING / status lain
      - Tidak ada bot aktif sama sekali
    """
    if not NO_MATCH_RESTART_ENABLED:
        print_info("[NO-MATCH] watchdog disabled")
        return

    # beri waktu bot login & cari match pertama
    await asyncio.sleep(NO_MATCH_RESTART_GRACE)

    async with _no_match_state["lock"]:
        if _no_match_state["global_last_match"] is None:
            _no_match_state["global_last_match"] = time.time()

    print_info(f"[NO-MATCH] watchdog aktif | timeout={NO_MATCH_RESTART_TIMEOUT:.0f}s "
               f"| grace={NO_MATCH_RESTART_GRACE:.0f}s")
    print_info("[NO-MATCH] restart HANYA saat semua bot ONLINE / SEARCHING")

    while True:
        await asyncio.sleep(10.0)
        try:
            if _restart_state["restarting"]:
                return
            if _no_match_state["restart_triggered"]:
                return

            # ---- Cek: hanya lanjut kalau SEMUA bot aktif ONLINE/SEARCHING ----
            if not _all_bots_are_online_or_searching():
                # Ada bot IN_MATCH / PAUSED / CONNECTING / dll → reset timer, skip
                async with _no_match_state["lock"]:
                    _no_match_state["global_last_match"] = time.time()

                # Log sesekali (biar tidak spam)
                statuses = {}
                for uid, a in bot_state.accounts.items():
                    s = str(a.get("status", "?")).upper().strip() or "?"
                    if s == "OFFLINE":
                        continue
                    statuses[s] = statuses.get(s, 0) + 1
                if statuses:
                    print_info(f"[NO-MATCH] skip restart — status={statuses} "
                               f"(butuh semua ONLINE/SEARCHING)")
                continue

            # ---- Hitung mundur ----
            async with _no_match_state["lock"]:
                last = _no_match_state["global_last_match"] or 0
            elapsed = time.time() - last

            if elapsed >= NO_MATCH_RESTART_TIMEOUT:
                # Double-check terakhir sebelum restart
                if not _all_bots_are_online_or_searching():
                    async with _no_match_state["lock"]:
                        _no_match_state["global_last_match"] = time.time()
                    print_info("[NO-MATCH] batal restart — status bot berubah")
                    continue

                async with _no_match_state["lock"]:
                    if _no_match_state["restart_triggered"]:
                        continue
                    _no_match_state["restart_triggered"] = True

                msg = (f"[NO-MATCH] ⚠️ Tidak ada match selama {elapsed:.0f}s "
                       f"(limit {NO_MATCH_RESTART_TIMEOUT:.0f}s) "
                       f"| semua bot ONLINE/SEARCHING → restart otomatis!")
                print_warning(msg)
                bot_state.log(msg, "warning")

                async with _restart_state["lock"]:
                    _restart_state["restarting"] = True
                await _schedule_restart()
                return
            else:
                if int(elapsed) % 30 < 10:
                    print_info(f"[NO-MATCH] idle {elapsed:.0f}s / "
                               f"{NO_MATCH_RESTART_TIMEOUT:.0f}s "
                               f"(semua bot ONLINE/SEARCHING)")
        except Exception as e:
            print_warning(f"[NO-MATCH] watchdog error: {e}")

async def _schedule_restart():
    await asyncio.sleep(3.0)
    print_warning("[RESTART] 🔄 Executing full process restart...")
    try:
        sys.stdout.flush(); sys.stderr.flush()
    except Exception:
        pass
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv)
    except Exception as e:
        print_error(f"[RESTART] execv failed: {e}")
        try:
            subprocess.Popen([sys.executable] + sys.argv)
            os._exit(0)
        except Exception as e2:
            print_error(f"[RESTART] subprocess fallback failed: {e2}")
            os._exit(1)

# ==================== PRINT HELPERS ====================
class C:
    GREEN='\033[92m'; FAIL='\033[91m'; WARNING='\033[93m'
    CYAN='\033[96m'; MAGENTA='\033[95m'; WHITE='\033[97m'; ENDC='\033[0m'

def print_success(t): print(f"{C.GREEN}[+] {t}{C.ENDC}"); bot_state.log(t, "success")
def print_error(t):   print(f"{C.FAIL}[-] {t}{C.ENDC}");   bot_state.log(t, "error")
def print_warning(t): print(f"{C.WARNING}[!] {t}{C.ENDC}"); bot_state.log(t, "warning")
def print_info(t):    print(f"{C.CYAN}[i] {t}{C.ENDC}");   bot_state.log(t, "info")

# ---- For MajorRegister ----
GGRE_RAW = bytes.fromhex(
    "47475245010101006d020000b260ee08e1f86b4c57f9c70f86bba26ed5d1436bcf52e142db3249d905eded757764991ca31a8373cdd26eab91b80f3f1f6f262f4f10b895d7b6937ddba30a27197453890e9da49373f736c679b8254e2f8e1623e91084a5fdd5374fe478ff99e010834553fddeb0bec4018d142e49df9bb236675b67e852f92a43586f7a7d4d0d7a197a1d4da32714dab4d069ad53214e2c33b3877420a12459738c4c619cbd815fa878bbd104776bb3e1ac7818d5397b04a666ac57f682763ff2df31bc2f846ddc7904f3dce1bda0c4b01f9698e9166f98c84f92640abe3f6317834a42e5c12573b46842c1a6ea8fe6ca9c006dca48a2087e1f983dfb5692771e4a0b15b337ad669b1d08b40862b176bace5331b49a767375b1a8469012aa70a67b55fe71b72478201d0b3ac7269c064c960361e92ee7ba8a42cc6b582bf9b965fb388fa9172ad44c4b073ac23c02080a6bcd106b691ffdf4c7cf71e42f2063fcf196e9bddc5e85be1fe5048eb1b31b460efbb46d76195eef9904c4cba326f2e17ff51fec17dd965aa06dadd4ab07d6966e4a7c38e8afd66dfde56c872bb87516a8f7313c797e4d80e5ad4a5f7afad95c1e0449254adae052e71a3fb98399f93ab30848e0d23252dd45e6fd41bc5fa7303dfb846a8fd713a0032a0b0ae96dfba1bbe41d20abd8099e2cb7fccc329d25bd153029139ef05d090a5093ea557693c0a8d491395b8a23ca844b3887dd5dfc29ac06f4b9be87883a793211b973465e2644c4f5de02b8ab01571401203fc2740423826858cc6da0194c195d27aac4ec4b9d23d506c1501d06440aca3fc180926d75d4a004d3dff21eb4ea1e8c86e6c9627248eff953e1d192d5c8efc006a7e512388ef2ebdb72ee3ad42d719f28e8f994e12ebf4c79f2ffe3abd7408ccd2236a7b89b2606247a732c10c4"
)

def _encode_proto_std(fields):
    out = b""
    for f in sorted(fields.keys()):
        v = fields[f]
        if isinstance(v, bool):
            out += encode_varint((f << 3) | 0) + encode_varint(int(v))
        elif isinstance(v, int):
            out += encode_varint((f << 3) | 0) + encode_varint(v)
        elif isinstance(v, (str, bytes)):
            if isinstance(v, str): v = v.encode()
            out += encode_varint((f << 3) | 2) + encode_varint(len(v)) + v
    return out

# ==================== DEVICE ====================
def _generate_new_device() -> dict:
    device_list = [
        ("Samsung","SM-G998B","Adreno (TM) 660","Android OS 12 / API-31"),
        ("Xiaomi","2201122G","Adreno (TM) 730","Android OS 13 / API-33"),
        ("Realme","RMX3700","Mali-G710","Android OS 14 / API-34"),
        ("OnePlus","CPH2451","Adreno (TM) 740","Android OS 13 / API-33"),
        ("OPPO","CPH2611","Adreno (TM) 720","Android OS 14 / API-34"),
    ]
    brand, model, gpu, os_ver = random.choice(device_list)
    return {"unique_device_id":f"Google|{uuid.uuid4()}","brand":brand,"model":model,
            "gpu_renderer":gpu,"system_software":os_ver,
            "screen_width":random.choice([1080,1440,720]),
            "screen_height":random.choice([2400,3200,1600]),
            "screen_dpi":str(random.randint(300,420)),
            "memory":random.randint(2800,6500)}

def get_device_for_account(acc_key: str) -> dict:
    devices = {}
    if os.path.exists(DEVICES_FILE):
        try:
            with open(DEVICES_FILE,"r",encoding="utf-8") as f:
                devices = json.load(f)
                if not isinstance(devices, dict): devices = {}
        except Exception: devices = {}
    if acc_key in devices: return devices[acc_key]
    nd = _generate_new_device()
    devices[acc_key] = nd
    try:
        with open(DEVICES_FILE,"w",encoding="utf-8") as f:
            json.dump(devices, f, indent=2)
    except Exception: pass
    return nd

def encode_f14(s):
    ks = [0x30,0x30,0x30,0x32,0x30,0x31,0x37,0x30,
          0x30,0x30,0x30,0x30,0x32,0x30,0x31,0x37,
          0x30,0x30,0x30,0x30,0x30,0x32,0x30,0x31,
          0x37,0x30,0x30,0x30,0x30,0x30,0x32,0x30]
    return bytes(ord(c) ^ ks[i % len(ks)] for i, c in enumerate(s))

# ==================== DNS ====================
_DNS_CACHE: Dict[str, Tuple[str,float]] = {}
_DNS_CACHE_TTL = 300.0

async def resolve_host_cloudflare(hostname: str) -> str:
    if not hostname: return hostname
    parts = hostname.split('.')
    if len(parts)==4 and all(p.isdigit() for p in parts): return hostname
    now = time.time()
    if hostname in _DNS_CACHE:
        ip, exp = _DNS_CACHE[hostname]
        if now < exp: return ip
    try:
        loop = asyncio.get_running_loop()
        info = await loop.getaddrinfo(hostname, None, family=socket.AF_INET)
        if info:
            ip = info[0][4][0]
            _DNS_CACHE[hostname] = (ip, now + _DNS_CACHE_TTL)
            return ip
    except Exception: pass
    return hostname

def optimize_tcp_socket(sock):
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
    except Exception: pass

async def safe_close_writer(writer):
    if not writer: return
    try:
        if not writer.is_closing(): writer.close()
        await asyncio.wait_for(writer.wait_closed(), timeout=1.5)
    except Exception: pass

def optimize_udp_socket(sock):
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 131072)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 131072)
    except Exception: pass

# ==================== HTTP CLIENT ====================
client = httpx.AsyncClient(verify=False, timeout=15.0,
    limits=httpx.Limits(max_connections=300, max_keepalive_connections=150))
_LOGIN_SEMAPHORE = asyncio.Semaphore(4)

# ==================== CRYPTO / PROTOBUF ====================
CRC7_TABLE = bytes([
    0,9,18,27,36,45,54,63,72,65,90,83,108,101,126,119,
    25,16,11,2,61,52,47,38,81,88,67,74,117,124,103,110,
    50,59,32,41,22,31,4,13,122,115,104,97,94,87,76,69,
    43,34,57,48,15,6,29,20,99,106,113,120,71,78,85,92,
    100,109,118,127,64,73,82,91,44,37,62,55,8,1,26,19,
    125,116,111,102,89,80,75,66,53,60,39,46,17,24,3,10,
    86,95,68,77,114,123,96,105,30,23,12,5,58,51,40,33,
    79,70,93,84,107,98,121,112,7,14,21,28,35,42,49,56,
    65,72,83,90,101,108,119,126,9,0,27,18,45,36,63,54,
    88,81,74,67,124,117,110,103,16,25,2,11,52,61,38,47,
    115,122,97,104,87,94,69,76,59,50,41,32,31,22,13,4,
    106,99,120,113,78,71,92,85,34,43,48,57,6,15,20,29,
    37,44,55,62,1,8,19,26,109,100,127,118,73,64,91,82,
    60,53,46,39,24,17,10,3,116,125,102,111,80,89,66,75,
    23,30,5,12,51,58,33,40,95,86,77,68,123,114,105,96,
    14,7,28,21,42,35,56,49,70,79,84,93,98,107,112,121,
])
_DELTA = 0x9E3779B9
_ROUNDS = 16
_FIELD_SIZES = {0:1,1:2,2:2,3:1,4:2}
_FIELD_NAMES = {0:"sendOption",1:"cmd",2:"orderId",3:"flags",4:"length"}

def crc7_sync(data: bytes) -> int:
    c = 0
    for b in data:
        c = CRC7_TABLE[((2*(c & 0xFF)) ^ (b & 0xFF)) & 0xFF] & 0x7F
    return c & 0x7F

def encode_varint(n: int) -> bytes:
    out = bytearray()
    if n < 0: n &= 0xFFFFFFFFFFFFFFFF
    while True:
        b = n & 0x7F; n >>= 7
        if n: b |= 0x80
        out.append(b)
        if not n: break
    return bytes(out)

def _pb_varint(fn, val): return encode_varint((fn<<3)|0) + encode_varint(val)
def _pb_length(fn, val):
    enc = val.encode('utf-8') if isinstance(val,str) else val
    return encode_varint((fn<<3)|2) + encode_varint(len(enc)) + enc

def create_proto(fields) -> bytes:
    packet = bytearray()
    for field, value in fields.items():
        if isinstance(field, str): field = int(field)
        if isinstance(value, dict):
            packet.extend(_pb_length(field, bytes(create_proto(value))))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    packet.extend(_pb_length(field, bytes(create_proto(item))))
                elif isinstance(item, str):
                    packet.extend(_pb_length(field, item))
                elif isinstance(item, (bytes, bytearray)):
                    packet.extend(_pb_length(field, bytes(item)))
                elif isinstance(item, bool):
                    packet.extend(_pb_varint(field, 1 if item else 0))
                elif isinstance(item, int):
                    packet.extend(_pb_varint(field, item))
        elif isinstance(value, bool):
            packet.extend(_pb_varint(field, 1 if value else 0))
        elif isinstance(value, int):
            packet.extend(_pb_varint(field, value))
        elif isinstance(value, str):
            packet.extend(_pb_length(field, value))
        elif isinstance(value, (bytes, bytearray)):
            packet.extend(_pb_length(field, bytes(value)))
    return bytes(packet)

def encrypt_aes(hex_data: str) -> str:
    cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
    return cipher.encrypt(pad(bytes.fromhex(hex_data), AES.block_size)).hex()

def decode_varint(data, offset):
    result = 0; shift = 0
    while offset < len(data):
        byte = data[offset]
        result |= (byte & 0x7F) << shift
        offset += 1
        if not (byte & 0x80): return result, offset
        shift += 7
    return None, offset

def decode_protobuf_2(data):
    result = {}; offset = 0
    data_len = len(data)
    while offset < data_len:
        header, offset = decode_varint(data, offset)
        if header is None: break
        fn = header >> 3; wt = header & 0x7
        if wt == 0:
            v, offset = decode_varint(data, offset)
            if v is not None: result[fn] = v
        elif wt == 2:
            length, offset = decode_varint(data, offset)
            if length is None: break
            value = data[offset:offset+length]; offset += length
            try:
                s = value.decode('utf-8')
                result[fn] = s if s else value.hex()
            except UnicodeDecodeError:
                nested = decode_protobuf_2(value)
                result[fn] = nested if nested else value.hex()
        elif wt == 1: offset += 8
        elif wt == 3: offset += 4
        else: break
    return result

def extract_raw_fields(data: bytes) -> dict:
    result = {}; offset = 0; data_len = len(data)
    while offset < data_len:
        header, offset = decode_varint(data, offset)
        if header is None: break
        fn = header >> 3; wt = header & 0x7
        if wt == 0:
            v, offset = decode_varint(data, offset)
            if v is not None: result[fn] = v
        elif wt == 2:
            length, offset = decode_varint(data, offset)
            if length is None: break
            if offset + length > data_len: break
            result[fn] = bytes(data[offset:offset+length]); offset += length
        elif wt == 1: offset += 8
        elif wt == 3: offset += 4
        else: break
    return result

def _parse_results_sync(results):
    out = {}
    for r in results:
        fd = {"wire_type": r.wire_type}
        if r.wire_type == "varint": fd["data"] = r.data
        elif r.wire_type in ("string","bytes"): fd["data"] = r.data
        elif r.wire_type == "length_delimited":
            if hasattr(r.data, "results"): fd["data"] = _parse_results_sync(r.data.results)
            elif isinstance(r.data, list): fd["data"] = _parse_results_sync(r.data)
            else: fd["data"] = str(r.data)
        out[str(r.field)] = fd
    return out

def decode_protobuf_dict(data: bytes) -> dict:
    try:
        parsed = Parser().parse(data)
        return _parse_results_sync(parsed)
    except Exception:
        return {}

async def decode_protobuf_async(data):
    try:
        parsed = Parser().parse(data)
        return json.dumps(_parse_results_sync(parsed))
    except Exception:
        return "{}"

# ==================== ZIGZAG / ULEB ====================
def zigzag_encode(n: int) -> bytes:
    z = (n << 1) if n >= 0 else ((-n) << 1) - 1
    return encode_varint(z)

def uleb_encode(n: int) -> bytes:
    return encode_varint(n)

def create_packet(Pk, N, K, V):
    if isinstance(K, str): K = bytes.fromhex(K) if len(K)==32 else K.encode()
    if isinstance(V, str): V = bytes.fromhex(V) if len(V)==32 else V.encode()
    PkEnc = AES.new(K, AES.MODE_CBC, V).encrypt(pad(bytes.fromhex(Pk),16)).hex()
    L = len(PkEnc)//2
    hexlen = hex(L)[2:]
    if len(hexlen)%2 != 0: hexlen = '0'+hexlen
    total_pad = 12 - len(N) - len(hexlen)
    if total_pad < 0: total_pad = 0
    return bytes.fromhex(N + ('0'*total_pad) + hexlen + PkEnc)

# ==================== MOVEMENT ====================
def build_movement_packet_2001(key_bytes, msg_key, player_state, x, y, z,
                                tick, counter, move_state, account_uid,
                                room_code, phase="arena"):
    if phase == "game":
        TAIL = (b"\x00"*7 + b"\x93\xca\xd5\xf9\x0b" + b"\x8e\xfe\xfc\xf7\x0b"
                + b"\xf4\xe0\xfe\xf7\x03" + b"\x00\x00")
    else:
        TAIL = (b"\x00"*7 + b"\xbd\xb6\xe1\xf3\x03" + b"\x00"
                + b"\xc4\xb6\xe1\xfb\x0b" + b"\x00\x00")
    body = bytearray()
    body += uleb_encode(int(account_uid))
    body += uleb_encode(int(player_state))
    body += zigzag_encode(int(x))
    body += zigzag_encode(int(y))
    body += zigzag_encode(int(z))
    body += b"\x00"
    body += uleb_encode(int(move_state))
    body += TAIL
    body += uleb_encode(int(tick))
    body += b"\x00\x00\x00"
    body += uleb_encode(int(counter))
    body = bytes(body)
    k = key_bytes[0]
    v80 = ((k << 8) | k) & 0xFFFF
    h = bytearray()
    h.append(msg_key)
    h.append(0x00)
    h.append(0x00 ^ k)
    h += ((2001 ^ v80) & 0xFFFF).to_bytes(2, "little")
    h += (len(body) ^ v80).to_bytes(2, "little")
    h.append(0x00 ^ k)
    h[1] = crc7_sync(bytes(h[2:]) + body)
    return bytes(h) + body

# ==================== TEAM PACKETS ====================
def create_team(key, iv, game_version="1.132.9"):
    fields = {
        1: 1, 2: {2: bytes.fromhex("01"), 3:1, 4:3, 5:"en",
            8: {1:"IDC3",2:123,3:"ME"}, 9:1,
            10: bytes.fromhex("01090a0b1219202729"), 11:2, 13:1,
            14: {1:"08FBA33B4BABE2790205390333330000009700050092005F72697EB2134DCBB2467625142201040e38e848dc0e748c3f6aa04d81000000ff1113090dcacfa16d",
                 2:478, 3:"7e585d55", 4:"y[Z]", 6:12, 7:"15686677735a7506101c",
                 8:str(game_version), 9:3, 10:1, 11:"03626253513637754174"},
            19: 329, 21: bytes.fromhex("374f521955"), 24:{1:21},
            27:"a_6534489873065906362"}}
    return create_packet(create_proto(fields).hex(), "0515", key, iv)

def team_fresh(account_id, key, iv):
    return create_packet(create_proto({1:72, 2:{1:int(account_id)}}).hex(), "0515", key, iv)

def invite_player_in_team(target_uid, key, iv):
    return create_packet(create_proto({1:2, 2:{1:int(target_uid),2:"ME",4:1}}).hex(), "0515", key, iv)

def accept_team_invite(target_uid, group_id, key, iv):
    fields = {1:4, 2:{1:int(target_uid), 3:int(target_uid),
        4: bytes.fromhex("0107090a0b120f19202729"), 8:1,
        9: {1:"08FBA33B0522052002041B0222220007000C00010004000547E265AE1080779546762514110104076fa2e8770e748c3f6a68ed75000000ff08080500cacfa16d",
            2:235, 3:{14:80,11:86}, 4:"{Y\\R", 6:13, 7:{2:4},
            8:"1.132.9", 9:2, 10:1, 11:"0362625351"},
        10: str(group_id),
        11: {"1":"IDC3","2":107,"3":"ME"}, 13:"en", 16:"374f5219",
        20:{1:21}, 23:"a_6534489873065906362",
        24:"https://dl-sg-production.freefiremobile.com/",
        27:{1:2, 2:4}}}
    return create_packet(create_proto(fields).hex(), "0515", key, iv)

def start_match_team(account_id, key, iv, region="ME"):
    fields = {1:9, 2:{1:int(account_id), 7:{1:"IDC3", 2:410, 3:region}}}
    return create_packet(create_proto(fields).hex(), "0515", key, iv)

def ready_for_game(uid, key, iv):
    fields = {1: 15, 2: {1: uid, 2: 1}}
    return create_packet(create_proto(fields).hex(), "0519", key, iv)

# ==================== KEY EXTRACT ====================
def _extract_key_iv(res_json):
    if "__RAW_HEX__" in res_json and "__OFFSET__" in res_json:
        try:
            raw = bytes.fromhex(res_json["__RAW_HEX__"])
            off = int(res_json["__OFFSET__"])
            rf = extract_raw_fields(raw[off:])
            for kf, vf in [(22,23),(24,25),(6,7)]:
                k = rf.get(kf); v = rf.get(vf)
                if isinstance(k,(bytes,bytearray)) and len(k)==16 and \
                   isinstance(v,(bytes,bytearray)) and len(v)==16:
                    return bytes(k), bytes(v)
        except Exception: pass
    found = {}
    for k, v in res_json.items():
        if str(k).startswith("__"): continue
        if not isinstance(v, dict): continue
        data = v.get("data")
        if data is None: continue
        raw = None
        if isinstance(data, (bytes,bytearray)) and len(data)==16: raw = bytes(data)
        elif isinstance(data, str) and len(data)==32:
            try:
                c = bytes.fromhex(data)
                if len(c)==16: raw = c
            except Exception: pass
        if raw and len(raw)==16:
            try: found[int(k)] = raw
            except Exception: pass
    if len(found) < 2: return None, None
    for kf, vf in [(22,23),(24,25),(6,7)]:
        if kf in found and vf in found: return found[kf], found[vf]
    sk = sorted(found.keys())
    return found[sk[0]], found[sk[1]]

async def aes_encrypt(payload, key, iv):
    return AES.new(key, AES.MODE_CBC, iv).encrypt(pad(payload, AES.block_size))

# ==================== VERSION / LOGIN ====================
def get_app_version():
    try: return play_scraper('com.dts.freefireth').get('version')
    except Exception: return "1.132.9"

async def version_config(region="ME"):
    app_version = get_app_version()
    lang = REGION_LANG.get(region.upper(), "en")
    url = (f"https://version.ggwhitehawk.com/live/ver.php?version={app_version}"
           f"&lang={lang}&device=android&channel=android&appstore=googleplay"
           f"&region={region.upper()}&whitelist_version=1.3.0&whitelist_sp_version=1.0.0")
    try:
        r = await client.get(url)
        j = r.json()
        server_url = j.get("server_url")
        remote_version = j.get("remote_version")
        latest_release_version = j.get("latest_release_version")
        gops = [u.strip() for u in j.get("gop_url","").split(";") if u.strip()]
        gop_1 = gops[0] if gops else "https://ffmconnect.ppmainecoonghj.com"
        if not server_url: return None
        return latest_release_version, remote_version, server_url, gop_1, app_version
    except Exception as e:
        print_error(f"[VERCONFIG] {e}"); return None

async def get_access_token(uid, password):
    url = "https://100067.connect.garena.com/oauth/guest/token/grant"
    hdrs = {"Host":"100067.connect.garena.com",
        "User-Agent":"Dalvik/2.1.0 (Linux; U; Android 12; SM-G998B)",
        "Content-Type":"application/x-www-form-urlencoded",
        "Accept-Encoding":"gzip, deflate, br","Connection":"close"}
    data = {"uid":str(uid),"password":password,"response_type":"token","client_type":"2",
        "client_secret":HEX_KEY,"client_id":"100067"}
    for _ in range(4):
        try:
            r = await client.post(url, headers=hdrs, data=data)
            if r.status_code == 200:
                j = r.json()
                oid = j.get("open_id"); at = j.get("access_token"); pf = j.get("platform",4)
                if oid and at: return oid, at, pf
        except Exception: pass
        await asyncio.sleep(0.7)
    return None

def generate_signature(payload_str):
    return hmac.new(API_KEY, payload_str.encode(), hashlib.sha256).hexdigest()

async def register_account(password, gop_url, app_version):
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    rp = {"app_id":100067,"client_type":2,"password":password,"source":2}
    payload = json.dumps(rp, separators=(',',':'))
    sig = generate_signature(payload)
    headers = {
        "User-Agent": f"GarenaMSDK/4.0.44(SM-E135F ;Android 14;en;GB;app {app_version} 2019118525;)",
        "Authorization": f"Signature {sig}",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8"}
    try:
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.post(f"{gop_url}/api/v2/oauth/guest:register",
                              headers=headers, data=payload,
                              timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = json.loads(await resp.text())
                if data.get("code") == 0: return data["data"]["uid"]
    except Exception as e:
        print_error(f"[REGISTER] {e}")
    return None

async def grant_token(uid, password, gop_url, app_version):
    connector = aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())
    gp = {"client_id":100067,"client_secret":HEX_KEY,"client_type":2,
          "device_id":f"02-{uuid.uuid4()}","password":password,
          "response_type":"token","uid":int(uid)}
    payload = json.dumps(gp, separators=(',',':'))
    sig = generate_signature(payload)
    headers = {
        "User-Agent": f"GarenaMSDK/4.0.44(SM-E135F ;Android 14;en;GB;app {app_version} 2019118525;)",
        "Authorization": f"Signature {sig}",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8"}
    try:
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.post(f"{gop_url}/api/v2/oauth/guest/token:grant",
                              headers=headers, data=payload,
                              timeout=aiohttp.ClientTimeout(total=15)) as resp:
                data = json.loads(await resp.text())
                if data.get("code") == 0: return data["data"]
    except Exception as e:
        print_error(f"[GRANT] {e}")
    return None

# ==================== PAYLOAD ====================
async def get_payload(access_token, open_id, platform, version, lang_code="en"):
    device_id = f"Google|{uuid.uuid4()}"
    major_login = {
        "3": str(datetime.now())[:-7], "4": "free fire", "5": 1,
        "7": f"{version}",
        "8": "Android OS 14 / API-34 (UP1A.231005.007/E135FXXSEEZE2)",
        "9": "Handheld", "11": "CarrierDataNetwork",
        "12": 1672, "13": 750, "14": "338",
        "15": "ARMv7 VFPv3 NEON | 2002 | 8",
        "16": 3702, "17": "Mali-G52",
        "18": "OpenGL ES 3.2 v1.r38p1-01bet0-mbs2v41_0.6d20ec041e51b2f2d25dfc265586ebe8",
        "19": device_id, "20": "104.28.197.151", "21": lang_code,
        "22": f"{open_id}", "23": f"{platform}", "24": "Handheld",
        "25": "samsung SM-E135F", "26": "ME",
        "29": f"{access_token}", "30": 1, "42": "Cellular",
        "57": "7428b253defc164018c604a1ebbfebdf",
        "60": 52037, "61": 5355, "62": 3723, "63": 3,
        "64": 5483, "65": 52037, "66": 5483, "67": 52037,
        "73": 2,
        "74": "/data/app/~~yACsV8QOk9OexgajWQP30A==/com.dts.freefireth-Bi_wPVrrhYM2nYncc29q7Q==/lib/arm",
        "76": 1,
        "77": "b8e0cd5e295eee42f5860d3c86e483dd|/data/app/~~yACsV8QOk9OexgajWQP30A==/com.dts.freefireth-Bi_wPVrrhYM2nYncc29q7Q==/base.apk",
        "78": 3, "79": 1, "81": "32", "83": "2019121229",
        "86": "OpenGLES2", "87": 8191, "88": int(platform),
        "92": 16091, "93": "android",
        "94": "KqsHT3Yeqo3RLRl8efNiL3aL/1SHwlSrtK0DwNAqdkNBQz70TeVy+icaIwuR3UK4b65Uf3IG5xBFEv0uu+jQZI+rOYCmNCsXjJT4QcZaYgoaaUGG",
        "95": 111107,
        "96": "{\"cur_rate\":null,\"support_etc2\":false}",
        "97": 1, "98": 1,
        "99": f"{platform}", "100": f"{platform}",
        "102": "4503454457550c0766",
        "104": 53792, "105": 1,
        "106": "https://dl-bs.ggpolarbear.com/live/ABHotUpdates/|https://core-bs.ggpolarbear.com/live/ABHotUpdates/|6b2078db9d22dd98f8e9386a39af8462",
        "107": "c8e41b7a93f02d56e1a94c7b8203f5d1",
    }
    get_login_data = dict(major_login)
    get_login_data["90"] = "Khulna"
    get_login_data["91"] = "D"
    get_login_data["99"] = "0"
    get_login_data["103"] = 1
    proto1 = create_proto(major_login).hex()
    proto2 = create_proto(get_login_data).hex()
    return bytes.fromhex(encrypt_aes(proto1)), bytes.fromhex(encrypt_aes(proto2))

async def major_login(payload, login_url, release_version):
    url = f"{login_url.rstrip('/')}/MajorLogin"
    req_headers = {
        "Accept": "*/*", "Authorization": "Bearer ",
        "Content-Type": "application/x-www-form-urlencoded",
        "ReleaseVersion": f"{release_version}",
        "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "X-GA": "v1 1", "X-GA-SV": str(int(time.time())),
        "X-Unity-Version": "2018.4.12f1",
    }
    try:
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(url, headers=req_headers, data=payload, ssl=ssl_context) as response:
                response_content = await response.read()
                print_info(f"[MAJOR-LOGIN] status={response.status} len={len(response_content)}")
                if response.status != 200:
                    print_warning(f"[MAJOR-LOGIN] HTTP {response.status}: {response_content[:120]!r}")
                    return None
                if len(response_content) < 200:
                    print_warning(f"[MAJOR-LOGIN] Short: {response_content.hex()}")
                    return None

                best = None; best_score = -1; best_off = 0
                offsets_to_try = [64, 0, 48, 32, 80, 16, 128, 8, 96, 60, 56, 40, 24, 20, 12, 4]
                for offset in offsets_to_try:
                    if offset >= len(response_content): continue
                    cb = response_content[offset:]
                    cand = None
                    try: cand = decode_protobuf_dict(cb)
                    except Exception: cand = None
                    if not cand:
                        try:
                            c2 = decode_protobuf_2(cb)
                            if c2: cand = {str(k): {"data": v} for k, v in c2.items()}
                        except Exception: cand = None
                    if not cand: continue
                    keys = set(cand.keys())
                    has_jwt = ("8" in keys) or (8 in keys)
                    has_url = ("10" in keys) or (10 in keys)
                    score = len(cand) + (100 if has_jwt else 0) + (50 if has_url else 0)
                    if score > best_score:
                        best = cand; best_score = score; best_off = offset
                    if has_jwt and has_url: break
                if not best:
                    print_warning(f"[MAJOR-LOGIN] Could not decode response")
                    return None
                keys_found = sorted(best.keys(), key=lambda x: int(x) if str(x).isdigit() else 0)
                print_info(f"[MAJOR-LOGIN] decoded at offset {best_off} | fields={keys_found}")
                if ("8" not in best) and (8 not in best):
                    print_warning(f"[MAJOR-LOGIN] No JWT (field 8)")
                    return None
                best["__RAW_HEX__"] = response_content.hex()
                best["__OFFSET__"] = best_off
                return best
    except asyncio.TimeoutError:
        print_error("[MAJOR-LOGIN] TIMEOUT"); return None
    except Exception as e:
        print_error(f"[MAJOR-LOGIN] {e}"); traceback.print_exc(); return None

async def get_login_data(payload, jwt_token, server_url, release_version):
    url = f"{server_url.rstrip('/')}/GetLoginData"
    hdrs = {"Accept":"*/*","Authorization":f"Bearer {jwt_token}",
        "Content-Type":"application/x-www-form-urlencoded",
        "ReleaseVersion":f"{release_version}",
        "User-Agent":"UnityPlayer/2018.4.12f1","X-GA":"v1 1",
        "X-GA-SV":str(int(time.time())),"X-Unity-Version":"2018.4.12f1"}
    try:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
            async with s.post(url, headers=hdrs, data=payload, ssl=ssl_ctx) as r:
                if r.status != 200: return None
                content = await r.read()
                for off in [0,64,48,32,80,16,128,8,96]:
                    if off >= len(content): continue
                    try:
                        c = decode_protobuf_dict(content[off:])
                        if c and len(c) >= 3: return c
                    except Exception: pass
                for off in [0,64,48,32,80]:
                    if off >= len(content): continue
                    c = decode_protobuf_2(content[off:])
                    if c and len(c) >= 3:
                        return {str(k):{"data":v} for k,v in c.items()}
        return None
    except Exception: return None

async def get_player_personal_show(target_uid, jwt_token, server_url, release_version):
    url = f"{server_url}/GetPlayerPersonalShow"
    payload = bytes.fromhex(encrypt_aes(create_proto({"1":int(target_uid)}).hex()))
    hdrs = {"authorization":f"Bearer {jwt_token}",
        "host":server_url.replace("https://","").split("/")[0],
        "x-ga":"v1 1","content-type":"application/x-www-form-urlencoded",
        "releaseversion":f"{release_version}",
        "user-agent":"UnityPlayer/2022.3.47f1","accept":"*/*",
        "x-unity-version":"2022.3.47f1"}
    try:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.post(url, headers=hdrs, data=payload, ssl=ssl_ctx) as r:
                if r.status != 200: return None
                return decode_protobuf_2(await r.read())
    except Exception: return None

# ==================== CLAN ====================
def _get_region_from_jwt(jwt_token):
    try:
        p = jwt_token.split('.')[1]
        p += '=' * (-len(p) % 4)
        d = json.loads(base64.urlsafe_b64decode(p))
        return (d.get('lock_region') or d.get('noti_region') or 'BD').upper()
    except Exception: return "ME"

def _get_region_url(region):
    region = region.upper()
    if region == "IND": return "https://client.ind.freefiremobile.com"
    if region in ["BR","US","NA"]: return "https://client.us.freefiremobile.com"
    return "https://clientbp.ggpolarbear.com"

def _create_clan_payload(clan_id):
    if not _CLAN_PB_AVAILABLE: return None
    try:
        msg = reqClan_pb2.MyMessage()
        msg.field_1 = int(clan_id)
        raw = msg.SerializeToString()
        return AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(raw, AES.block_size))
    except Exception as e:
        print_error(f"[CLAN] payload: {e}"); return None

async def join_clan(jwt_token, clan_id):
    if not _CLAN_PB_AVAILABLE:
        print_warning("[CLAN] reqClan_pb2 not available"); return False
    try:
        region = _get_region_from_jwt(jwt_token)
        base_url = _get_region_url(region)
        url = base_url.rstrip("/") + "/RequestJoinClan"
        host = base_url.replace("https://","")
        payload = _create_clan_payload(clan_id)
        if not payload: return False
        hdrs = {"Authorization":f"Bearer {jwt_token}",
            "X-Unity-Version":"2018.4.11f1","X-GA":"v1 1","ReleaseVersion":"OB55",
            "Content-Type":"application/octet-stream","Host":host,
            "Connection":"Keep-Alive","Accept-Encoding":"gzip"}
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(url, headers=hdrs, data=payload, ssl=ssl_ctx) as r:
                if r.status == 200:
                    print_success(f"[CLAN] ✅ Join sent for {clan_id}"); return True
                print_warning(f"[CLAN] Join status {r.status}"); return False
    except Exception as e:
        print_error(f"[CLAN] {e}"); return False

# ==================== TCP ====================
async def build_tcp_startup_packet(account_id, token, server_time, key, iv, region="ID", typ='OnLine'):
    uid_hex = f"{int(account_id):016x}"
    ts_hex = f"{int(server_time):08x}"
    enc = (await aes_encrypt(token.encode(), key, iv)).hex()
    enc_len = f"{len(enc)//2:08x}"
    reg = str(region).upper() if region else "ID"
    if typ == 'OnLine':
        pfx = '7219' if reg == 'BD' else ('7214' if reg == 'IND' else '7215')
        return f"{pfx}{uid_hex}{ts_hex}00000000{enc_len}{enc}"
    pfx = '8119' if reg == 'BD' else ('8114' if reg == 'IND' else '8115')
    return f"{pfx}{uid_hex}{ts_hex}{enc_len}{enc}"

async def send_keep_alive(region="ID"):
    reg = str(region).upper() if region else "ID"
    return bytes.fromhex("0219" if reg == "ID" else ("0214" if reg == "IND" else "0215"))

async def start_game_battle_royale(region, client_version, writer, key, iv):
    packet = bytes.fromhex("080112800a0a010110013a110a044944433110aa011a064555524f50453a100a044944433210311a064555524f504540014a0801090a0b1219202758016291090a8001303838463832424630324139363736373032303130313030303030303030303030303136303030313030313530303032323246393745454530463030303030303436373632353134303030303030303030303030303030303030303030303030303030303030303030303030303066663030303030303030636163666131366410241afb02735d5e571400024a775d45414d1a041b1c001f11010449715f4243481a001e1d071c1703004b1a4066785c524570735c51486775421b5c5a4c07504042685a63610816054e19025e75196001477c015165406370195f5547404e4550640103020f1304064863754268676c755f65576e40467e5f0a417a4701026d675d6e73670b1108495a4c6a0b78470b740065645e525a057258425f584a447d4e6759440c11044e7c596d7f4b625f7d04055a47505c4e1d6b5b4107447d7201057d7f0f14084e430457674f7e517d72015172415d027473577c4d615f79535256780911030f4d5e027a797f614165067806505d53777750475e75064257076500460817014e741e7e5078487e7a7c465e7669767153497064605a7376677773550d160148037e18675966787f4c42607a645f577e7b441b460776026b18685d0b110205490060020f70676175654674706671797f41067346677c4e06585e780f15074c57047b40517075415f6364027259674b5b0166407f7340600407770a22047a5d5c52300b3a0a167305067162727516134208312e3133302e3232480350015ae90403626253513635686e556f4e36416456324b796f566c636f477776484f624e56526c4d727073504b4f43654177616848494176795556497273743752737149734a7a786b3247525268377a2f637664626d504f6a73552f79626d38547a4c69586d2f474351696d494b53486833447955726f39515152756c34545350626d6d624b7949565937545671577059455372323646572f59624578507338514f706d317372785455736c30796a434144444d4f34616a654b615753366361496c554b4963797a494e396d52516f715277687939797257476d337a644345337a6a61436f492f5a585233656f65365a42647a64677654636b6b665733356e4d4c6a6a565072564b6433523172756174394e50514150724a5546627859696c4c5a3859707336654d5447666b6649793574666a526c314d4648706b51774c6373374439656378566c41636f374e664f6d2b30654756466c4434744478706771385533595973587645384842502f70666c767a737138316a32524f4d7857437556445442492f684735625462773166456e4249725162762b636144775147696f74554e316d4c4b77734379456f4766706746614251457645672b736a764c4c78704743334c304a5344532f74526169504354553344374e6249306547516651622f5a466f4c36455630775a324d6f583932414c572f5049752f56634663584e70596b356f7966326151416a536971486a2f363276354843644f525551303578754e6171795251625653704654303137655237675255636b4966366c6f447476342b514e4a4670766d74757077707774396a5a5974437a4b56743657726d6e36785837706658456251555434684f3758a201050803108703a201050804108103a20105080510c001a20105081d10cc01a2010408161078a20105080e10af01a201020815")
    try:
        proto = thunderFF_pb2.StartMatch()
        proto.ParseFromString(packet)
        if hasattr(proto.main,'region_list') and len(proto.main.region_list) > 0:
            proto.main.region_list[0].region = region
            if len(proto.main.region_list) > 1: proto.main.region_list[1].region = region
        if hasattr(proto.main,'client_version'):
            proto.main.client_version.remote_version = client_version
        p = proto.SerializeToString()
    except Exception:
        p = packet
    enc = (await aes_encrypt(p, key, iv)).hex()
    plen = len(enc)//2
    hexlen = hex(plen)[2:]
    if len(hexlen) < 2: hexlen = "0" + hexlen
    reg = str(region).upper() if region else "ID"
    pfx = "031500" if reg == "ID" else ("031400" if reg == "IND" else "031500")
    final = pfx + "0"*(6-len(hexlen)) + hexlen + enc
    writer.write(bytes.fromhex(final))
    await writer.drain()
    print_info(f"[⚔] BR Match search sent ({plen}B)")

# ==================== UDP HELPERS ====================
async def has_ssan_zig(n):
    z = (n << 1) & 0xFFFFFFFFFFFFFFFF
    out = bytearray()
    while z >= 0x80:
        out.append((z & 0x7F) | 0x80); z >>= 7
    out.append(z); return bytes(out)

async def uleb_async(n):
    out = bytearray()
    while True:
        b = n & 0x7F; n >>= 7
        if n: b |= 0x80
        out.append(b)
        if not n: break
    return bytes(out)

async def tea_enc(v0, v1, k0, k1, k2, k3):
    s = 0
    for _ in range(_ROUNDS):
        s = (s + _DELTA) & 0xFFFFFFFF
        v0 = (v0 + (((((v1<<4)&0xFFFFFFFF)+k0)&0xFFFFFFFF ^
                      ((v1+s)&0xFFFFFFFF) ^
                      (((v1>>5)+k1)&0xFFFFFFFF)))) & 0xFFFFFFFF
        v1 = (v1 + (((((v0<<4)&0xFFFFFFFF)+k2)&0xFFFFFFFF ^
                      ((v0+s)&0xFFFFFFFF) ^
                      (((v0>>5)+k3)&0xFFFFFFFF)))) & 0xFFFFFFFF
    return v0, v1

async def tea_dec(v0, v1, k0, k1, k2, k3):
    s = (_DELTA * _ROUNDS) & 0xFFFFFFFF
    for _ in range(_ROUNDS):
        v1 = (v1 - (((((v0<<4)&0xFFFFFFFF)+k2)&0xFFFFFFFF ^
                      ((v0+s)&0xFFFFFFFF) ^
                      (((v0>>5)+k3)&0xFFFFFFFF)))) & 0xFFFFFFFF
        v0 = (v0 - (((((v1<<4)&0xFFFFFFFF)+k0)&0xFFFFFFFF ^
                      ((v1+s)&0xFFFFFFFF) ^
                      (((v1>>5)+k1)&0xFFFFFFFF)))) & 0xFFFFFFFF
        s = (s - _DELTA) & 0xFFFFFFFF
    return v0, v1

async def tea_cbc_encrypt(padded, key_bytes):
    k0,k1,k2,k3 = (struct.unpack_from("<I", key_bytes, o)[0] for o in (0,4,8,12))
    out = bytearray(len(padded))
    pc = bytearray(8); pi = bytearray(8)
    for i in range(0, len(padded), 8):
        x = bytearray(8)
        for j in range(8): x[j] = padded[i+j] ^ pc[j]
        e0,e1 = await tea_enc(struct.unpack_from("<I", x, 0)[0],
                              struct.unpack_from("<I", x, 4)[0], k0,k1,k2,k3)
        enc = bytearray(8)
        struct.pack_into("<I", enc, 0, e0)
        struct.pack_into("<I", enc, 4, e1)
        for j in range(8): out[i+j] = enc[j] ^ pi[j]
        pc[:] = out[i:i+8]; pi[:] = x
    return bytes(out)

async def tea_cbc_decrypt(body, key_bytes):
    k0,k1,k2,k3 = (struct.unpack_from("<I", key_bytes, o)[0] for o in (0,4,8,12))
    out = bytearray(len(body))
    pi = bytearray(8); pc = bytearray(8)
    x = bytearray(8); dec = bytearray(8)
    for i in range(0, len(body), 8):
        for j in range(8): x[j] = body[i+j] ^ pi[j]
        d0,d1 = await tea_dec(struct.unpack_from("<I", x, 0)[0],
                              struct.unpack_from("<I", x, 4)[0], k0,k1,k2,k3)
        struct.pack_into("<I", dec, 0, d0)
        struct.pack_into("<I", dec, 4, d1)
        for j in range(8): out[i+j] = dec[j] ^ pc[j]
        pc[:] = body[i:i+8]; pi[:] = dec
    return bytes(out)

async def build_padded(content):
    pad_len = (8 - (len(content) + 10) % 8) % 8
    return bytes([pad_len,0,0]) + b"\x00"*pad_len + content + b"\x00"*7

async def encode_header(layout, so, cmd, oid, flags, length, k, v80):
    out = bytearray()
    for code in layout:
        val = {0:so,1:cmd,2:oid,3:flags,4:length}[code]
        if _FIELD_SIZES[code] == 1:
            out.append((val & 0xFF) ^ k)
        else:
            v = ((val & 0xFFFF) ^ v80) & 0xFFFF
            out.append(v & 0xFF); out.append((v >> 8) & 0xFF)
    return bytes(out)

async def crc7_buff(crc, buf):
    c = crc & 0x7F
    for b in buf:
        c = CRC7_TABLE[((2*(c & 0xFF)) ^ (b & 0xFF)) & 0xFF] & 0x7F
    return c & 0x7F

async def sv_frame(msg_key, layout, so, cmd, oid, flags, content, key, encrypted=True):
    k = key[0]; v80 = ((k<<8)|k) & 0xFFFF
    body = await tea_cbc_encrypt(await build_padded(content), key) if encrypted else content
    hdr = bytearray([msg_key,0]) + await encode_header(layout, so, cmd, oid, flags, len(body), k, v80)
    pkt = bytearray(hdr + body)
    pkt[1] = await crc7_buff(0, bytes(pkt[2:])) & 0x7F
    return bytes(pkt)

async def build_match_startup_packets(token, udp_key, match_code, account_id, block_val,
                                      server_ip="", region="ID", client_version="1.132.9",
                                      client_version_code="2019121229",
                                      access_token="", mode_id=1, map_id=1):
    token = token.strip()
    udp_key = bytes.fromhex(udp_key)
    match_code = [int(ch) for ch in str(match_code).strip()]
    thunder_jwt = token[:660] if len(token) > 660 else token
    sharma_jwt = token[660:] if len(token) > 660 else ""
    eth = thunder_jwt.encode() if isinstance(thunder_jwt, str) else thunder_jwt
    esh = sharma_jwt.encode() if isinstance(sharma_jwt, str) else sharma_jwt
    garena420 = await has_ssan_zig(len(eth)) + eth
    reg = str(region).upper() if region else "ID"
    csoversea_block = bytes.fromhex(
        "ca0163736f7665727365612e7374726f6e67686f6c642e66726565666972656d6f62696c652e636f6d"
        "3b302e302e302e303b33342e3132362e37362e34353b33342e38372e3137372e31343b33342e38372e"
        "3137302e3233303b33352e3138352e3138332e35370000000000000100000000000000000000000001"
        "00000800000100000000000100a8a2d7bebd8d8bdf110200")
    mid = bytes.fromhex('0000000001000102030101') + await has_ssan_zig(len(reg)) + reg.encode()
    mid += bytes.fromhex('0001030003000004')
    mid += await has_ssan_zig(len(client_version)) + client_version.encode()
    mid += await has_ssan_zig(len(client_version_code)) + client_version_code.encode()
    mid += csoversea_block
    clean_ip = server_ip.split(':')[0] if server_ip else "0.0.0.0"
    mid += await has_ssan_zig(len(clean_ip)) + clean_ip.encode()
    clean_acc_tok = access_token.strip() if access_token else ""
    if clean_acc_tok:
        mid += await has_ssan_zig(len(clean_acc_tok)) + clean_acc_tok.encode()
    mid += await has_ssan_zig(len(esh)) + esh
    tg = (await uleb_async(int(account_id)) + await uleb_async(int(block_val)) +
          await uleb_async(1) + await uleb_async(int(mode_id)) +
          await uleb_async(int(block_val)) + await uleb_async(int(map_id)) + mid)
    process = await sv_frame(0x5E, match_code, 2, 447, 0, 1, garena420, udp_key)
    loading = await sv_frame(0x5A, match_code, 2, 448, 1, 1, tg, udp_key)
    return process.hex(), loading.hex()

async def build_hello_packet(text, key, layout):
    data = text.encode("utf-8")
    if len(data) > 25: raise ValueError("too long")
    content = b"\x10\x00\x00\x00" + data + b"\x00" * (29 - 4 - len(data))
    k = key[0]; v80 = ((k<<8)|k) & 0xFFFF
    layout = [int(c) for c in str(layout).strip()] if isinstance(layout, str) else list(layout)
    padded = await build_padded(content)
    enc_body = await tea_cbc_encrypt(padded, key)
    hbytes = await encode_header(layout, 1, 1, 0, 1, len(enc_body), k, v80)
    pkt = bytearray([0x63, 0x00]) + hbytes + enc_body
    pkt[1] = await crc7_buff(0, pkt[2:]) & 0x7F
    return bytes(pkt).hex()

async def classify(frame):
    cmd = frame["cmd"]
    msg_name = MESSAGE_ID_TO_NAME.get(cmd, f"UNKNOWN_{cmd}")
    if msg_name == "UDP_HELLO": return "HELLO"
    if msg_name == "UDP_ACK": return "ACK"
    if msg_name == "UDP_PING": return "PING"
    if msg_name == "RUDP_JOIN_MATCH": return "JOIN_MATCH"
    if msg_name.startswith("RUDP_"): return msg_name
    if msg_name.startswith("UDP_"): return msg_name
    return "DATA"

async def build_packet(msg_key, layout, so, cmd, oid, flags, content, key, encrypted=True):
    k = key[0]; v80 = ((k<<8)|k) & 0xFFFF
    body = await tea_cbc_encrypt(await build_padded(content), key) if encrypted else content
    hdr = bytearray([msg_key, 0])
    for code in layout:
        val = {0:so,1:cmd,2:oid,3:flags,4:len(body)}[code]
        if _FIELD_SIZES[code] == 1: hdr.append((val & 0xFF) ^ k)
        else:
            v = ((val & 0xFFFF) ^ v80) & 0xFFFF
            hdr.append(v & 0xFF); hdr.append((v >> 8) & 0xFF)
    pkt = bytearray(hdr + body)
    pkt[1] = await crc7_buff(0, bytes(pkt[2:])) & 0x7F
    return bytes(pkt)

async def layouts_from_mask(mask):
    ru = [int(c) for c in str(mask).strip()]
    nr = [c for c in ru if c != 2]
    return ru, nr

async def reply_for(frame, key, mask, ack_key=0x68, ping_key=0x6D, hello_key=0x5B, ack_style="short"):
    ru, nr = await layouts_from_mask(mask)
    typ = await classify(frame)
    if typ == "HELLO":
        if ack_style == "echo":
            content = frame["content"] if frame["content"] else b"\x10\x00\x00\x00"
            return typ, await build_packet(hello_key, nr, 1, 1, None, 1, content, key)
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, b"\x01\x00", key)
    if typ == "ACK":
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1,
                                        frame["content"] or b"\x01\x00", key)
    if typ == "PING":
        c = frame["content"]
        counter = c[:4] if len(c) >= 4 else c
        return typ, await build_packet(ping_key, nr, 0, 3, None, 0,
                                        counter + b"\x00\x00\x00", key, encrypted=False)
    if typ == "JOIN_MATCH":
        return typ, await build_packet(ack_key, nr, 0, 2, None, 1, b"\x02\x00", key)
    return typ, None

async def keepalive_ping(sock, ip, port, key_bytes, mask, stop_event):
    nr = (await layouts_from_mask(mask))[1]
    ping_keys = [0x66, 0x6D, 0x69, 0x6C, 0x6B, 0x6E, 0x6F, 0x70]
    loop = asyncio.get_event_loop(); i = 0
    while not stop_event.is_set():
        pk = ping_keys[i % len(ping_keys)]
        counter = int(time.time() * 1000) & 0xFFFFFFFF
        pkt = await build_packet(pk, nr, 0, 3, None, 0,
                                 struct.pack("<I", counter) + b"\x00\x00\x00",
                                 key_bytes, encrypted=False)
        try: await loop.sock_sendto(sock, pkt, (ip, port))
        except Exception: pass
        i += 1
        try: await asyncio.wait_for(stop_event.wait(), timeout=3.0)
        except asyncio.TimeoutError: pass

async def try_header(buf, layout, k, v80):
    off = 2; out = {}
    for code in layout:
        size = _FIELD_SIZES[code]
        if off + size > len(buf): return None
        out[_FIELD_NAMES[code]] = (buf[off] ^ k) if size == 1 else \
            ((buf[off] | (buf[off+1] << 8)) ^ v80) & 0xFFFF
        off += size
    out["headerLen"] = off
    return out

async def oicq_unpad(padded):
    if not padded or len(padded) < 8: return None
    if not all(padded[-1-i] == 0 for i in range(7)): return None
    pad_len = padded[0] & 0x07
    s = 3 + pad_len; e = len(padded) - 7
    return padded[s:e] if s < e else b""

async def decode_packet(packet, key, mask=None):
    data = bytes(packet) if isinstance(packet, bytes) else bytes.fromhex(packet)
    if len(data) < 8: return None
    k = key[0]; v80 = ((k<<8)|k) & 0xFFFF
    crc_ok = (data[1] & 0x7F) == await crc7_buff(0, data[2:])
    candidates = []
    if mask:
        ru, nr = await layouts_from_mask(mask)
        layouts = [("RUDP", ru), ("nonRUDP", nr)]
    else:
        layouts = [("RUDP", list(p)) for p in itertools.permutations([0,1,2,3,4])]
        layouts += [("nonRUDP", list(p)) for p in itertools.permutations([0,1,3,4])]
    for kind, layout in layouts:
        f = await try_header(data, layout, k, v80)
        if not f: continue
        if f["flags"] > 7 or f["sendOption"] > 7: continue
        if f["length"] != len(data) - f["headerLen"]: continue
        body = data[f["headerLen"]:f["headerLen"]+f["length"]]
        content = None; padded = None
        if f["flags"] & 1:
            if len(body) < 8 or len(body) % 8 != 0: continue
            padded = await tea_cbc_decrypt(body, key)
            content = await oicq_unpad(padded)
            if content is None: continue
        else:
            content = body
        score = (1 if crc_ok else 0) + (1 if content is not None else 0)
        candidates.append({"kind":kind, "layout":layout, "headerLen":f["headerLen"],
            "msgKey":data[0], "cmd":f["cmd"], "flags":f["flags"],
            "sendOption":f["sendOption"], "orderId":f.get("orderId"),
            "length":f["length"], "content":content, "crcOk":crc_ok, "score":score})
    if not candidates: return None
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[0]

# ==================== MATCH COUNTERS ====================
_match_counters: Dict[str, int] = {}
_match_counter_lock = asyncio.Lock()

async def _inc_match(uid):
    async with _match_counter_lock:
        _match_counters[uid] = _match_counters.get(uid, 0) + 1
        return _match_counters[uid]

async def _dec_match(uid):
    async with _match_counter_lock:
        if uid in _match_counters and _match_counters[uid] > 0:
            _match_counters[uid] -= 1
        return _match_counters.get(uid, 0)

# ==================== TOKEN CACHE ====================
_token_cache_memo: Dict[str, Any] = {}
_token_cache_memo_time: float = 0.0
_TOKEN_CACHE_MEMO_TTL = 5.0

def _json_serializer(obj):
    if isinstance(obj, (bytes, bytearray)):
        return {"__bytes_hex__": bytes(obj).hex()}
    raise TypeError(f"Type {type(obj)} not serializable")

def _json_deserializer(obj):
    if isinstance(obj, dict):
        if "__bytes_hex__" in obj and len(obj) == 1:
            try: return bytes.fromhex(obj["__bytes_hex__"])
            except Exception: return b""
        return {k: _json_deserializer(v) for k, v in obj.items()}
    if isinstance(obj, list): return [_json_deserializer(x) for x in obj]
    return obj

def _load_token_cache():
    global _token_cache_memo, _token_cache_memo_time
    now = time.time()
    if _token_cache_memo and (now - _token_cache_memo_time) < _TOKEN_CACHE_MEMO_TTL:
        return _token_cache_memo
    if not os.path.exists(TOKEN_CACHE_FILE): return {}
    try:
        with open(TOKEN_CACHE_FILE,"r",encoding="utf-8") as f: content = f.read().strip()
        if not content: return {}
        data = json.loads(content)
        parsed = _json_deserializer(data)
        _token_cache_memo = parsed; _token_cache_memo_time = now
        return parsed
    except Exception:
        try: os.remove(TOKEN_CACHE_FILE)
        except Exception: pass
        return {}

def _save_token_cache(cache):
    global _token_cache_memo, _token_cache_memo_time
    try:
        with open(TOKEN_CACHE_FILE,"w",encoding="utf-8") as f:
            json.dump(cache, f, indent=2, default=_json_serializer)
        _token_cache_memo = cache; _token_cache_memo_time = time.time()
    except Exception as e: print_error(f"Cache save: {e}")

def cache_get(uid):
    cache = _load_token_cache()
    e = cache.get(str(uid))
    if not e: return None
    if time.time() - e.get("cached_at",0) > TOKEN_CACHE_TTL:
        cache_invalidate(uid); return None
    if str(e.get("account_id","")).isdigit(): e["account_id"] = int(e["account_id"])
    if not isinstance(e.get("login_payload_data"), (bytes, bytearray)):
        cache_invalidate(uid); return None
    return e

def cache_set(uid, data):
    cache = _load_token_cache()
    e = dict(data); e["cached_at"] = time.time()
    cache[str(uid)] = e; _save_token_cache(cache)

def cache_invalidate(uid):
    cache = _load_token_cache()
    if str(uid) in cache:
        del cache[str(uid)]; _save_token_cache(cache)

# ==================== PLAY GAME ====================
async def play_game(server_ip_port, thunder, sharma, udp_key, match_code,
                    account_id, player_region, client_version, key, iv, match_index):
    match_start_time = time.time()
    ping_task = None; sock = None
    ping_stop = asyncio.Event()
    movement_stop = asyncio.Event()
    uid_str = str(account_id)
    completed_cleanly = False
    try:
        ip, port = server_ip_port.split(":")
        port = int(port)
        resolved_ip = await resolve_host_cloudflare(ip)
        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try: sock.bind(('0.0.0.0', 0))
        except Exception: pass
        optimize_udp_socket(sock); sock.setblocking(False)
        udp_key_bytes = bytes.fromhex(udp_key)
        hello_packet = await build_hello_packet(f"{account_id}_2585", udp_key_bytes, match_code)
        await loop.sock_sendto(sock, bytes.fromhex(hello_packet), (resolved_ip, port))

        ack_state = "waiting_for_hello_reply"
        thunder_sent = False
        join_match_received = False
        local_closed = False
        send_lock = asyncio.Lock()
        ping_task = asyncio.create_task(keepalive_ping(sock, resolved_ip, port, udp_key_bytes, match_code, ping_stop))
        last_activity = time.time()

        movement_counter = 0
        tick = 0
        move_x, move_y, move_z = 0, 0, 0

        async def movement_loop():
            nonlocal movement_counter, tick, move_x, move_y, move_z
            while not movement_stop.is_set():
                try:
                    move_x += random.randint(-30, 30)
                    move_y += random.randint(-30, 30)
                    move_z += random.randint(-5, 5)
                    if abs(move_x) > 2000: move_x = 0
                    if abs(move_y) > 2000: move_y = 0
                    if abs(move_z) > 500:  move_z = 0
                    tick = (tick + 1) & 0xFFFF
                    movement_counter = (movement_counter + 1) & 0xFFFF
                    pkt = build_movement_packet_2001(
                        key_bytes=udp_key_bytes, msg_key=0x5C,
                        player_state=1, x=move_x, y=move_y, z=move_z,
                        tick=tick, counter=movement_counter,
                        move_state=1, account_uid=account_id,
                        room_code=match_code, phase="arena")
                    try:
                        await loop.sock_sendto(sock, pkt, (resolved_ip, port))
                    except Exception: pass
                except Exception: pass
                try: await asyncio.wait_for(movement_stop.wait(), timeout=0.8)
                except asyncio.TimeoutError: pass

        async def send_thunder_sharma_inline():
            nonlocal ack_state, thunder_sent
            if thunder_sent: return
            async with send_lock:
                if thunder_sent: return
                try:
                    await loop.sock_sendto(sock, bytes.fromhex(thunder), (resolved_ip, port))
                    thunder_sent = True
                    await asyncio.sleep(0.3)
                    prepare_ack = await build_packet(0x68, (await layouts_from_mask(match_code))[1],
                                                     0, 2, None, 1, b"\x01\x00", udp_key_bytes)
                    await loop.sock_sendto(sock, prepare_ack, (resolved_ip, port))
                    await asyncio.sleep(0.4)
                    await loop.sock_sendto(sock, bytes.fromhex(sharma), (resolved_ip, port))
                    ack_state = "thunder_sharma_sent"
                    print_success(f"[MATCH #{match_index}] Playing in-game!")
                    asyncio.create_task(movement_loop())
                except Exception as e:
                    print_error(f"[MATCH #{match_index}] startup: {e}")

        while not local_closed:
            if time.time() - match_start_time > MAX_MATCH_DURATION:
                print_warning(f"[MATCH #{match_index}] Max duration {MAX_MATCH_DURATION}s reached")
                completed_cleanly = True; break
            try:
                response, server_addr = await asyncio.wait_for(loop.sock_recvfrom(sock, 65535), timeout=2.0)
                if response:
                    last_activity = time.time()
                    frame = await decode_packet(response, udp_key_bytes, match_code)
                    if frame:
                        ptype = await classify(frame)
                        # Only 103 (match_end) and 107 are real end-of-match commands.
                        if frame['cmd'] in [103, 107]:
                            print_success(f"[★] Match #{match_index} END (cmd={frame['cmd']})")
                            completed_cleanly = True; local_closed = True
                            continue
                        if frame['cmd'] == 101:
                            try:
                                ack = await build_packet(0x68, (await layouts_from_mask(match_code))[1],
                                                         0,2,None,1, b"\x01\x00", udp_key_bytes)
                                await loop.sock_sendto(sock, ack, server_addr)
                            except Exception: pass
                            continue
                        if ptype in ["ACK","PING","HELLO","JOIN_MATCH"]:
                            if ptype == "HELLO" and ack_state == "waiting_for_hello_reply":
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code, ack_style="short")
                                if reply: await loop.sock_sendto(sock, reply, server_addr)
                                ack_state = "ack_sent_waiting"
                            elif ptype == "ACK":
                                if ack_state == "waiting_for_hello_reply":
                                    typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                    if reply: await loop.sock_sendto(sock, reply, server_addr)
                                    ack_state = "ready_to_send_thunder"
                                elif ack_state == "ack_sent_waiting":
                                    ack_state = "ready_to_send_thunder"
                                else:
                                    typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                    if reply: await loop.sock_sendto(sock, reply, server_addr)
                            elif ptype == "PING":
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                if reply: await loop.sock_sendto(sock, reply, server_addr)
                            elif ptype == "JOIN_MATCH" and not join_match_received:
                                typ, reply = await reply_for(frame, udp_key_bytes, match_code)
                                if reply:
                                    await loop.sock_sendto(sock, reply, server_addr)
                                    join_match_received = True
            except asyncio.TimeoutError:
                if ack_state == "ready_to_send_thunder" and not thunder_sent:
                    await send_thunder_sharma_inline()
                elif ack_state == "waiting_for_hello_reply":
                    if (time.time() - last_activity) > 7.0:
                        try:
                            pkt = await build_hello_packet(f"{account_id}_2585", udp_key_bytes, match_code)
                            await loop.sock_sendto(sock, bytes.fromhex(pkt), (resolved_ip, port))
                        except Exception: pass
                        last_activity = time.time()
                    if (time.time() - match_start_time) > 25.0:
                        print_warning(f"[MATCH #{match_index}] Handshake timeout"); break
                elif ack_state == "thunder_sharma_sent":
                    if (time.time() - last_activity) > MATCH_IDLE_TIMEOUT:
                        # No UDP packets from the match server: the match never really
                        # started. Counting it as "completed" made the dashboard show
                        # matches>0 while EXP/glory stayed 0. Abort and retry instead.
                        print_warning(f"[MATCH #{match_index}] idle {MATCH_IDLE_TIMEOUT:.0f}s with no server packets — ABORTED (not counted)")
                        completed_cleanly = False
                        local_closed = True
                        break
            except (BlockingIOError, OSError):
                await asyncio.sleep(0.5); continue
            except Exception:
                await asyncio.sleep(0.5); continue
            if ack_state == "ready_to_send_thunder" and not thunder_sent:
                await send_thunder_sharma_inline()
        return f"match #{match_index} finished"
    except Exception as e:
        print_error(f"[MATCH #{match_index}] {e}")
        return f"match #{match_index} error"
    finally:
        if completed_cleanly:
            try:
                bot_state.increment_match(uid_str)
                print_success(f"[★] Match #{match_index} Complete | UID: {uid_str}")
                try:
                    await _mark_bot_match_finished(uid_str)
                except Exception as _me:
                    print_warning(f"[RESTART] mark failed: {_me}")
                async def _post_exp(uid):
                    try:
                        for wait in (5.0, 8.0, 12.0):
                            await asyncio.sleep(wait)
                            await refresh_account_profile(uid)
                    except Exception: pass
                asyncio.create_task(_post_exp(uid_str))
            except Exception: pass
        movement_stop.set()
        ping_stop.set()
        if ping_task:
            ping_task.cancel()
            try: await ping_task
            except asyncio.CancelledError: pass
        if sock:
            try: sock.close()
            except Exception: pass
        remaining = await _dec_match(uid_str)
        try: bot_state.update_status(uid_str, "ONLINE" if remaining == 0 else "IN_MATCH", remaining)
        except Exception: pass

# ==================== FUNCTIONAL LONE WOLF (4-BOT TEAM LOOP) ====================
async def functional_lone_wolf(addrs, starter_packet, account_region, client_version,
                                key, iv, account_id="", account_data=None, max_reconnects=10):
    reconnects = 0
    ip, port = addrs.split(":")
    play_matches: List[asyncio.Task] = []
    no_response_count = 0
    search_attempts = 0
    last_start_time = 0.0
    uid_str = str(account_id)

    current_token = starter_packet
    current_key = key
    current_iv = iv
    current_account_data = account_data
    _writer_ref = {"w": None}
    _team_active = {"flag": True}
    # once this bot has played its match, park it (TCP stays connected, only friend requests)
    _match_played = {"flag": False}

    async def _process_social_packet(raw_bytes, tag="MAIN"):
        try:
            hex_data = raw_bytes.hex()
            print_info(f"[SOCIAL-{tag}] len={len(raw_bytes)} hex={hex_data[:60]}")

            candidates = []

            if len(raw_bytes) >= 6:
                plen = raw_bytes[4]
                if 0 < plen <= (len(raw_bytes) - 5):
                    try:
                        inner = bytes(raw_bytes[5:5 + plen])
                        d0 = decode_protobuf_dict(inner)
                        if not d0:
                            a0 = decode_protobuf_2(inner)
                            if a0:
                                d0 = {str(k): {"data": v} for k, v in a0.items()}
                        if d0:
                            candidates.append((5, d0))
                            print_info(f"[SOCIAL-{tag}] direct-slice@5 len={plen}")
                    except Exception:
                        pass

            for off in [5, 6, 10, 8, 12, 14, 4, 16, 0]:
                if off >= len(hex_data): continue
                try:
                    inner_bytes = bytes.fromhex(hex_data[off:])
                except Exception: continue

                decoded = None
                try:
                    d1 = decode_protobuf_dict(inner_bytes)
                    if d1: decoded = d1
                except Exception: pass

                if not decoded:
                    try:
                        d2 = decode_protobuf_2(inner_bytes)
                        if d2: decoded = {str(k): {"data": v} for k, v in d2.items()}
                    except Exception: pass

                if decoded:
                    candidates.append((off, decoded))

            if not candidates:
                print_warning(f"[SOCIAL-{tag}] no decodable protobuf found")
                return

            def _score(item):
                _off, d = item
                s = len(d)
                if "0" in d or 0 in d: s -= 100
                if "5" in d: s += 6
                if "1" in d: s += 4
                if "2" in d: s += 2
                return s

            candidates.sort(key=_score, reverse=True)
            best_off, decoded = candidates[0]
            print_info(f"[SOCIAL-{tag}] decoded@{best_off} fields={list(decoded.keys())[:15]}")

            def _val(container, k):
                if not isinstance(container, dict): return None
                v = container.get(k) if k in container else container.get(str(k))
                if isinstance(v, dict) and "data" in v: return v["data"]
                return v

            f5wrap = decoded.get("5") or decoded.get(5)
            f5 = None
            if isinstance(f5wrap, dict) and "data" in f5wrap: f5 = f5wrap["data"]
            elif isinstance(f5wrap, dict): f5 = f5wrap

            target_uid = None
            if isinstance(f5, dict):
                for fk in [1, 2, 3, 4, 6]:
                    cand = _val(f5, fk)
                    if cand and str(cand).isdigit() and 6 <= len(str(cand)) <= 15:
                        target_uid = str(cand); break
            if not target_uid:
                for fk in [1, 2, 3]:
                    cand = _val(decoded, fk)
                    if cand and str(cand).isdigit() and 6 <= len(str(cand)) <= 15:
                        target_uid = str(cand); break
            if not target_uid:
                print_warning(f"[SOCIAL-{tag}] could not find target UID"); return

            own_acc = None
            if current_account_data:
                own_acc = str(current_account_data.get('account_id', ''))
            if own_acc and target_uid == own_acc:
                print_info(f"[SOCIAL-{tag}] skip (self)"); return
            if not _accept_friend_request or not current_account_data: return

            jwt_l = current_account_data.get('token')
            srv_l = current_account_data.get('server_url')
            rel_l = current_account_data.get('release_version')
            own_id = current_account_data.get('account_id')
            if not (jwt_l and srv_l and rel_l and own_id): return

            print_info(f"[FRIEND-{tag}] sending ConfirmFriendRequest: target={target_uid} own={own_id} srv={srv_l}")

            url = f"{srv_l.rstrip('/')}/ConfirmFriendRequest"
            fields = {"1": int(target_uid), "2": int(own_id)}
            try:
                payload = bytes.fromhex(encrypt_aes(create_proto(fields).hex()))
                hdrs = {
                    "authorization": f"Bearer {jwt_l}",
                    "host": srv_l.replace("https://", "").replace("http://", "").split("/")[0],
                    "x-ga": "v1 1",
                    "content-type": "application/x-www-form-urlencoded",
                    "accept-encoding": "deflate, gzip",
                    "releaseversion": f"{rel_l}",
                    "user-agent": "UnityPlayer/2022.3.47f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
                    "accept": "*/*", "x-unity-version": "2022.3.47f1",
                }
                ssl_ctx = ssl.create_default_context()
                ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
                async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
                    async with s.post(url, headers=hdrs, data=payload, ssl=ssl_ctx) as r:
                        body = await r.read()
                        if r.status == 200 and len(body) > 0:
                            print_success(f"[FRIEND-{tag}] ✅ Accepted {target_uid}")
                        else:
                            print_warning(f"[FRIEND-{tag}] HTTP {r.status} len={len(body)} for {target_uid}")
            except Exception as e:
                print_warning(f"[FRIEND-{tag}] {type(e).__name__}: {e}")
        except Exception as e:
            print_warning(f"[SOCIAL-{tag}] exception: {type(e).__name__}: {e}")

    async def _team_coordinator():
        try:
            while True:
                async with _team_state["lock"]:
                    n = len(_team_state["peers"])
                if n >= TEAM_SIZE: break
                if bot_state.is_paused(uid_str): return
                await asyncio.sleep(0.5)

            await asyncio.sleep(TEAM_READY_DELAY)

            async with _team_state["lock"]:
                is_leader = (_team_state["leader_uid"] == uid_str)
                peers_snapshot = dict(_team_state["peers"])

            if not is_leader:
                print_info(f"[TEAM] {uid_str} waiting as member..."); return

            print_success(f"[TEAM] 👑 {uid_str} — creating team...")

            async with _team_state["lock"]:
                _team_state["team_code"] = None

            own_id_num = None
            if current_account_data: own_id_num = current_account_data.get('account_id')
            if not own_id_num:
                try: own_id_num = int(uid_str)
                except Exception: own_id_num = 0

            try:
                pkt = create_team(current_key, current_iv, client_version)
                if _writer_ref["w"] and not _writer_ref["w"].is_closing():
                    _writer_ref["w"].write(pkt); await _writer_ref["w"].drain()
                    print_info(f"[TEAM] create_team sent ({len(pkt)}B)")
            except Exception as e:
                print_error(f"[TEAM] create_team err: {e}")

            await asyncio.sleep(1.5)

            try:
                pkt = team_fresh(int(own_id_num), current_key, current_iv)
                if _writer_ref["w"] and not _writer_ref["w"].is_closing():
                    _writer_ref["w"].write(pkt); await _writer_ref["w"].drain()
                    print_success(f"[TEAM] 🔄 team_fresh #1 sent ({len(pkt)}B)")
            except Exception as e:
                print_warning(f"[TEAM] team_fresh err: {e}")

            await asyncio.sleep(1.0)

            async def _invite_uid(target_uid, label=""):
                for attempt in range(2):
                    try:
                        inv = invite_player_in_team(int(target_uid), current_key, current_iv)
                        if _writer_ref["w"] and not _writer_ref["w"].is_closing():
                            _writer_ref["w"].write(inv); await _writer_ref["w"].drain()
                            print_info(f"[TEAM]   invite{label} to {target_uid} #{attempt+1} ({len(inv)}B)")
                    except Exception as e:
                        print_warning(f"[TEAM] invite err: {e}")
                    await asyncio.sleep(0.6)

            invite_count = 0
            for peer_uid, peer_info in peers_snapshot.items():
                if peer_uid == uid_str: continue
                peer_acc_id = None
                try: peer_acc_id = peer_info["data"].get("account_id")
                except Exception: pass
                if not peer_acc_id: peer_acc_id = peer_uid
                print_success(f"[TEAM] 📨 Inviting bot{invite_count+2} {peer_acc_id}...")
                await _invite_uid(int(peer_acc_id), f" bot{invite_count+2}")
                invite_count += 1

            await asyncio.sleep(TEAM_INVITE_WAIT + 3.0)

            try:
                pkt = team_fresh(int(own_id_num), current_key, current_iv)
                if _writer_ref["w"] and not _writer_ref["w"].is_closing():
                    _writer_ref["w"].write(pkt); await _writer_ref["w"].drain()
                    print_success(f"[TEAM] 🔄 team_fresh #2 sent ({len(pkt)}B)")
            except Exception as e:
                print_warning(f"[TEAM] team_fresh #2 err: {e}")

            await asyncio.sleep(1.0)

            print_success(f"[TEAM] 🚀 {uid_str} loop: ready → 1s → start (every 5s)")
            try:
                bot_state.update_status(uid_str, "SEARCHING", 0)
            except Exception:
                pass
            while _team_active["flag"] and not _match_played["flag"]:
                try:
                    if bot_state.is_paused(uid_str): break
                    async with _team_state["lock"]:
                        if _team_state["leader_uid"] != uid_str: break
                    own_id = current_account_data.get('account_id', own_id_num) if current_account_data else own_id_num
                    try:
                        rdy = ready_for_game(int(own_id), current_key, current_iv)
                        if _writer_ref["w"] and not _writer_ref["w"].is_closing():
                            _writer_ref["w"].write(rdy); await _writer_ref["w"].drain()
                    except Exception as e:
                        print_warning(f"[TEAM] ready_for_game err: {e}")
                    await asyncio.sleep(1.0)
                    sm = start_match_team(int(own_id), current_key, current_iv, region=account_region)
                    if _writer_ref["w"] and not _writer_ref["w"].is_closing():
                        _writer_ref["w"].write(sm); await _writer_ref["w"].drain()
                except Exception as e:
                    print_warning(f"[TEAM] start_match err: {e}")
                try: await asyncio.wait_for(asyncio.sleep(5.0), timeout=5.5)
                except asyncio.TimeoutError: pass
        except asyncio.CancelledError: raise
        except Exception as e:
            print_error(f"[TEAM] coordinator error: {e}")

    async def _member_match_trigger():
        try:
            await asyncio.sleep(TEAM_READY_DELAY + 18.0)
            async with _team_state["lock"]:
                if _team_state["leader_uid"] == uid_str: return
            print_success(f"[TEAM] 🚀 Member {uid_str} loop: ready → 1s → start (every 5s)")
            try:
                bot_state.update_status(uid_str, "SEARCHING", 0)
            except Exception:
                pass
            my_id = current_account_data.get('account_id', uid_str) if current_account_data else uid_str
            while _team_active["flag"] and not _match_played["flag"]:
                try:
                    if bot_state.is_paused(uid_str): break
                    if _writer_ref["w"] and not _writer_ref["w"].is_closing():
                        try:
                            rdy = ready_for_game(int(my_id), current_key, current_iv)
                            _writer_ref["w"].write(rdy); await _writer_ref["w"].drain()
                        except Exception as e:
                            print_warning(f"[TEAM] member ready err: {e}")
                        await asyncio.sleep(1.0)
                        sm = start_match_team(int(my_id), current_key, current_iv, region=account_region)
                        _writer_ref["w"].write(sm); await _writer_ref["w"].drain()
                except Exception as e:
                    print_warning(f"[TEAM] member trigger: {e}")
                try: await asyncio.wait_for(asyncio.sleep(5.0), timeout=5.5)
                except asyncio.TimeoutError: pass
        except asyncio.CancelledError: raise
        except Exception as e:
            print_warning(f"[TEAM] member trigger outer: {e}")

    try:
        while True:
            while bot_state.is_paused(uid_str):
                try: bot_state.update_status(uid_str, "PAUSED", 0)
                except Exception: pass
                await asyncio.sleep(1.0)

            writer = None
            gateway_ping_task = None
            team_coord_task = None
            member_trigger_task = None
            try:
                if current_account_data:
                    fresh = None
                    if current_account_data.get('auth_type') == 'guest' and current_account_data.get('auth_uid'):
                        fresh = cache_get(str(current_account_data['auth_uid']))
                    elif current_account_data.get('auth_type') == 'token' and current_account_data.get('auth_token'):
                        fresh = cache_get(f"tok_{current_account_data['auth_token'][:20]}")
                    if fresh:
                        current_account_data = fresh
                        current_key = fresh['aes_ak']; current_iv = fresh['iv_i']
                        current_token = await build_tcp_startup_packet(
                            fresh['account_id'], fresh['token'], fresh['server_time'],
                            current_key, current_iv,
                            region=fresh.get('region', account_region), typ='OnLine')
                    else:
                        raise ConnectionError("Cache expired")

                resolved_ip = await resolve_host_cloudflare(ip)
                reader, writer = await asyncio.open_connection(resolved_ip, int(port))
                bot_state.register_writer(uid_str, writer)
                _writer_ref["w"] = writer
                raw_sock = writer.get_extra_info('socket')
                if raw_sock: optimize_tcp_socket(raw_sock)
                writer.write(bytes.fromhex(current_token)); await writer.drain()
                try:
                    init_ka = await send_keep_alive(account_region)
                    if init_ka and not writer.is_closing():
                        writer.write(init_ka)
                        await asyncio.wait_for(writer.drain(), timeout=3)
                except Exception: pass

                async def func_gateway_keepalive():
                    ka_bytes = await send_keep_alive(account_region)
                    while True:
                        await asyncio.sleep(5)
                        try:
                            if writer and not writer.is_closing():
                                writer.write(ka_bytes); await writer.drain()
                        except Exception: break

                gateway_ping_task = asyncio.create_task(func_gateway_keepalive())
                print_success(f"[✓] TCP Gateway Connected | UID: {uid_str}")
                reconnects = 0
                no_response_count = 0

                async with _team_state["lock"]:
                    _team_state["peers"][uid_str] = {
                        "writer": writer, "data": current_account_data,
                        "key": current_key, "iv": current_iv}
                    if _team_state["leader_uid"] is None:
                        _team_state["leader_uid"] = uid_str
                        print_success(f"[TEAM] 👑 {uid_str} is first peer (leader)")

                async def send_solo_start_match():
                    nonlocal search_attempts
                    search_attempts += 1
                    try:
                        try:
                            bot_state.update_status(uid_str, "SEARCHING", 0)
                        except Exception:
                            pass
                        await asyncio.sleep(random.uniform(0.2, 0.4))
                        try:
                            own_id = current_account_data.get('account_id') if current_account_data else uid_str
                            rdy = ready_for_game(int(own_id), current_key, current_iv)
                            if writer and not writer.is_closing():
                                writer.write(rdy); await writer.drain()
                        except Exception: pass
                        await asyncio.sleep(1.0)
                        await start_game_battle_royale("ID", client_version, writer, current_key, current_iv)
                    except Exception as e:
                        print_warning(f"[!] Solo: {e}")

                if not _match_played["flag"]:
                    if not TEAM_MODE: await send_solo_start_match()
                    if TEAM_MODE:
                        team_coord_task = asyncio.create_task(_team_coordinator())
                        member_trigger_task = asyncio.create_task(_member_match_trigger())
                else:
                    print_info(f"[IDLE] {uid_str} resuming in idle mode (TCP only friend requests)")

                # ============ MAIN READ LOOP ============
                while True:
                    play_matches[:] = [m for m in play_matches if not m.done()]
                    if bot_state.is_paused(uid_str): break

                    if _match_played["flag"]:
                        try:
                            data = await asyncio.wait_for(reader.read(8192), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        if not data:
                            raise ConnectionError("Connection closed")
                        if len(data) > 20 and data.hex().startswith("0600"):
                            await _process_social_packet(data, tag="IDLE")
                        continue

                    now = asyncio.get_running_loop().time()
                    if not TEAM_MODE and len(play_matches) == 0 and (now - last_start_time >= START_MATCH_INTERVAL):
                        await send_solo_start_match()
                        last_start_time = now

                    try:
                        data = await asyncio.wait_for(reader.read(8192), timeout=0.5)
                    except asyncio.TimeoutError:
                        no_response_count += 1; continue
                    if not data:
                        raise ConnectionError("Connection closed")
                    hex_data = data.hex()
                    packet_length = len(data)
                    no_response_count = 0

                    if DEBUG_ALL_PACKETS:
                        print_info(f"[RAW-PKT] len={packet_length} prefix={hex_data[:20]}")

                    # 0600: friend request
                    if hex_data.startswith("0600") and packet_length > 20:
                        await _process_social_packet(data, tag="MAIN"); continue

                    # 0500: team/invite
                    if hex_data.startswith("0500") and packet_length > 10:
                        try:
                            inner = bytes.fromhex(hex_data[10:])
                            dec = decode_protobuf_dict(inner)
                            if not dec:
                                alt = decode_protobuf_2(inner)
                                if alt: dec = {str(k): {"data": v} for k, v in alt.items()}
                            f5w = dec.get("5") or dec.get(5)
                            f5 = f5w.get("data") if isinstance(f5w, dict) and "data" in f5w else f5w
                            if not isinstance(f5, dict): f5 = dec
                            def _f5g(key):
                                if not isinstance(f5, dict): return None
                                v = f5.get(key) if key in f5 else f5.get(str(key))
                                if isinstance(v, dict) and "data" in v: return v["data"]
                                return v
                            tc = None
                            for fk in [11, 5, 1, 3]:
                                cand = _f5g(fk)
                                if cand and str(cand).isdigit() and len(str(cand)) == 7:
                                    tc = str(cand); break
                            if tc:
                                async with _team_state["lock"]:
                                    _team_state["team_code"] = tc
                                print_success(f"[TEAM] 📢 Team Code captured: {tc}")
                            group_id = _f5g(8)
                            if group_id and isinstance(group_id, str) and 20 <= len(group_id) <= 40:
                                pass
                            else: group_id = None
                            own_acc = None
                            if current_account_data: own_acc = str(current_account_data.get('account_id'))
                            inviter = None
                            for fk in [1, 2, 3, 4, 6, 7, 13, 15, 20]:
                                cand = _f5g(fk)
                                if cand and str(cand).isdigit() and 6 <= len(str(cand)) <= 15:
                                    if str(cand) != uid_str and str(cand) != own_acc:
                                        inviter = cand; break
                            if inviter and group_id and _team_state["leader_uid"] != uid_str:
                                print_success(f"[TEAM] 📨 Invite from {inviter} → accepting...")
                                for _ in range(2):
                                    try:
                                        acc = accept_team_invite(int(inviter), group_id, current_key, current_iv)
                                        if writer and not writer.is_closing():
                                            writer.write(acc); await writer.drain()
                                            print_success(f"[TEAM] ✅ accept sent ({len(acc)}B)")
                                    except Exception as e:
                                        print_warning(f"[TEAM] accept err: {e}")
                                    await asyncio.sleep(0.4)
                        except Exception as e:
                            print_warning(f"[TEAM] 0500 parse: {e}")
                        continue

                    if hex_data.startswith("0300") and 10 < packet_length < 30:
                        continue

                    if hex_data.startswith("0300") and packet_length >= 300:
                        print_success(f"[⚔] Match Found | UID: {uid_str}")
                        # ▼▼ TANDAI MATCH DITEMUKAN → reset timer watchdog ▼▼
                        try:
                            await _mark_match_found(uid_str)
                        except Exception as _e:
                            print_warning(f"[NO-MATCH] mark failed: {_e}")
                        # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

                        # stop team triggers NOW — TCP stays connected, UDP does the match
                        for tk in (team_coord_task, member_trigger_task):
                            if tk and not tk.done():
                                tk.cancel()
                        team_coord_task = None
                        member_trigger_task = None
                        _team_active["flag"] = False

                        try:
                            res = json.loads(await decode_protobuf_async(hex_data[10:]))
                            token = udp_key = match_code = server_ip_port = None
                            match_account_id = block_val = None
                            if '42' in res and 'data' in res['42']: match_code = res['42']['data']
                            if '5' in res and 'data' in res['5']:
                                rf5 = res['5']['data']
                                server_ip_port = rf5.get('2', {}).get('data')
                                udp_key = rf5.get('3', {}).get('data')
                                token = rf5.get('4', {}).get('data')
                                if '42' in rf5: match_code = rf5['42']['data']
                            if '1' in res and 'data' in res['1']: match_account_id = res['1']['data']
                            if '5' in res and 'data' in res['5']:
                                block_val = res['5']['data'].get('1', {}).get('data')
                            effective_acc_id = match_account_id or account_id or "BD_BOT"
                            if token and udp_key and match_code and server_ip_port:
                                acc_tok = current_account_data.get('access_token', '') if current_account_data else ""
                                thunder, sharma = await build_match_startup_packets(
                                    token, udp_key, match_code, effective_acc_id, block_val or 0,
                                    server_ip=server_ip_port, region=account_region,
                                    client_version=client_version, access_token=acc_tok,
                                    mode_id=1, map_id=1)
                                match_index = await _inc_match(uid_str)
                                print_info(f"[⚔] Match #{match_index} Injected → {server_ip_port}")
                                try:
                                    bot_state.increment_match_started()
                                    bot_state.update_status(uid_str, "IN_MATCH (TEAM)", 1)
                                except Exception: pass
                                new_match = asyncio.create_task(
                                    play_game(server_ip_port, thunder, sharma, udp_key,
                                              match_code, effective_acc_id, "ID",
                                              client_version, current_key, current_iv,
                                              match_index=match_index))
                                play_matches.append(new_match)

                                # During match, TCP reads only friend requests
                                async def drain_gw():
                                    while not new_match.done():
                                        try:
                                            dg = await asyncio.wait_for(reader.read(4096), timeout=1.0)
                                            if not dg: break
                                            gh = dg.hex()
                                            if gh.startswith("0600") and len(dg) > 20:
                                                await _process_social_packet(dg, tag="DRAIN")
                                        except asyncio.TimeoutError: continue
                                        except Exception: break
                                drain_task = asyncio.create_task(drain_gw())
                                try:
                                    await new_match
                                except Exception as e:
                                    print_error(f"[MATCH #{match_index}] {e}")
                                finally:
                                    drain_task.cancel()
                                    try: await drain_task
                                    except asyncio.CancelledError: pass
                                play_matches[:] = [m for m in play_matches if not m.done()]

                                # mark this bot as "match played" — TCP stays connected
                                _match_played["flag"] = True
                                print_success(f"[IDLE] {uid_str} parked after match — TCP stays connected (friend-request only).")
                                continue
                        except Exception as e:
                            print_warning(f"[MATCH] {e}"); continue

                    if 30 <= packet_length <= 40:
                        continue

            except asyncio.CancelledError: raise
            except Exception as e:
                if "Cache expired" in str(e):
                    print_warning(f"[!] Token expired {uid_str}"); break
                reconnects += 1
                if reconnects > max_reconnects:
                    if current_account_data:
                        try:
                            if current_account_data.get('auth_uid'):
                                cache_invalidate(str(current_account_data['auth_uid']))
                            if current_account_data.get('auth_token'):
                                cache_invalidate(f"tok_{current_account_data['auth_token'][:20]}")
                        except Exception: pass
                    reconnects = 0; break
                print_warning(f"[{uid_str}] Reconnect #{reconnects}: {e}")
                await asyncio.sleep(min(reconnects * 0.5, 2.0))
            finally:
                _team_active["flag"] = False
                async with _team_state["lock"]:
                    _team_state["peers"].pop(uid_str, None)
                    if _team_state["leader_uid"] == uid_str:
                        _team_state["leader_uid"] = None
                        _team_state["team_code"] = None
                for tk in (team_coord_task, member_trigger_task):
                    if tk and not tk.done():
                        tk.cancel()
                        try: await tk
                        except asyncio.CancelledError: pass
                if gateway_ping_task: gateway_ping_task.cancel()
                if writer:
                    bot_state.unregister_writer(uid_str, writer)
                    await safe_close_writer(writer)
                if not _match_played["flag"]:
                    _team_active["flag"] = True
    except asyncio.CancelledError:
        print_info(f"[FUNCTIONAL] Task cancelled for {uid_str}")
        raise
    finally:
        for m in play_matches:
            if not m.done(): m.cancel()

# ==================== INFORMATIONAL ====================
async def informational(addrs, starter_packet, key, iv, region="ID",
                        account_id="", max_reconnects=3, account_data=None):
    uid_str = str(account_id); reconnects = 0
    ip, port = addrs.split(":")
    while True:
        while uid_str and bot_state.is_paused(uid_str):
            await asyncio.sleep(1.0)
        writer = None; ping_task = None
        try:
            resolved_ip = await resolve_host_cloudflare(ip)
            reader, writer = await asyncio.open_connection(resolved_ip, int(port))
            if uid_str: bot_state.register_writer(uid_str, writer)
            raw_sock = writer.get_extra_info('socket')
            if raw_sock: optimize_tcp_socket(raw_sock)
            writer.write(bytes.fromhex(starter_packet)); await writer.drain()
            reconnects = 0
            try:
                init_ka = await send_keep_alive(region)
                if init_ka and not writer.is_closing():
                    writer.write(init_ka)
                    await asyncio.wait_for(writer.drain(), timeout=3)
            except Exception: pass
            async def info_ka():
                ka = await send_keep_alive(region)
                while True:
                    await asyncio.sleep(5)
                    try:
                        if writer and not writer.is_closing():
                            writer.write(ka); await writer.drain()
                    except Exception: break
            ping_task = asyncio.create_task(info_ka())
            print_success(f"[INFO] Connected | UID: {uid_str}")
            while True:
                if uid_str and bot_state.is_paused(uid_str):
                    if ping_task: ping_task.cancel()
                    if uid_str: bot_state.unregister_writer(uid_str, writer)
                    await safe_close_writer(writer); writer = None
                    while bot_state.is_paused(uid_str): await asyncio.sleep(1.0)
                    break
                try: data = await asyncio.wait_for(reader.read(8192), timeout=1.0)
                except asyncio.TimeoutError: continue
                if not data: raise ConnectionError("closed")
        except asyncio.CancelledError:
            if ping_task: ping_task.cancel()
            if uid_str: bot_state.unregister_writer(uid_str, writer)
            await safe_close_writer(writer); raise
        except Exception:
            if ping_task: ping_task.cancel()
            if uid_str: bot_state.unregister_writer(uid_str, writer)
            await safe_close_writer(writer)
            reconnects += 1
            if reconnects > max_reconnects:
                await asyncio.sleep(3); reconnects = 0
            else: await asyncio.sleep(1)

# ==================== ACCOUNT HELPERS ====================
def _register_credentials(ad):
    try:
        bot_state.account_credentials[str(ad['account_id'])] = ad
        if ad.get('auth_uid'): bot_state.account_credentials[str(ad['auth_uid'])] = ad
    except Exception: pass

async def refresh_account_profile(ad_or_uid):
    try:
        if isinstance(ad_or_uid, str):
            uid = str(ad_or_uid); ad = bot_state.account_credentials.get(uid)
        else:
            ad = ad_or_uid; uid = str(ad.get('account_id'))
        if not ad: return
        token = ad.get('token'); acc_id = ad.get('account_id')
        srv = ad.get('server_url'); rel = ad.get('release_version')
        if not (token and acc_id and srv and rel): return
        data = await get_player_personal_show(int(acc_id), token, srv, rel)
        if not data: return
        pd = data.get(1, {}) if isinstance(data.get(1), dict) else {}
        nickname = pd.get(3); lvl = pd.get(6); exp = pd.get(7)
        if exp is None: return
        try:
            exp = int(exp); lvl = int(lvl) if lvl else 1
        except Exception: return
        aid = str(acc_id)
        if aid not in bot_state.accounts:
            bot_state.register_account(aid, nickname or f"Player_{aid}",
                region=ad.get('region','BD'), level=lvl, exp=exp,
                auth_uid=ad.get('auth_uid'))
            return
        old = bot_state.accounts[aid].get("current_exp", 0)
        bot_state.update_exp(aid, exp, lvl)
        if nickname: bot_state.accounts[aid]["nickname"] = nickname
        if exp > old: print_success(f"[★] +{exp-old:,} EXP | {aid}")
    except Exception: pass

async def process_account_uid_pass(uid, password, region_override=None):
    cached = cache_get(uid)
    if cached:
        aid = str(cached['account_id'])
        nick = cached.get('nickname', f"Player_{aid}")
        lvl = cached.get('level', 1); expv = cached.get('exp', 0)
        print_success(f"[✓] Online (cache): {aid} | {nick}")
        bot_state.register_account(aid, nick, cached.get('region','BD'),
            lvl, expv, cached.get('likes',0), auth_uid=str(uid))
        _register_credentials(cached); return cached
    print_info(f"[LOGIN] UID: {uid}")
    try:
        async with _LOGIN_SEMAPHORE:
            vc = await version_config(region_override or "ID")
            if not vc: return None
            release_version, client_version, server_url, gop_1, app_version = vc
            tg = await get_access_token(uid, password)
            if not tg: return None
            open_id, access_token, platform = tg

            major_payload, login_payload = await get_payload(
                access_token, open_id, platform, client_version)

            res_json = await major_login(major_payload, server_url, release_version)
            if not res_json: return None

            def _gd(k):
                v = res_json.get(str(k))
                if isinstance(v, dict) and "data" in v: return v["data"]
                if k in res_json and not isinstance(res_json[k], dict): return res_json[k]
                if isinstance(v, dict): return v.get("data")
                return None

            acc_id_raw = _gd(1); region_raw = _gd(2) or "ID"
            jwt_token = _gd(8); server_url_raw = _gd(10); server_time = _gd(21)
            aes_ak, iv_i = _extract_key_iv(res_json)
            if not jwt_token or not server_url_raw or not aes_ak: return None

            getlogin_json = await get_login_data(
                login_payload, jwt_token, server_url_raw, release_version)
            if not getlogin_json: return None

        def _gf(f, d=None):
            v = getlogin_json.get(str(f), {}).get("data")
            return v if v is not None else d

        level = int(_gf(6,1) or 1); exp = int(_gf(7,0) or 0)
        likes = int(_gf(8,0) or 0)
        nickname = _gf(4, f"Player_{acc_id_raw}") or f"Player_{acc_id_raw}"
        region = region_raw or _gf(3,"ID") or "ID"
        acc_id = str(acc_id_raw)

        bot_state.register_account(acc_id, nickname, region, level, exp, likes, auth_uid=str(uid))
        print_success(f"[✓] Login: {acc_id} | {nickname} | Lvl {level}")

        ad = {'account_id':acc_id_raw,'nickname':nickname,'region':region,
            'level':level,'exp':exp,'likes':likes,'open_id':open_id,
            'access_token':access_token,'platform':str(platform),
            'token':jwt_token,'server_time':server_time,
            'aes_ak':aes_ak,'iv_i':iv_i,
            'functional_addrs':_gf(14),'informational_addrs':_gf(32),
            'release_version':release_version,'client_version':client_version,
            'server_url':server_url_raw,'login_payload_data':login_payload,
            'auth_type':'guest','auth_uid':uid,'auth_password':password}
        _register_credentials(ad); cache_set(uid, ad); return ad
    except Exception as e:
        print_error(f"login err: {e}"); return None

async def run_account_worker(ad, label, region_override=None, clan_override=None):
    acc_id = str(ad['account_id'])
    info_task = None; exp_task = None
    try:
        reg = region_override or ad.get('region','BD')
        tcp_online = await build_tcp_startup_packet(
            ad['account_id'], ad['token'], ad['server_time'],
            ad['aes_ak'], ad['iv_i'], region=reg, typ='OnLine')
        tcp_chat = await build_tcp_startup_packet(
            ad['account_id'], ad['token'], ad['server_time'],
            ad['aes_ak'], ad['iv_i'], region=reg, typ='ChaT')
        if clan_override:
            try:
                await asyncio.sleep(1.0)
                await join_clan(ad['token'], clan_override)
            except Exception as e: print_warning(f"[CLAN] {e}")
        info_task = asyncio.create_task(informational(
            ad['informational_addrs'], tcp_chat, ad['aes_ak'], ad['iv_i'],
            region=reg, account_id=acc_id, account_data=ad))
        async def exp_refresher():
            while True:
                await asyncio.sleep(90 + random.uniform(-10,10))
                fresh = bot_state.account_credentials.get(acc_id)
                if fresh: await refresh_account_profile(fresh)
        exp_task = asyncio.create_task(exp_refresher())
        await functional_lone_wolf(
            ad['functional_addrs'], tcp_online, reg, ad['client_version'],
            ad['aes_ak'], ad['iv_i'], account_id=acc_id, account_data=ad)
    except asyncio.CancelledError: raise
    except Exception as e:
        print_error(f"worker err {label}: {e}")
    finally:
        for t in (info_task, exp_task):
            if t and not t.done(): t.cancel()
        for t in (info_task, exp_task):
            if t:
                try: await t
                except Exception: pass

async def account_loop_guest(uid, password, region_override=None, clan_override=None):
    uid_str = str(uid)
    bot_state.account_workers[uid_str] = asyncio.current_task()
    acc_id = None
    while True:
        try:
            bot_state.update_status(uid_str, "CONNECTING")
            ad = await process_account_uid_pass(uid_str, password, region_override)
            if not ad:
                print_error(f"Login failed {uid_str}. Retry 15s"); await asyncio.sleep(15); continue
            acc_id = str(ad['account_id'])
            bot_state.account_workers[acc_id] = asyncio.current_task()
            bot_state.auth_to_game_id[uid_str] = acc_id
            bot_state.game_to_auth_id[acc_id] = uid_str
            await run_account_worker(ad, uid_str, region_override, clan_override)
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            try:
                bot_state.update_status(uid_str, "OFFLINE")
                if acc_id: bot_state.update_status(acc_id, "OFFLINE")
            except Exception: pass
            break
        except Exception as e:
            print_error(f"err {uid_str}: {e}"); await asyncio.sleep(10)

# ==================== CREATE ACCOUNTS ====================
def _gf_field(ld, key, default=None):
    if not ld: return default
    v = ld.get(str(key), {})
    if isinstance(v, dict): return v.get("data", default)
    return v if v is not None else default

async def choose_region(region, jwt_token, login_url, release_version):
    region_code = region.upper()
    url = f"{login_url.rstrip('/')}/ChooseRegion"
    proto = create_proto({1: region_code})
    payload = bytes.fromhex(encrypt_aes(proto.hex()))
    hdrs = {
        "Accept-Encoding": "gzip",
        "Authorization": f"Bearer {jwt_token}",
        "Connection": "Keep-Alive",
        "Content-Type": "application/x-www-form-urlencoded",
        "ReleaseVersion": f"{release_version}",
        "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
        "X-GA": "v1 1", "X-GA-SV": str(int(time.time())),
        "X-Unity-Version": "2018.4.12f1",
    }
    try:
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False; ssl_ctx.verify_mode = ssl.CERT_NONE
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.post(url, data=payload, headers=hdrs, ssl=ssl_ctx) as r:
                body = await r.read()
                print_info(f"[CHOOSE-REGION] {region} → HTTP {r.status} | body={body[:100]!r}")
                return r.status
    except Exception as e:
        print_error(f"[CHOOSE-REGION] {e}"); return None

async def major_register(release_v, access_token, open_id, name, game_version,
                          login_url, lang_code="en"):
    try:
        field14 = bytes(ord(c) ^ KEYSTREAM[i % 32] for i, c in enumerate(open_id))

        fields = {
            1: name,
            2: access_token,
            3: open_id,
            5: 102000007,
            6: 4,
            7: 1,
            13: 1,
            14: field14,
            15: lang_code,
            16: 2,
            20: "2.132.4",
            21: 1,
            22: GGRE_RAW,
        }
        proto = _encode_proto_std(fields)
        payload = AES.new(AES_KEY, AES.MODE_CBC, AES_IV).encrypt(pad(proto, 16))

        url = f"{login_url.rstrip('/')}/MajorRegister"
        hdrs = {
            "Accept": "*/*",
            "Accept-Encoding": "deflate, gzip",
            "Authorization": "Bearer",
            "Content-Type": "application/x-www-form-urlencoded",
            "ReleaseVersion": f"{release_v}",
            "User-Agent": "UnityPlayer/2018.4.12f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)",
            "X-GA": "v1 1",
            "X-GA-SV": str(int(time.time())),
            "X-Unity-Version": "2018.4.12f1",
        }

        print_info(f"[MAJOR-REG] POST {url} | name={name} | lang={lang_code} "
                   f"| proto={len(proto)}B | enc={len(payload)}B")

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as s:
            async with s.post(url, headers=hdrs, data=payload, ssl=ssl_ctx) as resp:
                content = await resp.read()
                print_info(f"[MAJOR-REG] status={resp.status} len={len(content)}")

                if resp.status != 200 or not content:
                    print_warning(f"[MAJOR-REG] HTTP {resp.status}: {content[:120]!r}")
                    return None

                for off in [0, 64, 48, 32, 80, 16, 8, 96, 128]:
                    if off >= len(content):
                        continue
                    try:
                        r = decode_protobuf_2(content[off:])
                        if r and 3 in r:
                            print_success(f"[MAJOR-REG] decoded at offset {off} "
                                          f"| keys={sorted(r.keys())}")
                            return {str(k): {"data": v} for k, v in r.items()}
                    except Exception:
                        pass

                if len(content) % 16 == 0:
                    try:
                        pt = AES.new(AES_KEY, AES.MODE_CBC, AES_IV).decrypt(content)
                        pad_len = pt[-1]
                        if 1 <= pad_len <= 16 and all(b == pad_len for b in pt[-pad_len:]):
                            pt = pt[:-pad_len]
                        r = decode_protobuf_2(pt)
                        if r and 3 in r:
                            print_success(f"[MAJOR-REG] decrypted response "
                                          f"| keys={sorted(r.keys())}")
                            return {str(k): {"data": v} for k, v in r.items()}
                    except Exception:
                        pass

                print_warning(f"[MAJOR-REG] Could not decode response: {content[:120]!r}")
                return None

    except asyncio.TimeoutError:
        print_error("[MAJOR-REG] TIMEOUT")
        return None
    except Exception as e:
        print_error(f"[MAJOR-REG] {e}")
        traceback.print_exc()
        return None

async def create_four_accounts(region: str, clan_id: str, count: int = 4, password: str = "ANON"):
    bot_state.creating = True
    bot_state.create_progress = []

    def _prog(s):
        try:
            print(f"[CREATE] {s}")
            bot_state.log(s, "info")
        except Exception:
            pass
        bot_state.create_progress.append(s)

    try:
        _prog(f"⌛ Fetching version config for {region}...")
        vc = await version_config(region)
        if not vc:
            _prog("❌ version config failed")
            return []

        release_v, remote_v, login_url, gop_1, app_version = vc
        if not login_url.endswith("/"):
            login_url += "/"
        lang = REGION_LANG.get(region.upper(), "en")
        game_version = remote_v or app_version
        _prog(f"✅ Version OK | release={release_v} | game={game_version} | lang={lang}")

        created = []

        for i in range(count):
            _prog(f"⌛ Creating bot {i+1}/{count}...")
            try:
                uid = await register_account(password, gop_1, app_version)
                if not uid:
                    _prog(f"❌ bot {i+1}: register failed")
                    continue
                _prog(f"   → registered UID {uid}")

                tok = await grant_token(uid, password, gop_1, app_version)
                if not tok:
                    _prog(f"❌ bot {i+1}: grant failed")
                    continue
                open_id = tok["open_id"]
                access_token = tok["access_token"]
                platform = tok.get("platform", 4)
                _prog(f"   → open_id={open_id[:12]}... platform={platform}")

                major_payload, login_payload = await get_payload(
                    access_token, open_id, platform, game_version, lang)
                if not major_payload or not login_payload:
                    _prog(f"❌ bot {i+1}: payload build failed")
                    continue

                nickname_final = "ANON•" + "".join(
                    random.choices(string.ascii_uppercase + string.digits, k=5))
                mr = await major_register(release_v, access_token, open_id,
                                          nickname_final, game_version, login_url, lang)
                if not mr:
                    _prog(f"❌ bot {i+1}: MajorRegister failed")
                    continue

                def _mrg(k, d=None):
                    v = mr.get(str(k))
                    if isinstance(v, dict) and "data" in v:
                        return v["data"]
                    return v if v is not None else d

                mr_acc_id = _mrg(3)
                _prog(f"   → MajorRegister OK | acc_id={mr_acc_id}")

                _prog("   → waiting 3s for provisioning...")
                await asyncio.sleep(3.0)

                res_json = None
                for attempt in range(3):
                    mp, lp = await get_payload(
                        access_token, open_id, platform, game_version, lang)
                    _prog(f"   → MajorLogin #1 attempt {attempt+1}/3...")
                    res_json = await major_login(mp, login_url, release_v)
                    if res_json and (("8" in res_json) or (8 in res_json)):
                        major_payload = mp
                        login_payload = lp
                        break
                    res_json = None
                    await asyncio.sleep(2.0)

                if not res_json:
                    _prog(f"❌ bot {i+1}: MajorLogin #1 failed")
                    continue

                def _gd(k):
                    v = res_json.get(str(k))
                    if isinstance(v, dict) and "data" in v:
                        return v["data"]
                    if k in res_json and not isinstance(res_json[k], dict):
                        return res_json[k]
                    if isinstance(v, dict):
                        return v.get("data")
                    return None

                jwt_token = _gd(8)
                server_url_raw = _gd(10)
                server_time = _gd(21)
                acc_id_raw = _gd(1)

                if not jwt_token:
                    _prog(f"❌ bot {i+1}: no JWT in MajorLogin #1")
                    continue

                if not server_url_raw:
                    _prog(f"   → server_url missing — ChooseRegion({region})...")
                    try:
                        cr_status = await choose_region(region, jwt_token, login_url, release_v)
                    except Exception as e:
                        cr_status = None
                        _prog(f"   ❌ ChooseRegion error: {e}")

                    if cr_status != 200:
                        _prog(f"❌ bot {i+1}: ChooseRegion failed ({cr_status})")
                        continue

                    _prog("   ✅ ChooseRegion OK — re-running MajorLogin...")
                    await asyncio.sleep(2.0)

                    res_json2 = None
                    for attempt in range(3):
                        mp2, lp2 = await get_payload(
                            access_token, open_id, platform, game_version, lang)
                        _prog(f"   → MajorLogin #2 attempt {attempt+1}/3...")
                        res_json2 = await major_login(mp2, login_url, release_v)
                        if res_json2 and (("10" in res_json2) or (10 in res_json2)):
                            res_json = res_json2
                            login_payload = lp2
                            break
                        res_json2 = None
                        await asyncio.sleep(2.0)

                    if not res_json2:
                        _prog(f"❌ bot {i+1}: MajorLogin #2 no server_url")
                        continue

                    res_json = res_json2

                    def _gd2(k):
                        v = res_json.get(str(k))
                        if isinstance(v, dict) and "data" in v:
                            return v["data"]
                        if k in res_json and not isinstance(res_json[k], dict):
                            return res_json[k]
                        if isinstance(v, dict):
                            return v.get("data")
                        return None

                    jwt_token = _gd2(8)
                    server_url_raw = _gd2(10)
                    server_time = _gd2(21)
                    acc_id_raw = _gd2(1)

                    if not server_url_raw:
                        _prog(f"❌ bot {i+1}: still no server_url")
                        continue

                _prog(f"   ✅ server_url = {server_url_raw}")

                aes_ak, iv_i = _extract_key_iv(res_json)
                if not aes_ak or not iv_i:
                    _prog(f"❌ bot {i+1}: AES key/IV fail")
                    continue

                ld = await get_login_data(login_payload, jwt_token, server_url_raw, release_v)
                level, exp, likes = 1, 0, 0
                if ld:
                    def _gf(f, d=None):
                        v = ld.get(str(f), {}).get("data")
                        return v if v is not None else d
                    level = int(_gf(6, 1) or 1)
                    exp = int(_gf(7, 0) or 0)
                    likes = int(_gf(8, 0) or 0)
                    nickname_final = _gf(4, nickname_final) or nickname_final

                ad = {
                    'account_id': acc_id_raw,
                    'nickname': nickname_final,
                    'region': region,
                    'level': level,
                    'exp': exp,
                    'likes': likes,
                    'open_id': open_id,
                    'access_token': access_token,
                    'platform': str(platform),
                    'token': jwt_token,
                    'server_time': server_time,
                    'aes_ak': aes_ak,
                    'iv_i': iv_i,
                    'functional_addrs': _gf_field(ld, 14),
                    'informational_addrs': _gf_field(ld, 32),
                    'release_version': release_v,
                    'client_version': game_version,
                    'server_url': server_url_raw,
                    'login_payload_data': login_payload,
                    'auth_type': 'guest',
                    'auth_uid': uid,
                    'auth_password': password,
                }
                _register_credentials(ad)
                cache_set(uid, ad)
                created.append(ad)
                _prog(f"✅ bot {i+1}: {nickname_final} | UID {acc_id_raw} | Lvl {level}")

                await asyncio.sleep(0.6)

            except Exception as e:
                _prog(f"❌ bot {i+1}: {e}")
                traceback.print_exc()

        if created:
            try:
                existing = []
                if os.path.exists(ACCOUNTS_FILE):
                    try:
                        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                            existing = json.load(f) or []
                            if not isinstance(existing, list):
                                existing = []
                    except Exception:
                        existing = []

                existing_uids = {str(a.get("uid")) for a in existing if isinstance(a, dict)}
                for a in created:
                    u = str(a['auth_uid'])
                    if u not in existing_uids:
                        existing.append({
                            "uid": u,
                            "password": a['auth_password'],
                            "region": region,
                            "clan_id": clan_id,
                        })

                with open(ACCOUNTS_FILE, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2)
                _prog(f"📦 {len(created)} accounts saved to accounts.json")
            except Exception as e:
                _prog(f"⚠️ save accounts.json: {e}")

        if created:
            _prog(f"🚀 Starting {len(created)} bot workers...")
            for i, a in enumerate(created):
                try:
                    task = asyncio.create_task(account_loop_guest(
                        str(a['auth_uid']), a['auth_password'],
                        region_override=region, clan_override=clan_id))
                    bot_state.account_workers[str(a['auth_uid'])] = task
                    _prog(f"   → worker started ({i+1}/{len(created)})")
                    await asyncio.sleep(0.35)
                except Exception as e:
                    _prog(f"   ❌ worker {i+1} failed: {e}")

            _prog(f"✅ All {len(created)} bots launched.")
        else:
            _prog("❌ No accounts created.")

        return created

    except Exception as e:
        _prog(f"❌ create_four_accounts error: {e}")
        traceback.print_exc()
        return []
    finally:
        bot_state.creating = False

# ==================== AUTO-LOAD ON STARTUP ====================
async def _autoload_accounts():
    if not os.path.exists(ACCOUNTS_FILE):
        print_info("[BOOT] No accounts.json — waiting for web create")
        return
    try:
        with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            accounts = json.load(f) or []
    except Exception as e:
        print_error(f"[BOOT] Failed to load accounts.json: {e}")
        return
    if not isinstance(accounts, list) or not accounts:
        print_info("[BOOT] accounts.json empty — waiting for web create")
        return

    print_success(f"[BOOT] Auto-loading {len(accounts)} account(s) from accounts.json")
    for i, a in enumerate(accounts):
        try:
            if not isinstance(a, dict): continue
            uid = str(a.get("uid", "")).strip()
            password = str(a.get("password", "")).strip()
            region = str(a.get("region", "")).strip() or None
            clan_id = str(a.get("clan_id", "")).strip() or CLAN_ID_DEFAULT
            if not uid or not password: continue
            task = asyncio.create_task(account_loop_guest(
                uid, password, region_override=region, clan_override=clan_id))
            bot_state.account_workers[uid] = task
            print_info(f"[BOOT] Started worker for UID {uid} ({i+1}/{len(accounts)})")
            await asyncio.sleep(0.5)
        except Exception as e:
            print_error(f"[BOOT] worker {i+1} failed: {e}")

# ==================== HTTP SERVER ====================
@aiohttp.web.middleware
async def _req_logger(request, handler):
    print(f"\033[90m[REQ] {request.method} {request.path}\033[0m")
    try:
        resp = await handler(request)
        print(f"\033[90m[RES] {request.method} {request.path} → {resp.status}\033[0m")
        return resp
    except aiohttp.web.HTTPException as e:
        print(f"\033[91m[ERR] {request.method} {request.path} → HTTP {e.status}\033[0m")
        if request.path.startswith("/api/"):
            return aiohttp.web.json_response(
                {"ok": False, "status": "error", "error": e.reason or str(e)},
                status=e.status)
        raise
    except Exception as e:
        print(f"\033[91m[ERR] {request.method} {request.path} → {e}\033[0m")
        traceback.print_exc()
        if request.path.startswith("/api/"):
            return aiohttp.web.json_response(
                {"ok": False, "status": "error", "error": str(e)}, status=500)
        raise

async def serve_index(request):
    for path in (INDEX_HTML_PATH, "index.html"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return aiohttp.web.Response(text=f.read(), content_type="text/html")
        except Exception:
            continue
    return aiohttp.web.Response(
        text="<h1>RAW ANON</h1><p>index.html not found</p>",
        content_type="text/html")

async def api_ping(request):
    return aiohttp.web.json_response({"pong": True, "app": "RAW ANON", "version": "2.0"})

async def api_stats(request):
    try:
        accs = list(bot_state.accounts.values())
        online = sum(1 for a in accs if a.get("status") in ("ONLINE","IN_MATCH","SEARCHING"))
        active_matches = sum(a.get("active_matches", 0) for a in accs)
        uptime = max(1, int(time.time() - bot_state.start_time))
        uptime_h = max(0.016, uptime / 3600.0)
        exp_rate = int(bot_state.total_exp_gained / uptime_h) if uptime_h > 0 else 0
        matches_rate = int(bot_state.matches_started / uptime_h) if uptime_h > 0 else 0
        logs_out = []
        for l in bot_state.logs[-200:]:
            try:
                logs_out.append({"time": str(l.get("time","")),
                                 "message": str(l.get("message","")),
                                 "level": str(l.get("level","info"))})
            except Exception: continue
        data = {
            "accounts": accs, "logs": logs_out,
            "total_accounts": len(accs), "total_online": online,
            "total_matches": bot_state.total_matches,
            "total_matches_started": bot_state.matches_started,
            "total_active_matches": active_matches,
            "total_gained_exp": bot_state.total_exp_gained,
            "uptime": uptime, "exp_per_hour": exp_rate,
            "matches_per_hour": matches_rate,
            "creating": bool(bot_state.creating),
            "create_progress": [str(x) for x in bot_state.create_progress[-40:]],
            "team_code": _team_state.get("team_code"),
            "leader_uid": _team_state.get("leader_uid"),
            "team_size": len(_team_state["peers"]),
            "restart_finished": len(_restart_state["finished_bots"]),
            "restart_target": _restart_state["target"],
            "restarting": _restart_state["restarting"],
        }
        return aiohttp.web.Response(text=json.dumps(data, default=str, ensure_ascii=False),
                                    content_type="application/json", charset="utf-8")
    except Exception as e:
        traceback.print_exc()
        return aiohttp.web.Response(text=json.dumps({
            "error": str(e), "accounts": [], "logs": [], "create_progress": [],
            "creating": False, "total_accounts": 0, "total_matches_started": 0,
            "total_matches": 0, "total_active_matches": 0, "total_gained_exp": 0,
            "uptime": 1, "exp_per_hour": 0,
        }), content_type="application/json", charset="utf-8")

async def api_state(request):
    try:
        return aiohttp.web.Response(text=json.dumps({
            "creating": bool(bot_state.creating),
            "create_progress": [str(x) for x in bot_state.create_progress[-40:]],
            "accounts": list(bot_state.accounts.values()),
            "team_size": len(_team_state["peers"]),
        }, default=str, ensure_ascii=False), content_type="application/json", charset="utf-8")
    except Exception as e:
        return aiohttp.web.Response(text=json.dumps({"error": str(e), "creating": False,
                                    "create_progress": []}),
                                    content_type="application/json", charset="utf-8")

async def api_progress(request):
    try:
        lines = []
        for x in bot_state.create_progress[-60:]:
            try: lines.append(str(x).encode("ascii","backslashreplace").decode("ascii"))
            except Exception: continue
        text = '{"creating":' + ("true" if bot_state.creating else "false") + \
               ',"create_progress":' + json.dumps(lines) + '}'
        return aiohttp.web.Response(text=text, content_type="application/json")
    except Exception:
        return aiohttp.web.Response(text='{"creating":false,"create_progress":[]}',
                                    content_type="application/json")

async def api_create_team(request):
    try:
        body = await request.json()
        region = str(body.get("region", "ID")).upper()
        clan_id = str(body.get("clan_id", "")).strip()
        count = int(body.get("count", 4))
        password = str(body.get("password", "ANON")).strip() or "ANON"
        if not clan_id:
            return aiohttp.web.json_response({"ok": False, "error": "clan_id required"})
        if bot_state.creating:
            return aiohttp.web.json_response({"ok": False, "error": "already creating"})
        count = max(1, min(4, count))
        asyncio.create_task(create_four_accounts(region, clan_id, count, password))
        return aiohttp.web.json_response({"ok": True, "msg": f"creating {count} bots for {region}"})
    except Exception as e:
        traceback.print_exc()
        return aiohttp.web.json_response({"ok": False, "error": str(e)})

async def api_account_add(request):
    try:
        body = await request.json()
        if body.get("token"):
            token = str(body["token"]).strip()
            if not token:
                return aiohttp.web.json_response({"status": "error", "error": "empty token"})
            asyncio.create_task(account_loop_token(token))
            return aiohttp.web.json_response({"status": "ok"})
        uid = str(body.get("uid", "")).strip()
        password = str(body.get("password", "")).strip()
        if not uid or not password:
            return aiohttp.web.json_response({"status": "error", "error": "uid and password required"})
        asyncio.create_task(account_loop_guest(uid, password))
        return aiohttp.web.json_response({"status": "ok"})
    except Exception as e:
        return aiohttp.web.json_response({"status": "error", "error": str(e)})

async def api_account_delete(request):
    try:
        body = await request.json()
        uid = str(body.get("uid", "")).strip()
        auth_uid = str(body.get("auth_uid", "")).strip()
        targets = {uid, auth_uid} - {""}
        for u in list(targets):
            if u in bot_state.account_workers:
                try: bot_state.account_workers[u].cancel()
                except Exception: pass
                bot_state.account_workers.pop(u, None)
            if u in bot_state.accounts:
                del bot_state.accounts[u]
            cache_invalidate(u)
            bot_state.toggle_pause(u, False)
        return aiohttp.web.json_response({"status": "ok"})
    except Exception as e:
        return aiohttp.web.json_response({"status": "error", "error": str(e)})

async def api_account_refresh(request):
    try:
        body = await request.json()
        uid = str(body.get("uid", "")).strip()
        asyncio.create_task(refresh_account_profile(uid))
        return aiohttp.web.json_response({"status": "ok"})
    except Exception as e:
        return aiohttp.web.json_response({"status": "error", "error": str(e)})

async def api_account_restart(request):
    try:
        body = await request.json()
        uid = str(body.get("uid", "")).strip()
        resolved = {uid}
        if uid in bot_state.game_to_auth_id: resolved.add(str(bot_state.game_to_auth_id[uid]))
        if uid in bot_state.auth_to_game_id: resolved.add(str(bot_state.auth_to_game_id[uid]))
        for u in resolved:
            if u in bot_state.account_workers:
                try: bot_state.account_workers[u].cancel()
                except Exception: pass
                bot_state.account_workers.pop(u, None)
        try:
            if os.path.exists(ACCOUNTS_FILE):
                with open(ACCOUNTS_FILE,"r",encoding="utf-8") as f:
                    accs = json.load(f)
                for a in accs:
                    if str(a.get("uid")) in resolved and a.get("password"):
                        t = asyncio.create_task(account_loop_guest(str(a["uid"]), a["password"]))
                        bot_state.account_workers[str(a["uid"])] = t
                        break
        except Exception: pass
        return aiohttp.web.json_response({"status": "ok"})
    except Exception as e:
        return aiohttp.web.json_response({"status": "error", "error": str(e)})

async def api_account_pause(request):
    try:
        body = await request.json()
        uid = str(body.get("uid", "")).strip()
        new_state = not bot_state.is_paused(uid)
        bot_state.toggle_pause(uid, new_state)
        if new_state: bot_state.close_writers_for_account(uid)
        return aiohttp.web.json_response({"status": "ok", "is_paused": new_state})
    except Exception as e:
        return aiohttp.web.json_response({"status": "error", "error": str(e)})

async def api_account_pause_all(request):
    try:
        accs = list(bot_state.accounts.keys())
        if not accs:
            return aiohttp.web.json_response({"status": "ok", "all_paused": False})
        all_paused = all(bot_state.is_paused(u) for u in accs)
        new_state = not all_paused
        for u in accs:
            bot_state.toggle_pause(u, new_state)
            if new_state: bot_state.close_writers_for_account(u)
        return aiohttp.web.json_response({"status": "ok", "all_paused": new_state})
    except Exception as e:
        return aiohttp.web.json_response({"status": "error", "error": str(e)})

async def api_logs_clear(request):
    bot_state.logs = []
    return aiohttp.web.json_response({"status": "ok"})

async def account_loop_token(token):
    """Token-based login loop."""
    print_info(f"[TOKEN] Login with token {token[:20]}...")
    try:
        vc = await version_config("ID")
        if not vc: return
        release_version, client_version, server_url, gop_1, app_version = vc

        url = f"https://100067.connect.garena.com/oauth/token/inspect?token={token}"
        hdrs = {"User-Agent":"GarenaMSDK/4.0.19P4(G011A ;Android 9;en;US;)"}
        r = await client.get(url, headers=hdrs)
        if r.status_code != 200: return
        data = r.json()
        if 'error' in data: return
        open_id = data.get('open_id')
        platform = data.get('platform', 4)
        if not open_id: return

        major_payload, login_payload = await get_payload(
            token, open_id, platform, client_version)

        res_json = await major_login(major_payload, server_url, release_version)
        if not res_json: return

        def _gd(k):
            v = res_json.get(str(k))
            if isinstance(v, dict) and "data" in v: return v["data"]
            return v

        acc_id_raw = _gd(1); jwt_token = _gd(8)
        server_url_raw = _gd(10); server_time = _gd(21)
        region_raw = _gd(2) or "ID"
        aes_ak, iv_i = _extract_key_iv(res_json)
        if not (jwt_token and server_url_raw and aes_ak): return

        ld = await get_login_data(login_payload, jwt_token, server_url_raw, release_version)

        def _gf(f, d=None):
            v = (ld or {}).get(str(f), {}).get("data")
            return v if v is not None else d

        level = int(_gf(6,1) or 1); exp = int(_gf(7,0) or 0)
        nickname = _gf(4, f"Player_{acc_id_raw}") or f"Player_{acc_id_raw}"

        ad = {'account_id':acc_id_raw,'nickname':nickname,'region':region_raw,
            'level':level,'exp':exp,'likes':int(_gf(8,0) or 0),
            'open_id':open_id,'access_token':token,'platform':str(platform),
            'token':jwt_token,'server_time':server_time,
            'aes_ak':aes_ak,'iv_i':iv_i,
            'functional_addrs':_gf_field(ld, 14),
            'informational_addrs':_gf_field(ld, 32),
            'release_version':release_version,'client_version':client_version,
            'server_url':server_url_raw,'login_payload_data':login_payload,
            'auth_type':'token','auth_token':token}
        _register_credentials(ad)
        cache_set(f"tok_{token[:20]}", ad)
        await run_account_worker(ad, str(acc_id_raw))
    except Exception as e:
        print_error(f"[TOKEN] {e}")

async def start_web_server():
    app = aiohttp.web.Application(middlewares=[_req_logger])

    app.router.add_get("/", serve_index)
    app.router.add_get("/index.html", serve_index)
    app.router.add_get("/api/ping", api_ping)
    app.router.add_get("/api/stats", api_stats)
    app.router.add_get("/api/state", api_state)
    app.router.add_post("/api/create_team", api_create_team)
    app.router.add_post("/api/account/add", api_account_add)
    app.router.add_post("/api/account/delete", api_account_delete)
    app.router.add_post("/api/account/refresh", api_account_refresh)
    app.router.add_post("/api/account/restart", api_account_restart)
    app.router.add_post("/api/account/pause", api_account_pause)
    app.router.add_post("/api/account/pause_all", api_account_pause_all)
    app.router.add_post("/api/logs/clear", api_logs_clear)
    app.router.add_get("/api/progress", api_progress)

    runner = aiohttp.web.AppRunner(app)
    await runner.setup()
    site = aiohttp.web.TCPSite(runner, WEB_HOST, WEB_PORT)
    await site.start()

    print_success(f"[WEB] RAW ANON Dashboard → http://localhost:{WEB_PORT}")
    print_info("[WEB] Registered routes:")
    for r in app.router.routes():
        method = ",".join(r.method)
        print(f"      {method:5} {r.resource}")
    return runner

# ==================== MAIN ====================
async def main():
    print("\033[95m" + "╔══════════════════════════════════════════════════════╗" + "\033[0m")
    print("\033[96m" + "║   Free Fire 4-Bot Team | Auto Create | Movement      ║" + "\033[0m")
    print("\033[95m" + "╚══════════════════════════════════════════════════════╝" + "\033[0m")
    try:
        await start_web_server()
    except Exception as e:
        print_error(f"[WEB] {e}")
        return

    # auto-load existing accounts on startup
    try:
        await _autoload_accounts()
    except Exception as e:
        print_error(f"[BOOT] autoload failed: {e}")

    # ▼▼ JALANKAN WATCHDOG NO-MATCH (HANYA ONLINE/SEARCHING) ▼▼
    asyncio.create_task(_no_match_watchdog())
    # ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        print_warning("Shutting down...")
        for t in list(bot_state.account_workers.values()):
            t.cancel()
        await asyncio.gather(*bot_state.account_workers.values(), return_exceptions=True)
        try: await client.aclose()
        except Exception: pass

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print_warning("stopped")