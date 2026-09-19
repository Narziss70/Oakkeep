#!/usr/bin/env python3
"""Start Oakkeep. Right-click this file → Open With → Python 3."""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def fail(err: str) -> None:
    sys.stderr.write(err + "\n")
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dlg = Gtk.MessageDialog(
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="Oakkeep could not start",
        )
        dlg.format_secondary_text(err[-3000:])
        dlg.run()
        dlg.destroy()
    except Exception:
        try:
            input("Press Enter to close")
        except Exception:
            pass
    sys.exit(1)


try:
    import gi

    gi.require_version("Gtk", "3.0")
    from oakkeep_app.icons import set_program_identity

    set_program_identity()
except Exception:
    pass

try:
    from oakkeep_app.window import main
except Exception:
    fail(
        "Could not load Oakkeep.\n\n"
        "Keep oakkeep.py next to the folder oakkeep_app (do not move this file out of the unzipped folder).\n\n"
        + traceback.format_exc()
    )

try:
    main()
except Exception:
    fail(traceback.format_exc())
