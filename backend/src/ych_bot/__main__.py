"""Run the local YCH control plane."""

import asyncio
from uuid import uuid4

import uvicorn

from ych_bot.api.app import create_app
from ych_bot.application.launcher_preflight import LauncherPreflightService
from ych_bot.config import Settings


def main() -> None:
    settings = Settings.load()
    process_instance_id = str(uuid4())
    launcher_result = asyncio.run(
        LauncherPreflightService(settings).inspect(process_instance_id=process_instance_id)
    )
    if not launcher_result.passed:
        blockers = ", ".join(
            probe.probe_code
            for probe in launcher_result.probes
            if probe.status.blocks_required_probe
        )
        raise SystemExit(f"YCH launcher preflight blocked startup: {blockers}")
    app = create_app(
        settings,
        process_instance_id=process_instance_id,
        launcher_result=launcher_result,
    )
    uvicorn.run(
        app,
        host=settings.admin_host,
        port=settings.admin_port,
        reload=False,
        access_log=False,
    )


if __name__ == "__main__":
    main()
