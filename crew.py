import subprocess
import sys
import time

AGENTS = ["intake", "sourcing", "specialist", "trust", "budget", "scout"]


def main():
    procs = []
    for name in AGENTS:
        procs.append(subprocess.Popen([sys.executable, "band_agent.py", name]))
        time.sleep(3)
    try:
        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        for p in procs:
            p.terminate()


if __name__ == "__main__":
    main()
