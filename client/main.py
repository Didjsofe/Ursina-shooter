"""Ursina GUI client. Run with: python client.py"""
import json
import math
import os
import sys
import time

from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path: sys.path.insert(0, ROOT)
ICON_PATH = os.path.join(ROOT, "logo.ico")
from client.network import NetworkClient
from client.world import RemotePlayer, build_arena
from shared.constants import CROUCH_HEIGHT, CROUCH_SPEED, SPRINT_SPEED, TICK_RATE, WALK_SPEED
from shared.weapons import WEAPONS, for_kills

if getattr(sys, "frozen", False):
    # Keep writable per-user credentials outside PyInstaller's temporary extraction folder.
    user_data = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".local", "share")
    CONFIG_PATH = os.path.join(user_data, "LanDeathmatch", "config.json")
    CONFIG_READ_PATHS = [os.path.join(os.path.dirname(sys.executable), "config.json"), CONFIG_PATH]
else:
    CONFIG_PATH = os.path.join(ROOT, "config.json")
    CONFIG_READ_PATHS = [CONFIG_PATH]

def load_config():
    result = {"server_ip": "127.0.0.1", "port": 27015, "connect_timeout": 5}
    for path in CONFIG_READ_PATHS:
        try:
            with open(path, encoding="utf-8") as f:
                result.update(json.load(f))
        except (OSError, ValueError):
            continue
    result["server_ip"] = os.environ.get("LAN_SERVER_IP", result.get("server_ip", "127.0.0.1"))
    result["port"] = int(os.environ.get("LAN_SERVER_PORT", result.get("port", 27015)))
    return result

cfg = load_config()
app = Ursina(title="LAN Deathmatch",
             icon=ICON_PATH if os.path.isfile(ICON_PATH) else "textures/ursina.ico",
             editor_ui_enabled=False, size=(1280, 720), borderless=False)
window.title = "LAN Deathmatch"
window.color = color.rgb32(18, 23, 34)
# This Ursina version creates its editor panel even when editor_ui_enabled=False.
if window.editor_ui:
    window.editor_ui.enabled = False
camera.fov = 90
net = None
local_id = None
remotes = {}
player = None
login_ui = []
game_ui = []
last_input = 0.0
enter_was_down = False
tab_open = False
match_active = False
current_hp = 100
current_kills = 0
current_deaths = 0
current_match_kills = 0
current_match_deaths = 0
current_weapon = for_kills(0)
current_ammo = 0
current_reserve = 0
reload_active = False
ammo_protocol = False
fire_held = False
zoom_held = False
paused = False
next_fire_at = 0.0
server_position = None

class DeathmatchController(FirstPersonController):
    """The server owns vertical movement; never run a second local jump simulation."""
    def input(self, key):
        # Space is transmitted in the regular input packet and simulated server-side.
        pass

def clear_group(items):
    while items: destroy(items.pop())

def set_mouse_capture(enabled):
    """Change capture without Ursina reapplying stale window geometry after maximize."""
    enabled = bool(enabled)
    mouse._locked = enabled
    window.set_mouse_mode(window.M_confined if enabled else window.M_absolute)
    window.set_cursor_hidden(enabled)
    # Ursina's locked setter calls requestProperties(window), which can restore old
    # position/size after the OS maximized the window while the game was paused.
    mouse._locked_mouse_last_frame = True
    mouse.velocity = Vec3(0, 0, 0)
    if enabled:
        mouse.position = (0, 0)
        mouse.prev_x, mouse.prev_y = mouse.x, mouse.y

def save_login_credentials(username, password, server_ip):
    """Remember the last successful login for the next launch on this PC."""
    cfg["username"] = username.strip()
    cfg["password"] = password
    cfg["server_ip"] = server_ip.strip()
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as config_file:
            json.dump(cfg, config_file, ensure_ascii=False, indent=2)
            config_file.write("\n")
    except OSError:
        # Login should still proceed if a portable/read-only install can't save config.
        pass

def spanish_connection_error(exc):
    """Keep transport and server errors readable without exposing raw trace text."""
    message = str(exc).lower()
    if "invalid credentials" in message:
        return "Usuario o contraseña incorrectos, o cuenta no disponible."
    if "server full" in message:
        return "Servidor lleno o esta cuenta ya está conectada."
    if "timed out" in message or "timeout" in message:
        return "Se agotó el tiempo de conexión. Comprueba el servidor."
    return "No se pudo conectar. Revisa la IP y que el servidor esté activo."

