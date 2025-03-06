#!/usr/bin/env python3

import argparse
import io
import json
import logging
import os
import re
import sys
import shutil
import subprocess

from abc import ABC
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import requests

# env var keys
ENV_RESTIC_TARGETS = "RESTIC_TARGETS"
ENV_RESTIC_EXCLUDE_FILE = "RESTIC_EXCLUDE_FILE"
ENV_RESTIC_REPOSITORY = "RESTIC_REPOSITORY"
ENV_RESTIC_BACKUP_ID = "RESTIC_BACKUP_ID"
ENV_RESTIC_EXCLUDE_ITEMS = "RESTIC_EXCLUDE_ITEMS"
ENV_RESTIC_TYPE = "_RESTIC_TYPE"
ENV_RESTIC_HOSTNAME = "RESTIC_HOSTNAME"
ENV_PUSHGATEWAY_URL = "PUSHGATEWAY_URL"
ENV_METRIC_LABELS = "METRIC_LABELS"
ENV_MARIADB_CONTAINER_NAME = "MARIADB_CONTAINER_NAME"
ENV_MARIADB_PASSWORD = "MARIADB_PASSWORD"
ENV_MARIADB_USER = "MARIADB_USER"
ENV_MARIADB_HOST = "MARIADB_HOST"
ENV_POSTGRES_CONTAINER_NAME = "POSTGRES_CONTAINER_NAME"
ENV_POSTGRES_PASSWORD = "POSTGRES_PASSWORD"
ENV_POSTGRES_HOST = "POSTGRES_HOST"
ENV_POSTGRES_USER = "POSTGRES_USER"

ARG_SPLIT_TOKEN = ","

# time to wait for the backup process to finish until cancelling it
BACKUP_TIMEOUT_SECONDS = 7200

DEFAULT_JOB_NAME = "restic-backup"

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


