"""Authoritative TCP server for the LAN Deathmatch game (stdlib only)."""
import getpass
import hashlib
import hmac
import json
import math
import os
import queue
import random
import re
import shutil
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager

from shared.constants import (ARENA_LIMIT, COVER_BLOCKS, CROUCH_SPEED, MAX_PLAYERS, PORT,
                              RESPAWN_SECONDS, SPAWNS, SPRINT_SPEED, TARGET_KILLS,
                              TICK_RATE, WALK_SPEED)
from shared.protocol import encode, extract_frames
from shared.weapons import for_kills

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(ROOT, "players.db")
_SERVER_ICON_HANDLE = None

def set_console_branding():
    """Set the classic Windows console title and icon without extra dependencies."""
    global _SERVER_ICON_HANDLE
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        kernel32.SetConsoleTitleW.argtypes = (wintypes.LPCWSTR,)
        user32.SendMessageW.argtypes = (wintypes.HWND, wintypes.UINT,
                                        wintypes.WPARAM, wintypes.LPARAM)
        user32.SendMessageW.restype = ctypes.c_ssize_t
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return  # Windows Terminal may not expose a separate console window.

        kernel32.SetConsoleTitleW("LAN Deathmatch Server")
        icon_path = os.path.join(ROOT, "logo.ico")
        if not os.path.isfile(icon_path):
            return

        IMAGE_ICON = 1
        LR_LOADFROMFILE = 0x0010
        WM_SETICON = 0x0080
        ICON_SMALL, ICON_BIG = 0, 1
        user32.LoadImageW.argtypes = (wintypes.HINSTANCE, wintypes.LPCWSTR,
                                      wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                      wintypes.UINT)
        user32.LoadImageW.restype = wintypes.HANDLE
        _SERVER_ICON_HANDLE = user32.LoadImageW(None, icon_path, IMAGE_ICON, 0, 0, LR_LOADFROMFILE)
        if _SERVER_ICON_HANDLE:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, _SERVER_ICON_HANDLE)
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, _SERVER_ICON_HANDLE)
    except (AttributeError, OSError, TypeError):
        # Icon assignment is cosmetic; it must never prevent the server from starting.
        return

