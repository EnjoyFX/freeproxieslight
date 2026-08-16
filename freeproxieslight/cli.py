"""Command-line interface for freeproxieslight."""
import argparse
import json
import logging
import sys
from dataclasses import asdict

from . import __version__
from .core import (DEFAULT_DOMAINS_FILE, DEFAULT_MAX_WORKERS,
                   DEFAULT_OUTPUT_FILE, DEFAULT_TIMEOUT, FreeProxies, Source,
                   _read_url_lines, parse_socks_table, parse_table_proxies)

logger = logging.getLogger('freeproxieslight')


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='freeproxieslight',
        description='Harvest and validate free proxies (standard library '
                    'only, no external dependencies).')
    p.add_argument('sources', nargs='*',
                   help='source URLs; if omitted, read from --sources-file')
    p.add_argument('-s', '--sources-file', default=DEFAULT_DOMAINS_FILE,
                   help=f'file with one source URL per line '
                        f'(default: {DEFAULT_DOMAINS_FILE})')
    p.add_argument('-o', '--output', default=DEFAULT_OUTPUT_FILE,
                   help=f"output file (default: {DEFAULT_OUTPUT_FILE}); "
                        f"use '-' for stdout")
    p.add_argument('-f', '--format', choices=('txt', 'json'), default='txt',
                   help='output format (default: txt)')
    p.add_argument('-t', '--timeout', type=int, default=DEFAULT_TIMEOUT,
                   help=f'per-request timeout in seconds '
                        f'(default: {DEFAULT_TIMEOUT})')
    p.add_argument('-w', '--workers', type=int, default=DEFAULT_MAX_WORKERS,
                   help=f'max concurrent workers '
                        f'(default: {DEFAULT_MAX_WORKERS})')
    p.add_argument('--socks', action='store_true',
                   help='parse sources as SOCKS proxy tables '
                        '(socks-proxy.net layout) and validate over SOCKS')
    p.add_argument('-q', '--quiet', action='store_true',
                   help='only log warnings and errors')
    p.add_argument('-V', '--version', action='version',
                   version=f'%(prog)s {__version__}')
    return p


def _load_sources(args):
    """Return a list of Source, or None if the sources file is missing."""
    parser = parse_socks_table if args.socks else parse_table_proxies
    if args.sources:
        return [Source(u, parser=parser) for u in args.sources]
    try:
        urls = _read_url_lines(args.sources_file)
    except FileNotFoundError:
        return None
    return [Source(u, parser=parser) for u in urls]


def _write_output(proxies, output, fmt):
    if fmt == 'json':
        payload = [{'address': p.address, **asdict(p)} for p in proxies]
        text = json.dumps(payload, indent=2) + '\n'
    else:
        text = ''.join(p.address + '\n' for p in proxies)
    if output == '-':
        sys.stdout.write(text)
    else:
        with open(output, 'w') as f:
            f.write(text)


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s')

    sources = _load_sources(args)
    if sources is None:
        logger.error('sources file not found: %s', args.sources_file)
        return 2
    if not sources:
        logger.error('no sources provided')
        return 2

    harvester = FreeProxies(sources, timeout=args.timeout,
                            max_workers=args.workers)

    # txt to a real file can stream as proxies are confirmed; json and stdout
    # need the full result set first.
    if args.format == 'txt' and args.output != '-':
        good = harvester.harvest(out_file=args.output)
    else:
        good = harvester.harvest()
        _write_output(good, args.output, args.format)

    logger.info('done: %d valid proxies', len(good))
    return 0


if __name__ == '__main__':
    sys.exit(main())
