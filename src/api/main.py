"""FastAPI application entrypoint."""

from fastapi import FastAPI

from .routes import alerts_router, companies_router, filings_router, health_router, risk_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="SEC Review Priority Monitor API",
        version="0.1.0",
        description=(
            "API for querying issuer review-priority scores, filing anomalies, "
            "and evidence-backed triage data."
        ),
    )

    app.include_router(health_router)
    app.include_router(companies_router)
    app.include_router(filings_router)
    app.include_router(alerts_router)
    app.include_router(risk_router)

    return app


app = create_app()
