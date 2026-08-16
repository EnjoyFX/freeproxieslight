# freeproxieslight

A lightweight, **dependency-free** command-line tool (and library) that
harvests *valid* proxies. Pure Python standard library — nothing to install
beyond the package itself.

## Why it's different

Most free-proxy scrapers only check that a proxy returns *something*. That is
easy to fake: a hostile or broken proxy can replay a canned "OK" body without
actually relaying your request. `freeproxieslight` validates with a **random
per-check nonce** — the endpoint must echo that nonce back, which only happens
if the request genuinely travelled through the proxy. It also compares the
observed exit IP against your real IP to flag **anonymous** proxies.

## Features

- **Zero dependencies** — stdlib only (`urllib`, `html.parser`, `json`,
  `concurrent.futures`), Python 3.8+.
- **Un-spoofable validation** via nonce echo, with fallback across multiple
  check endpoints.
- **Rich results** — each proxy carries `latency`, `anonymous` and
  `checked_at`, not just `ip:port` (see `--format json`).
- **Concurrent + streaming** — sources and checks run on a bounded thread
  pool; valid proxies are written out the moment they are confirmed.
- **Pluggable sources** — a `Source` pairs a URL with a parser, so a new site
  or JSON API is added without touching the core.

## Install

```bash
pip install .
# or, for an isolated CLI:
pipx install .
```

This exposes the `freeproxieslight` command. You can also run it without
installing via `python -m freeproxieslight`.

## CLI usage

```bash
# Read sources from domains.txt, write valid proxies to proxies_checked.txt
freeproxieslight

# Pass sources directly and print JSON (with latency/anonymity) to stdout
freeproxieslight https://www.sslproxies.org/ -f json -o -

# Custom sources file, tighter timeout, more workers
freeproxieslight -s my_sources.txt -t 5 -w 100 -o good.txt
```

```
positional arguments:
  sources               source URLs; if omitted, read from --sources-file

options:
  -s, --sources-file    file with one source URL per line (default: domains.txt)
  -o, --output          output file (default: proxies_checked.txt); '-' = stdout
  -f, --format {txt,json}   output format (default: txt)
  -t, --timeout         per-request timeout in seconds (default: 8)
  -w, --workers         max concurrent workers (default: 50)
  -q, --quiet           only log warnings and errors
  -V, --version         print version and exit
```

Copy `domains.example.txt` to `domains.txt` and edit it (blank lines and
`#` comments are ignored).

## Library usage

```python
from freeproxieslight import FreeProxies, Source

harvester = FreeProxies.from_file("domains.txt", timeout=8, max_workers=50)
valid = harvester.harvest(out_file="proxies_checked.txt")

for p in valid:
    print(p.address, p.latency, "anon" if p.anonymous else "transparent")

# A custom parser lets you add a differently shaped source without core changes
def parse_json_api(text):
    import json
    from freeproxieslight import Proxy
    return [Proxy(r["ip"], str(r["port"])) for r in json.loads(text)]

FreeProxies([
    Source("https://www.sslproxies.org/"),
    Source("https://example.com/api/proxies", parser=parse_json_api),
]).harvest()
```

## Configuration

Defaults live at the top of `freeproxieslight/core.py`: `DEFAULT_TIMEOUT`,
`DEFAULT_MAX_WORKERS`, and `CHECK_ENDPOINTS` (the nonce/anonymity endpoints).
`timeout` and `max_workers` are also settable per run / per CLI invocation.

## Testing

```bash
python -m unittest discover -s tests -v
```

No dependencies or virtualenv needed — the suite mocks all network I/O and
resolves fixtures relative to the test file, so it runs from any directory.
