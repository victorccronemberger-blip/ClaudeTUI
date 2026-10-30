"""Exercise real subprocess/RPC/MCP boundaries without Google credentials."""

import asyncio
import json
import sys

import httpx
import httpx2
import pytest

from free_claude_code.application.connected_accounts import ConnectedAccountLoginMode
from free_claude_code.config.provider_catalog import PROVIDER_CATALOG
from free_claude_code.config.settings import Settings
from free_claude_code.core.anthropic import aggregate_anthropic_sse_to_message
from free_claude_code.core.anthropic.models import MessagesRequest
from free_claude_code.providers.gemini_cli import acp
from free_claude_code.providers.gemini_cli.auth import GeminiCLIAuthManager
from free_claude_code.providers.gemini_cli.provider import GeminiCLIProvider
from free_claude_code.providers.gemini_cli.transport import (
    GeminiACPTransport,
    ToolBridge,
    conversation_prompt,
)
from tests.api.support import create_test_app
from tests.providers.support import immediate_admission, make_provider_config

WORKER = r"""
import json, os, sys, threading, time, urllib.request
servers=[]
def emit(data):
 print(json.dumps(dict(jsonrpc="2.0",**data)),flush=True)
def result(i,value):emit({"id":i,"result":value})
def generate(i,p):
 text=p['prompt'][0]['text']
 if 'NEED_TOOL' in text and 'tool-result-token' not in text:
  server=servers[0]
  headers={h['name']:h['value'] for h in server['headers']}
  headers['Content-Type']='application/json'
  headers['Accept']='application/json, text/event-stream'
  body={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'Read','arguments':{'path':'fixture.txt'}}}
  req=urllib.request.Request(server['url'],data=json.dumps(body).encode(),headers=headers)
  urllib.request.urlopen(req,timeout=20).read()
 else:
  for chunk in ['native ', 'tool-result-token' if 'tool-result-token' in text else 'answer']:
   emit({'method':'session/update','params':{'sessionId':'session-1','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':chunk}}}})
  result(i,{'stopReason':'end_turn','usage':{'inputTokens':17,'outputTokens':3,'totalTokens':20}})
for line in sys.stdin:
 p=json.loads(line);i=p['id'];m=p['method'];args=p.get('params',{})
 if m=='initialize':
  settings=json.load(open(os.path.join(os.getcwd(),'.gemini','settings.json')))
  assert all(n.startswith('mcp_fcc_') for n in settings['tools']['core']) and settings['mcp']['allowed']==['fcc']
  assert 'GEMINI_API_KEY' not in os.environ
  result(i,{'protocolVersion':1,'authMethods':[{'id':'oauth-personal'}],'agentCapabilities':{'mcpCapabilities':{'http':True}}})
 elif m=='authenticate':
  if os.environ.get('FCC_WORKER_LOGIN_WAIT'):time.sleep(60)
  if os.environ.get('FCC_WORKER_AUTH_ERROR'):
   emit({'id':i,'error':{'code':-32000,'message':os.environ['FCC_WORKER_AUTH_ERROR']}});continue
  assert args['methodId']=='oauth-personal';result(i,{})
 elif m=='session/new':
  servers=args['mcpServers']
  result(i,{'sessionId':'session-1','models':{'availableModels':[{'modelId':'auto-gemini-3'},{'modelId':'gemini-3.5-flash'}]}})
 elif m in ['session/set_mode','session/set_model']:result(i,{})
 elif m=='session/prompt':threading.Thread(target=generate,args=(i,args),daemon=True).start()
 else:emit({'id':i,'error':{'code':-32601,'message':'unknown'}})
"""


@pytest.fixture
def worker(monkeypatch, tmp_path):
    script = tmp_path / "native_agent.py"
    script.write_text(WORKER, encoding="utf-8")
    monkeypatch.setattr(
        acp, "cli_command", lambda configured=None: [sys.executable, str(script)]
    )
    # auth imports this function directly.
    monkeypatch.setattr(
        "free_claude_code.providers.gemini_cli.auth.cli_command", acp.cli_command
    )
    monkeypatch.setenv("GEMINI_API_KEY", "must-not-reach-native-child")
    return script


async def connect(manager):
    status = await manager.start_login(ConnectedAccountLoginMode.BROWSER)
    assert status.state == "connecting"
    assert manager._task is not None
    await asyncio.wait_for(manager._task, 15)
    assert manager.status().state == "connected", manager.status().message


