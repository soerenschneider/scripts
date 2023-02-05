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

from datetime import datetime
from pathlib import Path
from typing import List, Optional

# time to wait for the backup process to finish until cancelling it
BACKUP_TIMEOUT_SECONDS = 7200

# prefix for all the metrics we're writing
METRIC_PREFIX = "restic_check"

# skeleton of the backup cmd we're invoking
RESTIC_BACKUP_CMD = ["restic", "check", "-r"]

INTERNAL_METRICS = {
    "start_time": ("_seconds", "Start time of the check process"),
    "end_time": ("_seconds", "End time of the check process"),
    "success": ("_bool", "Boolean indicating the success of the check"),
    "exporter_errors": ("_bool", "Exporter errors unrelated to restic"),
}


class ResticError(Exception):
    pass


def run_check(repo: str) -> None:
    """ Performs the backup operation. Returns the JSONified stdout of the restic backup call. """
    command = RESTIC_BACKUP_CMD + [repo]
    logging.info("Starting check using command: %s", command)
    with subprocess.Popen(command, stdin=subprocess.PIPE) as proc:
        proc.communicate()
        proc.wait(BACKUP_TIMEOUT_SECONDS)
        if proc.returncode != 0:
            logging.error("Check was not successful")
            raise ResticError()

        logging.info("Check was successful!")


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
    parser.add_argument("-r", "--repo", default=os.environ.get("RESTIC_REPOSITORY"), help="The restic repository")
    parser.add_argument("-i", "--id", dest="backup_id", default=os.environ.get("RESTIC_BACKUP_ID"), help="An identifier for this backup")
    parser.add_argument("-m", "--metric-dir", default="/var/lib/node_exporter", help="Dir to write metrics to")
    return parser.parse_args()


def main() -> None:
    """ Main does mainly main things. """
    start_time = datetime.now().timestamp()
    args = parse_args()
    success = False
    json_output = {}
    try:
        validate_args(args)
        run_check(args.repo)
        success = True
    except ValueError as err:
        logging.error("Can not start the check")
        sys.exit(1)
    except ResticError as err:
        logging.error("Failed to run check: %s", err)

    # add exporter metrics
    json_output["success"] = int(success)
    json_output["exporter_errors"] = 0
    json_output["start_time"] = start_time
    json_output["end_time"] = datetime.now().timestamp()

    metrics_data = format_data(json_output, args.backup_id)
    target_dir = Path(args.metric_dir)
    write_metrics(metrics_data, target_dir, args.backup_id)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.INFO)
    main()
