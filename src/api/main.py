"""FastAPI application entrypoint."""

from fastapi import FastAPI

from .routes import alerts_router, companies_router, filings_router, health_router, risk_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="SEC Disclosure-Risk Monitor API",
        version="0.1.0",
        description="API for querying SEC filing risk signals and related data.",
    )

    app.include_router(health_router)
    app.include_router(companies_router)
    app.include_router(filings_router)
    app.include_router(alerts_router)
    app.include_router(risk_router)

    return app


app = create_app()