@pytest.mark.asyncio
async def test_native_oauth_and_model_list_are_persisted_without_tokens(
    worker, tmp_path
):
    path = tmp_path / "connection.json"
    manager = GeminiCLIAuthManager(state_path=path)
    try:
        await connect(manager)
        assert manager.model_ids == ("auto-gemini-3", "gemini-3.5-flash")
        assert manager.status().as_dict()["supported_login_modes"] == ["browser"]
        saved = json.loads(path.read_text())
        assert saved == {"version": 1, "models": list(manager.model_ids)}
        restored = GeminiCLIAuthManager(state_path=path)
        try:
            assert restored.is_connected()
            await restored.disconnect()
            assert not path.exists()
            assert not restored.is_connected()
        finally:
            await restored.close()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_cancel_native_browser_login_reaps_process_without_saving_connection(
    worker, monkeypatch, tmp_path
):
    monkeypatch.setenv("FCC_WORKER_LOGIN_WAIT", "1")
    path = tmp_path / "cancelled-connection.json"
    auth = GeminiCLIAuthManager(state_path=path)
    try:
        await auth.start_login(ConnectedAccountLoginMode.BROWSER)
        await asyncio.sleep(0.1)
        status = await asyncio.wait_for(auth.cancel_login(), 10)
        assert status.state == "disconnected"
        assert not auth.is_connected()
        assert not path.exists()
        assert auth._task is None
    finally:
        await auth.close()


@pytest.mark.asyncio
async def test_retired_consumer_account_explains_google_eligibility_without_exposing_native_output(
    worker, monkeypatch, tmp_path
):
    monkeypatch.setenv(
        "FCC_WORKER_AUTH_ERROR",
        "This client is no longer supported for Gemini Code Assist for individuals. "
        "Native credential must-stay-private and https://example.invalid/?code=private-code",
    )
    path = tmp_path / "consumer-connection.json"
    auth = GeminiCLIAuthManager(state_path=path)
    try:
        await auth.start_login(ConnectedAccountLoginMode.BROWSER)
        assert auth._task is not None
        await asyncio.wait_for(auth._task, 15)
        status = auth.status()
        assert status.state == "error"
        assert not status.connected
        assert status.message is not None
        assert "June 18, 2026" in status.message
        assert "Google AI Pro" in status.message
        assert "Antigravity" in status.message
        assert "developers.google.com" in status.message
        assert "must-stay-private" not in status.message
        assert "private-code" not in status.message
        assert not path.exists()
    finally:
        await auth.close()


def test_unrecognized_native_rpc_error_never_exposes_credentials():
    error = acp._rpc_error({"code": -32000, "message": "access_token=private-token"})
    assert "private-token" not in str(error)
    assert "Check the native account login" in str(error)


@pytest.mark.asyncio
async def test_sdk_close_cancels_native_initialization_and_closes_mcp_listener(
    worker, monkeypatch, tmp_path
):
    from openai import AsyncOpenAI

    auth = GeminiCLIAuthManager(state_path=tmp_path / "connection.json")
    auth._models = ("gemini-3.5-flash",)
    started = asyncio.Event()
    connections = []
    original_call = acp.ACPConnection.call

    async def hold_initialization(self, method, params):
        if method == "initialize":
            connections.append(self)
            started.set()
            await asyncio.Event().wait()
        return await original_call(self, method, params)

    monkeypatch.setattr(acp.ACPConnection, "call", hold_initialization)
    transport = GeminiACPTransport(auth, 120)
    client = AsyncOpenAI(
        api_key="local",
        base_url="http://gemini-cli.invalid/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=transport),
    )
    pending = asyncio.create_task(
        client.chat.completions.create(
            model="gemini-3.5-flash",
            messages=[{"role": "user", "content": "Hello"}],
            tools=[
                {
                    "type": "function",
                    "function": {"name": "Read", "parameters": {"type": "object"}},
                }
            ],
            stream=True,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), 10)
        connection = connections[0]
        assert connection._process is not None
        url = connection.mcp_servers[0]["url"]
        await asyncio.wait_for(client.close(), 10)
        await asyncio.gather(pending, return_exceptions=True)
        assert connection._process is None
        assert not transport._setups and not transport._streams
        async with httpx.AsyncClient() as http:
            with pytest.raises((httpx.ConnectError, httpx.ConnectTimeout)):
                await http.post(url, timeout=2)
        response = await transport.handle_async_request(
            httpx2.Request("POST", "http://gemini-cli.invalid/v1/chat/completions")
        )
        assert response.status_code == 503
        assert len(connections) == 1
    finally:
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await client.close()
        await auth.close()


