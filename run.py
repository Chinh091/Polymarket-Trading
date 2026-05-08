"""
run.py
Starts the Polymarket paper trading bot.
Run: python run.py
Dashboard: streamlit run dashboard/app.py  (in a separate terminal)
"""
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Ensure UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Load .env file
load_dotenv()

# Make sure we can import from subdirs
sys.path.insert(0, str(Path(__file__).parent))

from core.logger import setup_logger
from core.orchestrator import MasterOrchestrator

logger = setup_logger("Main")


def main():
    print("""
╔══════════════════════════════════════════════════════╗
║      POLYMARKET PAPER TRADING BOT                    ║
║      Simulation mode - no real money                 ║
╚══════════════════════════════════════════════════════╝

Starting all agents...
Open dashboard in another terminal:
  streamlit run dashboard/app.py
""")
    bot = MasterOrchestrator()
    bot.start()


if __name__ == "__main__":
    main()
