import socket
import json

SERVER_IP = "127.0.0.1" # Локальный адрес для одного ПК
PORT = 9999

server = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server.bind((SERVER_IP, PORT))

print(f"[СЕРВЕР ЗАПУЩЕН] Ожидание игроков на localhost:{PORT}...")

clients = []
player_positions = {}

while True:
    try:
        data, addr = server.recvfrom(2048)
        if addr not in clients:
            if len(clients) < 2:
                clients.append(addr)
                print(f"[ПОДКЛЮЧЕНИЕ] Игрок {len(clients)} подключился: {addr}")
            else:
                continue

        packet = json.loads(data.decode('utf-8'))
        player_id = packet.get("id")
        pos = packet.get("pos")
        rot = packet.get("rot")

        player_positions[player_id] = {"pos": pos, "rot": rot}

        # Рассылаем координаты всем подключенным клиентам
        response = json.dumps(player_positions).encode('utf-8')
        for client in clients:
            server.sendto(response, client)

    except Exception as e:
        print(f"[ОШИБКА] {e}")
        break