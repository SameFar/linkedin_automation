"""Streamlit multipage entries.

Each future page is a module in this directory named `NN_Title.py`; Streamlit picks
them up automatically and orders them by the numeric prefix. Pages read and write
exclusively through `linkedos.services`.
"""

from __future__ import annotations
