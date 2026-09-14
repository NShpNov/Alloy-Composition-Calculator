"""
	This is the main file. It verifies alloys database integrity and launches GUI.
"""
import ctypes
import platform
from tkinter import Tk
if platform.system() == "Windows":
	alloyappid = 'NShpNov.AlloyCompositionCalculator.1.0.'
	ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(alloyappid)
from gui import *
import parcer
import sys
def ask_instance_path():
    root = Tk()
    root.withdraw()

    path = filedialog.askdirectory(
        title="Select your Minecraft instance (where is your mods folder is located):"
    )

    root.destroy()

    if not path:
        raise SystemExit("Minecraft instance was not selected.")

    return path
if __name__ == "__main__":
    try:
        alloys, colors = load_data()
    except Exception:
        instance = ask_instance_path()
        sys.argv = ["parcer.py", instance]
        exit_code = parcer.main()
        alloys, colors = load_data()
    app = AlloyApp(alloys, colors)
    app.mainloop()
