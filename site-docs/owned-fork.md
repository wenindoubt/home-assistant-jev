# Our maintained copy

This checkout belongs to `wenindoubt/home-assistant-jev`. It retains HA-Jev's
integration and bundles the Jev client under `custom_components/jev/client`.
There is no runtime dependency on the author's `jevclient` PyPI package.

## Installation

Home Assistant 2026.9 or newer is required. This repository is public.
Add `wenindoubt/home-assistant-jev` in HACS as a custom repository with category
Integration, download it and restart Home Assistant. See [installation](install.md).

For manual installation, run `python scripts/package_integration.py`, then extract
`dist/jev.zip` into Home Assistant's config directory and restart. Back up an existing
`custom_components/jev` first and remove upstream HACS management so it cannot
overwrite this copy. The two copies share the `jev` domain.

Configure Jev (TypeSafe) and select its conversation agent in a dedicated Assist
pipeline. For daily control, expose only intended entities and reviewed scripts,
leave the fallback unset, and keep optional scheduled checks off.

## Rust

The optional `rust/jev-gateway` service speaks the integration's existing
`/v1/systemone` protocol. It is a transport gateway, not a complete Rust conversation
agent. Home Assistant still loads Python for registry access, routing and execution.

The gateway holds the TypeSafe API key; Home Assistant supplies an independent
gateway token. Point the integration's API address at the gateway base URL and use
the same configured model. Localhost must be reachable in Home Assistant's network
namespace. Use HTTPS termination for connections across machines.

A gateway adds a network hop and is not a verified performance improvement.
TypeSafe's hosted Jev model remains an external dependency.

## Documentation status

The other pages preserve upstream feature documentation and historical measurements.
They are not evidence of a Rust speedup or an end-to-end Siri test. The repository
README and installation page describe this repository's HACS installation.
Use our repository URL, not the original upstream URL.

The inherited optional sensors, actions, AI Task and house-check features remain
in this baseline. They have not been stripped to create a daily-control-only build.
