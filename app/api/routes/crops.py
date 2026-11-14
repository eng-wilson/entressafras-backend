from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.schemas.crop import CropDetail, CropPage
from app.services.ecocrop import CropRepository, get_repository

router = APIRouter(prefix="/crops", tags=["crops"])

Repo = Annotated[CropRepository, Depends(get_repository)]


@router.get("", response_model=CropPage)
def list_crops(
    repo: Repo,
    q: Annotated[
        str | None,
        Query(description="Nome científico ou popular (sem diferenciar maiúsculas/acentos)"),
    ] = None,
    category: Annotated[str | None, Query(description="Ex.: 'cereals & pseudocereals'")] = None,
    life_span: Annotated[str | None, Query(description="Ex.: 'annual', 'perennial'")] = None,
    life_form: Annotated[str | None, Query(description="Ex.: 'herb', 'tree', 'grass'")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CropPage:
    total, items = repo.search(
        q,
        category=category,
        life_span=life_span,
        life_form=life_form,
        limit=limit,
        offset=offset,
    )
    return CropPage(total=total, limit=limit, offset=offset, items=items)


@router.get("/{eco_port_code}", response_model=CropDetail)
def get_crop(eco_port_code: int, repo: Repo) -> CropDetail:
    crop = repo.get(eco_port_code)
    if crop is None:
        raise HTTPException(status_code=404, detail="Crop not found")
    return crop
