"""FastAPI application entrypoint."""

from fastapi import FastAPI

from .routes import alerts_router, companies_router, filings_router, health_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="SEC Filing Anomaly Detector API",
        version="0.1.0",
        description="API for querying SEC filing anomalies and related data.",
    )

    app.include_router(health_router)
    app.include_router(companies_router)
    app.include_router(filings_router)
    app.include_router(alerts_router)

    return app


app = create_app()
