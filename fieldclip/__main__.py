# -*- coding: utf-8 -*-
"""
python -m fieldclip [--profile NAME|PATH] [--validate-profile [NAME|PATH]]
                    [--list-profiles] [--version]

--profile picks the profile for this run and remembers it for the next one.
"""
import argparse
import sys

from . import __version__
from . import settings as S


def _parse(argv):
    p = argparse.ArgumentParser(
        prog="fieldclip",
        description="Rename, organise, verify and export field videos.")
    p.add_argument("--profile", metavar="NAME|PATH",
                   help="profile to use (a file in profiles/ or a path)")
    p.add_argument("--validate-profile", nargs="?", const="", metavar="NAME|PATH",
                   help="check a profile and print example names, then exit")
    p.add_argument("--list-profiles", action="store_true",
                   help="list the profiles found in profiles/ and exit")
    p.add_argument("--version", action="version", version=f"FieldClip {__version__}")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)

    if args.list_profiles:
        for path in S.list_profiles():
            print(path)
        return 0

    if args.validate_profile is not None:
        target = args.validate_profile or args.profile or ""
        if target and not S.find_profile(target):
            print(f"INVALID: no profile named {target!r}")
            return 1
        return S._main([target] if target else [])

    user_cfg = S.load_user_config()
    if args.profile and not S.find_profile(args.profile):
        print(f"No profile named {args.profile!r}. Available:")
        for path in S.list_profiles():
            print("  ", path)
        return 1
    path = S.choose_profile_path(["--profile", args.profile] if args.profile else [],
                                 user_cfg)
    try:
        S.set_active(S.load_profile(path))
    except S.ProfileError as e:
        print(f"Profile error: {e}")
        return 1
    if path and user_cfg.get("profile") != path:
        user_cfg["profile"] = path
        S.save_user_config(user_cfg)

    from . import app          # imports Tk; only now that the profile is set
    return app.main()


if __name__ == "__main__":
    sys.exit(main())