def show_login(message=""):
    global player
    clear_group(game_ui)
    if player:
        destroy(player); player = None
    for obj in remotes.values(): destroy(obj)
    remotes.clear()
    set_mouse_capture(False)
    bg = Entity(parent=camera.ui, model="quad", position=(0, 0, 1), scale=(window.aspect_ratio * 2, 2), color=color.rgb32(18, 23, 34)); login_ui.append(bg)
    panel = Entity(parent=camera.ui, model="quad", position=(0, -.02, .82), scale=(.86,.88), color=color.rgb32(36, 35, 49)); login_ui.append(panel)
    accent = Entity(parent=camera.ui, model="quad", position=(-.421, -.02, .78), scale=(.018,.88), color=color.rgb32(226, 91, 83)); login_ui.append(accent)
    top_line = Entity(parent=camera.ui, model="quad", position=(0, .405, .77), scale=(.84,.012), color=color.rgb32(56, 185, 178)); login_ui.append(top_line)
    title = Text(parent=camera.ui, text="LAN DEATHMATCH", y=.345, scale=2.3, origin=(0,0), color=color.rgb32(246, 192, 151)); login_ui.append(title)
    subtitle = Text(parent=camera.ui, text="COMBATE EN RED  ·  DEATHMATCH", y=.285, scale=1.0, origin=(0,0), color=color.rgb32(84, 208, 196)); login_ui.append(subtitle)
    username = InputField(parent=camera.ui, y=.16, scale=(.52,.06), default_value=cfg.get("username", ""), color=color.rgb32(72, 79, 94)); login_ui.append(username)
    password = InputField(parent=camera.ui, y=.01, scale=(.52,.06), password=True, default_value=cfg.get("password", ""), color=color.rgb32(72, 79, 94)); login_ui.append(password)
    host = InputField(parent=camera.ui, y=-.14, scale=(.52,.06), default_value=cfg.get("server_ip", "127.0.0.1"), color=color.rgb32(72, 79, 94)); login_ui.append(host)
    for field in (username, password, host):
        field.text_color = color.rgb32(246, 232, 214)
        field.highlight_color = color.rgb32(98, 112, 127)
    status = Text(parent=camera.ui, text=message, y=-.245, scale=1.0, origin=(0,0), color=color.rgb32(255, 151, 132)); login_ui.append(status)
    def login():
        global net, local_id
        status.text = "Conectando…"
        try:
            net = NetworkClient(host.text.strip(), cfg.get("port", 27015), cfg.get("connect_timeout", 5))
            info = net.connect(username.text.strip(), password.text)
            local_id = info["id"]
            save_login_credentials(username.text, password.text, host.text)
            clear_group(login_ui)
            enter_game(info)
        except Exception as exc:
            status.text = spanish_connection_error(exc)
    login_btn = Button(parent=camera.ui, text="Entrar", y=-.34, scale=(.24,.065), color=color.rgb32(210, 86, 78), highlight_color=color.rgb32(242, 123, 83), on_click=login); login_ui.append(login_btn)
    exit_btn = Button(parent=camera.ui, text="Salir del juego", y=-.425, scale=(.32,.06), color=color.rgb32(57, 67, 82), highlight_color=color.rgb32(78, 96, 111), on_click=application.quit); login_ui.append(exit_btn)
    # Center the three labels on their matching fields to avoid uneven Spanish alignment.
    for label, y in [("Usuario",.22),("Contraseña",.07),("IP del servidor",-.08)]:
        label_obj = Text(parent=camera.ui, text=label, x=0, y=y, scale=1.0, origin=(0,0), color=color.rgb32(190, 205, 219)); login_ui.append(label_obj)

