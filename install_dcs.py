#!/usr/bin/env python3
"""
Headless DCS export script installer for Linux.

Usage:
    python install_dcs.py [--prefix /path/to/wine/prefix]

If --prefix is omitted, tries to auto-detect a Steam/Proton DCS installation.
"""

import argparse
import glob
import os
import shutil
import sys
from pathlib import Path

LUA_LINE = "local telemffblfs=require('lfs');dofile(telemffblfs.writedir()..'Scripts/TelemFFB.lua')"
DCS_DIRS = ['DCS', 'DCS.openbeta']

SCRIPT_DIR = Path(__file__).parent.resolve()
LOCAL_LUA = SCRIPT_DIR / 'export' / 'TelemFFB.lua'


def find_saved_games_in_prefix(prefix: Path) -> Path | None:
    """Find the Saved Games directory inside a Wine prefix."""
    users_dir = prefix / 'drive_c' / 'users'
    if not users_dir.exists():
        return None
    for user_dir in users_dir.iterdir():
        sg = user_dir / 'Saved Games'
        if sg.exists():
            return sg
    return None


def auto_detect_saved_games() -> Path | None:
    """Try to find DCS Saved Games via Steam/Proton paths."""
    home = Path.home()
    steam_roots = [
        home / '.local' / 'share' / 'Steam',
        home / '.steam' / 'steam',
        home / '.steam' / 'Steam',
    ]
    for steam_root in steam_roots:
        # DCS-specific Proton prefix (app ID 223750)
        sg = steam_root / 'steamapps' / 'compatdata' / '223750' / 'pfx' / 'drive_c' / 'users' / 'steamuser' / 'Saved Games'
        if sg.exists():
            return sg
        # Generic scan
        compat = steam_root / 'steamapps' / 'compatdata'
        if compat.exists():
            for app_dir in compat.iterdir():
                sg = app_dir / 'pfx' / 'drive_c' / 'users' / 'steamuser' / 'Saved Games'
                if sg.exists() and any(d.name.startswith('DCS') for d in sg.iterdir() if d.is_dir()):
                    return sg
    return None


def install_to(saved_games: Path) -> bool:
    if not LOCAL_LUA.exists():
        print(f"ERROR: Source file not found: {LOCAL_LUA}", file=sys.stderr)
        return False

    installed_any = False
    for dcs_dir in DCS_DIRS:
        dcs_path = saved_games / dcs_dir
        if not dcs_path.exists():
            continue

        scripts_dir = dcs_path / 'Scripts'
        scripts_dir.mkdir(exist_ok=True)

        export_lua = scripts_dir / 'Export.lua'
        target_lua = scripts_dir / 'TelemFFB.lua'

        # Read existing Export.lua
        try:
            export_data = export_lua.read_text(encoding='utf-8')
        except FileNotFoundError:
            export_data = ''

        # Remove any stale DLL-style lines
        lines = export_data.splitlines()
        lines = [l for l in lines if 'require("telemffb")' not in l and 'package.cpath' not in l]
        export_data = '\n'.join(lines)
        if export_data and not export_data.endswith('\n'):
            export_data += '\n'

        # Add Lua line if not present
        if LUA_LINE not in export_data:
            export_data += LUA_LINE + '\n'
            export_lua.write_text(export_data, encoding='utf-8')
            print(f"  Patched: {export_lua}")
        else:
            print(f"  Already present in: {export_lua}")

        # Copy TelemFFB.lua and patch broadcast address for Linux
        lua_src = LOCAL_LUA.read_text(encoding='utf-8')
        # On Linux a socket bound to 127.0.0.1 doesn't receive loopback broadcasts.
        # Replace the broadcast target with a direct unicast address.
        lua_src = lua_src.replace(
            'self.sock_udp:setpeername("127.255.255.255", 34380)',
            'self.sock_udp:setpeername("127.0.0.1", 34380)',
        )
        target_lua.write_text(lua_src, encoding='utf-8')
        print(f"  Installed: {target_lua} (patched broadcast → 127.0.0.1)")
        installed_any = True

    return installed_any


def main():
    parser = argparse.ArgumentParser(description='Install TelemFFB DCS export script on Linux')
    parser.add_argument('--prefix', metavar='PATH',
                        help='Wine prefix root (e.g. /home/user/mnt/DCS). '
                             'Auto-detected from Steam/Proton if omitted.')
    args = parser.parse_args()

    if args.prefix:
        prefix = Path(args.prefix)
        saved_games = find_saved_games_in_prefix(prefix)
        if saved_games is None:
            print(f"ERROR: Could not find 'Saved Games' inside prefix: {prefix}", file=sys.stderr)
            print("Expected: <prefix>/drive_c/users/<username>/Saved Games", file=sys.stderr)
            sys.exit(1)
    else:
        saved_games = auto_detect_saved_games()
        if saved_games is None:
            print("ERROR: Could not auto-detect DCS installation.", file=sys.stderr)
            print("Try: python install_dcs.py --prefix /path/to/wine/prefix", file=sys.stderr)
            sys.exit(1)

    print(f"Using Saved Games: {saved_games}")
    found = [d for d in DCS_DIRS if (saved_games / d).exists()]
    if not found:
        print(f"WARNING: No DCS folder found under {saved_games}", file=sys.stderr)
        print(f"Expected one of: {DCS_DIRS}", file=sys.stderr)
        print("Creating 'DCS' folder and installing anyway...")
        (saved_games / 'DCS').mkdir(parents=True, exist_ok=True)

    if install_to(saved_games):
        print("\nDone. Launch DCS and fly — TelemFFB effects should now work.")
    else:
        print("\nNothing was installed.", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
