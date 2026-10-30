"""Exercise the native NDJSON/MCP boundary without credentials or billable calls."""

import asyncio
import json
import sys
from pathlib import Path

import httpx2
import pytest
from openai import AsyncOpenAI

from free_claude_code.application.connected_accounts import ConnectedAccountLoginMode
from free_claude_code.core.anthropic import aggregate_anthropic_sse_to_message
from free_claude_code.core.anthropic.models import MessagesRequest
from free_claude_code.providers.native_accounts import google
from free_claude_code.providers.native_accounts.auth import NativeAccountAuthManager
from free_claude_code.providers.native_accounts.environment import native_environment
from free_claude_code.providers.native_accounts.provider import GoogleAccountProvider
from free_claude_code.providers.native_accounts.transport import NativeChatTransport
from tests.providers.support import immediate_admission, make_provider_config

WORKER = r"""
import json,os,pathlib,sys,time,urllib.request
def emit(event,value):print(json.dumps({'event':event,event:value}),flush=True)
if sys.argv[1:] == ['models']:
 print('Fetching available models...');print('gemini-test-low\tGemini Test');sys.exit(0)
settings=json.loads(pathlib.Path(sys.argv[sys.argv.index('--settings')+1]).read_text())
assert settings['useG1Credits'] is False and 'command(*)' in settings['permissions']['deny']
assert not any(k.startswith('ANTHROPIC_') for k in os.environ)
assert 'GEMINI_API_KEY' not in os.environ
prompt=json.loads(sys.stdin.readline())['message']['content']
emit('init',{'tools':[]})
if os.environ.get('FCC_GOOGLE_FIXTURE_ERROR'):
 emit('result',{'status':'ERROR','error':os.environ['FCC_GOOGLE_FIXTURE_ERROR']});sys.exit(1)
if 'HOLD' in prompt:time.sleep(60)
if 'NEED_TOOL' in prompt and 'result-token-47' not in prompt:
 server=json.loads(pathlib.Path('.agents/mcp_config.json').read_text())['mcpServers']['fcc']
 headers=dict(server['headers'],**{'Content-Type':'application/json','Accept':'application/json, text/event-stream'})
 packet={'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'Read','arguments':{'path':'fixture.txt'}}}
 urllib.request.urlopen(urllib.request.Request(server['serverUrl'],data=json.dumps(packet).encode(),headers=headers),timeout=20).read()
else:
 answer='result-token-47' if 'result-token-47' in prompt else 'native answer'
 emit('step_update',{'step_type':'agent_response','state':'ACTIVE','text_delta':answer})
 emit('result',{'status':'SUCCESS','response':answer,'usage':{'input_tokens':17,'output_tokens':3,'total_tokens':20}})
"""


@pytest.fixture
def native_worker(monkeypatch, tmp_path):
    worker = tmp_path / "native.py"
    worker.write_text(WORKER, encoding="utf-8")
    monkeypatch.setattr(
        google, "google_command", lambda _configured=None: [sys.executable, str(worker)]
    )
    monkeypatch.setattr(google, "launcher_temp_dir_path", lambda: tmp_path / "sessions")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "fixture-must-not-reach-client")
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-must-not-reach-client")
    return tmp_path


async def connect(auth):
    await auth.start_login(ConnectedAccountLoginMode.BROWSER)
    await asyncio.wait_for(auth._task, 10)


@pytest.mark.asyncio
async def test_connection_requires_real_generation_and_persists_no_credentials(
    native_worker,
):
    state = native_worker / "account.json"
    engine = google.GoogleAccountEngine()
    auth = NativeAccountAuthManager(engine, state_path=state)
    try:
        await connect(auth)
        assert auth.is_connected(), auth.status().message
        assert json.loads(state.read_text()) == {
            "version": 1,
            "models": ["gemini-test-low"],
        }
        assert not list((native_worker / "sessions").iterdir())
        await auth.disconnect()
        assert not state.exists()
    finally:
        await auth.close()


@pytest.mark.asyncio
async def test_eligibility_error_clears_cached_connection_and_redacts_native_error(
    native_worker, monkeypatch
):
    state = native_worker / "account.json"
    state.write_text(json.dumps({"version": 1, "models": ["old-model"]}))
    monkeypatch.setenv(
        "FCC_GOOGLE_FIXTURE_ERROR",
        "Your account is not eligible. Verify your account https://accounts.google.com/?secret=hidden",
    )
    auth = NativeAccountAuthManager(google.GoogleAccountEngine(), state_path=state)
    try:
        await connect(auth)
        assert auth.status().state == "error" and not auth.is_connected()
        assert "verification" in auth.status().message
        assert "hidden" not in auth.status().message and not state.exists()
    finally:
        await auth.close()


