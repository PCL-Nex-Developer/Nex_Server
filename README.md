# Nex_Server static API

This repository publishes versioned launcher metadata as ordinary static JSON files. It does not
run a dedicated API process. The Git repository is the source of truth and GitHub Raw is the API transport.
Developer trust and plugin discovery are published together in one market document.

Canonical client endpoints:

- `https://raw.githubusercontent.com/PCL-Nex-Developer/Nex_Server/refs/heads/main/apiv2/plugin-market.json`

## HTTP metadata

HTTP headers are owned by the static host, not by files in this repository. GitHub Raw may serve the
valid UTF-8 JSON bytes as `text/plain; charset=utf-8`; clients must therefore validate and parse the
payload as JSON instead of requiring one exact raw-host media type. Cache invalidation is represented
by the content hashes in `apiv2/cache.json`; ETags and cache lifetimes remain host-managed.

## Validation and publication

Run all checks locally with:

```text
python scripts/validate_static_api.py --write-cache
python -m unittest discover -s tests -v
```

`plugin-market.json` contains `developers` and supports direct `manifests` and inline `plugins`.
It must not contain `topics`: the launcher owns the hard-coded `pclnexplugin` GitHub Topic search,
while users can add multiple independent plugin-market JSON addresses. The official file currently
keeps `manifests` and `plugins` empty. EasyTier and ProfileUnlock
are intentionally not listed as direct manifests yet: their current files use the legacy
`versions`-only contract, which the launcher can safely normalize only when it has GitHub repository
metadata from Topic discovery. A direct `manifests` entry must use the current complete manifest
contract (`id`, `name`, `author`, `description`, `repository`, and platform downloads).

The update-sync Action validates the combined public registry before and after release synchronization,
rebuilds `cache.json`, and stages the registry together with the generated update feed. Publication
only happens after the complete unittest suite succeeds.
