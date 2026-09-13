"""
	This is the main file. It verifies alloys database integrity and launches GUI.
"""
import ctypes
import platform
if platform.system() == "Windows":
	alloyappid = 'NShpNov.AlloyCompositionCalculator.1.0.'
	ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(alloyappid)
from gui import *
import parcer
import sys

if __name__ == "__main__":
    try:
        alloys, colors = load_data()
    except Exception:
        instance = input(
            " File 'alloys.json' is either empty, corrupted or does not exist.\n"\
			" Please, enter a full path to your minecraft instance.\n" \
			" E.g. C:\\Users\\MyUsername\\AppData\\Roaming\\.minecraft\\versions\\1.20.1 TFG. (where your mods folder is loacated)\n"
        )
        sys.argv = ["parcer.py", instance]
        exit_code = parcer.main()
        alloys, colors = load_data()
    app = AlloyApp(alloys, colors)
    app.mainloop()
