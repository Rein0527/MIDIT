import sys, os
from PySide6 import QtWidgets, QtGui
from modules.ui.main_window import MainWindow

def resource_path(filename: str) -> str:
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(__file__)
    return os.path.join(base, filename)

if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    icon_path = resource_path("icon.ico") 
    app.setWindowIcon(QtGui.QIcon(icon_path))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