class PostgresDbBackup(BackupImpl):
    def __init__(self,
                 user: str = None,
                 password: str = None,
                 postgres_host: str = None,
                 hostname: str = None,
                 container_name: str = None):

        if not user:
            self._user = os.getenv(ENV_POSTGRES_USER)
        else:
            self._user = user

        if not password:
            self._password = os.getenv(ENV_POSTGRES_PASSWORD)
        else:
            self._password = password

        if not postgres_host:
            self._postgres_host = os.getenv(ENV_POSTGRES_HOST)
        else:
            self._postgres_host = postgres_host

        if not hostname:
            self._hostname = os.getenv(ENV_RESTIC_HOSTNAME)
        else:
            self._hostname = hostname

        if not container_name:
            self._container_name = os.getenv(ENV_POSTGRES_CONTAINER_NAME)
        else:
            self._container_name = container_name

    def run_backup(self) -> Optional[List[bytes]]:
        pg_dump_cmd = ["pg_dumpall", "--clean", f"--username={self._user}"]
        if self._postgres_host:
            pg_dump_cmd.append(f"--host={self._postgres_host}")

        if self._container_name:
            pg_dump_cmd = ["docker", "exec", self._container_name] + pg_dump_cmd

        p1 = subprocess.Popen(pg_dump_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        restic_cmd = ["restic", "--json"]
        if self._hostname:
            restic_cmd.append(f"--host={self._hostname}")
        restic_cmd += ["backup", "--compression=max", "--stdin", "--stdin-filename", "database_dump.sql"]

        p2 = subprocess.Popen(restic_cmd, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout, stderr = p2.communicate()
        p2.wait(BACKUP_TIMEOUT_SECONDS)
        if p2.returncode != 0:
            logging.error("Backup was not successful: %s", stderr)
            raise ResticError(stderr)

        logging.info("Backup was successful!")
        return stdout.splitlines()[-1]


class MariaDbBackup(BackupImpl):
    def __init__(self,
                 host: str = None,
                 user: str = None,
                 password: str = None,
                 hostname: str = None,
                 container_name: str = None):
        if not host:
            self._mariadb_host = os.getenv(ENV_MARIADB_HOST)
        else:
            self._mariadb_host = host

        if not user:
            self._user = os.getenv(ENV_MARIADB_USER)
        else:
            self._user = user

        if not password:
            self._password = os.getenv(ENV_MARIADB_PASSWORD)
        else:
            self._password = password

        if not hostname:
            self._hostname = os.getenv(ENV_RESTIC_HOSTNAME)
        else:
            self._hostname = hostname

        if not container_name:
            self._container_name = os.getenv(ENV_MARIADB_CONTAINER_NAME)
        else:
            self._container_name = container_name

    def run_backup(self) -> Optional[List[bytes]]:
        if not os.getenv("MYSQL_PWD"):
            os.environ["MYSQL_PWD"] = self._password

        mysql_dump_cmd = ["mariadb-dump", f"--user={self._user}", "--all-databases"]
        if self._mariadb_host:
            mysql_dump_cmd.append(f"--host={self._mariadb_host}")
        else:
            # if we connect to localhost, don't try to verify the tls cert
            mysql_dump_cmd.append("--ssl-verify-server-cert=false")

        if self._container_name:
            mysql_dump_cmd = ["docker", "exec", f"-e=MYSQL_PWD={self._password}", self._container_name] + mysql_dump_cmd

        p1 = subprocess.Popen(mysql_dump_cmd, stdout=subprocess.PIPE)
        restic_cmd = ["restic", "--compression=max", "--json", "backup", "--stdin", "--stdin-filename"]
        if self._hostname:
            restic_cmd.append(f"--host={self._hostname}")
        restic_cmd.append("database_dump.sql")

        p2 = subprocess.Popen(restic_cmd, stdin=p1.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        stdout, stderr = p2.communicate()
        p2.wait(BACKUP_TIMEOUT_SECONDS)
        if p2.returncode != 0:
            logging.error("Backup was not successful: %s", stderr)
            raise ResticError(stderr)
        logging.info("Backup was successful!")
        return stdout.splitlines()[-1]


class DirectoryBackup(BackupImpl):
    def __init__(self,
                 repo: str,
                 dirs: str,
                 exclude_file: str = None,
                 exclude_items: str = None,
                 hostname: str = None):
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

        if not hostname:
            self._hostname = os.getenv(ENV_RESTIC_HOSTNAME)
        else:
            self._hostname = hostname

    def run_backup(self) -> Optional[List[bytes]]:
        """ Performs the backup operation. Returns the JSONified stdout of the restic backup call. """
        # skeleton of the backup cmd we're invoking
        restic_base_cmd = ["restic", "-q", "--json", "backup", "--one-file-system"]
        if self._exclude_file:
            restic_base_cmd += [f"--exclude-file={self._exclude_file}"]

        if self._exclude_items:
            for item in self._exclude_items:
                restic_base_cmd += [f"--exclude={item}"]

        if self._hostname:
            restic_base_cmd.append(f"--host={self._hostname}")

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


def restic_upsert_repo():
    if not restic_repo_exists():
        restic_init_repo()


def restic_repo_exists() -> bool:
    command = ["restic", "snapshots", "--json"]
    logging.info("Checking for existing snapshots")
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        _, stderr = proc.communicate()
        proc.wait(BACKUP_TIMEOUT_SECONDS)
        if proc.returncode != 0:
            logging.error("Listing snapshots was not successful, this can either indicate the repository does not exist yet OR there's a problem accessing the repository (server error, credentials, etc.): %s", stderr)
            return False

        logging.info("Repository exists")
        return True


def restic_init_repo() -> bool:
    command = ["restic", "init"]
    logging.info("Trying to initialize repo")
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        stdout, stderr = proc.communicate()
        proc.wait(BACKUP_TIMEOUT_SECONDS)
        if proc.returncode != 0:
            logging.error("Initiliazing repo not successful: %s", stderr)
            return False

        return True


def write_metrics(metrics_data: io.StringIO, target_dir: Path, backup_id: str) -> None:
    """ Writes the metrics file to the target directory. """
    backup_id = re.sub(r"[^\w\s]", "", backup_id)
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


def push_metrics(pushgateway_url: str, metric_data: io.StringIO, backup_id: str = None) -> None:
    """ Pushes metrics to Prometheus pushgateway. """
    if not backup_id:
        backup_id = DEFAULT_JOB_NAME

    api_endpoint = f"{pushgateway_url}/metrics/job/restic_backup/instance/{backup_id}"
    data = metric_data.getvalue()
    response = requests.post(api_endpoint, data=data, timeout=30)
    response.raise_for_status()
    logging.info("Successfully pushed metrics to pushgateway %s", pushgateway_url)


def _format_labels(input_string: str) -> str:
    if not input_string:
        return ""

    key_value_pairs = input_string.split(',')
    formatted_pairs = [f'{pair.split("=")[0]}="{pair.split("=")[1]}"' for pair in key_value_pairs]
    formatted_string = ','.join(formatted_pairs)
    return formatted_string


def format_data(output: dict, identifier: str, metric_labels: str = None) -> io.StringIO:
    """ Poor man's Open Metrics formatting of the JSON output. """
    additional_labels = _format_labels(metric_labels)
    if additional_labels == "":
        labels = f'{{repo="{identifier}"}}'
    else:
        labels = f'{{repo="{identifier},{additional_labels}"}}'

    buffer = io.StringIO()
    for metric in RESTIC_METRICS:
        if metric not in output:
            logging.error("Excepted metric to be around but wasn't: %s", metric)
            output["exporter_errors"] += 1
        else:
            value = output[metric]
            buffer.write(f"# HELP {METRIC_PREFIX}_{metric}{RESTIC_METRICS[metric][0]} {RESTIC_METRICS[metric][1]}\n")
            buffer.write(f"# TYPE {METRIC_PREFIX}_{metric}{RESTIC_METRICS[metric][0]} gauge\n")
            buffer.write(f'{METRIC_PREFIX}_{metric}{RESTIC_METRICS[metric][0]}{labels} {value}\n')

    for metric in INTERNAL_METRICS:
        value = output[metric]
        buffer.write(f"# HELP {METRIC_PREFIX}_{metric}{INTERNAL_METRICS[metric][0]} {INTERNAL_METRICS[metric][1]}\n")
        buffer.write(f"# TYPE {METRIC_PREFIX}_{metric}{INTERNAL_METRICS[metric][0]} gauge\n")
        buffer.write(f'{METRIC_PREFIX}_{metric}{INTERNAL_METRICS[metric][0]}{labels} {value}\n')

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

    if not args.pushgateway_url and not Path(args.metric_dir).exists():
        raise ValueError(f"Dir to write metrics to does not exist: '{args.metric_dir}' ")


def parse_args() -> argparse.Namespace:
    """ Parses the arguments and returns the parsed namespace. """
    parser = argparse.ArgumentParser()
    parser.add_argument("-r", "--repo", default=os.environ.get(ENV_RESTIC_REPOSITORY), help="The restic repository")
    parser.add_argument("--targets", default=os.environ.get(ENV_RESTIC_TARGETS), help=f"The targets to include in the snapshot. Provide as a single string, separated by '{ARG_SPLIT_TOKEN}'")
    parser.add_argument("-t", "--type", default=os.environ.get(ENV_RESTIC_TYPE), help="The type defines what exactly to backup")
    parser.add_argument("-i", "--id", dest="backup_id", default=os.environ.get(ENV_RESTIC_BACKUP_ID), help="An identifier for this backup")
    parser.add_argument("--hostname", default=os.environ.get(ENV_RESTIC_HOSTNAME), help="Set the hostname for restic. This is useful if run in docker machines.")
    parser.add_argument("-e", "--exclude-items", default=os.environ.get(ENV_RESTIC_EXCLUDE_ITEMS), help=f"Item(s) to exclude from backup. Separate with '{ARG_SPLIT_TOKEN}'")
    parser.add_argument("-ef", "--exclude-file", default=os.environ.get(ENV_RESTIC_EXCLUDE_FILE), help="Path to file containing exclude patterns")

    parser.add_argument("-d", "--metric-dir", default="/var/lib/node_exporter", help="Dir to write metrics to")
    parser.add_argument("-p", "--pushgateway-url", default=os.environ.get(ENV_PUSHGATEWAY_URL), help="Prometheus Pushgateway URL to send metrics to")
    parser.add_argument("-l", "--metric-labels", default=os.environ.get(ENV_METRIC_LABELS), help="Label(s) to add to metrics. Separate with '{ARG_SPLIT_TOKEN}'")

    return parser.parse_args()


def get_backup_impl(args: argparse.Namespace) -> BackupImpl:
    if not args.type:
        logging.warning("no backup type specified, falling back to 'directory'")
        args.type = "directory"

    if args.type.lower() == "postgres":
        logging.info("Using 'postgres' backup impl")
        return PostgresDbBackup()

    if args.type.lower() == "directory":
        logging.info("Using 'directory' backup impl")
        return DirectoryBackup(repo=args.repo,
                               dirs=args.targets,
                               exclude_file=args.exclude_file,
                               exclude_items=args.exclude_items,
                               hostname=args.hostname)

    if args.type.lower() == "mariadb":
        logging.info("Using 'mariadb' backup impl")
        return MariaDbBackup()

    raise ValueError(f"Unknown backup type '{args.type}'")


def _log_backup_data(output: dict):
    try:
        humanized = humanize_bytes(output["total_bytes_processed"])
        logging.info("%d/%d new files/dirs, %d/%d changed files/dirs, processed %s of data", output["files_new"], output["dirs_new"], output["files_changed"], output["dirs_changed"], humanized)
    except KeyError:
        logging.warning("Missing restic metric data")


def humanize_bytes(num_bytes: int):
    """ Convert a number of bytes into a human-readable format. """
    suffixes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']

    index = 0
    while num_bytes >= 1024 and index < len(suffixes) - 1:
        num_bytes /= 1024.0
        index += 1

    # Format the number to two decimal points
    return f"{num_bytes:.2f}{suffixes[index]}"


def setup_logging(debug=False) -> None:
    """ Set up the logging configuration. """
    loglevel = logging.INFO
    if debug:
        loglevel = logging.DEBUG
    logging.basicConfig(level=loglevel, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    """ Runs restic. """
    setup_logging()
    start_time = datetime.utcnow().timestamp()
    args = parse_args()
    success = False
    json_output = {}
    impl = get_backup_impl(args)
    try:
        validate_args(args)
        restic_upsert_repo()
        stdout = impl.run_backup()
        json_output = json.loads(stdout)
        success = True
    except NameError as err:
        logging.error("Can not start the backup: %s", err.args[0])
        sys.exit(1)
    except ValueError as err:
        logging.error("Wrong configuration: %s", err)
        sys.exit(1)
    except ResticError as err:
        logging.error("Failed to run backup: %s", err)

    # add exporter metrics
    json_output["success"] = int(success)
    json_output["exporter_errors"] = 0
    json_output["start_time"] = start_time

    metrics_data = format_data(json_output, args.backup_id, args.metric_labels)
    pushgateway_success = False
    if args.pushgateway_url:
        try:
            push_metrics(args.pushgateway_url, metrics_data)
            pushgateway_success = True
        except requests.exceptions.HTTPError as e:
            logging.error(f"Could not push metrics to pushgateway {args.pushgateway_url}: %s", e)

    if not args.pushgateway_url or not pushgateway_success:
        target_dir = Path(args.metric_dir)
        write_metrics(metrics_data, target_dir, args.backup_id)

    if not success:
        sys.exit(1)

    _log_backup_data(json_output)


if __name__ == "__main__":
    logging.basicConfig(format="%(asctime)-15s %(levelname)-8s %(message)s", level=logging.INFO)
    main()
