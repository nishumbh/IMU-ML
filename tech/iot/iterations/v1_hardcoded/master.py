"""Bench entry: copy as /main.py with settings.py and app/; bypasses OTA loader."""
from settings import MASTER_CONFIG
from app.master import run
run(MASTER_CONFIG)
