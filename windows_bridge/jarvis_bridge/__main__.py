"""CLI del puente:  python -m jarvis_bridge
[--print-token | --rotate-token | --init-config]"""

from __future__ import annotations

import argparse
import ipaddress
import sys
import time
from pathlib import Path

from jarvis_bridge.security import DpapiProtector, Policy, TokenStore
from jarvis_bridge.server import (
    DEFAULT_PORT,
    BridgeServer,
    data_dir,
    detect_wsl_interface,
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="jarvis_bridge")
    ap.add_argument(
        "--host", default="auto", help="IP de escucha (auto = interfaz WSL)"
    )
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument(
        "--print-token", action="store_true", help="descifra e imprime el token"
    )
    ap.add_argument("--rotate-token", action="store_true")
    ap.add_argument(
        "--init-config",
        action="store_true",
        help="escribe bridge_config.json por defecto",
    )
    ap.add_argument(
        "--wait-wsl",
        type=float,
        default=20.0,
        help="segundos esperando al adaptador WSL",
    )
    args = ap.parse_args(argv)

    d = data_dir()
    store = TokenStore(d / "token.bin", DpapiProtector())
    cfg_path = d / "bridge_config.json"

    if args.rotate_token:
        store.rotate()
        print("token rotado")
        return 0
    if args.print_token:
        sys.stdout.write(store.load_or_create())
        return 0
    if args.init_config:
        if not cfg_path.exists():
            Policy.default().save(cfg_path)
        print(cfg_path)
        return 0

    if not cfg_path.exists():
        Policy.default().save(cfg_path)
    policy = Policy.load(cfg_path)
    token = store.load_or_create()

    host, networks = args.host, []
    if host == "auto":
        info = None
        deadline = time.monotonic() + args.wait_wsl
        while info is None and time.monotonic() < deadline:
            info = detect_wsl_interface()
            if info is None:
                time.sleep(1.0)
        if info is None:
            print("No hay adaptador WSL: se escucha solo en 127.0.0.1", file=sys.stderr)
            host = "127.0.0.1"
        else:
            host = info[0]
            networks.append(ipaddress.ip_network(f"{info[0]}/{info[1]}", strict=False))
    if host in ("127.0.0.1", "::1"):
        networks.append(ipaddress.ip_network("127.0.0.0/8"))
    if host == "0.0.0.0":  # nunca: seria exponer el puente a la red
        print("Rechazado: el puente no escucha en 0.0.0.0", file=sys.stderr)
        return 2
    if not networks:
        networks.append(ipaddress.ip_network(f"{host}/32"))

    from jarvis_bridge import win32

    srv = BridgeServer(policy, token, win32, networks, d / "logs" / "bridge.jsonl")
    http = srv.make_http_server(host, args.port)
    srv.log(
        {
            "event": "start",
            "bind": f"{host}:{args.port}",
            "networks": [str(n) for n in networks],
        }
    )
    print(
        f"JARVIS bridge escuchando en http://{host}:{args.port} (datos en {Path(d)})",
        flush=True,
    )
    try:
        http.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.log({"event": "stop"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
