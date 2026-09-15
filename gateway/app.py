from __future__ import annotations

import hmac
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from .config import GatewayConfig
from .service import GatewayService

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app(cfg: Optional[GatewayConfig] = None) -> FastAPI:
    cfg = cfg or GatewayConfig.load()
    service = GatewayService(cfg)

    app = FastAPI(
        title="Herman Gateway",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.cfg = cfg
    app.state.service = service

    def require_token(x_gateway_token: str = Header(default="")) -> None:
        if not cfg.token:
            return
        if not hmac.compare_digest(x_gateway_token, cfg.token):
            raise HTTPException(status_code=401, detail="Invalid gateway token")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        return FileResponse(
            STATIC_DIR / "manifest.webmanifest",
            media_type="application/manifest+json",
        )

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        return FileResponse(
            STATIC_DIR / "sw.js",
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/healthz")
    def healthz():
        return {
            "ok": True,
            "service": "herman-gateway",
            "version": "1.0.0",
            "read_only": True,
        }

    @app.get("/api/overview", dependencies=[Depends(require_token)])
    def overview():
        return service.overview()

    @app.get("/api/runtime", dependencies=[Depends(require_token)])
    def runtime():
        return service.runtime()

    @app.get("/api/fills", dependencies=[Depends(require_token)])
    def fills(
        days: int = Query(default=30, ge=1, le=90),
        limit: int = Query(default=200, ge=1, le=500),
    ):
        try:
            return {
                "days": days,
                "fills": service.fills(days, limit),
            }
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content={"detail": f"Exchange fill history unavailable: {exc}"},
            )

    @app.get("/api/analytics", dependencies=[Depends(require_token)])
    def analytics(days: int = Query(default=30, ge=1, le=90)):
        try:
            return {
                "days": days,
                "analytics": service.analytics(days),
            }
        except Exception as exc:
            return JSONResponse(
                status_code=502,
                content={"detail": f"Exchange analytics unavailable: {exc}"},
            )

    return app
