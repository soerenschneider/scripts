#!/usr/bin/env python3

import datetime
import os
import subprocess
import json

from typing import Dict, Any, List

import jinja2

SCRIPT_PATH = os.path.dirname(os.path.abspath(__file__))
MAX_TASKS = 100
ENV_KEY_KANBAN_FILE = "TASK_KANBAN_FILE"

COLORS = {
    "todo": {
        "bg": "#ef959d",
        "nth_bg": "#69585f",
        "nth_fg": "#fcddbc"
    },
    "started": {
        "bg": "#fcddbc",
        "nth_bg": "#69585f",
        "nth_fg": "#fcddbc"
    },
    "completed": {
        "bg": "#b8d8ba",
        "nth_bg": "#69585f",
        "nth_fg": "#fcddbc"
    },
}


def get_tasks(tags: List[str] = None) -> Dict[str, Any]:
    if not tags:
        tags = []

    command = ['task', 'rc.json.depends.array=no', 'status:completed', 'or', 'status:pending'] + tags + ['export']
    data = subprocess.check_output(command)
    data = data.decode('utf-8')
    data = data.replace('\n', '')

    return json.loads(data)


def get_tasks_old(tags: List[str] = None) -> Dict[str, Any]:
    if not tags:
        tags = []

    command = ['task', 'rc.json.depends.array=no', 'status:completed', 'or', 'status:pending'] + tags + ['export']
    data = subprocess.check_output(command)
    data = data.decode('utf-8')
    data = data.replace('\n', '')

    return json.loads(data)


def check_due_date(tasks: List[Dict[str, Any]]) -> None:
    limit = datetime.timedelta(days=7) + datetime.datetime.utcnow()
    for task in tasks:
        if 'due' in task and task['due']:
            due_date = datetime.datetime.strptime(task['due'], '%Y%m%dT%H%M%SZ')
            if due_date > limit:
                task.pop('due', None)
            else:
                task['due'] = (due_date - datetime.datetime.utcnow()).days


def render_template(data: Dict[str, Any]) -> str:
    template_loader = jinja2.FileSystemLoader(searchpath=SCRIPT_PATH)
    template_env = jinja2.Environment(loader=template_loader)
    template_file = 'template.jinja'
    template = template_env.get_template(template_file)

    return template.render(data=data)


def write_html(data: str, filename: str) -> None:
    with open(filename, 'w', encoding="utf-8") as html_file:
        html_file.write(data)


def main() -> None:
    tasks = get_tasks()
    completed, pending, started = [], [], []
    for task in tasks:
        if task["status"] == "pending":
            if "start" in task:
                started.append(task)
            else:
                pending.append(task)
        elif task["status"] == "completed":
            completed.append(task)

    pending = sorted(pending, key=lambda t: t['urgency'], reverse=True)
    started = sorted(started, key=lambda t: t['start'], reverse=True)
    completed = sorted(completed, key=lambda t: t['end'], reverse=True)

    data = {
        'todo_tasks': pending[:MAX_TASKS],
        'started_tasks': started[:MAX_TASKS],
        'completed_tasks': completed[:MAX_TASKS],
        'timestamp': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "colors": COLORS
    }
    check_due_date(pending)
    check_due_date(started)

    html = render_template(data)
    dest = os.getenv(ENV_KEY_KANBAN_FILE, SCRIPT_PATH + '/index.html')
    write_html(html, dest)


if __name__ == '__main__':
    main()
