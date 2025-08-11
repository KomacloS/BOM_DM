.PHONY: install dev test gui

install:
	pip install -e .[full]

dev:
	uvicorn app.api:app --reload

test:
	pytest

gui:
	python -m app.gui
