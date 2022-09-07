#!/usr/bin/env python3

import sys
import subprocess
import os
from pathlib import Path
from typing import Optional, Dict


def get_dirs() -> Dict[str, str]:
    # todo: maybe read from json config in future
    return {
        "/mnt/wdred/media-crypt":         "/srv/files/media",
        "/mnt/wdred/photos-crypt":        "/srv/files/photos",
        "/mnt/wdred/games-crypt":         "/srv/files/games",
        "/home/soeren/.crypto/documents": "/home/soeren/docs/plain",
        "/home/soeren/.crypto/scans":     "/home/soeren/docs/scans",
    }


def find_base(dirs: Dict[str, str], file: Path) -> Optional[Path]:
    for k in dirs:
        v = dirs[k]

        if str(file).startswith(k):
            return Path(v)

    return None


def find_file_by_inode(base: Path, inode: int) -> None:
    subprocess.run(["find", base, "-inum", str(inode)])


def main():
    if len(sys.argv) < 2:
        print("Please provide a file to check, exiting")
        sys.exit(1)

    provided_file = Path(sys.argv[1])
    if not provided_file.exists():
        print(f"File '{provided_file}' does not exist, exiting")
        sys.exit(1)

    dirs = get_dirs()
    abs_file = provided_file.resolve()
    search_dir = find_base(dirs, abs_file)
    if not search_dir:
        print(f"No matching base dir defined for file '{abs_file}', exiting")
        sys.exit(1)

    inode = os.stat(abs_file).st_ino
    find_file_by_inode(search_dir, inode)


if __name__ == "__main__":
    main()