def enter_game(info):
    global player, server_position, current_kills, current_deaths, current_match_kills, current_match_deaths
    global current_weapon, match_active, current_ammo, current_reserve, ammo_protocol, paused
    paused = False
    match_active = bool(info.get("match", False))
    # In-game counters and weapon progression are match-local; lifetime totals stay on the server.
    current_match_kills = int(info.get("match_kills", 0))
    current_match_deaths = int(info.get("match_deaths", 0))
    current_kills, current_deaths = current_match_kills, current_match_deaths
    current_weapon = info.get("weapon", for_kills(current_match_kills).name)
    current_ammo, current_reserve = info.get("ammo", 0), info.get("reserve", 0)
    ammo_protocol = "ammo" in info and "reserve" in info
    build_arena()
    spawn_position = Vec3(info.get("x", 0), max(0, info.get("y", 1) - 1), info.get("z", 0))
    server_position = Vec3(spawn_position)
    player = DeathmatchController(position=spawn_position, speed=7, gravity=0)
    player.rotation_y = info.get("yaw", 0)
    player.cursor.visible = False
    player.health = 100
    # Server-rendered weapon silhouette attached to camera; damage/stat authority remains server-side.
    gun = Entity(parent=camera, model="cube", position=(.48,-.38,.72), rotation=(0,0,-5), scale=(.16,.13,.65), color=color.rgb32(94,104,115)); game_ui.append(gun)
    Entity(parent=camera, model="cube", position=(.48,-.30,.53), scale=(.21,.09,.28), color=color.rgb32(70,78,87))
    hp_text = Text(parent=camera.ui, text="VIDA 100", x=-.86, y=-.43, scale=1.3); game_ui.append(hp_text)
    crosshair = Entity(parent=camera.ui)
    game_ui.append(crosshair)
    crosshair_parts = [
        Entity(parent=crosshair, model="quad", scale=(.003,.018), color=color.rgb32(238,235,222)),
        Entity(parent=crosshair, model="quad", scale=(.003,.018), color=color.rgb32(238,235,222)),
        Entity(parent=crosshair, model="quad", scale=(.018,.003), color=color.rgb32(238,235,222)),
        Entity(parent=crosshair, model="quad", scale=(.018,.003), color=color.rgb32(238,235,222)),
    ]
    shotgun_crosshair = Entity(parent=camera.ui)
    shotgun_parts = [
        Entity(parent=shotgun_crosshair, model="quad", scale=(.018,.003), color=color.rgb32(255,208,125))
        for _ in range(8)
    ]
    scope_overlay = Entity(parent=camera.ui, enabled=False)
    scope_parts = [
        Entity(parent=scope_overlay, model="quad", position=(-.5,0,0), scale=(.98,.002), color=color.rgb32(222,226,218)),
        Entity(parent=scope_overlay, model="quad", position=(.5,0,0), scale=(.98,.002), color=color.rgb32(222,226,218)),
        Entity(parent=scope_overlay, model="quad", position=(0,.5,0), scale=(.002,.98), color=color.rgb32(222,226,218)),
        Entity(parent=scope_overlay, model="quad", position=(0,-.5,0), scale=(.002,.98), color=color.rgb32(222,226,218)),
        Entity(parent=scope_overlay, model="quad", position=(0,0,0), scale=(.012,.002), color=color.rgb32(222,226,218)),
    ]
    game_ui.append(scope_overlay)
    game_ui.append(shotgun_crosshair)
    initial_score = f"DEATHMATCH — {current_match_kills}/100 bajas" if match_active else "ENTRENAMIENTO — las bajas no cuentan"
    score_text = Text(parent=camera.ui, text=initial_score, y=.46, scale=1.15, origin=(0,0)); game_ui.append(score_text)
    weapon_text = Text(parent=camera.ui, text="", x=.86, y=-.39, origin=(1,0), scale=1.0); game_ui.append(weapon_text)
    ammo_text = Text(parent=camera.ui, text="", x=.86, y=-.47, origin=(1,0), scale=1.0, color=color.rgb32(235, 230, 216)); game_ui.append(ammo_text)
    leaderboard = Text(parent=camera.ui, text="", x=.78, y=.43, origin=(1,1), scale=1.0, background=True); game_ui.append(leaderboard)
    stat_panel = Entity(parent=camera.ui, enabled=False)
    Entity(parent=stat_panel, model="quad", position=(0,0,.8), scale=(.86,.68), color=color.rgb32(28,32,43))
    Entity(parent=stat_panel, model="quad", position=(0,.315,.75), scale=(.86,.012), color=color.rgb32(56,185,178))
    Text(parent=stat_panel, text="ESTADÍSTICAS DE LA PARTIDA", y=.265, scale=1.5, origin=(0,0), color=color.rgb32(246,192,151))
    Text(parent=stat_panel, text="JUGADOR", x=-.42, y=.155, scale=1.0, origin=(-1,0), color=color.rgb32(84,208,196))
    Text(parent=stat_panel, text="BAJAS", x=.155, y=.155, scale=1.0, origin=(1,0), color=color.rgb32(84,208,196))
    Text(parent=stat_panel, text="MUERTES", x=.425, y=.155, scale=1.0, origin=(1,0), color=color.rgb32(84,208,196))
    game_ui.append(stat_panel)
    initial_notice = "PARTIDA EN CURSO" if match_active else "ENTRENAMIENTO LIBRE — puedes jugar en solitario"
    notice = Text(parent=camera.ui, text=initial_notice, y=.34, scale=1.1, origin=(0,0), color=color.yellow); game_ui.append(notice)
    pause_overlay = Entity(parent=camera.ui, enabled=False)
    Entity(parent=pause_overlay, model="quad", position=(0,0,.55),
           scale=(window.aspect_ratio*2,2), color=color.rgb32(12,16,25))
    Entity(parent=pause_overlay, model="quad", position=(0,0,.4),
           scale=(.64,.30), color=color.rgb32(36,35,49))
    Entity(parent=pause_overlay, model="quad", position=(0,.145,.35),
           scale=(.64,.012), color=color.rgb32(56,185,178))
    Text(parent=pause_overlay, text="PAUSA", y=.045, scale=1.8, origin=(0,0), color=color.rgb32(246,192,151))
    Text(parent=pause_overlay, text="Pulsa ESC para volver al combate", y=-.065,
         scale=.72, origin=(0,0), color=color.rgb32(190,205,219))
    game_ui.append(pause_overlay)
    # Keep references to UI elements.
    app.hp_text, app.score_text, app.weapon_text = hp_text, score_text, weapon_text
    app.leaderboard, app.stat_panel, app.notice, app.ammo_text = leaderboard, stat_panel, notice, ammo_text
    app.crosshair_parts = crosshair_parts
    app.crosshair, app.scope_overlay = crosshair, scope_overlay
    app.shotgun_crosshair, app.shotgun_parts = shotgun_crosshair, shotgun_parts
    app.pause_overlay = pause_overlay
    app.stat_rows = []
    app.last_send = time.monotonic()
    set_mouse_capture(True)
    process_events()

