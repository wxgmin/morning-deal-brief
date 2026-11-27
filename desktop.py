"""Windows launcher for the browser dashboard and the existing collection CLI."""

import argparse
import logging
import sys
import webbrowser

from src.paths import DATA_DIR


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["cli"]:
        from src.main import main as run_cli

        return run_cli(argv[1:])

    parser = argparse.ArgumentParser(description="Open the Morning Deal Brief dashboard")
    parser.add_argument("--port", type=int, default=5000, help="Local port (default: 5000)")
    parser.add_argument("--no-browser", action="store_true", help="Print the dashboard address without opening it")
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    from waitress import create_server

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    url = f"http://127.0.0.1:{args.port}/"
    try:
        from app import app

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        server = create_server(app, host="127.0.0.1", port=args.port, threads=4)
    except OSError as exc:
        print(f"Could not start Morning Deal Brief at {url}\n{exc}", file=sys.stderr)
        print("If another copy is open, use its existing browser tab or close it and try again.", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Check your settings in {DATA_DIR}\n{exc}", file=sys.stderr)
        return 1

    print(f"Morning Deal Brief is ready at {url}", flush=True)
    print("Keep this window open while using the app. Press Ctrl+C to exit.", flush=True)
    try:
        if not args.no_browser:
            try:
                webbrowser.open(url)
            except webbrowser.Error:
                print(f"Open {url} in your browser.", flush=True)
        server.run()
    finally:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
