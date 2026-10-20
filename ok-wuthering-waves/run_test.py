import faulthandler
import sys
import threading
import time
import unittest


def main():
    faulthandler.enable()
    faulthandler.dump_traceback_later(120, repeat=True)
    try:
        program = unittest.main(module=None, verbosity=2, exit=False)
    finally:
        # 等待真实 OCR 模型初始化完成后退出 Python。
        deadline = time.monotonic() + 120
        for thread in threading.enumerate():
            if thread.name == 'DefaultOCRInit':
                thread.join(timeout=max(0, deadline - time.monotonic()))
                if thread.is_alive():
                    raise TimeoutError('OCR initialization did not finish within 120 seconds')
        faulthandler.cancel_dump_traceback_later()
    return 0 if program.result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
