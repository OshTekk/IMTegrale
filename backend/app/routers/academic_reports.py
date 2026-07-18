from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.security import AuthContext, LoginRateLimiter, get_auth_context
from app.services.academic_report import AcademicReportUnavailable, build_academic_report

router = APIRouter(prefix="/api/v1/academic-reports", tags=["academic-reports"])
report_rate_limiter = LoginRateLimiter(limit=8, window_seconds=60, max_keys=5_000)


@router.get("/personal.pdf")
def download_personal_report(
    semester: Literal["all", "S5", "S6", "S7", "S8", "S9", "S10"] = Query(default="all"),
    include_assessments: bool = Query(default=True),
    include_identity: bool = Query(default=True),
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> Response:
    if auth.session.share_token_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Le relevé personnel n'est accessible qu'au titulaire du compte",
        )
    report_rate_limiter.check(auth.account.id)
    try:
        content, filename = build_academic_report(
            db,
            auth.account,
            semester=semester,
            include_assessments=include_assessments,
            include_identity=include_identity,
        )
    except AcademicReportUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(content)),
            "X-Robots-Tag": "noindex, noarchive",
        },
    )
