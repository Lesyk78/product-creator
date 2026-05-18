"""Bitrix24 task-based alerting. Deduplicates by title: open task → comment, no task → create.

Same pattern as warehouse-app/alerts.py and the original Windmill scripts.
"""
import requests
from datetime import datetime, timezone

import config


# Post error to Bitrix24 (creates task by title, or comments on existing open one).
# In: process_name, script_path, error_message, error_details. Out: None — swallows delivery errors.
def send_alert(process_name: str, script_path: str, error_message: str, error_details: str = "") -> None:
    """Post error to Bitrix24 as task (or comment on existing open task by same title).
    In: process_name (used in title), script_path (URL/module), error_message, error_details.
    Out: None. Swallows all delivery exceptions — alert path must never break the app."""
    if not config.BITRIX_WEBHOOK:
        return

    title = f"🚨 {process_name} | product-creator"
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC")

    try:
        r = requests.post(
            f"{config.BITRIX_WEBHOOK}/tasks.task.list",
            json={
                "filter": {"TITLE": title, "GROUP_ID": 1, "!STATUS": 5},
                "select": ["ID"], "order": {"ID": "desc"}, "limit": 1,
            },
            timeout=15,
        )
        tasks = r.json().get("result", {}).get("tasks", [])

        if tasks:
            requests.post(
                f"{config.BITRIX_WEBHOOK}/task.commentitem.add",
                json={
                    "taskId": tasks[0]["id"],
                    "fields": {
                        "POST_MESSAGE": (
                            f"[B]Повторна помилка — {now}[/B]\n\n"
                            f"[B]Помилка:[/B] {error_message}\n\n"
                            f"[B]Деталі:[/B]\n{error_details}"
                        ),
                        "AUTHOR_ID": 107,
                    },
                },
                timeout=15,
            )
        else:
            desc = (
                f"[B]Алерт від автоматики[/B]\n\n"
                f"[B]Процес:[/B] {process_name}\n"
                f"[B]Джерело:[/B] product-creator (Coolify)\n"
                f"[B]Шлях:[/B] {script_path}\n"
                f"[B]Час:[/B] {now}\n\n"
                f"[B]Помилка:[/B]\n[color=red]{error_message}[/color]\n\n"
                + (f"[B]Деталі:[/B]\n{error_details}" if error_details else "")
            )
            requests.post(
                f"{config.BITRIX_WEBHOOK}/tasks.task.add",
                json={"fields": {
                    "TITLE": title, "DESCRIPTION": desc,
                    "RESPONSIBLE_ID": config.ALERT_RESPONSIBLE_ID, "CREATED_BY": 107,
                    "GROUP_ID": 1, "STAGE_ID": 705, "PRIORITY": 2,
                }},
                timeout=15,
            )
    except Exception as e:
        print(f"[alerts] Bitrix24 alert delivery failed: {e}")