def process_events():
    global current_hp, current_kills, current_deaths, current_match_kills, current_match_deaths
    global current_weapon, match_active
    global current_ammo, current_reserve, reload_active, ammo_protocol, server_position
    if not net: return
    while True:
        try: m = net.events.get_nowait()
        except Exception: break
        t = m.get("type")
        if t == "snapshot":
            match_active = m.get("match", False)
            sync_match_ui(match_active)
            for d in m.get("players", []):
                if d["id"] != local_id: add_or_update(d)
        elif t == "joined": add_or_update(m["player"])
        elif t == "state": add_or_update(m["player"])
        elif t == "own_state":
            d = m["player"]
            if "match" in m and bool(m["match"]) != match_active:
                match_active = bool(m["match"])
                sync_match_ui(match_active)
            # Store only the newest authority sample; smooth it once per rendered frame.
            # This avoids applying a backlog of 20 Hz snapshots as visible jumps.
            server_position = Vec3(d["x"], max(0, d["y"] - 1), d["z"])
            current_hp = d["hp"]
        elif t == "left":
            old = remotes.pop(m["id"], None)
            if old: destroy(old)
        elif t == "health":
            if m["id"] == local_id: current_hp = m["hp"]
            elif m["id"] in remotes and m["hp"] <= 0: remotes[m["id"]].enabled = False
        elif t == "kill":
            if m["killer"] == local_id:
                current_kills = current_match_kills = m["killer_match_kills"]
            if m["victim"] == local_id:
                current_deaths = current_match_deaths = m["victim_match_deaths"]
        elif t == "respawn":
            if m["id"] == local_id:
                current_hp = 100
                server_position = Vec3(m["x"], max(0, m["y"]-1), m["z"])
                player.position = server_position
            elif m["id"] in remotes: remotes[m["id"]].apply({**m,"alive":True}, snap=True)
        elif t == "match_start":
            current_match_kills = current_match_deaths = 0
            current_kills = current_deaths = 0
            for remote in remotes.values():
                remote.match_kills = remote.match_deaths = 0
            match_active = True; sync_match_ui(True)
        elif t == "match_stop":
            match_active = False; sync_match_ui(False)
        elif t == "match_end":
            match_active = False; app.notice.text = f"GANADOR: {m['winner']} — nueva partida en 10 segundos"
        elif t == "weapon":
            current_weapon = m["name"]
            app.notice.text = f"Nueva arma: {m['name']}"
        elif t == "kills_update":
            current_kills = current_match_kills = int(m.get("kills", current_kills))
        elif t == "deaths_update":
            current_deaths = current_match_deaths = int(m.get("deaths", current_deaths))
        elif t == "ammo":
            ammo_protocol = True
            current_ammo, current_reserve = m["current"], m["reserve"]
            reload_active = m.get("reloading", False)
        elif t == "reload_start":
            reload_active = True
        elif t == "dry_fire":
            app.notice.text = "Cargador vacío — pulsa R para recargar"
        elif t == "shot":
            origin = Vec3(*m["origin"])
            direction = Vec3(*m["direction"])
            tracer_length = max(1.0, float(m.get("distance", 120)))
            tracer = Entity(model="cube", position=origin + direction * (tracer_length / 2),
                            scale=(.04, .04, tracer_length), color=color.rgb32(255, 215, 105))
            tracer.look_at(origin + direction * tracer_length)
            destroy(tracer, delay=.32)
            if m.get("id") == local_id:
                flash = Entity(parent=camera, model="sphere", position=(.42, -.31, .92), scale=.10,
                               color=color.rgb32(255, 178, 64), add_to_scene_entities=False)
                destroy(flash, delay=.07)
        elif t == "stats":
            for row in m["players"]:
                if row.get("id") == local_id:
                    current_kills, current_deaths = row["kills"], row["deaths"]
                    current_match_kills = row.get("match_kills", current_match_kills)
                    current_match_deaths = row.get("match_deaths", current_match_deaths)
                elif row.get("id") in remotes:
                    remotes[row["id"]].match_kills = row.get("match_kills", remotes[row["id"]].match_kills)
                    remotes[row["id"]].match_deaths = row.get("match_deaths", remotes[row["id"]].match_deaths)
            render_stats(m["players"])
            current_weapon = m.get("weapon", current_weapon.name if hasattr(current_weapon, "name") else current_weapon)
        elif t == "disconnected":
            app.notice.text = "Se perdió la conexión con el servidor"
    app.hp_text.text = f"VIDA {current_hp}  |  PARTIDA {current_match_kills} B / {current_match_deaths} M"
    if match_active:
        app.score_text.text = f"DEATHMATCH — {current_match_kills}/100 bajas"
    weapon_name = current_weapon.name if hasattr(current_weapon, "name") else current_weapon
    app.weapon_text.text = f"{weapon_name}  [{min(current_match_kills//10+1, len(WEAPONS))}/{len(WEAPONS)}]"
    app.ammo_text.text = (f"{current_ammo} / {current_reserve}" + ("  RECARGANDO" if reload_active else "  [R] Recargar")
                          if ammo_protocol else "Actualiza el servidor para usar la munición")
    app.leaderboard.text = "\n".join(f"{p.tag.text}: {getattr(p,'match_kills',0)}" for p in remotes.values())

