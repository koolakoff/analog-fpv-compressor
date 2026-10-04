"""Absolute-import entry point for the frozen Windows desktop application."""

import sys
import traceback

if __name__ == "__main__":
    try:
        if len(sys.argv) == 3 and sys.argv[1] == "--verify-bundle":
            from analog_fpv_compressor.gui.smoke import verify
            raise SystemExit(verify(sys.argv[2]))
        from analog_fpv_compressor.gui import main
        raise SystemExit(main())
    except Exception:
        from analog_fpv_compressor.models import Event
        from analog_fpv_compressor.runner import EventLogger
        with EventLogger(echo=False) as log:
            log(Event("application.failed", {"traceback": traceback.format_exc()}))
        raise SystemExit(1)
