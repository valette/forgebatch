import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(ROOT)
sys.path.insert(0, REPO)

import config
from app import ForgeBatchApp

OUT = ROOT
SIZE = (100, 46)


async def snap():
    config.set_config_file(os.path.join(REPO, "config.example.json"))
    os.makedirs(OUT, exist_ok=True)
    app = ForgeBatchApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()

        app._activate("settings")
        await pilot.pause()
        app.deliver_screenshot(filename="settings.svg", path=OUT)
        await pilot.pause()

        app._activate(0)
        await pilot.pause()
        app.deliver_screenshot(filename="files.svg", path=OUT)
        await pilot.pause()

        app.action_preview()
        app._activate("preview")
        await pilot.pause()
        app.deliver_screenshot(filename="preview.svg", path=OUT)
        await pilot.pause()

        app._activate("running")
        await app._refresh_running()
        await pilot.pause()
        app.deliver_screenshot(filename="jobs.svg", path=OUT)
        await pilot.pause()


asyncio.run(snap())
print("done ->", OUT)
