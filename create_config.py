import getpass
import ipaddress
import json
import os
import secrets
import sys
import tempfile
from pathlib import Path

from werkzeug.security import generate_password_hash


def main():
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "/etc/homedash/config.json")
    if destination.exists():
        raise SystemExit(f"Configuration already exists; refusing to overwrite: {destination}")
    username = input("Dashboard username [admin]: ").strip() or "admin"
    password = getpass.getpass("Dashboard password (minimum 12 characters): ")
    if len(password) < 12:
        raise SystemExit("Password must be at least 12 characters.")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match.")

    print("Enter allowed client networks in CIDR notation (for example 192.168.1.0/24).")
    print("Use only your actual trusted LAN network. Enter one network per line; blank line ends.")
    networks = []
    while True:
        value = input("Allowed network: ").strip()
        if not value:
            break
        try:
            networks.append(str(ipaddress.ip_network(value, strict=False)))
        except ValueError as error:
            raise SystemExit(f"Invalid network: {error}")
    if not networks:
        raise SystemExit("At least one allowed network is required.")

    config = {
        "secret_key": secrets.token_hex(32),
        "user": username,
        "password_hash": generate_password_hash(password),
        "allowed_networks": networks,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".config-", dir=destination.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(config, file, indent=2)
            file.write("\n")
        os.chmod(temporary, 0o600)
        os.link(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
    os.unlink(temporary)
    print(f"Configuration saved to {destination}")


if __name__ == "__main__":
    main()
