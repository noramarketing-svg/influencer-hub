# Render deployment entry point
# All logic is in server.py
import sys, os

# Ensure project root is in path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, "engines"))
sys.path.insert(0, os.path.join(BASE_DIR, "configs"))

from server import app