@pytest.mark.asyncio
async def test_native_text_tools_and_tool_result_round_trip(worker, tmp_path):
    auth = GeminiCLIAuthManager(state_path=tmp_path / "connection.json")
    await connect(auth)
    provider = GeminiCLIProvider(
        make_provider_config("fcc-native-cli", "http://gemini-cli.invalid/v1"),
        auth=auth,
        admission=immediate_admission(max_attempts=1),
    )
    try:
        request = MessagesRequest(
            model="gemini-3.5-flash",
            max_tokens=100,
            messages=[{"role": "user", "content": "Hello"}],
        )
        message, error, done = await aggregate_anthropic_sse_to_message(
            provider.stream_messages(request)
        )
        assert done and error is None
        assert message["content"] == [{"type": "text", "text": "native answer"}]
        assert message["usage"]["input_tokens"] == 17
        assert {m.model_id for m in await provider.list_model_infos()} == set(
            auth.model_ids
        )

        request = MessagesRequest(
            model="gemini-3.5-flash",
            max_tokens=100,
            tools=[
                {
                    "name": "Read",
                    "description": "Read a file",
                    "input_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                    },
                }
            ],
            messages=[{"role": "user", "content": "NEED_TOOL"}],
        )
        message, error, done = await aggregate_anthropic_sse_to_message(
            provider.stream_messages(request)
        )
        assert done and error is None
        assert message["stop_reason"] == "tool_use"
        call = next(
            block for block in message["content"] if block["type"] == "tool_use"
        )
        assert call["name"] == "Read" and call["input"] == {"path": "fixture.txt"}
        # No native file read occurred: only a tool-use block reached the caller.
        payload = request.model_dump()
        payload.update(
            {
                "messages": [
                    *payload["messages"],
                    {"role": "assistant", "content": message["content"]},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": call["id"],
                                "content": "tool-result-token",
                            }
                        ],
                    },
                ]
            }
        )
        # Validate nested dictionaries into the normal public request models.
        request = MessagesRequest.model_validate(payload)
        message, error, done = await aggregate_anthropic_sse_to_message(
            provider.stream_messages(request)
        )
        assert done and error is None
        assert message["content"] == [
            {"type": "text", "text": "native tool-result-token"}
        ]
    finally:
        await provider.cleanup()
        await auth.close()


@pytest.mark.asyncio
async def test_mcp_bridge_requires_per_request_secret_and_valid_tool():
    bridge = ToolBridge(
        [
            {
                "type": "function",
                "function": {"name": "Read", "parameters": {"type": "object"}},
            }
        ]
    )
    async with bridge.server() as server, httpx.AsyncClient() as client:
        response = await client.post(
            server["url"], json={"id": 1, "method": "tools/list"}
        )
        assert response.status_code == 401
        headers = {h["name"]: h["value"] for h in server["headers"]}
        response = await client.post(
            server["url"], headers=headers, json={"id": 2, "method": "tools/list"}
        )
        assert response.json()["result"]["tools"][0]["name"] == "Read"
        response = await client.post(
            server["url"],
            headers=headers,
            json={
                "id": 3,
                "method": "tools/call",
                "params": {"name": "shell", "arguments": {}},
            },
        )
        assert response.json()["error"]["code"] == -32602
        assert bridge.calls.empty()


def test_inline_images_and_history_are_preserved_without_remote_fetch():
    prompt = conversation_prompt(
        {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Inspect"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,aGVsbG8="},
                        },
                    ],
                }
            ]
        }
    )
    assert prompt[1] == {"type": "image", "mimeType": "image/png", "data": "aGVsbG8="}
    assert '"attachment": 1' in prompt[0]["text"]
    assert "aGVsbG8=" not in prompt[0]["text"]


@pytest.mark.asyncio
async def test_connected_native_models_appear_in_gateway_catalog(worker, tmp_path):
    from fastapi.testclient import TestClient

    auth = GeminiCLIAuthManager(state_path=tmp_path / "connection.json")
    try:
        await connect(auth)
        app = create_test_app(
            Settings(MODEL="gemini_cli/gemini-3.5-flash"),
            connected_accounts={"gemini_cli": auth},
        )
        with TestClient(app) as client:
            rows = client.get("/v1/models").json()["data"]
            assert any("gemini_cli/gemini-3.5-flash" in row["id"] for row in rows)
        assert PROVIDER_CATALOG["gemini_cli"].credential_env is None
    finally:
        await auth.close()


def test_npm_wrapper_resolves_declared_entry_point_without_shell(monkeypatch, tmp_path):
    wrapper = tmp_path / "gemini.cmd"
    wrapper.touch()
    package = tmp_path / "node_modules/@google/gemini-cli"
    (package / "bundle").mkdir(parents=True)
    entry = package / "bundle/gemini.js"
    entry.touch()
    (package / "package.json").write_text(
        json.dumps({"bin": {"gemini": "bundle/gemini.js"}})
    )
    monkeypatch.setattr(acp.shutil, "which", lambda name: "node.exe")
    assert acp.cli_command(str(wrapper)) == ["node.exe", str(entry.resolve())]
