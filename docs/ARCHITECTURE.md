# Architecture and Rust boundary

## Why retain a Python adapter

Home Assistant discovers custom integrations as Python modules under
`custom_components/<domain>`. Config flows, conversation entities, registry access,
Assist exposure, lifecycle hooks and intents use its in-process Python APIs.
A Rust crate copied into that directory is not a Home Assistant integration.

Rust can implement the independent core behind an HTTP interface. The Python
adapter then supplies home context and executes validated Home Assistant intents.
This preserves the Assist interface, so phone clients and Siri shortcuts do not
need to know the core's implementation language.

A compiled Python extension is another option, but would require platform-specific
wheels for each Home Assistant Python version and architecture. A sidecar is a simpler
first boundary and avoids running native code in Home Assistant's process.

## What is implemented now

The owned integration preserves the upstream policy and behavior. Its client is
bundled, and every production import and test uses that copy instead of PyPI.

The optional Rust gateway owns the TypeSafe transport boundary:
- A TypeSafe-compatible `POST /v1/systemone` endpoint.
- Structured request validation, including a configured model and primitive bounds.
- Choice option order preservation.
- Separate constant-time-checked gateway authentication.
- A fixed, administrator-configured upstream. Request bodies cannot choose a URL.
- HTTPS upstreams, with plain HTTP allowed only for loopback mock/local endpoints.
- No redirects or automatic retries, to avoid forwarding credentials or duplicate costs.
- Persistent HTTP connection pooling and timeouts.
- Bounded bodies and concurrent evaluations.
- Response key/type/distribution validation and redacted upstream error bodies.

All action selection, ambiguity handling, entity resolution and Home Assistant
execution still live in the Python integration. The Rust gateway is not an agent,
does not classify a natural-language command on its own, and has no Home Assistant
credential. It does not bypass Assist exposure rules.

Direct TypeSafe access remains the default. The adapter's configurable API address
is the only switch needed to use the gateway, so all existing request paths use
the same wire contract. The gateway is optional and does not run automatically.

## Proposed next step, not implemented

To move most of the independent routing into Rust:

1. Freeze representative upstream conversation cases as protocol fixtures, especially
   ambiguity, negation, excluded devices, exceptions and unsupported requests.
2. Define a versioned `/v1/interpret` contract taking the command plus an explicit
   snapshot of exposed entities, aliases, areas and floors.
3. Port question construction and deterministic answer interpretation to Rust.
4. Return an allowlisted typed plan, clarification or unsupported outcome, never
   arbitrary service names, templates, Python code or configuration edits.
5. Make a thin Python conversation agent call that endpoint.
6. Revalidate the plan in Python against current exposure, capabilities and policy
   immediately before executing Home Assistant intents.
7. Test the new implementation in shadow mode before it can act.
8. Remove unneeded upstream administration/automation surfaces deliberately, with
   migration and regression tests, rather than deleting imports opportunistically.

The Rust core should still not hold a broad Home Assistant token. An out-of-process
plan is untrusted input; confidence is not permission.

## Performance and control

The Python client is already asynchronous and shares Home Assistant's pooled
aiohttp session. The bulk of latency is outside the small client: TypeSafe's API,
speech recognition, networking and device execution.

A Rust gateway adds overhead. We have verified compatibility, not a speedup.
A full Rust decision core should be justified by maintainability, isolation and
measured workload rather than assuming a language rewrite makes cloud calls faster.

We own source, deployment, release timing and action policy. We do not own the Jev
weights, have a local Jev inference mode or control TypeSafe availability.

## Deployment

Our GitHub repository is private. HACS explicitly does not support private
repositories. Package the integration and copy it through an authenticated
operator-controlled deployment channel. Future automation should back up the old
directory and support rollback; it should not embed GitHub or Home Assistant
credentials in an archive or image.

The `jev` domain is retained, so this integration replaces the upstream copy.
No Home Assistant installation, device action, phone setup or API key validation
against the real service was performed during this repository bootstrap.
