from fastapi import APIRouter

from fengsuan.app.api.v1 import (
    admin,
    agent,
    auth,
    authors,
    cockpit,
    collab,
    credentials,
    datasets,
    insight,
    kb,
    labs,
    language,
    literature,
    manuscripts,
    runs,
    workspace,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(labs.router)
api_router.include_router(credentials.router)
api_router.include_router(literature.router)
api_router.include_router(authors.router)
api_router.include_router(manuscripts.router)
api_router.include_router(cockpit.router)
api_router.include_router(kb.router)
api_router.include_router(agent.router)
api_router.include_router(language.router)
api_router.include_router(runs.router)
api_router.include_router(collab.router)
api_router.include_router(datasets.router)
api_router.include_router(insight.router)
api_router.include_router(admin.router)
api_router.include_router(workspace.router)
