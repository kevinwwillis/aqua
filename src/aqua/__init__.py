"""aqua: Agent Questions & User Answers."""


def main() -> None:
    from .cli import main as cli_main

    raise SystemExit(cli_main())


def serve() -> None:
    from .server import main as server_main

    server_main()
