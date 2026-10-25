"""Thread-safe TCP client. Socket reader runs independently from Ursina's loop."""
import queue
import socket
import threading

from shared.protocol import encode, extract_frames

class NetworkClient:
    def __init__(self, host, port, timeout=5):
        self.host, self.port, self.timeout = host, int(port), timeout
        self.sock = None
        self.events = queue.Queue()
        self.send_lock = threading.Lock()
        self.closed = threading.Event()

    def connect(self, username, password):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(None)
        self.send({"type": "login", "username": username, "password": password})
        buffer = b""
        self.sock.settimeout(self.timeout)
        while True:
            data = self.sock.recv(4096)
            if not data: raise ConnectionError("Server closed connection")
            frames, buffer = extract_frames(buffer + data)
            for index, frame in enumerate(frames):
                if frame.get("type") == "login":
                    if not frame.get("ok"):
                        self.close()
                        raise PermissionError(frame.get("error", "Login failed"))
                    self.player_id = frame["id"]
                    self.username = frame["username"]
                    self.events.put(frame)
                    # The server may have already sent snapshot/events in the same TCP read.
                    for pending in frames[index + 1:]:
                        self.events.put(pending)
                    self.sock.settimeout(None)
                    threading.Thread(target=self._reader, daemon=True).start()
                    return frame
                self.events.put(frame)

    def _reader(self):
        buffer = b""
        try:
            while not self.closed.is_set():
                data = self.sock.recv(8192)
                if not data: break
                frames, buffer = extract_frames(buffer + data)
                for frame in frames: self.events.put(frame)
        except (OSError, ValueError):
            pass
        finally:
            self.closed.set()
            self.events.put({"type": "disconnected"})

    def send(self, message):
        if self.sock and not self.closed.is_set():
            with self.send_lock: self.sock.sendall(encode(message))

    def close(self):
        self.closed.set()
        if self.sock:
            try: self.sock.shutdown(socket.SHUT_RDWR)
            except OSError: pass
            try: self.sock.close()
            except OSError: pass
