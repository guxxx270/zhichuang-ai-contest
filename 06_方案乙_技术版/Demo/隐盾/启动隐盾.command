#!/bin/bash
cd "$(dirname "$0")" || exit 1
if [ ! -d .venv ]; then python3 -m venv .venv && ./.venv/bin/pip install -q -r requirements.txt; fi
./.venv/bin/streamlit run app.py