class GameServer:
    def __init__(self, host="0.0.0.0", port=PORT, db_path=DB_PATH):
        self.host, self.port, self.db_path = host, int(port), db_path
        self.lock = threading.RLock()
        self.players = {}  # session id -> authoritative player state
        self.banned = set()
        self.match_running = False
        self.match_id = 0
        self.started_at = 0.0
        self.stopping = threading.Event()
        self.sock = None
        self._init_db()

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _init_db(self):
        try:
            self._create_and_check_db()
        except sqlite3.OperationalError as exc:
            reason = str(exc).lower()
            if not any(s in reason for s in ("readonly", "read-only", "unable to open database file", "disk i/o error")):
                raise
            source_path = self.db_path
            fallback_path = self._fallback_db_path()
            os.makedirs(os.path.dirname(fallback_path), exist_ok=True)
            if os.path.isfile(source_path) and not os.path.exists(fallback_path):
                shutil.copyfile(source_path, fallback_path)
            self.db_path = fallback_path
            try:
                self._create_and_check_db()
            except sqlite3.Error as fallback_error:
                self.db_path = source_path
                raise RuntimeError(f"SQLite is not writable at {source_path} or {fallback_path}: {fallback_error}") from fallback_error
            print(f"WARNING: {source_path} is read-only; using writable database {fallback_path}")

    @staticmethod
    def _fallback_db_path():
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        else:
            base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
        return os.path.join(base, "LanDeathmatch", "players.db")

    def _create_and_check_db(self):
        db = sqlite3.connect(self.db_path, timeout=10)
        try:
            db.execute("""CREATE TABLE IF NOT EXISTS players (
                username TEXT PRIMARY KEY COLLATE NOCASE, password_hash BLOB NOT NULL,
                salt BLOB NOT NULL, kills INTEGER NOT NULL DEFAULT 0,
                deaths INTEGER NOT NULL DEFAULT 0, matches_played INTEGER NOT NULL DEFAULT 0,
                wins INTEGER NOT NULL DEFAULT 0, banned INTEGER NOT NULL DEFAULT 0)""")
            # BEGIN IMMEDIATE detects databases/directories that allow reads but reject writes.
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS _write_probe (value INTEGER)")
            db.execute("DROP TABLE _write_probe")
            db.commit()
        except sqlite3.Error:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _password(password, salt):
        return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)

    def create_account(self, username, password):
        if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username) or len(password) < 6:
            return False, "username: 3-20 letters/numbers/_, password: at least 6 chars"
        salt = os.urandom(16)
        try:
            with self._db() as db:
                db.execute("INSERT INTO players(username,password_hash,salt) VALUES(?,?,?)",
                           (username, self._password(password, salt), salt))
            return True, f"Account created: {username}"
        except sqlite3.IntegrityError:
            return False, "username already exists"

    def _send(self, player, message):
        try:
            lock = player.setdefault("_send_lock", threading.Lock())
            with lock:
                player["sock"].sendall(encode(message))
        except OSError:
            pass

    @staticmethod
    def _spawn_yaw(x, z):
        return math.degrees(math.atan2(-x, -z)) % 360

    def _choose_spawn(self):
        occupied = {(round(p["x"], 1), round(p["z"], 1)) for p in self.players.values() if p["alive"]}
        available = [point for point in SPAWNS
                     if (round(point[0], 1), round(point[2], 1)) not in occupied]
        return random.choice(available or SPAWNS)

    def _broadcast(self, message, exclude=None):
        with self.lock:
            for sid, p in list(self.players.items()):
                if sid != exclude:
                    self._send(p, message)

    def _snapshot(self):
        return {"type": "snapshot", "match": self.match_running, "match_id": self.match_id,
                "players": [{k: p[k] for k in ("id", "username", "x", "y", "z", "yaw", "pitch", "hp", "kills", "deaths", "match_kills", "match_deaths", "alive", "respawn_at", "crouched", "sprinting", "paused")}
                            for p in self.players.values()]}

    def _weapon(self, p):
        return for_kills(p["match_kills"])

    def add_ten_kills(self, username):
        """Add ten profile kills and, when online, ten test frags to the current match."""
        with self.lock:
            with self._db() as db:
                row = db.execute("SELECT username FROM players WHERE username=? COLLATE NOCASE", (username,)).fetchone()
                if row is None:
                    return False, f"Player not found: {username}"
                db.execute("UPDATE players SET kills=kills+10 WHERE username=? COLLATE NOCASE", (username,))

            live = next((p for p in self.players.values()
                         if p["username"].lower() == row["username"].lower()), None)
            if live:
                live["kills"] += 10
                live["match_kills"] += 10
                weapon = self._weapon(live)
                live["ammo"], live["reserve"] = weapon.magazine_size, weapon.reserve_size
                live["reload_until"] = 0.0
                self._send(live, {"type": "kills_update", "kills": live["match_kills"]})
                self._send(live, {"type": "weapon", "name": weapon.name})
                self._send_ammo(live)
                self._send(live, {"type": "stats", "players": self._match_stats(),
                                  "weapon": weapon.name})
                if self.match_running and live["match_kills"] >= TARGET_KILLS:
                    self._finish_match(live)
                return True, f"Added 10 kills to {live['username']}; now using {weapon.name}"

            with self._db() as db:
                total = db.execute("SELECT kills FROM players WHERE username=? COLLATE NOCASE",
                                   (username,)).fetchone()["kills"]
            return True, f"Added 10 kills to {row['username']}; lifetime kills: {total}"

    def _match_stats(self):
        with self.lock:
            return sorted([{"id": p["id"], "username": p["username"],
                            "kills": p["match_kills"], "deaths": p["match_deaths"],
                            "match_kills": p["match_kills"], "match_deaths": p["match_deaths"]}
                           for p in self.players.values()], key=lambda row: row["kills"], reverse=True)

    def reset_player_stats(self, username):
        """Reset persisted and live lifetime stats, then push fresh HUD/TAB data."""
        with self.lock:
            with self._db() as db:
                if username.lower() == "all":
                    db.execute("UPDATE players SET kills=0,deaths=0,matches_played=0,wins=0")
                    affected = {p["username"].lower() for p in self.players.values()}
                else:
                    cur = db.execute("UPDATE players SET kills=0,deaths=0,matches_played=0,wins=0 WHERE username=? COLLATE NOCASE",
                                     (username,))
                    if cur.rowcount == 0:
                        return False, f"Player not found: {username}"
                    affected = {username.lower()}

            for live in self.players.values():
                if live["username"].lower() not in affected:
                    continue
                live["kills"], live["deaths"] = 0, 0
                live["match_kills"], live["match_deaths"] = 0, 0
                live["ammo"], live["reserve"] = self._weapon(live).magazine_size, self._weapon(live).reserve_size
                live["reload_until"] = 0.0
                self._send(live, {"type": "kills_update", "kills": 0})
                self._send(live, {"type": "deaths_update", "deaths": 0})
                self._send(live, {"type": "weapon", "name": self._weapon(live).name})
                self._send_ammo(live)

            fresh_stats = self._match_stats()
            for live in self.players.values():
                self._send(live, {"type": "stats", "players": fresh_stats,
                                  "weapon": self._weapon(live).name})
            return True, "Statistics reset" if username.lower() != "all" else "All statistics reset"

    def _login(self, conn, addr, msg):
        username, password = str(msg.get("username", "")), str(msg.get("password", ""))
        with self._db() as db:
            row = db.execute("SELECT * FROM players WHERE username=? COLLATE NOCASE", (username,)).fetchone()
        if row is None or row["banned"] or not hmac.compare_digest(bytes(row["password_hash"]), self._password(password, bytes(row["salt"]))):
            self._send({"sock": conn}, {"type": "login", "ok": False, "error": "Invalid credentials or account unavailable"})
            return None
        with self.lock:
            if len(self.players) >= MAX_PLAYERS or any(p["username"].lower() == username.lower() for p in self.players.values()):
                self._send({"sock": conn}, {"type": "login", "ok": False, "error": "Server full or account already online"})
                return None
            sid = os.urandom(8).hex()
            x, y, z = self._choose_spawn()
            p = {"id": sid, "username": row["username"], "sock": conn, "addr": addr,
                 "x": float(x), "y": float(y), "z": float(z), "yaw": 0.0, "pitch": 0.0,
                 "hp": 100, "vy": 0.0, "kills": row["kills"], "deaths": row["deaths"],
                 "jump_held": False, "crouched": False, "sprinting": False, "aiming": False, "paused": False,
                 "alive": True, "respawn_at": 0.0, "last_shot": 0.0, "reload_until": 0.0,
                 # Weapon progression belongs to the current match and starts fresh on login.
                 "ammo": self._weapon({"match_kills": 0}).magazine_size,
                 "reserve": self._weapon({"match_kills": 0}).reserve_size,
                 "match_kills": 0, "match_deaths": 0}
            p["yaw"] = self._spawn_yaw(x, z)
            self.players[sid] = p
        self._send(p, {"type": "login", "ok": True, "id": sid, "username": p["username"],
                       "kills": p["kills"], "deaths": p["deaths"], "match": self.match_running,
                       "match_kills": p["match_kills"], "match_deaths": p["match_deaths"],
                       "x": p["x"], "y": p["y"], "z": p["z"], "yaw": p["yaw"],
                       "weapon": self._weapon(p).name, "ammo": p["ammo"], "reserve": p["reserve"],
                       "crouched": p["crouched"], "sprinting": p["sprinting"], "paused": p["paused"]})
        self._broadcast({"type": "joined", "player": {k: p[k] for k in ("id", "username", "x", "y", "z", "yaw", "hp", "kills", "deaths", "match_kills", "match_deaths", "alive", "crouched", "sprinting", "paused")}}, sid)
        self._send(p, self._snapshot())
        print(f"{p['username']} connected from {addr[0]}")
        return sid

    def _move(self, p, m):
        # Inputs are normalized and speed/bounds are enforced here. No client position is accepted.
        if not p["alive"] or p.get("paused", False):
            p["crouched"] = p["sprinting"] = p["aiming"] = False
            p["jump_held"], p["vy"] = False, 0.0
            return
        dt = min(max(float(m.get("dt", 1 / TICK_RATE)), 0.0), 0.1)
        forward = min(max(float(m.get("forward", 0)), -1), 1)
        side = min(max(float(m.get("side", 0)), -1), 1)
        move_length = math.hypot(forward, side)
        if move_length > 1:
            forward, side = forward / move_length, side / move_length
        p["crouched"] = bool(m.get("crouch", False))
        p["sprinting"] = bool(m.get("sprint", False)) and not p["crouched"]
        p["aiming"] = bool(m.get("aim", False))
        speed = CROUCH_SPEED if p["crouched"] else SPRINT_SPEED if p["sprinting"] else WALK_SPEED
        yaw = float(m.get("yaw", p["yaw"])) % 360
        pitch = min(max(float(m.get("pitch", p["pitch"])), -80), 80)
        rad = math.radians(yaw)
        dx = (math.sin(rad) * forward + math.cos(rad) * side) * speed * dt
        dz = (math.cos(rad) * forward - math.sin(rad) * side) * speed * dt
        # Match FirstPersonController's forward collision ray: stop the full move at cover.
        next_x = min(max(p["x"] + dx, -ARENA_LIMIT), ARENA_LIMIT)
        next_z = min(max(p["z"] + dz, -ARENA_LIMIT), ARENA_LIMIT)
        if not self._inside_cover(next_x, next_z):
            p["x"], p["z"] = next_x, next_z
        jump_down = bool(m.get("jump", False))
        if jump_down and not p["jump_held"] and p["y"] <= 1.0:
            p["vy"] = 7.0
        p["jump_held"] = jump_down
        p["vy"] -= 18.0 * dt
        p["y"] = max(1.0, p["y"] + p["vy"] * dt)
        if p["y"] <= 1.0:
            p["vy"] = 0.0
        p["yaw"], p["pitch"] = yaw, pitch

    @staticmethod
    def _inside_cover(x, z):
        radius = 0.5
        return any(abs(x - cx) < sx / 2 + radius and abs(z - cz) < sz / 2 + radius
                   for cx, cz, sx, sz in COVER_BLOCKS)

    def _shoot(self, shooter):
        if not shooter["alive"] or shooter.get("paused", False):
            return
        now = time.monotonic()
        weapon = self._weapon(shooter)
        if shooter["reload_until"] > now:
            return
        if now - shooter["last_shot"] < weapon.interval:
            return
        if shooter["ammo"] <= 0:
            self._send(shooter, {"type": "dry_fire"})
            return
        shooter["last_shot"] = now
        shooter["ammo"] -= 1
        self._send_ammo(shooter)
        yaw, pitch = math.radians(shooter["yaw"]), math.radians(shooter["pitch"])
        # The server generates spread and tests a ray against player capsules.
        spread = weapon.spread * (0.55 if shooter["crouched"] else 1.0)
        if shooter.get("aiming"):
            spread *= .7
        yaw += random.gauss(0, spread)
        pitch += random.gauss(0, spread)
        direction = (math.sin(yaw) * math.cos(pitch), -math.sin(pitch), math.cos(yaw) * math.cos(pitch))
        # Match the client's camera_pivot: 2.0 standing, CROUCH_HEIGHT while crouched.
        shooter_eye_height = 1.25 if shooter["crouched"] else 2.0
        # The arena is wide; keep a generous distance and intersect a vertical
        # player capsule so shots work consistently uphill and downhill.
        best, best_t, best_hit_y = None, weapon.max_range, None
        ox, oy, oz = shooter["x"], shooter["y"] + shooter_eye_height, shooter["z"]
        for target in self.players.values():
            if target is shooter or not target["alive"] or target.get("paused", False):
                continue
            hit = self._ray_capsule_distance((ox, oy, oz), direction, target,
                                             1.0 if target["crouched"] else 1.8, .42, best_t)
            if hit is not None:
                best, best_t, best_hit_y = target, hit[0], hit[1]
        upper_hit = bool(best and best_hit_y >= best["y"] + (1.0 if best["crouched"] else 1.8) * .5)
        self._broadcast({"type": "shot", "id": shooter["id"], "weapon": weapon.name,
                         # Client world has its floor at y=0; server world uses y=1.
                         "origin": [ox, oy-1.0, oz], "direction": direction,
                         "distance": best_t, "hit": best["id"] if best else None,
                         "hit_region": ("upper" if upper_hit else "lower") if best else None,
                         "practice": not self.match_running})
        # Free-practice shots are visual only and never change health or statistics.
        if not self.match_running:
            if shooter["ammo"] <= 0:
                self._reload(shooter)
            return
        if best:
            if weapon.name == "AWP":
                damage = 100 if upper_hit else 99
            else:
                damage = max(1, round(weapon.damage * (1.2 if upper_hit else .78)))
            best["hp"] = max(0, best["hp"] - damage)
            self._broadcast({"type": "health", "id": best["id"], "hp": best["hp"],
                             "by": shooter["id"], "damage": damage,
                             "region": "upper" if upper_hit else "lower"})
            if best["hp"] == 0:
                shooter["kills"] += 1; shooter["match_kills"] += 1
                best["deaths"] += 1; best["match_deaths"] += 1
                best["alive"] = False; best["respawn_at"] = now + RESPAWN_SECONDS
                self._persist_stat(shooter["username"], "kills")
                self._persist_stat(best["username"], "deaths")
                self._broadcast({"type": "kill", "killer": shooter["id"], "victim": best["id"],
                                 "killer_kills": shooter["kills"], "victim_deaths": best["deaths"],
                                 "killer_match_kills": shooter["match_kills"],
                                 "victim_match_deaths": best["match_deaths"],
                                 "weapon": self._weapon(shooter).name})
                if shooter["match_kills"] % 10 == 0:
                    new_weapon = self._weapon(shooter)
                    shooter["ammo"], shooter["reserve"] = new_weapon.magazine_size, new_weapon.reserve_size
                    self._send(shooter, {"type": "weapon", "name": new_weapon.name})
                    self._send_ammo(shooter)
                if shooter["match_kills"] >= TARGET_KILLS:
                    self._finish_match(shooter)
        if shooter["ammo"] <= 0:
            self._reload(shooter)

    @staticmethod
    def _ray_capsule_distance(origin, direction, player, height, radius, max_distance):
        """Return (first ray distance, impact height) for a vertical player capsule."""
        ox, oy, oz = origin
        dx, dy, dz = direction
        px, pz = player["x"], player["z"]
        feet = player["y"]
        candidates = []

        # Intersect the cylindrical body in the horizontal plane.
        rx, rz = ox - px, oz - pz
        a = dx * dx + dz * dz
        b = 2 * (rx * dx + rz * dz)
        c = rx * rx + rz * rz - radius * radius
        if a > 1e-10:
            discriminant = b*b - 4*a*c
            if discriminant >= 0:
                root = math.sqrt(discriminant)
                for t in ((-b-root)/(2*a), (-b+root)/(2*a)):
                    y = oy + dy*t
                    if 0 <= t <= max_distance and feet+radius <= y <= feet+height-radius:
                        candidates.append((t, y))

        # The rounded feet and head caps make edge hits forgiving at different heights.
        for center_y in (feet + radius, feet + height - radius):
            sx, sy, sz = ox-px, oy-center_y, oz-pz
            projection = sx*dx + sy*dy + sz*dz
            discriminant = projection*projection - (sx*sx+sy*sy+sz*sz-radius*radius)
            if discriminant < 0:
                continue
            root = math.sqrt(discriminant)
            for t in (-projection-root, -projection+root):
                if 0 <= t <= max_distance:
                    candidates.append((t, oy + dy*t))
        return min(candidates, key=lambda hit: hit[0]) if candidates else None

    def _send_ammo(self, player):
        weapon = self._weapon(player)
        self._send(player, {"type": "ammo", "weapon": weapon.name,
                            "current": player["ammo"], "magazine": weapon.magazine_size,
                            "reserve": player["reserve"], "reloading": player["reload_until"] > time.monotonic()})

    def _reload(self, player):
        weapon = self._weapon(player)
        if player["reload_until"] > time.monotonic() or player["ammo"] >= weapon.magazine_size or player["reserve"] <= 0:
            return
        player["reload_until"] = time.monotonic() + 1.6
        self._send(player, {"type": "reload_start", "duration": 1.6})
        self._send_ammo(player)

    def _persist_stat(self, username, field):
        if field not in ("kills", "deaths", "matches_played"):
            return False
        try:
            with self._db() as db:
                db.execute(f"UPDATE players SET {field}={field}+1 WHERE username=?", (username,))
            return True
        except sqlite3.Error as exc:
            print(f"WARNING: could not save {field} for {username}: {exc}; match continues")
            return False

    def _finish_match(self, winner):
        if not self.match_running:
            return
        self.match_running = False
        finished_match_id = self.match_id
        for p in self.players.values():
            self._persist_stat(p["username"], "matches_played")
        try:
            with self._db() as db:
                db.execute("UPDATE players SET wins=wins+1 WHERE username=?", (winner["username"],))
        except sqlite3.Error as exc:
            print(f"WARNING: could not save win for {winner['username']}: {exc}; match result is still broadcast")
        self._broadcast({"type": "match_end", "winner": winner["username"], "scoreboard": self._leaderboard()})
        threading.Thread(target=self._restart_timer, args=(finished_match_id,), daemon=True).start()

    def _restart_timer(self, finished_match_id):
        time.sleep(10)
        with self.lock:
            should_restart = (self.players and not self.stopping.is_set()
                              and not self.match_running and self.match_id == finished_match_id)
        if should_restart:
            self.start_match()

    def _leaderboard(self):
        return sorted([{"id": p["id"], "username": p["username"], "kills": p["match_kills"], "deaths": p["match_deaths"]} for p in self.players.values()], key=lambda x: x["kills"], reverse=True)

    def _handle(self, conn, addr):
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(1.0); buffer = b""; sid = None
        try:
            deadline = time.monotonic() + 30
            while sid is None and time.monotonic() < deadline:
                try: data = conn.recv(4096)
                except socket.timeout: continue
                if not data: return
                frames, buffer = extract_frames(buffer + data)
                if frames:
                    if frames[0].get("type") != "login": return
                    sid = self._login(conn, addr, frames[0])
            if not sid: return
            while not self.stopping.is_set():
                try: data = conn.recv(4096)
                except socket.timeout:
                    self._tick_respawns(); continue
                if not data: break
                frames, buffer = extract_frames(buffer + data)
                with self.lock:
                    p = self.players.get(sid)
                    if not p: break
                    for m in frames:
                        if m.get("type") == "input":
                            self._move(p, m)
                            state = {k: p[k] for k in ("id", "username", "x", "y", "z", "yaw", "hp", "kills", "deaths", "match_kills", "match_deaths", "alive", "crouched", "sprinting", "paused")}
                            self._broadcast({"type": "state", "player": state}, sid)
                            self._send(p, {"type": "own_state", "match": self.match_running,
                                           "match_id": self.match_id, "player": state})
                        elif m.get("type") == "shoot":
                            self._shoot(p)
                        elif m.get("type") == "pause":
                            p["paused"] = bool(m.get("paused", False))
                            if p["paused"]:
                                p["crouched"], p["sprinting"], p["aiming"] = False, False, False
                                p["vy"], p["jump_held"] = 0.0, False
                            state = {k: p[k] for k in ("id", "username", "x", "y", "z", "yaw", "hp", "kills", "deaths", "match_kills", "match_deaths", "alive", "crouched", "sprinting", "paused")}
                            self._broadcast({"type": "state", "player": state}, sid)
                            self._send(p, {"type": "own_state", "match": self.match_running,
                                           "match_id": self.match_id, "player": state})
                        elif m.get("type") == "reload":
                            self._reload(p)
                        elif m.get("type") == "stats":
                            self._send(p, {"type": "stats", "players": self._match_stats(), "weapon": self._weapon(p).name})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            if not self.stopping.is_set(): print(f"Client error {addr}: {exc}")
        finally:
            if sid:
                with self.lock:
                    p = self.players.pop(sid, None)
                if p:
                    self._broadcast({"type": "left", "id": sid})
                    print(f"{p['username']} disconnected")
            try: conn.close()
            except OSError: pass

    def _tick_respawns(self):
        now = time.monotonic()
        with self.lock:
            for p in self.players.values():
                if p["reload_until"] and now >= p["reload_until"]:
                    weapon = self._weapon(p)
                    amount = min(weapon.magazine_size - p["ammo"], p["reserve"])
                    p["ammo"] += amount
                    p["reserve"] -= amount
                    p["reload_until"] = 0.0
                    self._send_ammo(p)
                if not p["alive"] and now >= p["respawn_at"]:
                    p["x"], p["y"], p["z"] = map(float, self._choose_spawn())
                    p["yaw"] = self._spawn_yaw(p["x"], p["z"])
                    p["hp"], p["alive"], p["vy"], p["jump_held"] = 100, True, 0.0, False
                    p["crouched"], p["sprinting"], p["aiming"] = False, False, False
                    self._broadcast({"type": "respawn", "id": p["id"], "x": p["x"], "y": p["y"], "z": p["z"], "hp": 100,
                                    "crouched": False, "sprinting": False})

    def start_match(self):
        with self.lock:
            self.match_id += 1; self.match_running = True; self.started_at = time.monotonic()
            for i, p in enumerate(self.players.values()):
                p["match_kills"] = p["match_deaths"] = 0
                p["hp"], p["alive"] = 100, True
                p["vy"], p["jump_held"] = 0.0, False
                p["crouched"], p["sprinting"], p["aiming"] = False, False, False
                # Preserve unique starts for up to ten connected players.
                p["x"], p["y"], p["z"] = map(float, SPAWNS[i % len(SPAWNS)])
                p["yaw"] = self._spawn_yaw(p["x"], p["z"])
                weapon = self._weapon(p)
                p["ammo"], p["reserve"], p["reload_until"] = weapon.magazine_size, weapon.reserve_size, 0.0
                self._send(p, {"type": "weapon", "name": weapon.name})
                self._send_ammo(p)
        self._broadcast({"type": "match_start", "match_id": self.match_id, "target": TARGET_KILLS})
        with self.lock:
            stats = self._match_stats()
            for p in list(self.players.values()):
                self._send(p, {"type": "stats", "players": stats, "weapon": self._weapon(p).name})
        print(f"Match {self.match_id} started (first to {TARGET_KILLS} kills)")

    def stop_match(self):
        self.match_running = False
        self._broadcast({"type": "match_stop"})
        print("Match stopped")

    def command_loop(self):
        print("Commands: create <user> <password>, delete <user>, list, ban/unban <user>, kick <user>, resetstats <user|all>, addkills <user>, startmatch, stopmatch, quit")
        while not self.stopping.is_set():
            try: line = input("server> ").strip().split()
            except (EOFError, KeyboardInterrupt):
                self.stopping.set()
                break
            if not line: continue
            cmd, args = line[0].lower(), line[1:]
            try:
                if cmd == "create" and len(args) == 2:
                    ok, message = self.create_account(*args); print(message)
                elif cmd == "delete" and len(args) == 1:
                    with self._db() as db: cur = db.execute("DELETE FROM players WHERE username=?", args)
                    print("Deleted" if cur.rowcount else "Not found")
                elif cmd == "list":
                    with self.lock:
                        for p in self.players.values(): print(f"{p['username']} {p['addr'][0]} hp={p['hp']} kills={p['match_kills']}")
                elif cmd in ("ban", "unban") and len(args) == 1:
                    with self._db() as db: cur = db.execute("UPDATE players SET banned=? WHERE username=?", (int(cmd == "ban"), args[0]))
                    print("Updated" if cur.rowcount else "Not found")
                elif cmd == "kick" and len(args) == 1:
                    with self.lock: target = next((p for p in self.players.values() if p["username"].lower() == args[0].lower()), None)
                    if target: target["sock"].shutdown(socket.SHUT_RDWR); print("Kicked")
                    else: print("Not online")
                elif cmd == "resetstats" and len(args) == 1:
                    ok, message = self.reset_player_stats(args[0]); print(message)
                elif cmd == "addkills" and len(args) == 1:
                    ok, message = self.add_ten_kills(args[0]); print(message)
                elif cmd == "startmatch": self.start_match()
                elif cmd == "stopmatch": self.stop_match()
                elif cmd in ("quit", "exit"):
                    print("Stopping server...")
                    self.stopping.set()
                    break
                else: print("Unknown command or invalid arguments")
            except (sqlite3.Error, OSError) as exc: print(f"Command failed: {exc}")

    def serve_forever(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port)); self.sock.listen(MAX_PLAYERS)
        # Tick respawns and reload completion at the same cadence as movement inputs.
        self.sock.settimeout(1 / TICK_RATE)
        print(f"Deathmatch server listening on {self.host}:{self.port}; database: {self.db_path}")
        threading.Thread(target=self.command_loop, daemon=True).start()
        try:
            while not self.stopping.is_set():
                self._tick_respawns()
                try: conn, addr = self.sock.accept()
                except socket.timeout: continue
                except OSError:
                    if self.stopping.is_set(): break
                    raise
                threading.Thread(target=self._handle, args=(conn, addr), daemon=True).start()
        finally:
            self.stopping.set()
            with self.lock:
                for p in self.players.values():
                    try: p["sock"].close()
                    except OSError: pass
            self.sock.close()

if __name__ == "__main__":
    set_console_branding()
    GameServer().serve_forever()
