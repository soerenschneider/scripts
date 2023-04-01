#!/bin/env python3

import argparse
import io
import json
import logging
import os
import re
import sys
import shutil
import subprocess
import typing

from abc import ABC
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# env var keys
ENV_RESTIC_TARGETS = "RESTIC_TARGETS"
ENV_RESTIC_EXCLUDE_FILE = "RESTIC_EXCLUDE_FILE"
ENV_RESTIC_REPOSITORY = "RESTIC_REPOSITORY"
ENV_RESTIC_BACKUP_ID = "RESTIC_BACKUP_ID"
ENV_RESTIC_EXCLUDE_ITEMS = "RESTIC_EXCLUDE_ITEMS"
ENV_RESTIC_TYPE = "_RESTIC_TYPE"
ENV_MARIADB_CONTAINER_NAME = "MARIADB_CONTAINER_NAME"
ENV_MARIADB_PASSWORD = "MARIADB_PASSWORD"
ENV_MARIADB_USER = "MARIADB_USER"

ARG_SPLIT_TOKEN=","

# time to wait for the backup process to finish until cancelling it
BACKUP_TIMEOUT_SECONDS = 7200

# prefix for all the metrics we're writing
METRIC_PREFIX = "restic_backup"

# the json fields output of the restic 'backup' cmd as keys with a nice suffix and a help text as tuple values
RESTIC_METRICS = {
    "files_new": ("_total", "New files created with this snapshot"),
    "files_changed": ("_total", "Files changed with this snapshot"),
    "files_unmodified": ("_total", "Amount of unmodified files with this snapshot"),
    "dirs_new": ("_total", "Newly created directories with this snapshot"),
    "dirs_changed": ("_total", "Changed directories with this snapshot"),
    "dirs_unmodified": ("_total", "Unmodified directories with this snapshot"),
    "data_blobs": ("_total", "Data blobs of the snapshot"),
    "tree_blobs": ("_total", "Tree blobs of the snapshot"),
    "data_added": ("_bytes_total", "Total bytes added during this snapshot"),
    "total_files_processed": ("", "Files processed with this snapshot"),
    "total_bytes_processed": ("", "Bytes processed with this snapshot"),
    "total_duration": ("_seconds", "Total duration of this snapshot"),
}

# additional metrics of this wrapper
INTERNAL_METRICS = {
    "start_time": ("_seconds", "Start time of the backup process"),
    "success": ("_bool", "Boolean indicating the success of the backup"),
    "exporter_errors": ("_bool", "Exporter errors unrelated to restic"),
}


class ResticError(Exception):
    pass


class BackupImpl(ABC):
    def run_backup(self) -> Optional[List[bytes]]:
        pass