def add_or_update(d):
    obj = remotes.get(d["id"])
    if obj is None:
        obj = remotes[d["id"]] = RemotePlayer(d)
    obj.apply(d)
    obj.kills = d.get("kills", 0)
    obj.match_kills = d.get("match_kills", obj.match_kills)

def render_stats(rows):
    """Draw each scoreboard column independently for consistent Spanish alignment."""
    for item in app.stat_rows:
        destroy(item)
    app.stat_rows.clear()
    for index, row in enumerate(rows[:10]):
        y = .095 - index * .043
        if index % 2 == 0:
            app.stat_rows.append(Entity(parent=app.stat_panel, model="quad", position=(0,y,.65),
                                        scale=(.78,.04), color=color.rgb32(39,45,57)))
        player_color = color.rgb32(246,192,151) if row.get("id") == local_id else color.rgb32(226,231,235)
        username = row["username"]
        if len(username) > 14:
            username = username[:13] + "…"
        app.stat_rows.append(Text(parent=app.stat_panel, text=username, x=-.36, y=y,
                                  scale=1.0, origin=(-1,0), color=player_color))
        app.stat_rows.append(Text(parent=app.stat_panel, text=str(row["kills"]), x=.10, y=y,
                                  scale=1.0, origin=(1,0), color=color.rgb32(226,231,235)))
        app.stat_rows.append(Text(parent=app.stat_panel, text=str(row["deaths"]), x=.33, y=y,
                                  scale=1.0, origin=(1,0), color=color.rgb32(226,231,235)))

