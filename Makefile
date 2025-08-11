.PHONY: install dev test

install:
	pip install -e .[full]

dev:
	uvicorn app.api:app --reload

test:
	pytest
