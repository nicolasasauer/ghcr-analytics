"""GHCR-Pulse – FastAPI backend + server-rendered dark-mode dashboard."""
from __future__ import annotations

import json
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncGenerator, Optional
from urllib.parse import quote

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import delete, func, select

from .database import AsyncSessionLocal, PullStat, Repo, init_db

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
)
logger = logging.getLogger("ghcr_pulse")

# ─── Configuration (from environment) ────────────────────────────────────────
GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
UPDATE_INTERVAL_HOURS: float = float(os.getenv("UPDATE_INTERVAL_HOURS", "6"))
AUTH_USER: str = os.getenv("AUTH_USER", "")
AUTH_PASSWORD: str = os.getenv("AUTH_PASSWORD", "")

# ─── APScheduler ─────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler(timezone="UTC")


# ─── App lifespan ─────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    await init_db()
    logger.info("Database initialised.")

    # Schedule periodic updates
    scheduler.add_job(
        update_all_repos,
        "interval",
        hours=UPDATE_INTERVAL_HOURS,
        id="periodic_update",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started (interval: %.1f h).", UPDATE_INTERVAL_HOURS)

    yield

    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped.")


app = FastAPI(title="GHCR-Pulse", lifespan=lifespan)

# ─── Templates ────────────────────────────────────────────────────────────────
_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)

# ─── HTTP Basic Auth ──────────────────────────────────────────────────────────
_http_basic = HTTPBasic(auto_error=False)


async def require_auth(
    credentials: Optional[HTTPBasicCredentials] = Depends(_http_basic),
) -> None:
    """Require HTTP Basic Auth only when AUTH_USER/AUTH_PASSWORD are configured."""
    if not AUTH_USER or not AUTH_PASSWORD:
        return  # Auth disabled

    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Basic"},
        )

    valid_user = secrets.compare_digest(
        credentials.username.encode("utf-8"), AUTH_USER.encode("utf-8")
    )
    valid_pass = secrets.compare_digest(
        credentials.password.encode("utf-8"), AUTH_PASSWORD.encode("utf-8")
    )
    if not (valid_user and valid_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


# ─── GitHub / GHCR API helpers ───────────────────────────────────────────────
def _github_headers() -> dict[str, str]:
    h: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


async def fetch_ghcr_pulls(owner: str, package_name: str) -> int:
    """Return the total GHCR pull count for *owner/package_name*.

    Tries the ``/users/`` namespace first, then ``/orgs/``.

    Raises
    ------
    ValueError
        When the package cannot be found (HTTP 404) in either namespace.
    RuntimeError
        On network errors or unexpected HTTP status codes.
    """
    encoded_pkg = quote(package_name, safe="")
    headers = _github_headers()

    async with httpx.AsyncClient(timeout=20.0) as client:
        for ns in ("users", "orgs"):
            url = (
                f"https://api.github.com/{ns}/{owner}"
                f"/packages/container/{encoded_pkg}"
            )
            try:
                resp = await client.get(url, headers=headers)
            except httpx.RequestError as exc:
                raise RuntimeError(f"Network error contacting GitHub API: {exc}") from exc

            if resp.status_code == 200:
                return int(resp.json().get("download_count", 0))
            if resp.status_code == 401:
                raise RuntimeError(
                    "GitHub API returned 401 – check your GITHUB_TOKEN and ensure "
                    "it has the 'read:packages' scope."
                )
            if resp.status_code == 404:
                continue  # Try the other namespace

            # Any other unexpected status
            raise RuntimeError(
                f"GitHub API error {resp.status_code} for {owner}/{package_name}"
            )

    raise ValueError(
        f"Package '{owner}/{package_name}' not found on GHCR "
        "(checked both /users/ and /orgs/ namespaces)."
    )


# ─── Background job ──────────────────────────────────────────────────────────
async def _record_pull_stat(repo_id: int, owner: str, name: str) -> None:
    """Fetch current pull count for one repo and persist it."""
    try:
        count = await fetch_ghcr_pulls(owner, name)
    except (ValueError, RuntimeError) as exc:
        logger.warning("Could not fetch stats for %s/%s: %s", owner, name, exc)
        return

    async with AsyncSessionLocal() as session:
        session.add(PullStat(repo_id=repo_id, pull_count=count))
        await session.commit()
    logger.info("Recorded %d pulls for %s/%s.", count, owner, name)


async def update_all_repos() -> None:
    """Scheduled job: update pull stats for every tracked repository."""
    logger.info("Running scheduled pull-stat update …")
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Repo))
        repos = result.scalars().all()

    for repo in repos:
        await _record_pull_stat(repo.id, repo.owner, repo.name)


