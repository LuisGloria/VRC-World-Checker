from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

import analyzer
from vrc_api import TwoFactorRequired, VRChatClient, VRCError, config_dir, extract_world_id


def log_in(client: VRChatClient) -> None:
    #Reuse the saved session if possible
    if client.resume():
        return
    #log back in lmaooooo!!!
    username = os.environ.get("VRC_USERNAME") or input("VRChat username: ")
    password = os.environ.get("VRC_PASSWORD") or getpass.getpass("VRChat password: ")
    try:
        client.login(username, password)
    except TwoFactorRequired as two_factor_error:
        code = input("2FA code: ")
        client.verify_2fa(two_factor_error.methods, code)


def run_cli(world_text: str, print_json: bool) -> int:
    world_id = extract_world_id(world_text)
    if not world_id:
        print("That ain't a world ID (ex: wrld_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx), Check if you mistyped!", file=sys.stderr)
        return 2

    client = VRChatClient()
    try:
        log_in(client)
        blocklist = analyzer.load_blocklist(config_dir() / "blocklist.json")
        report = analyzer.fetch_and_analyze(client, world_id, blocklist)
    except VRCError as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1

    if print_json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(analyzer.render_text(report))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="assumption safety check for VRChat worlds thingamabob")
    parser.add_argument("world", nargs="?", help="world ID or link; omit to open the GUI")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text (CLI)")
    arguments = parser.parse_args()

    if arguments.world:
        return run_cli(arguments.world, arguments.json)

    import app  # imported here so the CLI keeps working without Tkinter installed lol
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
