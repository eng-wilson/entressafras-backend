from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    """`earth_engine`: ok, erro (ver log do start) ou nao_configurado."""
    return {
        "status": "ok",
        "earth_engine": getattr(request.app.state, "ee_status", "nao_configurado"),
    }
