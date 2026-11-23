"""Entry point.

    python main.py          # terminal chat
    python main.py web      # browser UI
    python main.py voice    # talk to it
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Your local AI agent")
    parser.add_argument("mode", nargs="?", default="cli", choices=["cli", "web", "voice"])
    mode = parser.parse_args().mode

    if mode == "web":
        sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run",
                                  str(ROOT / "interfaces" / "web.py")]))
    if mode == "voice":
        from interfaces.voice import main as run
    else:
        from interfaces.cli import main as run
    run()


if __name__ == "__main__":
    main()
