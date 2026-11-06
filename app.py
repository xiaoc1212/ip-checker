"""Desktop entry point. The local dashboard is the only UI implementation."""
from pathlib import Path
import subprocess
import sys
from network import ROOT, python_exe

if __name__ == '__main__':
    if Path(sys.executable).name.lower() == 'pythonw.exe':
        subprocess.Popen([python_exe(), str(Path(__file__).resolve()), *sys.argv[1:]],
                         cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        from dashboard import main
        main()