def sync_match_ui(active):
    app.notice.text = "PARTIDA EN CURSO" if active else "ENTRENAMIENTO LIBRE — puedes jugar en solitario"
    app.score_text.text = f"DEATHMATCH — {current_match_kills}/100 bajas" if active else "ENTRENAMIENTO — las bajas no cuentan"

def update():
    global last_input, tab_open, enter_was_down, fire_held, zoom_held
    enter_down = bool(held_keys.get("enter", 0) or held_keys.get("return", 0) or held_keys.get("kp_enter", 0))
    if not enter_down:
        enter_was_down = False
    elif player and not enter_was_down:
        leave_game()
        return
    enter_was_down = enter_down
    if not net or not player: return
    process_events()
    if current_hp <= 0:
        player.speed = 0
        fire_held = False
        now = time.monotonic()
        if now - last_input >= 1 / TICK_RATE:
            net.send({"type":"input", "forward":0, "side":0, "jump":False,
                      "sprint":False, "crouch":False, "aim":False,
                      "yaw":player.rotation_y, "pitch":camera.rotation_x, "dt":1/TICK_RATE})
            last_input = now
        return
    if paused:
        player.speed = 0
        player.mouse_sensitivity = Vec2(0, 0)
        mouse.velocity = Vec3(0, 0, 0)
        fire_held = False
        zoom_held = False
        if server_position is not None:
            player.position = server_position
        now = time.monotonic()
        if now - last_input >= 1 / TICK_RATE:
            net.send({"type":"input", "forward":0, "side":0, "jump":False,
                      "sprint":False, "crouch":False, "aim":False,
                      "yaw":player.rotation_y, "pitch":camera.rotation_x, "dt":1/TICK_RATE})
            last_input = now
        return
    # Keep local prediction responsive while easing toward server samples at render rate.
    if server_position is not None:
        blend = 1 - math.exp(-12 * min(max(time.dt, .001), .1))
        horizontal_error = math.hypot(server_position.x - player.x, server_position.z - player.z)
        if horizontal_error > 8:  # server teleport/respawn or a long network interruption
            player.x, player.z = server_position.x, server_position.z
        else:
            player.x = lerp(player.x, server_position.x, blend)
            player.z = lerp(player.z, server_position.z, blend)
        player.y = lerp(player.y, server_position.y, blend)
    crouching = bool(held_keys["control"] or held_keys["left control"] or held_keys["right control"])
    sprinting = bool(held_keys["shift"] or held_keys["left shift"] or held_keys["right shift"]) and not crouching
    player.speed = CROUCH_SPEED if crouching else SPRINT_SPEED if sprinting else WALK_SPEED
    stance_blend = 1 - math.exp(-12 * min(max(time.dt, .001), .1))
    player.height = lerp(player.height, CROUCH_HEIGHT if crouching else 2.0, stance_blend)
    player.camera_pivot.y = player.height
    weapon_name = current_weapon.name if hasattr(current_weapon, "name") else current_weapon
    weapon = next((item for item in WEAPONS if item.name == weapon_name), for_kills(current_match_kills))
    sniper_scoped = zoom_held and weapon.name == "AWP"
    shotgun_reticle = weapon.name == "Nova"
    target_fov = weapon.zoom_fov if zoom_held else 90
    fov_blend = 1 - math.exp(-14 * min(max(time.dt, .001), .1))
    camera.fov = lerp(camera.fov, target_fov, fov_blend)
    app.crosshair.enabled = not sniper_scoped and not shotgun_reticle
    app.scope_overlay.enabled = sniper_scoped
    app.shotgun_crosshair.enabled = shotgun_reticle
    moving = math.hypot(held_keys["w"]-held_keys["s"], held_keys["d"]-held_keys["a"])
    base_gap = .012 + min(weapon.spread, .08) * .55
    movement_gap = (.012 if sprinting else .006 if moving else 0.0)
    crouch_factor = .68 if crouching else 1.0
    if zoom_held:
        crouch_factor *= .72
    gap = (base_gap + movement_gap) * crouch_factor
    crosshair_color = color.rgb32(166,255,190) if crouching else color.rgb32(238,235,222)
    for index, part in enumerate(app.crosshair_parts):
        part.color = crosshair_color
        if index == 0: part.position = (0, gap + .012, 0)
        elif index == 1: part.position = (0, -gap - .012, 0)
        elif index == 2: part.position = (-gap - .012, 0, 0)
        else: part.position = (gap + .012, 0, 0)
    if shotgun_reticle:
        radius = (.027 + weapon.spread * .65 + movement_gap * 1.5) * crouch_factor
        length = .018
        corners = [
            ((-radius-length/2, radius, 0), (length,.003)),
            ((-radius, radius+length/2, 0), (.003,length)),
            ((radius+length/2, radius, 0), (length,.003)),
            ((radius, radius+length/2, 0), (.003,length)),
            ((-radius-length/2, -radius, 0), (length,.003)),
            ((-radius, -radius-length/2, 0), (.003,length)),
            ((radius+length/2, -radius, 0), (length,.003)),
            ((radius, -radius-length/2, 0), (.003,length)),
        ]
        for part, (position, scale) in zip(app.shotgun_parts, corners):
            part.position, part.scale = position, scale
    now = time.monotonic()
    if fire_held:
        _send_shot_if_ready(now)
    if now - last_input >= 1 / TICK_RATE:
        f = int(held_keys["w"]) - int(held_keys["s"])
        s = int(held_keys["d"]) - int(held_keys["a"])
        input_dt = min(max(now - last_input, 1 / TICK_RATE), .1) if last_input else 1 / TICK_RATE
        net.send({"type":"input", "forward":f, "side":s, "jump":bool(held_keys["space"]),
                  "sprint":sprinting, "crouch":crouching, "aim":zoom_held, "yaw":player.rotation_y,
                  "pitch":camera.rotation_x, "dt":input_dt})
        last_input = now
    if held_keys["tab"] and not tab_open:
        tab_open = True; net.send({"type":"stats"}); app.stat_panel.enabled = True
    elif not held_keys["tab"] and tab_open:
        tab_open = False; app.stat_panel.enabled = False

