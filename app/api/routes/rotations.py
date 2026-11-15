from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends

from app.schemas.rotation import RotationRecommendationsResponse, RotationRequest
from app.services.ecocrop import CropRepository, get_repository
from app.services.rotations import mock_recommendations

router = APIRouter(prefix="/rotations", tags=["rotations"])


def get_today() -> date:
    return date.today()


Repo = Annotated[CropRepository, Depends(get_repository)]
Today = Annotated[date, Depends(get_today)]


@router.post("/recommendations", response_model=RotationRecommendationsResponse)
def recommend_rotations(
    request: RotationRequest, repo: Repo, today: Today
) -> RotationRecommendationsResponse:
    """Up to 4 two-crop sequences for the next season, each crop with its own water risk.

    Currently a MOCKED ranking (see `warnings`); the contract is final.
    """
    return mock_recommendations(repo, request, today)
