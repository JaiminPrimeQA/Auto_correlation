"""How a newly created job starts executing.

`LocalJobLauncher` (development): hand `run_job` to the in-process bounded
dispatcher, exactly as before. `QueueJobLauncher` (AWS): write the run
material to S3 (KMS-encrypted) and enqueue the job id for the separate
worker; the API process never executes a collection (spec §4, §5.2).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from ..core.config import Settings
from ..core.logging import get_logger
from ..domain.execution_job import NewmanRunner
from ..repositories.analysis_store import SessionStore
from ..repositories.execution_job_store import ExecutionJobStore
from ..repositories.s3_object_store import S3ObjectStore, material_key
from . import execution_job_service
from .job_dispatcher import JobDispatcher

log = get_logger("job_launcher")


@dataclass(frozen=True)
class RunMaterial:
    """Everything `run_job` needs besides the job record. `variable_values`
    and `supplied_values` may hold secrets: this object is only ever kept in
    memory or in the encrypted material object."""

    collection_data: dict
    environment_data: dict | None
    variable_values: dict[str, str]
    supplied_values: dict[str, str]
    folder_id: str | None

    def to_document(self) -> dict:
        return {"version": 1, **asdict(self)}

    @classmethod
    def from_document(cls, doc: dict) -> RunMaterial:
        return cls(
            collection_data=doc["collection_data"],
            environment_data=doc.get("environment_data"),
            variable_values=dict(doc.get("variable_values") or {}),
            supplied_values=dict(doc.get("supplied_values") or {}),
            folder_id=doc.get("folder_id"),
        )


class JobQueue(Protocol):
    def enqueue(self, job_id: str) -> None: ...


class JobLauncher(Protocol):
    def launch(self, job_id: str, material: RunMaterial) -> None: ...


class LocalJobLauncher:
    def __init__(
        self,
        *,
        runner: NewmanRunner,
        dispatcher: JobDispatcher,
        job_store: ExecutionJobStore,
        analysis_store: SessionStore,
        settings: Settings,
    ) -> None:
        self._runner = runner
        self._dispatcher = dispatcher
        self._job_store = job_store
        self._analysis_store = analysis_store
        self._settings = settings

    def launch(self, job_id: str, material: RunMaterial) -> None:
        self._dispatcher.submit(
            execution_job_service.run_job,
            job_id,
            collection_data=material.collection_data,
            environment_data=material.environment_data,
            variable_values=material.variable_values,
            supplied_values=material.supplied_values,
            folder_id=material.folder_id,
            runner=self._runner,
            store=self._job_store,
            analysis_store=self._analysis_store,
            settings=self._settings,
        )


class QueueJobLauncher:
    def __init__(self, *, objects: S3ObjectStore, queue: JobQueue, job_store: ExecutionJobStore) -> None:
        self._objects = objects
        self._queue = queue
        self._job_store = job_store

    def launch(self, job_id: str, material: RunMaterial) -> None:
        try:
            self._objects.put_json(material_key(job_id), material.to_document())
            self._queue.enqueue(job_id)
        except Exception as exc:  # any AWS failure: the job can never run
            log.warning(
                "job could not be queued", extra={"stage": "launch", "job_id": job_id, "exc_type": type(exc).__name__},
            )
            execution_job_service.fail_job(
                job_id, self._job_store, "runner_unavailable", "The execution queue is unavailable. Try again shortly.",
            )
            try:
                self._objects.delete(material_key(job_id))
            except Exception:  # noqa: BLE001 - best effort; the bucket lifecycle removes it anyway
                pass
