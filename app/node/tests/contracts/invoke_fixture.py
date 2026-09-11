"""Run real node transport/dispatch for the TypeScript contract suite.

Input and output are JSON over stdio. Only HA network I/O is mocked; there is
no live gateway/HA access and no alternate production authorization path.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import AsyncMock, patch

from openclaw_node.config import NodeConfig
from openclaw_node.gateway_ws import GatewayClient
from openclaw_node.ha_client import HAClientError
from openclaw_node.identity import generate_identity


async def invoke(request: dict[str, Any]) -> dict[str, Any]:
    with TemporaryDirectory() as directory:
        config = NodeConfig(
            addon_mode=False,
            gateway_url="wss://gateway.example.invalid/ws",
            pairing_token="",
            node_name="contract-fixture",
            hass_url="",
            hass_token="",
            supervisor_token="",
            data_dir=Path(directory),
        )
        client = GatewayClient(config=config, identity=generate_identity(), device_token="")
        socket = AsyncMock()
        post = AsyncMock(
            return_value=[
                {
                    "entity_id": "light.test",
                    "state": "on",
                    "attributes": {
                        "brightness": 128,
                        "rgb_color": [1, 2, 3],
                        "supported_color_modes": ["rgb"],
                        "fixture": {"enabled": False, "transition": 0},
                    },
                    "context": {"id": "fixture-context", "parent_id": None},
                }
            ]
        )
        if request.get("ha_error"):
            post.side_effect = HAClientError("HA_NETWORK", "Fixture HA unavailable")
        with (
            patch("openclaw_node.commands.ha.ha_post", post),
            patch("openclaw_node.commands.ha_config_automation.ha_post", post),
            patch("openclaw_node.commands.ha_config_automation.ha_get", post),
            patch("openclaw_node.commands.ha_config_automation.ha_delete", post),
        ):
            await client._handle_invoke(
                socket,
                {
                    "id": "contract-test",
                    "nodeId": request["nodeId"],
                    "command": request["command"],
                    "paramsJSON": json.dumps(request["params"]),
                },
            )
        return {
            "response": json.loads(socket.send.call_args.args[0])["params"],
            "ha_calls": [list(call.args) for call in post.call_args_list],
        }


if __name__ == "__main__":
    print(json.dumps(asyncio.run(invoke(json.load(sys.stdin)))))
