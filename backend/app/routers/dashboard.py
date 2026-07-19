from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api_models import DashboardResponse
from app.database import get_db
from app.security import AuthContext, get_auth_context
from app.services.dashboard import dashboard_snapshot

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict:
    return dashboard_snapshot(
        db,
        auth.account,
        role=auth.role,
        include_simulations=auth.session.share_token_id is None,
    )
