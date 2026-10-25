"""PyInstaller-friendly entry point: python client.py"""
from client.main import app, input, show_login, update

if __name__ == "__main__":
    # Ursina looks up input/update in __main__; expose them before app.run().
    show_login()
    app.run()