class MariaDbBackup(BackupImpl):
    def __init__(self, user=None, password=None, container_name=None):
        if not user:
            self._user = os.getenv(ENV_MARIADB_USER)
        else:
            self._user = user

        if not password:
            self._password = os.getenv(ENV_MARIADB_PASSWORD)
        else:
            self._password = password

        if not container_name:
            self._container_name = os.getenv(ENV_MARIADB_CONTAINER_NAME)
        else:
            self._container_name = container_name

    def run_backup(self) -> Optional[List[bytes]]:
        mysql_dump_cmd = ["mysqldump", "-u", self._user, f"-p{self._password}", "--all-databases" ]
        if self._container_name:
            mysql_dump_cmd = ["docker", "exec", self._container_name, "mysqldump", "-u", self._user, f"-p{self._password}", "--all-databases" ]

        p1 = subprocess.Popen(mysql_dump_cmd, stdout=subprocess.PIPE)
        backup_date = datetime.utcnow().strftime('%Y%m%d-%H%M%S')
        restic_cmd = ["restic", "--json", "backup", "--stdin", "--stdin-filename", f"database_dump-{backup_date}.sql"]
        p2 = subprocess.Popen(restic_cmd, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout, stderr = p2.communicate()
        p2.wait(BACKUP_TIMEOUT_SECONDS)
        if p2.returncode != 0:
            logging.error("Backup was not successful: %s", stderr)
            raise ResticError(stderr)
        logging.info("Backup was successful!")
        return stdout.splitlines()[-1]


class DirectoryBackup(BackupImpl):
    def __init__(self, repo: str, dirs: str, exclude_file: str = None, exclude_items: str = None):
        if not repo:
            raise ValueError("no repo provided")

        if not dirs:
            raise ValueError("No targets to backup defined")

        dirs = [os.path.expanduser(repo) for repo in dirs.split(ARG_SPLIT_TOKEN)]
        for target in dirs:
            if not Path(target).exists():
                raise ValueError(f"One of the targets does not exist: {target}")

        self._repo = repo
        if isinstance(dirs, str):
            self._dirs = [dirs]
        else:
            self._dirs = dirs

        self._exclude_file = exclude_file
        if exclude_items:
            self._exclude_items = [os.path.expanduser(item) for item in exclude_items.split(ARG_SPLIT_TOKEN)]
        else:
            self._exclude_items = []

    def run_backup(self) -> Optional[List[bytes]]:
        """ Performs the backup operation. Returns the JSONified stdout of the restic backup call. """
        # skeleton of the backup cmd we're invoking
        restic_base_cmd = ["restic", "-q", "--json", "backup", "--one-file-system"]
        if self._exclude_file:
            restic_base_cmd += [f"--exclude-file={self._exclude_file}"]

        if self._exclude_items:
            for item in self._exclude_items:
                restic_base_cmd += [f"--exclude={item}"]

        command = restic_base_cmd + ["-r", self._repo] + self._dirs
        logging.info("Starting backup using command: %s", command)
        with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
            stdout, stderr = proc.communicate()
            proc.wait(BACKUP_TIMEOUT_SECONDS)
            if proc.returncode != 0:
                logging.error("Backup was not successful: %s", stderr)
                raise ResticError(stderr)

            logging.info("Backup was successful!")
            return stdout.splitlines()[-1]


def write_metrics(metrics_data: io.StringIO, target_dir: Path, backup_id: str) -> None:
    """ Writes the metrics file to the target directory. """
    target_file = f"{METRIC_PREFIX}_{backup_id}.prom"
    tmp_file = f"{target_file}.{os.getpid()}"
    try:
        # we're kind of defeating the purpose of the stream here
        with open(tmp_file, mode="w", encoding="utf-8") as fd:
            print(metrics_data.getvalue(), file=fd)
        logging.info("Moving temporary metric file '%s' to '%s'", tmp_file, target_dir / target_file)
        shutil.move(tmp_file, target_dir / target_file)
    finally:
        metrics_data.close()


def format_data(output: dict, identifier: str) -> io.StringIO:
    """ Poor man's Open Metrics formatting of the JSON output. """
    buffer = io.StringIO()
    for metric in RESTIC_METRICS:
        if metric not in output:
            logging.error("Excepted metric to be around but wasn't: %s", metric)
            output["exporter_errors"] += 1
        else:
            value = output[metric]
            buffer.write(f"# HELP {METRIC_PREFIX}_{metric}{RESTIC_METRICS[metric][0]} {RESTIC_METRICS[metric][1]}\n")
            buffer.write(f"# TYPE {METRIC_PREFIX}_{metric}{RESTIC_METRICS[metric][0]} gauge\n")
            buffer.write(f'{METRIC_PREFIX}_{metric}{RESTIC_METRICS[metric][0]}{{repo="{identifier}"}} {value}\n')

    for metric in INTERNAL_METRICS:
        value = output[metric]
        buffer.write(f"# HELP {METRIC_PREFIX}_{metric}{INTERNAL_METRICS[metric][0]} {INTERNAL_METRICS[metric][1]}\n")
        buffer.write(f"# TYPE {METRIC_PREFIX}_{metric}{INTERNAL_METRICS[metric][0]} gauge\n")
        buffer.write(f'{METRIC_PREFIX}_{metric}{INTERNAL_METRICS[metric][0]}{{repo="{identifier}"}} {value}\n')

    return buffer


def validate_args(args: argparse.Namespace) -> None:
    """ Validates the parsed arguments. As we're relying heavily on env vars, we can't use
        argparse functionality directly for this. """
    # check repo parameter
    if not args.repo:
        raise ValueError("No repository defined")
    args.repo = os.path.expanduser(args.repo)

    # check backup id
    if not args.backup_id:
        raise ValueError("No backup_id given")
    args.backup_id = re.sub(r"[^\w\s]", "", args.backup_id)

    # check metric dir
    if not Path(args.metric_dir).exists():
        raise ValueError(f"Dir to write metrics to does not exist: '{args.metric_dir}' ")


def parse_args() -> argparse.Namespace:
    """ Parses the arguments and returns the parsed namespace. """
    parser = argparse.ArgumentParser()
    parser.add_argument("-r", "--repo", default=os.environ.get(ENV_RESTIC_REPOSITORY), help="The restic repository")
    parser.add_argument("--targets", default=os.environ.get(ENV_RESTIC_TARGETS), help=f"The targets to include in the snapshot. Provide as a single string, separated by '{ARG_SPLIT_TOKEN}'")
    parser.add_argument("-t", "--type", default=os.environ.get(ENV_RESTIC_TYPE), help="The type defines what exactly to backup")
    parser.add_argument("-i", "--id", dest="backup_id", default=os.environ.get(ENV_RESTIC_BACKUP_ID), help="An identifier for this backup")
    parser.add_argument("-m", "--metric-dir", default="/var/lib/node_exporter", help="Dir to write metrics to")
    parser.add_argument("-e", "--exclude-items", default=os.environ.get(ENV_RESTIC_EXCLUDE_ITEMS), help=f"Item(s) to exclude from backup. Separate with '{ARG_SPLIT_TOKEN}'")
    parser.add_argument("-ef", "--exclude-file", default=os.environ.get(ENV_RESTIC_EXCLUDE_FILE), help="Path to file containing exclude patterns")
    return parser.parse_args()


def get_backup_impl(args: argparse.Namespace) -> BackupImpl:
    if not args.type:
        logging.warning("no backup type specified, falling back to 'directory'")
        args.type = "directory"

    if args.type.lower() == "directory":
        logging.info("Using 'directory' backup impl")
        return DirectoryBackup(repo=args.repo, dirs=args.targets, exclude_file=args.exclude_file, exclude_items=args.exclude_items)

    if args.type.lower() == "mariadb":
        logging.info("Using 'mariadb' backup impl")
        return MariaDbBackup()

    raise ValueError(f"Unknown backup type '{args.type}'")


def main() -> None:
    """ Main does mainly main things. """
    start_time = datetime.utcnow().timestamp()
    args = parse_args()
    success = False
    json_output = {}
    impl = get_backup_impl(args)
    try:
        validate_args(args)
        stdout = impl.run_backup()
        json_output = json.loads(stdout)
        success = True
    except NameError as err:
        logging.error("Can not start the backup: %s", err.args[0])
        sys.exit(1)
    except ResticError as err:
        logging.error("Failed to run backup: %s", err)

    # add exporter metrics
    json_output["success"] = int(success)
    json_output["exporter_errors"] = 0
    json_output["start_time"] = start_time

    metrics_data = format_data(json_output, args.backup_id)
    target_dir = Path(args.metric_dir)
    write_metrics(metrics_data, target_dir, args.backup_id)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.INFO)
    main()
