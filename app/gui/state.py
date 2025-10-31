"""Application state helpers and threading utilities."""

from __future__ import annotations

from typing import Callable, Iterable, Optional

from PyQt6.QtCore import QObject, pyqtSignal, QThread
from sqlmodel import Session

from ..database import new_session, ensure_schema
from .. import services


ensure_schema()


def get_session() -> Session:
    """Return a new database session bound to the configured engine."""

    return new_session()


class _Worker(QThread):
    """Simple thread worker executing ``fn`` and emitting ``finished``
    with either the result or any raised exception."""

    finished = pyqtSignal(object)

    def __init__(self, fn: Callable, *args, **kwargs) -> None:
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self) -> None:  # pragma: no cover - Qt thread
        try:
            result = self._fn(*self._args, **self._kwargs)
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
    bomImportProgress = pyqtSignal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self._workers: list[_Worker] = []

    def _run(self, fn: Callable, signal: pyqtSignal, *args) -> None:
        worker = _Worker(fn, *args)
        worker.finished.connect(signal.emit)
        worker.finished.connect(lambda _res, w=worker: self._workers.remove(w))
        self._workers.append(worker)
        worker.start()

    # ------------------------------------------------------------------
    def refresh_customers(self, q: Optional[str] = None) -> None:
        self._run(lambda: services.list_customers(q, get_session()), self.customersChanged)

    def refresh_projects(self, customer_id: int) -> None:
        self._run(
            lambda: services.list_projects(customer_id, get_session()), self.projectsChanged
        )

    def refresh_assemblies(self, project_id: int) -> None:
        self._run(
            lambda: services.list_assemblies(project_id, get_session()), self.assembliesChanged
        )

    def refresh_bom_items(self, assembly_id: int) -> None:
        self._run(
            lambda: services.list_bom_items(assembly_id, get_session()),
            self.bomItemsChanged,
        )

    def refresh_tasks(self, project_id: int, status: Optional[str] = None) -> None:
        self._run(
            lambda: services.list_tasks(project_id, status, get_session()), self.tasksChanged
        )

    def import_bom(self, assembly_id: int, data: bytes) -> None:
        self._run(
            lambda: services.import_bom(
                assembly_id,
                data,
                get_session(),
                progress_cb=lambda current, total: self.bomImportProgress.emit(
                    current, total
                ),
            ),
            self.bomImported,
        )

