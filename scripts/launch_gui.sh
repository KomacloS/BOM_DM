#!/usr/bin/env bash
set -e
python -m pip install -e .[full]
bom-gui
