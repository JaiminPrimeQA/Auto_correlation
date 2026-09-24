"""JMX and manifest download endpoints."""

from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends
from fastapi.responses import Response

from ...core.errors import conflict
from ...repositories.analysis_store import Analysis
from ..deps import require_analysis

router = APIRouter(prefix="/analyses/{analysis_id}/download", tags=["downloads"])


def _stem(analysis: Analysis) -> str:
    """Filename reflects the true state - never 'correlated'/'validated' unless earned."""
    collection = analysis.sequence_run.collection_name or "baseline11"
    slug = re.sub(r"[^a-z0-9]+", "-", collection.lower()).strip("-")[:60] or "baseline11"
    state = "validated" if analysis.jmx_status == "validated" else "generated"
    return f"{slug}-{state}"


@router.get("/jmx")
async def download_jmx(analysis: Analysis = Depends(require_analysis)) -> Response:
    if not analysis.generated_jmx:
        raise conflict("No JMX has been generated yet. Call generate first.")
    return Response(
        content=analysis.generated_jmx,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{_stem(analysis)}.jmx"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/manifest")
async def download_manifest(analysis: Analysis = Depends(require_analysis)) -> Response:
    if not analysis.generated_manifest:
        raise conflict("No manifest has been generated yet. Call generate first.")
    return Response(
        content=json.dumps(analysis.generated_manifest, indent=2, ensure_ascii=False),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{_stem(analysis)}-manifest.json"',
            "Cache-Control": "no-store",
        },
    )
