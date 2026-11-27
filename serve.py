"""Production server for Windows (and Linux): waitress.

    python serve.py              check readiness, then serve
    python serve.py --skip-checks   serve even if a check FAILs (not recommended)

Settings from .env: HOST (default 127.0.0.1: only this computer and a tunnel or reverse
proxy on it can connect), PORT (default 8000), WAITRESS_THREADS (default 8).
Stop with Ctrl+C.
"""
import os
import sys

from waitress import serve

from app import create_app
from app.cli import print_readiness

MAX_BODY_BYTES = 6 * 1024 * 1024  # face-check frames fit easily; anything bigger is refused early


def main():
    app = create_app()
    if "--skip-checks" not in sys.argv:
        from app.services.ops_service import readiness

        with app.app_context():
            print("Readiness:")
            if not print_readiness(readiness()):
                print("Not starting. Fix the problems above, or use --skip-checks.")
                sys.exit(1)

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    threads = int(os.environ.get("WAITRESS_THREADS", "8"))
    app.logger.info("serving on http://%s:%s with %s threads (%s settings)", host, port, threads, app.config["CONFIG_NAME"])
    print(f"Serving on http://{host}:{port}  (Ctrl+C to stop)")
    serve(
        app,
        host=host,
        port=port,
        threads=threads,
        channel_timeout=60,          # drop connections idle for a minute
        max_request_body_size=MAX_BODY_BYTES,
        ident="",                    # do not advertise the server software
    )


if __name__ == "__main__":
    main()