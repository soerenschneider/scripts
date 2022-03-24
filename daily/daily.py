#!/usr/bin/env python3

import argparse
import os.path
import re
import subprocess
import sys
import sqlite3

from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, List, Tuple

from pyfzf.pyfzf import FzfPrompt

DEFAULT_EDITOR = 'vim'
ENTRIES_DIR = "~/Work/daily"
SQLITE_DB_FILE = "~/Work/daily.db"
DEFAULT_EXTENSION = "txt"
DATE_FORMAT = "%Y-%m-%d"

daily_entry_regex = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class IllegalDateException(Exception):
    pass


@dataclass
class Result:
    items: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    daily_date: str = ""


class Daily:
    def __init__(self, driver):
        self.driver = driver

    @staticmethod
    def _validate_date(daily_date: str) -> None:
        if not daily_date:
            raise IllegalDateException("empty date given")

        if not daily_entry_regex.match(daily_date.strip()):
            raise IllegalDateException(f"Invalid date {daily_date}, "
                                       f"date must be in format {DATE_FORMAT}")

    @staticmethod
    def compute_date(days_offset=0) -> str:
        computed_date = date.today() + timedelta(days_offset)
        return computed_date.strftime(DATE_FORMAT)

    def translate_date(self, special_date: str) -> str:
        special_date = special_date.lower().strip()
        if special_date in ["today", "t"]:
            return Daily.compute_date(days_offset=0)
        if special_date in ["yesterday", "y"]:
            return Daily.compute_date(days_offset=-1)
        if special_date in ["last", "l"]:
            return self.get_latest_entry()

        Daily._validate_date(special_date)
        return special_date

    def has_entry(self, daily_date) -> bool:
        return self.driver.has_entry(daily_date)

    def get_latest_entry(self) -> Optional[str]:
        # this was written for the fs driver, doesn't scale well for sqlite
        for i in range(30):
            daily_date = Daily.compute_date(-i)
            if self.has_entry(daily_date):
                return daily_date
        return None

    def get_entry(self, daily_date: str) -> Optional[Result]:
        result = Result()
        if not self.has_entry(daily_date):
            new_daily_date = self.get_latest_entry()
            if not new_daily_date:
                result.warnings.append("No entries found for the last 30 days")
                return result

            result.warnings.append(f"Nothing found for {daily_date}, "
                                   f"showing results for {new_daily_date}")
            daily_date = new_daily_date

        result.items = self.driver.get_entry(daily_date)
        result.daily_date = daily_date
        return result

    def nuke_entries(self, daily_date: str) -> bool:
        return self.driver.nuke_entries(daily_date)

    def edit_entry(self, daily_date: str, entry_id: int, updated: str) -> int:
        return self.driver.edit_entry(daily_date, entry_id, updated)

    def add_entry(self, daily_date: str, content: str) -> None:
        return self.driver.add_entry(daily_date, content)

    def remove_entry(self, daily_date: str, entry_id: int) -> int:
        return self.driver.remove_entry(daily_date, entry_id)

    def get_ids(self, daily_date: str) -> List[Tuple[int, str]]:
        return self.driver.get_ids(daily_date)


