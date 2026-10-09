# Home Assistant Jev

Private, independently maintained copy of [AboveColin/HA-Jev](https://github.com/AboveColin/HA-Jev),
with its Python Jev client bundled in the same repository and an optional Rust API gateway.

Repository: https://github.com/wenindoubt/home-assistant-jev

## What we control

- Integration source, Jev client source, prompts, routing safeguards and releases.
- Whether and when upstream changes are merged.
- Deployment to our Home Assistant, without relying on the author's PyPI releases.
- An optional Rust transport service, with separate gateway and TypeSafe credentials.

We do **not** own Jev's model weights or control TypeSafe's hosted API. This is not
an offline or self-hosted Jev model.

## Current implementation

```text
Siri Shortcut / Home Assistant Assist
    -> Python conversation agent: exposed entities, typed questions, action policy
    -> bundled Python Jev client
        -> TypeSafe directly (default)
        OR
        -> our Rust gateway -> TypeSafe
    -> Python agent validates routing and invokes Home Assistant intents
    -> Assist returns a short response
```

Home Assistant loads Python custom integrations, not Rust binaries. The Rust gateway
speaks the same HTTP protocol the integration already understands. It does not need
a Home Assistant token and cannot execute Home Assistant actions itself.

**This is not a full Rust rewrite.** The existing conversation routing and Home
Assistant lifecycle remain Python. The optional Rust service implements request
validation, pooled HTTP transport, authentication, bounded concurrency, response
validation and error handling. See [the architecture and next steps](docs/ARCHITECTURE.md).

The default direct path does not require Rust. Adding a gateway introduces a network
hop; it is not a demonstrated speed improvement. Jev API, speech recognition and device
latency are likely to dominate ordinary daily commands.

## Install the private integration

Requires Home Assistant **2026.9 or newer**. The integration version here is **1.20.1**.

[HACS does not support private repositories](https://www.hacs.xyz/docs/faq/private_repositories/).
Do not add this private URL to HACS and expect it to download or update.

1. Clone this repository with your own GitHub authentication.
2. Run `python scripts/package_integration.py`. It produces `dist/jev.zip`.
3. Back up any existing `/config/custom_components/jev` directory.
4. Extract the archive into Home Assistant's config directory. The result must be
   `/config/custom_components/jev/manifest.json`, including the bundled `client/`.
5. Restart Home Assistant.
6. Add **Jev (TypeSafe)** in **Settings -> Devices & services** and enter a TypeSafe API key.
7. Create a daily-control Assist pipeline and select **Jev** as its conversation agent.

The domain stays `jev` for compatibility. This copy **replaces** upstream HA-Jev;
the two cannot be installed side by side under the same domain. Remove the upstream
HACS-managed installation before switching, after taking a backup, so HACS cannot
overwrite this copy. Do not remove your config entry just to replace the files.

Packaging does not deploy, restart, or modify a running Home Assistant. Updates are
manual for now; no live installation has been performed as part of creating this repository.

## Daily-control scope

The inherited conversation agent supports on/off, toggle, light brightness and
state queries through Home Assistant intents, including supported scene/script
activation and room/floor targets. It is not an administrative agent.

Expose only intended devices and approved scripts to Assist. Leave the fallback
agent unset initially and the optional weekly house check disabled. Do not add
question contexts, blueprints or other features unless deliberately wanted.

Locks and garage/gate/door covers are excluded. Compound commands, thermostat
setpoints, playback, scheduled commands and exceptions are not all supported by the
Jev agent. See [the retained conversation documentation](site-docs/conversation.md).
An approved script is not a sandbox: review what it does before exposing it.

The upstream sensors, automation actions, AI Task and house-check features are still
in the source. This baseline has **not** removed those features or introduced a new
daily-control-only permission boundary.

## Optional Rust gateway

Build:

```sh
cargo build --release --locked -p jev-gateway
```

Set these environment variables in your service manager or secret store:

| Variable | Meaning |
| --- | --- |
| `TYPESAFE_API_KEY` | Real TypeSafe credential, held by the gateway |
| `JEV_GATEWAY_TOKEN` | Independent random token, at least 32 characters |
| `JEV_BIND` | Default `127.0.0.1:8093` |
| `JEV_MODEL` | Default `jev-latest`; the requested model must match |
| `JEV_UPSTREAM_URL` | Default `https://api.typesafe.ai` |

Generate a gateway token with `openssl rand -hex 32`. Do not commit secrets.
The program does not automatically read a `.env` file.

Run `target/release/jev-gateway`. In the integration's configuration/reconfiguration:

- API address: the gateway base URL, **without** `/v1/systemone`.
- API key: the **gateway token**, not the TypeSafe key.
- Model: the same value as `JEV_MODEL`.

Use `http://127.0.0.1:8093` only when the gateway is reachable in Home Assistant's
own network namespace. In HA OS or a container, localhost is not the host machine.
For a separate host/container, deploy on a trusted network and put authenticated
HTTPS termination in front of the gateway. The binary itself does not serve TLS.
Do not expose its plain HTTP listener directly to the internet.

The gateway forwards one request once, without automatic retries. It preserves
upstream error status and Retry-After while redacting upstream error bodies.
It pools connections, limits request/response sizes and allows eight concurrent
evaluations. It logs neither command bodies nor credentials. The health endpoint
is local process liveness, not a check of TypeSafe or Home Assistant.

## Siri

In the Home Assistant iOS app, an Apple Shortcut can use:

1. **Dictate Text**
2. **Home Assistant -> Assist prompt**, selecting the Jev daily-control pipeline
3. **Speak Text**, using the returned response

Invoke the named shortcut with Siri, then dictate the command. This does not replace
Siri's native HomeKit interpretation. End-to-end Siri operation and multi-turn
clarification still need testing on the actual phone and Home Assistant.

## Development and verification

Python 3.14 is required by the pinned Home Assistant test harness.

```sh
python3.14 -m venv .venv
.venv/bin/pip install -r requirements-test.txt -r requirements-docs.txt ruff==0.16.8 mypy==2.3.1
.venv/bin/ruff check custom_components tests scripts
.venv/bin/ruff format --check custom_components tests scripts
.venv/bin/mypy --strict --ignore-missing-imports custom_components/jev
.venv/bin/pytest
.venv/bin/mkdocs build --strict
cargo fmt --all -- --check
cargo clippy --workspace --all-targets --locked -- -D warnings
cargo test --workspace --locked
cargo build --locked -p jev-gateway
.venv/bin/python scripts/smoke_gateway.py
```

Tests and the cross-language smoke check use fake credentials and mock endpoints.
No live TypeSafe calls are needed. The smoke check exercises the bundled Python
client -> Rust gateway -> local mock TypeSafe API, not a real device.

See [third-party provenance](THIRD_PARTY.md) for source commits and MIT notices.
The original upstream overview is retained in [docs/upstream-README.md](docs/upstream-README.md).
