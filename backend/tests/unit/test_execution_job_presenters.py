import time

from app.domain.execution_job import ExecutionJob, ExecutionJobState
from app.schemas.presenters import execution_job_dto


def test_execution_job_dto_shape():
    job = ExecutionJob(
        id="j1", owner_key="127.0.0.1", collection_name="Demo",
        created_at=time.time(), expires_at=time.time() + 60,
        state=ExecutionJobState.READY, analysis_id="a1",
        stage_history=["validating", "running_baseline"], warnings=["schema unknown"],
    )
    dto = execution_job_dto(job)
    assert dto == {
        "job_id": "j1",
        "state": "ready",
        "stage_history": ["validating", "running_baseline"],
        "warnings": ["schema unknown"],
        "error_code": None,
        "error_detail": None,
        "analysis_id": "a1",
    }


def test_execution_job_dto_never_includes_owner_key_or_collection_data():
    job = ExecutionJob(id="j1", owner_key="127.0.0.1", collection_name="Demo",
                        created_at=time.time(), expires_at=time.time() + 60)
    dto = execution_job_dto(job)
    assert "owner_key" not in dto
    assert "127.0.0.1" not in str(dto)