class SqliteDriver:
    def __init__(self, filename: str):
        self._con = sqlite3.connect(os.path.expanduser(filename))
        self._init_db()

    def _init_db(self):
        cursor = self._con.cursor()
        cursor.execute('CREATE TABLE IF NOT EXISTS daily (id INTEGER PRIMARY KEY, date INTEGER, desc TEXT, tag TEXT)')
        cursor.execute('CREATE INDEX IF NOT EXISTS date ON daily(date)')
        self._con.commit()

    @staticmethod
    def _convert_date(daily_date: str) -> int:
        return int(daily_date.replace("-", ""))

    def has_entry(self, daily_date: str) -> bool:
        converted = SqliteDriver._convert_date(daily_date)
        cursor = self._con.cursor()
        cursor.execute('SELECT COUNT(date) FROM daily WHERE date = ?', (converted,))
        results = cursor.fetchone()
        return results[0] > 0

    def get_entry(self, daily_date: str) -> List[str]:
        cursor = self._con.cursor()
        converted = SqliteDriver._convert_date(daily_date)
        cursor.execute('SELECT desc FROM daily WHERE date = ? ORDER BY id ASC', (converted,))
        results = cursor.fetchall()
        ret = []
        for result in results:
            ret.append(result[0])
        return ret

    def add_entry(self, daily_date: str, content: str, tag="") -> None:
        converted = SqliteDriver._convert_date(daily_date)
        cursor = self._con.cursor()
        args = (None, converted, content, tag)
        cursor.execute('INSERT INTO daily VALUES (?, ?, ?, ?)', args)
        self._con.commit()
        self._con.close()

    def nuke_entries(self, daily_date: str) -> int:
        converted = SqliteDriver._convert_date(daily_date)
        cursor = self._con.cursor()
        result = cursor.execute('DELETE FROM daily WHERE date = ?', (converted,))
        self._con.commit()
        self._con.close()
        return result.rowcount

    def remove_entry(self, daily_date: str, entry_id: int) -> int:
        cursor = self._con.cursor()
        result = cursor.execute('DELETE FROM daily WHERE id = ?', (entry_id,))
        self._con.commit()
        self._con.close()
        return result.rowcount

    def edit_entry(self, daily_date: str, entry_id: int, updated: str) -> int:
        cursor = self._con.cursor()
        result = cursor.execute('UPDATE daily SET desc = ? WHERE id = ?', (updated, entry_id))
        self._con.commit()
        self._con.close()
        return result.rowcount

    def get_ids(self, daily_date: str) -> List[Tuple[int, str]]:
        cursor = self._con.cursor()
        converted = SqliteDriver._convert_date(daily_date)
        cursor.execute('SELECT id, desc FROM daily WHERE date = ? ORDER BY id ASC', (converted,))
        return cursor.fetchall()


class FsDriver:
    def __init__(self, daily_entries_dir=ENTRIES_DIR):
        self._daily_entries_dir = os.path.expanduser(daily_entries_dir)
        self._sanitize()

    def _sanitize(self):
        daily_db_dir = Path(self._daily_entries_dir)
        if not daily_db_dir.is_dir():
            print(f"Creating dir {self._daily_entries_dir}")
            daily_db_dir.mkdir(parents=True)

    def _get_filename(self, daily_date: str) -> str:
        return os.path.join(self._daily_entries_dir, f"{daily_date}.{DEFAULT_EXTENSION.lstrip('.')}")

    def has_entry(self, daily_date) -> bool:
        filename = self._get_filename(daily_date)
        file_path = Path(filename)
        return file_path.exists()

    def nuke_entries(self, daily_date: str) -> bool:
        if self.has_entry(daily_date):
            filename = self._get_filename(daily_date)
            os.remove(filename)
            return True
        return False

    def get_entry(self, daily_date: str) -> List[str]:
        if not self.has_entry(daily_date):
            return []

        filename = self._get_filename(daily_date)
        with open(filename, 'r') as content:
            return content.readlines()

    def add_entry(self, daily_date: str, content: str) -> None:
        mode = "a"
        if not self.has_entry(daily_date):
            mode = "w"

        filename = self._get_filename(daily_date)
        with open(filename, mode) as entries_file:
            entries_file.write(content + os.linesep)

    def remove_entry(self, daily_date: str, entry_id: int) -> int:
        raise NotImplementedError()

    def get_ids(self, daily_date: str) -> List[Tuple[int, str]]:
        raise NotImplementedError()

    def edit_entry(self, daily_date: str, entry_id: int, updated: str) -> int:
        raise NotImplementedError()


