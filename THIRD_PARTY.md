# Third-party source

This private repository is an independently maintained copy, not a GitHub fork
attached to the public upstream network. Original history and licences are retained.

| Component | Source | Snapshot | Licence |
| --- | --- | --- | --- |
| Home Assistant integration | https://github.com/AboveColin/HA-Jev | `ee44101480890c49544748e8441f87d65151dd6f` (1.20.0) | MIT, copyright 2026 AboveColin |
| Bundled Python client | https://github.com/AboveColin/jevclient | `a19745af82aadfeaa5e62c50b557c605ee2d1e36` (1.2.0) | MIT, copyright 2026 AboveColin |

The integration's notice is in `LICENSE`; the client's notice is also retained in
`custom_components/jev/client/LICENSE`. Copying code does not transfer the original
author's copyright. MIT permits us to use, modify and distribute the code, including
privately, provided those notices remain.

The client is bundled under `custom_components/jev/client`. Runtime imports and
tests use that copy, and the integration manifest no longer installs `jevclient`
from PyPI. Home Assistant still supplies aiohttp and its own integration APIs.

The Rust gateway is new code in this repository under the root MIT licence.
`Cargo.lock` records its dependency versions; dependencies retain their own licences.

The retained upstream documentation, blueprints, measurements and contributor
instructions describe the upstream baseline. The root README and
`site-docs/owned-fork.md` describe this private copy. Historical upstream measurements
are not benchmarks of our Rust gateway or our actual home.

No affiliation with TypeSafe, AboveColin or Home Assistant is implied.
