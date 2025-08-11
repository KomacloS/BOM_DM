from sqlmodel import Session, SQLModel
import os, sys

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import engine
from app.auth import create_default_users


def main() -> None:
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        create_default_users(session)
    print("admin user ensured")


if __name__ == "__main__":
    main()

