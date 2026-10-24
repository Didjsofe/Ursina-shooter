from ursina import *
from ursina.prefabs.first_person_controller import FirstPersonController
import socket
import threading
import json
import time

PORT = 9999
server_socket = None
client_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
client_socket.setblocking(False)

# Спрашиваем роль в консоли перед запуском окна
choice = input("Вы хотите запустить Сервер (Хост) или Клиент? (введите 'h' или 'c'): ").strip().lower()

if choice == 'h':
    MY_ID = "1"
    # Запускаем встроенный сервер
    def run_server():
        global server_socket
        try:
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(("127.0.0.1", PORT))
            print(f"[СЕРВЕР] Успешно запущен на порту {PORT}")
            
            clients = []
            player_positions = {}
            
            while True:
                data, addr = server_socket.recvfrom(2048)
                if addr not in clients:
                    if len(clients) < 2:
                        clients.append(addr)
                        print(f"[СЕРВЕР] Подключился клиент: {addr}")
                
                packet = json.loads(data.decode('utf-8'))
                player_positions[packet["id"]] = {"pos": packet["pos"], "rot": packet["rot"]}
                
                response = json.dumps(player_positions).encode('utf-8')
                for client in clients:
                    server_socket.sendto(response, client)
        except Exception as e:
            print(f"[ОШИБКА СЕРВЕРА]: {e}")

    threading.Thread(target=run_server, daemon=True).start()
    time.sleep(0.5)
else:
    MY_ID = "2"

# --- ЗАПУСК URSINA И КАРТЫ ---
app = Ursina()

ground = Entity(model='plane', scale=(50, 1, 50), color=color.lime, texture='white_cube', texture_scale=(50, 50), collider='box')

for i in range(10):
    Entity(model='cube', color=color.azure, position=(Vec3(i * 2 - 10, 1, 5)), collider='box')

player = FirstPersonController()
player.x = int(MY_ID) * 3 - 5
player.y = 2

other_player = Entity(model='cube', color=color.red, scale=(1, 2, 1), collider='box')
other_player.position = (0, -100, 0)

Sky()
DirectionalLight(y=2, z=3, shadows=True)

server_data = {}

def receive_data():
    global server_data
    while True:
        try:
            data, _ = client_socket.recvfrom(2048)
            server_data = json.loads(data.decode('utf-8'))
        except:
            time.sleep(0.01)

threading.Thread(target=receive_data, daemon=True).start()

def update():
    # Отправляем свои координаты на сервер (localhost)
    try:
        packet = json.dumps({
            "id": MY_ID,
            "pos": [player.x, player.y, player.z],
            "rot": [player.rotation_x, player.rotation_y]
        }).encode('utf-8')
        client_socket.sendto(packet, ("127.0.0.1", PORT))
    except Exception as e:
        pass

    # Обновляем второго игрока
    for pid, pdata in server_data.items():
        if pid != MY_ID:
            pos = pdata["pos"]
            rot = pdata["rot"]
            other_player.position = Vec3(pos[0], pos[1], pos[2])
            other_player.rotation_y = rot[1]

app.run()