from __future__ import annotations

import uvicorn

from app.api.server import build_app
from app.config import AppConfig


def main() -> None:
    config = AppConfig.from_env()
    uvicorn.run(build_app(config), host=config.app_host, port=config.app_port)


if __name__ == "__main__":
    main()
