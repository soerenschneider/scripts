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

from datetime import datetime
from pathlib import Path
from typing import Optional, List, Any, Dict

import requests

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
ENV_PRUNE_KEEP_YEARS = "RETENTION_YEARS"
ENV_PUSHGATEWAY_URL = "PUSHGATEWAY_URL"
ENV_METRIC_LABELS = "METRIC_LABELS"

DEFAULT_JOB_NAME = "restic-prune"


class ResticError(Exception):
    pass


def run_prune(repo: str, days=None, weeks=None, months=None, years=None) -> Optional[str]:
    """ Performs the backup operation. Returns the JSONified stdout of the restic backup call. """

    command = RESTIC_PRUNE_CMD + [repo]
    if days:
        if isinstance(days, int):
            days = str(days)
        command += [f"--keep-daily={days}"]
    if weeks:
        if isinstance(weeks, int):
            weeks = str(weeks)
        command += [f"--keep-weekly={weeks}"]
    if months:
        if isinstance(months, int):
            months = str(months)
        command += [f"--keep-monthly={months}"]
    if years:
        if isinstance(years, int):
            years = str(years)
        command += [f"--keep-yearly={years}"]

    logging.info("Starting restic prune using command: %s", command)
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as proc:
        stdout, stderr = proc.communicate()
        proc.wait(BACKUP_TIMEOUT_SECONDS)
        if proc.returncode != 0:
            logging.error("Backup was not successful: %s", stderr)
            raise ResticError(stderr)

        logging.info("Prune call was successful!")
        return stdout.splitlines()[-1]


def push_metrics(pushgateway_url: str, metric_data: io.StringIO, backup_id: str = None) -> None:
    if not backup_id:
        backup_id = DEFAULT_JOB_NAME

    api_endpoint = f"{pushgateway_url}/metrics/job/restic_prune/instance/{backup_id}"
    data = metric_data.getvalue()
    response = requests.post(api_endpoint, data=data, timeout=30)
    if response.status_code != 200:
        logging.error("error sending metrics: %s", response.text)
    response.raise_for_status()
    logging.info("Successfully pushed metrics to pushgateway %s", pushgateway_url)


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


def format_data(output: dict, identifier: str, success: bool, start_time: datetime) -> io.StringIO:
    """ Poor man's Open Metrics formatting of the JSON output. """
    buffer = io.StringIO()

    buffer.write(f'# HELP {METRIC_PREFIX}_success_bool Success of the prune call\n')
    buffer.write(f'# TYPE {METRIC_PREFIX}_success_bool gauge\n')
    buffer.write(f'{METRIC_PREFIX}_success_bool{{repo="{identifier}"}} {int(success)}\n')

    buffer.write(f'# HELP {METRIC_PREFIX}_end_time_seconds Date when the process finished\n')
    buffer.write(f'# TYPE {METRIC_PREFIX}_end_time_seconds gauge\n')
    buffer.write(f'{METRIC_PREFIX}_end_time_seconds{{repo="{identifier}"}} {datetime.now().timestamp()}\n')

    buffer.write(f'# HELP {METRIC_PREFIX}_start_time_seconds Date when the process started\n')
    buffer.write(f'# TYPE {METRIC_PREFIX}_start_time_seconds gauge\n')
    buffer.write(f'{METRIC_PREFIX}_start_time_seconds{{repo="{identifier}"}} {start_time.timestamp()}\n')

    buffer.write(f'# HELP {METRIC_PREFIX}_snapshots_total Snapshots by host\n')
    buffer.write(f'# TYPE {METRIC_PREFIX}_snapshots_total gauge\n')
    buffer.write(f'# HELP {METRIC_PREFIX}_snapshots_removed_total Snapshots removed\n')
    buffer.write(f'# TYPE {METRIC_PREFIX}_snapshots_removed_total gauge\n')
    metric_data = parse_metrics(output)
    for host_data in metric_data:
        buffer.write(f'{METRIC_PREFIX}_snapshots_total{{repo="{identifier}",host=\"{host_data["host"]}\"}} {host_data["keep"]}\n')
        buffer.write(f'{METRIC_PREFIX}_snapshots_removed_total{{repo="{identifier}",host=\"{host_data["host"]}\"}} {host_data["remove"]}\n')

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

    # check metric dir
    if not args.pushgateway_url and not Path(args.metric_dir).exists():
        raise ValueError(f"Dir to write metrics to does not exist: '{args.metric_dir}' ")

    if not args.daily and not args.weekly and not args.monthly:
        raise ValueError("Neither daily, weekly nor monthly specified")


