"""Run Qt headless so GUI tests never open windows."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
