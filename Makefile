.PHONY: install dev test gui parts

install:
	pip install -e .[full]

dev:
	uvicorn app.api:app --reload

test:
	QT_QPA_PLATFORM=offscreen pytest -q

gui:
	python -m app.gui

parts:
	python -m app.gui.parts_terminal
