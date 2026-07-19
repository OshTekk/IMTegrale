from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api_models import (
    SimulationComparisonResponse,
    SimulationListResponse,
    SimulationScenarioResponse,
)
from app.database import get_db
from app.schemas_simulations import (
    SimulationConflictResolution,
    SimulationCreate,
    SimulationDuplicate,
    SimulationUpdate,
    SimulationVersion,
)
from app.security import AuthContext, require_primary_owner, require_primary_owner_action
from app.services import simulations

router = APIRouter(prefix="/api/v1/simulations", tags=["simulations"])

def _run(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except simulations.SimulationNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except simulations.SimulationLimitReached as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except simulations.SimulationVersionConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "simulation_version_conflict",
                "message": str(exc),
                "current_version": exc.current_version,
            },
        ) from exc
    except simulations.SimulationEntryNotFound as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except simulations.SimulationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("", response_model=SimulationListResponse)
def simulation_list(
    auth: AuthContext = Depends(require_primary_owner),
    db: Session = Depends(get_db),
) -> dict:
    return simulations.list_scenarios(db, auth.account)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=SimulationScenarioResponse,
)
def simulation_create(
    payload: SimulationCreate,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    return _run(
        lambda: simulations.create_scenario(
            db,
            auth.account,
            name=payload.name,
            import_current=payload.import_current,
            actor=auth.actor,
        )
    )


@router.get("/compare", response_model=SimulationComparisonResponse)
def simulation_compare(
    left_id: str = Query(min_length=36, max_length=36),
    right_id: str = Query(min_length=36, max_length=36),
    auth: AuthContext = Depends(require_primary_owner),
    db: Session = Depends(get_db),
) -> dict:
    return _run(
        lambda: simulations.compare_scenarios(
            db,
            auth.account,
            left_id,
            right_id,
        )
    )


@router.get("/{scenario_id}", response_model=SimulationScenarioResponse)
def simulation_get(
    scenario_id: str,
    auth: AuthContext = Depends(require_primary_owner),
    db: Session = Depends(get_db),
) -> dict:
    return _run(lambda: simulations.get_scenario(db, auth.account, scenario_id))


@router.put("/{scenario_id}", response_model=SimulationScenarioResponse)
def simulation_save(
    scenario_id: str,
    payload: SimulationUpdate,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    return _run(
        lambda: simulations.save_scenario(
            db,
            auth.account,
            scenario_id,
            version=payload.version,
            name=payload.name,
            entries=payload.entries,
        )
    )


@router.post(
    "/{scenario_id}/duplicate",
    status_code=status.HTTP_201_CREATED,
    response_model=SimulationScenarioResponse,
)
def simulation_duplicate(
    scenario_id: str,
    payload: SimulationDuplicate,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    return _run(
        lambda: simulations.duplicate_scenario(
            db,
            auth.account,
            scenario_id,
            version=payload.version,
            name=payload.name,
            actor=auth.actor,
        )
    )


@router.post("/{scenario_id}/reset", response_model=SimulationScenarioResponse)
def simulation_reset(
    scenario_id: str,
    payload: SimulationVersion,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    return _run(
        lambda: simulations.reset_scenario(
            db,
            auth.account,
            scenario_id,
            version=payload.version,
            actor=auth.actor,
        )
    )


@router.post("/{scenario_id}/rebase", response_model=SimulationScenarioResponse)
def simulation_rebase(
    scenario_id: str,
    payload: SimulationVersion,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    return _run(
        lambda: simulations.rebase_scenario(
            db,
            auth.account,
            scenario_id,
            version=payload.version,
            actor=auth.actor,
        )
    )


@router.post(
    "/{scenario_id}/entries/{entry_id}/resolve",
    response_model=SimulationScenarioResponse,
)
def simulation_resolve_conflict(
    scenario_id: str,
    entry_id: str,
    payload: SimulationConflictResolution,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    return _run(
        lambda: simulations.resolve_conflict(
            db,
            auth.account,
            scenario_id,
            entry_id,
            version=payload.version,
            resolution=payload.resolution,
        )
    )


@router.delete("/{scenario_id}", status_code=status.HTTP_204_NO_CONTENT)
def simulation_delete(
    scenario_id: str,
    version: int = Query(ge=1),
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
) -> None:
    _run(
        lambda: simulations.delete_scenario(
            db,
            auth.account,
            scenario_id,
            version=version,
            actor=auth.actor,
        )
    )