class Tui:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKCYAN = '\033[96m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'

    def __init__(self):
        self.fzf = FzfPrompt()

    def confirm_action(self, prompt: str) -> bool:
        self.notify_warn(prompt)
        self.notify_warn("Confirm with y/N")
        choice = input().lower()
        return choice in {'yes', 'y'}

    def read_input(self, prompt: str) -> str:
        try:
            return input(f"{self.OKGREEN}{prompt}:{self.ENDC}\n")
        except:
            return None

    def _color_print(self, color: str, msg: str) -> None:
        print(f"{color}{msg}{self.ENDC}")

    def pick_entry(self, choices: List[Tuple[int, str]]) -> Optional[str]:
        choices = [f"{result[0]}, {result[1]}" for result in choices]
        if not choices:
            self.notify_warn("Nothing returned")
            return None

        return self._pick_choice(choices)

    def _pick_choice(self, choices: List) -> Optional[str]:
        try:
            return self.fzf.prompt(choices)[0]
        except:
            return None

    def render_output(self, result: Result) -> None:
        if result.warnings:
            for warning in result.warnings:
                self.notify_warn(warning)

        for i in result.items:
            end = "\n"
            if i.endswith("\n"):
                end = ""
            print(f"- {i}", end=end)

    def notify_fail(self, msg: str) -> None:
        self._color_print(self.FAIL, msg)

    def notify_ok(self, msg: str) -> None:
        self._color_print(self.OKGREEN, msg)

    def notify_warn(self, msg: str) -> None:
        self._color_print(self.WARNING, msg)


def run_subcommands(daily: Daily, ui: Tui, arg: argparse.Namespace, parsed_date: str):
    if arg.command == "add":
        if not arg.message:
            ui.notify_fail("No message provided")
            return
        for messages in arg.message:
            daily.add_entry(parsed_date, " ".join(messages))
    elif arg.command == "edit":
        results = daily.get_ids(parsed_date)
        choice = ui.pick_entry(results)
        if not choice:
            return

        entry_id = choice.split(",")[0]
        updated_desc = ui.read_input("Enter a new description, confirm with Enter")
        if not updated_desc:
            ui.notify_warn("Discarding")
            return
        if ui.confirm_action(f"Accept new description: '{updated_desc}'?"):
            n = daily.edit_entry(parsed_date, entry_id, updated_desc)
            ui.notify_ok(f"Updated {n} entries")

    elif arg.command == "remove":
        results = daily.get_ids(parsed_date)
        choice = ui.pick_entry(results)
        if not choice:
            return

        entry_id = choice.split(",")[0]
        entries_removed = daily.remove_entry(parsed_date, entry_id)
        ui.notify_ok(f"Removed {entries_removed} entries")

    elif arg.command == "nuke":
        daily.get_entry(parsed_date)
        ui.confirm_action(f"Do you want to delete all entries for {parsed_date}? y/N")
        nuked_entries = daily.nuke_entries(parsed_date)
        if nuked_entries:
            ui.notify_ok(f"Deleted {nuked_entries} entries")
        else:
            ui.notify_warn("There were no entries to delete")
    else:
        result = daily.get_entry(parsed_date)
        ui.render_output(result)


def main():
    arg = parse_args()

    # todo: make configurable
    driver = SqliteDriver(SQLITE_DB_FILE)
    daily = Daily(driver)

    ui = Tui()
    try:
        parsed_date = daily.translate_date(arg.date)
    except IllegalDateException as err:
        ui._color_print(Tui.FAIL, str(err))
        sys.exit(1)

    run_subcommands(daily, ui, arg, parsed_date)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()

    parser.add_argument("-d", '--date', type=str, help="specify a date the command applies to",
                        default="today", action="store")
    subparsers = parser.add_subparsers(dest="command")

    parser_add = subparsers.add_parser('add', help='add one or more entries for a given day')
    parser_add.add_argument("-m", help='express the work item', dest="message",
                            nargs="+", action="append")

    subparsers.add_parser('get', help='read entries for a given day')
    subparsers.add_parser('edit', help='edit entries for a given day')
    subparsers.add_parser('nuke', help='delete entries for a given day')
    subparsers.add_parser('remove', help='delete entries for a given day')

    return parser.parse_args()


if __name__ == '__main__':
    main()