# ─── Dashboard data helpers ──────────────────────────────────────────────────
async def _build_dashboard_data() -> dict[str, Any]:
    """Assemble all data required to render the dashboard."""
    cutoff_24h = datetime.now(tz=timezone.utc).replace(tzinfo=None) - timedelta(hours=24)

    async with AsyncSessionLocal() as session:
        repos_result = await session.execute(select(Repo).order_by(Repo.created_at))
        repos = repos_result.scalars().all()

        total_pulls = 0
        growth_parts: list[int] = []
        repo_list: list[dict[str, Any]] = []
        charts: list[dict[str, Any]] = []

        for repo in repos:
            stats_result = await session.execute(
                select(PullStat)
                .where(PullStat.repo_id == repo.id)
                .order_by(PullStat.recorded_at)
            )
            stats = stats_result.scalars().all()

            latest = stats[-1].pull_count if stats else 0
            total_pulls += latest

            # 24-h growth for this repo
            if len(stats) >= 2:
                old_stats = [s for s in stats if s.recorded_at <= cutoff_24h]
                if old_stats:
                    growth_parts.append(latest - old_stats[-1].pull_count)

            repo_list.append(
                {
                    "id": repo.id,
                    "full_name": repo.full_name,
                    "owner": repo.owner,
                    "name": repo.name,
                    "latest_pulls": latest,
                    "data_points": len(stats),
                }
            )

            charts.append(
                {
                    "full_name": repo.full_name,
                    "x": [s.recorded_at.strftime("%Y-%m-%dT%H:%M:%S") for s in stats],
                    "y": [s.pull_count for s in stats],
                }
            )

    total_growth_24h: Optional[int] = sum(growth_parts) if growth_parts else None

    return {
        "repos": repo_list,
        "charts": charts,
        "kpis": {
            "total_repos": len(repos),
            "total_pulls": total_pulls,
            "growth_24h": total_growth_24h,
        },
    }


# ─── Routes ───────────────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    _: None = Depends(require_auth),
) -> HTMLResponse:
    data = await _build_dashboard_data()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "kpis": data["kpis"],
            "repos": data["repos"],
            "charts_json": json.dumps(data["charts"]),
            "charts_data": data["charts"],
            "update_interval_h": UPDATE_INTERVAL_HOURS,
            "error": request.query_params.get("error"),
            "message": request.query_params.get("message"),
        },
    )


@app.post("/repos", response_class=HTMLResponse)
async def add_repo(
    request: Request,
    full_name: str = Form(...),
    _: None = Depends(require_auth),
) -> RedirectResponse:
    """Add a new GHCR package to track.

    *full_name* must be in the format ``owner/package-name``.
    """
    full_name = full_name.strip().lower()

    if "/" not in full_name or full_name.count("/") != 1:
        return RedirectResponse(
            url="/?error=Invalid+format.+Use+owner%2Fpackage-name.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    owner, name = full_name.split("/", 1)

    if not owner or not name:
        return RedirectResponse(
            url="/?error=Owner+and+package+name+must+not+be+empty.",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    async with AsyncSessionLocal() as session:
        existing = await session.execute(
            select(Repo).where(Repo.full_name == full_name)
        )
        if existing.scalar_one_or_none() is not None:
            return RedirectResponse(
                url=f"/?error={quote(f'{full_name} is already tracked.')}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        # Validate the package exists on GHCR before persisting
        try:
            pull_count = await fetch_ghcr_pulls(owner, name)
        except ValueError as exc:
            return RedirectResponse(
                url=f"/?error={quote(str(exc))}",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        except RuntimeError as exc:
            return RedirectResponse(
                url=f"/?error={quote(str(exc))}",
                status_code=status.HTTP_303_SEE_OTHER,
            )

        new_repo = Repo(owner=owner, name=name, full_name=full_name)
        session.add(new_repo)
        await session.flush()  # populate new_repo.id

        # Record the very first data point immediately
        session.add(PullStat(repo_id=new_repo.id, pull_count=pull_count))
        await session.commit()

    logger.info(
        "Added new repo '%s' with initial pull count %d.", full_name, pull_count
    )
    return RedirectResponse(
        url=f"/?message={quote(f'Added {full_name} – initial pull count: {pull_count:,}')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@app.post("/repos/{repo_id}/delete", response_class=HTMLResponse)
async def delete_repo(
    repo_id: int,
    _: None = Depends(require_auth),
) -> RedirectResponse:
    """Remove a tracked repository and all its historical data."""
    async with AsyncSessionLocal() as session:
        repo = await session.get(Repo, repo_id)
        if repo is None:
            return RedirectResponse(
                url="/?error=Repository+not+found.",
                status_code=status.HTTP_303_SEE_OTHER,
            )
        full_name = repo.full_name
        await session.execute(delete(PullStat).where(PullStat.repo_id == repo_id))
        await session.delete(repo)
        await session.commit()

    logger.info("Deleted repo '%s'.", full_name)
    return RedirectResponse(
        url=f"/?message={quote(f'Removed {full_name}.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
