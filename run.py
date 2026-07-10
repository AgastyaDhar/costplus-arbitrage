"""Entry point so `python run.py` works from the repo root (see README.md).
The actual pipeline lives in costplus_suite/run.py."""
import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).resolve().parent / "costplus_suite" / "run.py"), run_name="__main__")
