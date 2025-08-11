#!/usr/bin/env bash
set -e
python -m pip install -e .[full]
python -m gui.control_center