@pytest.mark.asyncio
async def test_native_tool_cycle_and_sdk_response_drain_use_one_turn(native_worker):
    engine = google.GoogleAccountEngine()
    auth = NativeAccountAuthManager(engine, state_path=native_worker / "account.json")
    await connect(auth)
    provider = GoogleAccountProvider(
        make_provider_config("local", "http://google-account.invalid/v1"),
        auth=auth,
        engine=engine,
        admission=immediate_admission(max_attempts=1),
    )
    tool = {
        "name": "Read",
        "description": "Read a caller file",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}},
    }

    async def ask(messages, tools):
        result, error, done = await aggregate_anthropic_sse_to_message(
            provider.stream_messages(
                MessagesRequest(
                    model="gemini-test-low",
                    max_tokens=100,
                    messages=messages,
                    tools=tools,
                )
            )
        )
        assert done and error is None
        return result

    try:
        plain = await ask([{"role": "user", "content": "Hello"}], [])
        assert plain["content"] == [{"type": "text", "text": "native answer"}]
        assert plain["usage"]["input_tokens"] == 17
        messages = [{"role": "user", "content": "NEED_TOOL"}]
        result = await ask(messages, [tool])
        assert result["stop_reason"] == "tool_use"
        call = next(x for x in result["content"] if x["type"] == "tool_use")
        assert call["name"] == "Read" and call["input"] == {"path": "fixture.txt"}
        final = await ask(
            [
                *messages,
                {"role": "assistant", "content": result["content"]},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": call["id"],
                            "content": "result-token-47",
                        }
                    ],
                },
            ],
            [tool],
        )
        assert final["content"] == [{"type": "text", "text": "result-token-47"}]
        assert not list((native_worker / "sessions").iterdir())
    finally:
        await provider.cleanup()
        await auth.close()


@pytest.mark.asyncio
async def test_transport_close_reaps_active_client_and_removes_private_workspace(
    native_worker,
):
    engine = google.GoogleAccountEngine()
    auth = NativeAccountAuthManager(engine, state_path=native_worker / "account.json")
    await connect(auth)
    transport = NativeChatTransport(auth, engine, 120)
    client = AsyncOpenAI(
        api_key="local",
        base_url="http://google-account.invalid/v1",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=transport),
    )
    stream = await client.chat.completions.create(
        model="gemini-test-low",
        messages=[{"role": "user", "content": "HOLD"}],
        stream=True,
    )
    reader = asyncio.create_task(anext(stream.__aiter__()))
    try:
        await reader
        active = next(iter(transport._streams))
        process = active.session.process
        await asyncio.wait_for(client.close(), 5)
        assert process.returncode is not None
        assert not transport._streams and not transport._setups
        assert not list((native_worker / "sessions").iterdir())
    finally:
        await stream.close()
        await client.close()
        await auth.close()


@pytest.mark.asyncio
async def test_blank_native_mcp_file_is_valid_and_images_fail_explicitly(native_worker):
    config = Path.home() / ".gemini/config/mcp_config.json"
    config.parent.mkdir(parents=True)
    config.touch()
    engine = google.GoogleAccountEngine()
    auth = NativeAccountAuthManager(engine, state_path=native_worker / "account.json")
    await connect(auth)
    assert auth.is_connected(), auth.status().message
    transport = NativeChatTransport(auth, engine, 30)
    try:
        response = await transport.handle_async_request(
            httpx2.Request(
                "POST",
                "http://google-account.invalid/v1/chat/completions",
                json={
                    "model": "gemini-test-low",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": "data:image/png;base64,AAAA"},
                                }
                            ],
                        }
                    ],
                },
            )
        )
        assert response.status_code == 400
        assert "text only" in response.json()["error"]["message"]
        assert not transport._streams
    finally:
        await transport.aclose()
        await auth.close()


def test_native_environment_keeps_keyring_location_and_removes_gateway_keys():
    assert native_environment(
        {
            "USERPROFILE": "user",
            "PATH": "native",
            "Anthropic_Base_Url": "gateway",
            "GOOGLE_API_KEY": "secret",
            "CLAUDE_CODE_OAUTH_TOKEN": "secret",
        }
    ) == {"USERPROFILE": "user", "PATH": "native"}
