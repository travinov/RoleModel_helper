from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class DBConfig:
    host: str
    port: int
    dbname: str
    user: str
    password: str
    schema: str = "public"

    @classmethod
    def from_env(cls) -> "DBConfig":
        missing = [
            name
            for name in (
                "RM_DB_HOST",
                "RM_DB_PORT",
                "RM_DB_NAME",
                "RM_DB_USER",
                "RM_DB_PASSWORD",
            )
            if not os.getenv(name)
        ]
        if missing:
            raise ValueError(
                "Missing required environment variables: " + ", ".join(sorted(missing))
            )
        return cls(
            host=os.environ["RM_DB_HOST"],
            port=int(os.environ["RM_DB_PORT"]),
            dbname=os.environ["RM_DB_NAME"],
            user=os.environ["RM_DB_USER"],
            password=os.environ["RM_DB_PASSWORD"],
            schema=os.getenv("RM_DB_SCHEMA", "public"),
        )

