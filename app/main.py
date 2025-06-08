# root: app/main.py
from fastapi import FastAPI

app = FastAPI()

@app.get("/hello")
def read_root():
    """Starter endpoint – will be replaced in Milestone 1."""
    return {"message": "Hello BOM World"}