def parse_args() -> argparse.Namespace:
    """ Parses the arguments and returns the parsed namespace. """
    parser = argparse.ArgumentParser()
    parser.add_argument("-r", "--repo", default=os.environ.get("RESTIC_REPOSITORY"), help="The restic repository")
    parser.add_argument("-d", "--daily", default=os.environ.get(ENV_PRUNE_KEEP_DAYS), help="The amount of daily backups to keep")
    parser.add_argument("-w", "--weekly", default=os.environ.get(ENV_PRUNE_KEEP_WEEKS), help="The amount of weekly backups to keep")
    parser.add_argument("-m", "--monthly", default=os.environ.get(ENV_PRUNE_KEEP_MONTHS), help="The amount of monthly backups to keep")
    parser.add_argument("-y", "--yearly", default=os.environ.get(ENV_PRUNE_KEEP_YEARS), help="The amount of yearly backups to keep")
    parser.add_argument("-i", "--id", dest="backup_id", default=os.environ.get("RESTIC_BACKUP_ID"), help="An identifier for this backup")
    parser.add_argument("-M", "--metric-dir", default="/var/lib/node_exporter", help="Dir to write metrics to")
    parser.add_argument("-p", "--pushgateway-url", default=os.environ.get(ENV_PUSHGATEWAY_URL), help="Prometheus Pushgateway URL to send metrics to")
    parser.add_argument("-l", "--metric-labels", default=os.environ.get(ENV_METRIC_LABELS), help="Label(s) to add to metrics. Separate with '{ARG_SPLIT_TOKEN}'")
    return parser.parse_args()


def humanize_bytes(byte_value: int) -> str:
    """ Function to humanize byte values """
    if byte_value is None:
        return "0 B"

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = byte_value
    unit = 0

    while size >= 1024 and unit < len(units) - 1:
        size /= 1024.0
        unit += 1

    return f"{size:.2f} {units[unit]}"


def parse_json(parsed_data: List[Dict[str, Any]]) -> List[List[Any]]:
    parsed_results = []

    for host in parsed_data:
        for item in host["keep"]:
            summary = item.get('summary', {})
            time = item.get('time')
            hostname = item.get('hostname')
            files_new = summary.get('files_new')
            files_changed = summary.get('files_changed')
            data_added = humanize_bytes(summary.get('data_added'))
            data_added_packed = humanize_bytes(summary.get('data_added_packed'))
            total_files_processed = summary.get('total_files_processed')
            total_bytes_processed = humanize_bytes(summary.get('total_bytes_processed'))

            # Add the extracted data to the list
            parsed_results.append([
                time, hostname, files_new, files_changed, data_added,
                data_added_packed, total_files_processed, total_bytes_processed
            ])

    parsed_results.sort(key=lambda x: datetime.fromisoformat(x[0].replace("Z", "+00:00")), reverse=True)
    return parsed_results


def print_table(data: List[List[Any]]) -> None:
    headers = ["Time", "Hostname", "Files New", "Files Changed", "Data Added", "Data Added Packed", "Total Files Processed", "Total Bytes Processed"]

    # Calculate the width for each column based on the longest value
    column_widths = [max(len(header), max(len(str(item[i])) for item in data)) for i, header in enumerate(headers)]

    # Print header row
    header_row = " | ".join(f"{header:<{column_widths[i]}}" for i, header in enumerate(headers))
    print(header_row)
    print("-" * (sum(column_widths) + (len(headers) - 1) * 3))  # Print a separator line

    # Print data rows
    for row in data:
        print(" | ".join(f"{str(item):<{column_widths[i]}}" for i, item in enumerate(row)))


def parse_metrics(json_output) -> List:
    metrics = []

    for host in json_output:
        metrics.append({
            "host": host["host"],
            "keep": 0 if not host["keep"] else len(host["keep"]),
            "remove": 0 if not host["remove"] else len(host["remove"]),
        })

    return metrics


def setup_logging(debug=False) -> None:
    """ Set up the logging configuration. """
    loglevel = logging.INFO
    if debug:
        loglevel = logging.DEBUG
    logging.basicConfig(level=loglevel, format="%(asctime)s %(levelname)s %(message)s")


def main() -> None:
    """ Main does mainly main things. """
    setup_logging()
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

    metrics_data = format_data(json_output, args.backup_id, success, start_time)
    pushgateway_success = False
    if args.pushgateway_url:
        try:
            push_metrics(args.pushgateway_url, metrics_data, args.backup_id)
            pushgateway_success = True
        except requests.exceptions.HTTPError as e:
            logging.error(f"Could not push metrics to pushgateway {args.pushgateway_url}: %s", e)

    if not args.pushgateway_url or not pushgateway_success:
        target_dir = Path(args.metric_dir)
        write_metrics(metrics_data, target_dir, args.backup_id)

    parsed_result = parse_json(json_output)
    print_table(parsed_result)

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()
