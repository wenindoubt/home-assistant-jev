"""Offline Python -> Rust -> mock API contract check. Never calls real TypeSafe."""

import asyncio
import importlib.util
import os
import signal
import sys
from pathlib import Path

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parent.parent
TOKEN = "gateway-test-token-not-a-real-key-123456"
KEY = "upstream-test-key-not-a-real-one"


def bundled_client():
    """Load the deployed client without booting Home Assistant's parent module."""
    path = ROOT / "custom_components" / "jev" / "client" / "__init__.py"
    spec = importlib.util.spec_from_file_location("owned_jevclient", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


async def main():
    client_module = bundled_client()
    calls = []

    async def mock_api(request):
        assert request.headers["Authorization"] == f"Bearer {KEY}"
        body = await request.json()
        calls.append(body)
        assert body["model"] == "jev-latest"
        assert list(body["questions"]["action"]["criteria"]) == ["off", "on"]
        return web.json_response(
            {
                "model": "jev-1.13.0",
                "answers": {
                    "action": {
                        "type": "choice",
                        "choice": "off",
                        "probabilities": {"off": 1.0, "on": 0.0},
                        "confidence": 1.0,
                    },
                    "compound": {"type": "noul", "noul": 0.01},
                    "urgency": {
                        "type": "score",
                        "score": 0.2,
                        "probabilities": {"0": 0.8, "1": 0.2},
                        "legend": {"0": "low", "1": "high"},
                        "confidence": 0.5,
                    },
                },
                "usage": {"input_tokens": 321, "output_tokens": 42},
            }
        )

    app = web.Application()
    app.router.add_post("/v1/systemone", mock_api)
    runner = web.AppRunner(app)
    await runner.setup()
    process = None
    try:
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        upstream_port = runner.addresses[0][1]
        process = await asyncio.create_subprocess_exec(
            str(ROOT / "target" / "debug" / "jev-gateway"),
            env={
                **os.environ,
                "TYPESAFE_API_KEY": KEY,
                "JEV_GATEWAY_TOKEN": TOKEN,
                "JEV_BIND": "127.0.0.1:0",
                "JEV_MODEL": "jev-latest",
                "JEV_UPSTREAM_URL": f"http://127.0.0.1:{upstream_port}",
            },
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        line = await asyncio.wait_for(process.stderr.readline(), timeout=10)
        prefix = "Jev gateway listening on "
        assert line.decode().startswith(prefix), line.decode()
        url = "http://" + line.decode().strip().removeprefix(prefix)
        async with client_module.JevClient(TOKEN, base_url=url) as client:
            result = await client.ask(
                {"command": "Turn off the example light"},
                {
                    "action": client_module.Choice(
                        "Which action?", {"off": None, "on": None}
                    ),
                    "compound": client_module.Noul("More than one command?"),
                    "urgency": client_module.Score("How urgent?", ["low", "high"]),
                },
            )
        assert result["action"].choice == "off"
        assert result["compound"].noul == 0.01
        assert result["urgency"].score == 0.2
        assert result.usage.input_tokens == 321
        async with (
            aiohttp.ClientSession() as session,
            session.post(url + "/v1/systemone", json={}) as response,
        ):
            assert response.status == 401
        assert len(calls) == 1
        print("PASS: bundled Python client -> Rust gateway -> local mock TypeSafe API")
        print("PASS: gateway/upstream credentials separated; unauthorized calls rejected")
        print("No real TypeSafe requests or Home Assistant device actions performed.")
    finally:
        if process is not None and process.returncode is None:
            process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
