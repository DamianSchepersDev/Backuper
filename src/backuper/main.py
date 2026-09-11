import json
import logging
import os
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from apscheduler.schedulers.blocking import BlockingScheduler


BASE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = BASE_DIR / "config.json"

RSYNC_TIMEOUT = 6 * 60 * 60
DISCORD_TIMEOUT = 10
BACKUP_RETRIES = 2
RETRY_DELAY = 60


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

logger = logging.getLogger(__name__)


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"Config file not found: {CONFIG_PATH}") from error
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid JSON in {CONFIG_PATH}: {error}") from error


def notify_discord(webhook_url, title, description, color):
    payload = {
        "embeds": [
            {
                "title": title,
                "description": description,
                "color": color,
            }
        ]
    }

    request = Request(
        webhook_url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=DISCORD_TIMEOUT):
            pass
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        logger.error("Discord notification failed: %s", error)


def run_rsync(server, backup):
    source = f"{server['user']}@{server['host']}:{backup['remote_source']}"

    command = [
        "rsync",
        "-av",
        "-e",
        f"ssh -p {server['port']}",
        source,
        backup["destination"],
    ]

    logger.info(
        "Starting backup: %s -> %s",
        source,
        backup["destination"],
    )

    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=RSYNC_TIMEOUT,
        )
    except FileNotFoundError as error:
        raise RuntimeError("rsync is not installed.") from error
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("rsync timed out.") from error
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() if error.stderr else str(error)
        raise RuntimeError(message) from error

    if result.stdout:
        logger.debug(result.stdout)

    logger.info("Backup completed.")


def backup(server, backup_config, webhook_url):
    for attempt in range(BACKUP_RETRIES + 1):
        try:
            run_rsync(server, backup_config)

            notify_discord(
                webhook_url,
                "Backup successful",
                (
                    f"**Server:** `{server['name']}`\n"
                    f"**Source:** `{backup_config['remote_source']}`\n"
                    f"**Destination:** `{backup_config['destination']}`"
                ),
                0x57F287,
            )

            return

        except Exception as error:
            logger.error(
                "Backup failed for %s (attempt %d/%d): %s",
                server["name"],
                attempt + 1,
                BACKUP_RETRIES + 1,
                error,
            )

            if attempt < BACKUP_RETRIES:
                time.sleep(RETRY_DELAY)

    notify_discord(
        webhook_url,
        "Backup failed",
        (
            f"**Server:** `{server['name']}`\n"
            f"**Source:** `{backup_config['remote_source']}`\n"
            f"**Destination:** `{backup_config['destination']}`\n"
            f"**Error:** `{error}`"
        ),
        0xED4245,
    )


def main():
    config = load_config()

    webhook_url = os.getenv("DISCORD_WEBHOOK_URL") or config.get(
        "discord_webhook"
    )

    if not webhook_url:
        raise RuntimeError("Discord webhook is not configured.")

    scheduler = BlockingScheduler(
        job_defaults={
            "max_instances": 1,
            "coalesce": True,
            "misfire_grace_time": 300,
        }
    )

    for server in config["servers"]:
        for backup_config in server["backups"]:
            backup(server, backup_config, webhook_url)

            schedule = backup_config["schedule"]

            scheduler.add_job(
                backup,
                "cron",
                hour=schedule["hour"],
                minute=schedule["minute"],
                args=[server, backup_config, webhook_url],
                id=f"{server['name']}-{backup_config['remote_source']}",
            )

            logger.info(
                "Scheduled %s at %02d:%02d",
                server["name"],
                schedule["hour"],
                schedule["minute"],
            )

    logger.info("Scheduler started.")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
