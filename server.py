"""Compatibility entry point: python server.py"""
import os
import runpy
import sys

if __name__ == "__main__":
    root = os.path.dirname(os.path.abspath(__file__))
    if root not in sys.path:
        sys.path.insert(0, root)
    runpy.run_path(os.path.join(root, "server", "main.py"), run_name="__main__")
