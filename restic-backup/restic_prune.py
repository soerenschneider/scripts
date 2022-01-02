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
METRIC_PREFIX = "restic_prune"

# skeleton of the backup cmd we're invoking
RESTIC_PRUNE_CMD = ["restic", "-q", "--json", "forget", "--prune", "-r"]


# additional metrics of this wrapper
INTERNAL_METRICS = {
    "start_time": ("_seconds", "Start time of the backup process"),
    "success": ("_bool", "Boolean indicating the success of the backup"),
    "exporter_errors": ("_bool", "Exporter errors unrelated to restic"),
}

ENV_PRUNE_KEEP_DAYS = "RETENTION_DAYS"
ENV_PRUNE_KEEP_WEEKS = "RETENTION_WEEKS"
ENV_PRUNE_KEEP_MONTHS = "RETENTION_MONTHS"


class ResticError(Exception):
    pass


def run_prune(repo: str, days=None, weeks=None, months=None) -> Optional[str]:
    """ Performs the backup operation. Returns the JSONified stdout of the restic backup call. """

    command = RESTIC_PRUNE_CMD + [repo]
    if days:
        if isinstance(days, int):
            days = str(days)
        command += ["-d", days]
    if weeks:
        if isinstance(weeks, int):
            weeks = str(weeks)
        command += ["-w", weeks]
    if months:
        if isinstance(months, int):
            months = str(months)
        command += ["-m", months]

    logging.info("Starting restic prune using command: %s", command)
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        stdout, stderr = proc.communicate()
        proc.wait(BACKUP_TIMEOUT_SECONDS)
        if proc.returncode != 0:
            logging.error("Backup was not successful: %s", stderr)
            raise ResticError(stderr)

        logging.info("Prune call was successful!")
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


def format_data(output: dict, identifier: str, success: bool, start_time: datetime) -> io.StringIO:
    """ Poor man's Open Metrics formatting of the JSON output. """
    buffer = io.StringIO()

    buffer.write(f'# HELP {METRIC_PREFIX}_deletions_total Total amount of files deleted for a given path\n')
    buffer.write(f"# TYPE {METRIC_PREFIX}_deletions_total gauge\n")
    buffer.write(f'# HELP {METRIC_PREFIX}_keep_total Total amount of files deleted for a given path\n')
    buffer.write(f"# TYPE {METRIC_PREFIX}_keep_total gauge\n")
    for line in output:
        removed = 0
        if line['remove']:
            removed += len(line['remove'])

        keep = 0
        if line['keep']:
            keep += len(line['keep'])

        path = line['paths'][0]
        buffer.write(f'{METRIC_PREFIX}_deletions_total{{repo="{identifier}",path="{path}"}} {removed}\n')
        buffer.write(f'{METRIC_PREFIX}_keep_total{{repo="{identifier}",path="{path}"}} {keep}\n')

    buffer.write(f'# HELP {METRIC_PREFIX}_success_bool{{repo="{identifier}"}} Success of the prune call\n')
    buffer.write(f'# TYPE {METRIC_PREFIX}_success_bool{{repo="{identifier}"}} gauge\n')
    buffer.write(f'{METRIC_PREFIX}_success_bool{{repo="{identifier}"}} {int(success)}\n')

    buffer.write(f'# HELP {METRIC_PREFIX}_end_time_seconds{{repo="{identifier}"}} Date when the process finished\n')
    buffer.write(f'# TYPE {METRIC_PREFIX}_end_time_seconds{{repo="{identifier}"}} gauge\n')
    buffer.write(f'{METRIC_PREFIX}_end_time_seconds{{repo="{identifier}"}} {datetime.now().timestamp()}\n')

    buffer.write(f'# HELP {METRIC_PREFIX}_start_time_seconds{{repo="{identifier}"}} Date when the process started\n')
    buffer.write(f'# TYPE {METRIC_PREFIX}_start_time_seconds{{repo="{identifier}"}} gauge\n')
    buffer.write(f'{METRIC_PREFIX}_start_time_seconds{{repo="{identifier}"}} {start_time.timestamp()}\n')

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

    if not args.daily and not args.weekly and not args.monthly:
        raise ValueError("Neither daily, weekly nor monthly specified")


def parse_args() -> argparse.Namespace:
    """ Parses the arguments and returns the parsed namespace. """
    parser = argparse.ArgumentParser()
    parser.add_argument("-r", "--repo", default=os.environ.get("RESTIC_REPOSITORY"), help="The restic repository")
    parser.add_argument("-d", "--daily", default=os.environ.get(ENV_PRUNE_KEEP_DAYS), help="The amount of daily backups to keep")
    parser.add_argument("-w", "--weekly", default=os.environ.get(ENV_PRUNE_KEEP_WEEKS), help="The amount of weekly backups to keep")
    parser.add_argument("-M", "--monthly", default=os.environ.get(ENV_PRUNE_KEEP_MONTHS), help="The amount of monthly backups to keep")
    parser.add_argument("-i", "--id", dest="backup_id", default=os.environ.get("RESTIC_BACKUP_ID"), help="An identifier for this backup")
    parser.add_argument("-m", "--metric-dir", default="/tmp", help="Dir to write metrics to")
    return parser.parse_args()


def main() -> None:
    """ Main does mainly main things. """
    start_time = datetime.now()
    args = parse_args()

    success = False
    json_output = []
    try:
        validate_args(args)
        stdout = run_prune(args.repo, days=args.daily, weeks=args.weekly, months=args.monthly)
        json_output = json.loads(stdout)
        success = True
    except ValueError as err:
        logging.error("Can not start the backup: %s", err.args[0])
        sys.exit(1)
    except ResticError as err:
        logging.error("Failed to run prune: %s", err)

    formatted = format_data(json_output, args.backup_id, success, start_time)
    target_dir = Path(args.metric_dir)
    write_metrics(formatted, target_dir, args.backup_id)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    logging.basicConfig(format="%(levelname)s:%(message)s", level=logging.INFO)
    main()
