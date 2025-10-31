"""Application state helpers and threading utilities."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional

from PyQt6.QtCore import QObject, pyqtSignal, QThread
from sqlmodel import Session

from ..database import new_session, ensure_schema
from .. import services


ensure_schema()


@contextmanager
def get_session() -> Iterator[Session]:
    """Provide a database session that is reliably closed."""

    session = new_session()
    try:
        yield session
    finally:
        session.close()


class _Worker(QThread):
    """Simple thread worker executing ``fn`` and emitting ``finished``
    with either the result or any raised exception."""

    finished = pyqtSignal(object)

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:  # pragma: no cover - Qt thread
        try:
            result = self._fn()
        except Exception as e:
            result = e
        self.finished.emit(result)


class AppState(QObject):
    """State container emitting signals when data is refreshed."""

    customersChanged = pyqtSignal(object)
    projectsChanged = pyqtSignal(object)
    assembliesChanged = pyqtSignal(object)
    bomItemsChanged = pyqtSignal(object)
    tasksChanged = pyqtSignal(object)
    bomImported = pyqtSignal(object)
    # (done, total)
    bomImportProgress = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self._workers: list[_Worker] = []

    def _run(self, fn: Callable[[], Any], signal: pyqtSignal) -> None:
        worker = _Worker(fn)
        worker.finished.connect(signal.emit)
        worker.finished.connect(lambda _res, w=worker: self._workers.remove(w))
        self._workers.append(worker)
        worker.start()

    def _run_with_session(self, fn: Callable[[Session], Any], signal: pyqtSignal) -> None:
        def call() -> Any:
            with get_session() as session:
                return fn(session)

        self._run(call, signal)

    # ------------------------------------------------------------------
    def refresh_customers(self, q: Optional[str] = None) -> None:
        self._run_with_session(
            lambda session: services.list_customers(q, session), self.customersChanged
        )

    def refresh_projects(self, customer_id: int) -> None:
        self._run_with_session(
            lambda session: services.list_projects(customer_id, session), self.projectsChanged
        )

    def refresh_assemblies(self, project_id: int) -> None:
        self._run_with_session(
            lambda session: services.list_assemblies(project_id, session), self.assembliesChanged
        )

    def refresh_bom_items(self, assembly_id: int) -> None:
        self._run_with_session(
            lambda session: services.list_bom_items(assembly_id, session),
            self.bomItemsChanged,
        )

    def refresh_tasks(self, project_id: int, status: Optional[str] = None) -> None:
        self._run_with_session(
            lambda session: services.list_tasks(project_id, status, session), self.tasksChanged
        )

    def import_bom(self, assembly_id: int, data: bytes) -> None:
        def _call(session: Session):
            def _progress(done: int, total: int) -> None:
                # emit tuple to keep signal signature simple
                self.bomImportProgress.emit((done, total))

            return services.import_bom(assembly_id, data, session, progress=_progress)

        self._run_with_session(_call, self.bomImported)

