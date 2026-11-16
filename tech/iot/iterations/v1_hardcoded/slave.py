"""Bench entry: copy as /main.py with settings.py and app/; bypasses OTA loader."""
from settings import SLAVE_CONFIG
from app.slave import run
run(SLAVE_CONFIG)