def input(key):
    global net, enter_was_down, fire_held, zoom_held, paused
    if key == "left mouse down" and net and player and not paused:
        fire_held = True
        _send_shot_if_ready(time.monotonic())
    elif key == "left mouse up":
        fire_held = False
    elif key == "right mouse down" and net and player and not paused:
        zoom_held = True
    elif key == "right mouse up":
        zoom_held = False
    elif key == "r" and net and not paused:
        net.send({"type":"reload"})
    elif key == "escape" and net and player:
        paused = not paused
        app.pause_overlay.enabled = paused
        fire_held = False
        zoom_held = False
        player.speed = 0 if paused else WALK_SPEED
        if paused:
            player.mouse_sensitivity = Vec2(0, 0)
            set_mouse_capture(False)
        else:
            player.mouse_sensitivity = Vec2(40, 40)
            set_mouse_capture(True)
        net.send({"type": "pause", "paused": paused})
    elif key in ("enter", "return") and player:
        enter_was_down = True
        leave_game()

def leave_game():
    global net, enter_was_down, fire_held, zoom_held, paused
    if net: net.close()
    net = None
    enter_was_down = True
    fire_held = False
    zoom_held = False
    paused = False
    camera.fov = 90
    clear_group(game_ui)
    show_login("Desconectado")

def _send_shot_if_ready(now):
    global next_fire_at
    if not net or not player or now < next_fire_at:
        return
    if ammo_protocol and current_ammo <= 0:
        return
    weapon_name = current_weapon.name if hasattr(current_weapon, "name") else current_weapon
    weapon = next((item for item in WEAPONS if item.name == weapon_name), for_kills(current_match_kills))
    net.send({"type": "shoot"})
    next_fire_at = now + weapon.interval

