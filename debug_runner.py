# debug_runner.py — 啟動你的 app.py，並把所有可見/不可見錯誤與 Qt 訊息寫到 crashlog_*.txt
from __future__ import annotations
import os, sys, io, time, runpy, traceback, threading, platform, signal, faulthandler
from datetime import datetime

ROOT = os.path.abspath(os.path.dirname(__file__))
LOG_NAME = f"crashlog_{datetime.now().strftime('%Y%m%d-%H%M%S')}.txt"
LOG_PATH = os.path.join(ROOT, LOG_NAME)

# ---------- 基礎 log ----------
_log_fp = open(LOG_PATH, "w", encoding="utf-8", buffering=1)  # line-buffered
def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
def log(msg: str):
    line = f"[{_ts()}] {msg}\n"
    # 直接寫檔
    try:
        _log_fp.write(line); _log_fp.flush()
    except Exception:
        pass
    # 顯示在原始主控台（不用 print，避免被 _Tee 轉寫到檔案第二次）
    try:
        sys.__stdout__.write(line); sys.__stdout__.flush()
    except Exception:
        pass

# ---------- 將 stdout/stderr 也 tee 到檔案 ----------
class _Tee(io.TextIOBase):
    def __init__(self, a, b): self.a, self.b = a, b
    def write(self, s): 
        try: self.a.write(s)
        except Exception: pass
        try: self.b.write(s)
        except Exception: pass
        try: self.a.flush()
        except Exception: pass
        try: self.b.flush()
        except Exception: pass
        return len(s)
    def flush(self):
        for t in (self.a, self.b):
            try: t.flush()
            except Exception: pass

sys.stdout = _Tee(sys.stdout, _log_fp)
sys.stderr = _Tee(sys.stderr, _log_fp)

# ---------- 讓 Windows 能找到專案內的 fluidsynth\bin（安全不影響現有程式） ----------
def _add_fs_dll_path():
    if not sys.platform.startswith("win"): return
    for p in (os.path.join(ROOT, "fluidsynth", "bin"), os.path.join(ROOT, "bin")):
        if os.path.isdir(p):
            try:
                os.add_dll_directory(p)
                log(f"Added DLL directory: {p}")
            except Exception as e:
                log(f"add_dll_directory failed: {e}")

# ---------- 安裝各式鉤子 ----------
def _install_hooks():
    log("=== install hooks ===")
    log(f"Python: {sys.version}")
    log(f"Executable: {sys.executable}")
    log(f"Platform: {platform.platform()}")
    log(f"Working dir: {os.getcwd()}")
    log(f"File dir: {ROOT}")
    # 1) faulthandler: 捕捉 SIGSEGV/ABRT/FPE/ILL 等致命錯（含全執行緒堆疊）
    try:
        faulthandler.enable(file=_log_fp, all_threads=True)
        for sig in (signal.SIGSEGV, signal.SIGABRT, signal.SIGFPE, signal.SIGILL, getattr(signal, "SIGBUS", None)):
            if sig is None: continue
            try:
                faulthandler.register(sig, file=_log_fp, all_threads=True, chain=True)
            except Exception:
                pass
        log("faulthandler enabled")
    except Exception as e:
        log(f"faulthandler enable failed: {e}")

    # 2) sys.excepthook：未捕捉例外
    def _excepthook(exc_type, exc, tb):
        log("UNCAUGHT EXCEPTION:\n" + "".join(traceback.format_exception(exc)))
    sys.excepthook = _excepthook

    # 3) threading.excepthook：子執行緒例外（py3.8+）
    def _thread_hook(args):
        log("UNCAUGHT THREAD EXC:\n" + "".join(traceback.format_exception(args.exc_value)))
    try:
        threading.excepthook = _thread_hook  # type: ignore
    except Exception:
        pass

    # 4) sys.unraisablehook：__del__ / 背景 callback 丟錯
    def _unraisable_hook(unraisable):
        log("UNRAISABLE:\n" + "".join(traceback.format_exception(unraisable.exc_value)))
    try:
        sys.unraisablehook = _unraisable_hook  # type: ignore
    except Exception:
        pass

    # 5) Qt 訊息管線 & QMessageBox 訊息
    try:
        from PySide6 import QtCore, QtWidgets
        def qt_msg_handler(mode, ctx, msg):
            lv = {
                QtCore.QtMsgType.QtDebugMsg:   "DEBUG",
                QtCore.QtMsgType.QtInfoMsg:    "INFO",
                QtCore.QtMsgType.QtWarningMsg: "WARN",
                QtCore.QtMsgType.QtCriticalMsg: "CRIT",
                QtCore.QtMsgType.QtFatalMsg:   "FATAL",
            }.get(mode, str(int(mode)))
            src = f"{ctx.file}:{ctx.line}" if ctx and ctx.file else ""
            log(f"Qt[{lv}] {msg} {src}")

        QtCore.qInstallMessageHandler(qt_msg_handler)

        # 也把 QMessageBox 各種彈窗記錄下來（標題與文字）
        _orig = {
            "information": QtWidgets.QMessageBox.information,
            "warning":     QtWidgets.QMessageBox.warning,
            "critical":    QtWidgets.QMessageBox.critical,
            "question":    QtWidgets.QMessageBox.question,
        }
        def _wrap(name):
            def _fn(parent, title, text, *a, **kw):
                log(f"QMessageBox.{name}: [{title}] {text}")
                return _orig[name](parent, title, text, *a, **kw)
            return _fn
        QtWidgets.QMessageBox.information = _wrap("information")  # type: ignore
        QtWidgets.QMessageBox.warning     = _wrap("warning")      # type: ignore
        QtWidgets.QMessageBox.critical    = _wrap("critical")     # type: ignore
        QtWidgets.QMessageBox.question    = _wrap("question")     # type: ignore
        log("Qt hooks installed")
    except Exception as e:
        log(f"Qt hooks skipped: {e}")

# ---------- 執行 app.py ----------
def main():
    log(f"Log file: {LOG_PATH}")
    _add_fs_dll_path()
    _install_hooks()

    # 把 argv 傳給 app.py（如需）
    saved_argv = sys.argv[:]
    sys.argv = ["app.py"] + saved_argv[1:]

    target = os.path.join(ROOT, "app.py")
    if not os.path.isfile(target):
        log("ERROR: app.py not found next to debug_runner.py")
        return 1

    log("=== run app.py begin ===")
    exit_code = 0
    try:
        # 在同一個行程裡執行，讓所有鉤子生效
        runpy.run_path(target, run_name="__main__")
    except SystemExit as e:
        exit_code = int(e.code) if isinstance(e.code, int) else 0
        log(f"SystemExit: {e.code}")
    except Exception as e:
        log("TOPLEVEL EXCEPTION:\n" + "".join(traceback.format_exception(e)))
        exit_code = 1
    finally:
        log("=== run app.py end ===")
        # 盡力 flush
        try: _log_fp.flush()
        except Exception: pass
    return exit_code

if __name__ == "__main__":
    rc = main()
    # 稍等一點讓 Qt/執行緒把尾端訊息寫完
    try: time.sleep(0.2)
    except Exception: pass
    try: _log_fp.close()
    except Exception: pass
    sys.exit(rc)
