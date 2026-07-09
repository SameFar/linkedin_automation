"""Entry point for `python -m linkedos.scheduler`.

A shim, so that `daemon` is always imported under its real name. Running
`python -m linkedos.scheduler.daemon` directly would load that module a second time as
`__main__`, giving it a duplicate logger and a duplicate copy of its globals.
"""

from __future__ import annotations

from linkedos.scheduler.daemon import main

raise SystemExit(main())
