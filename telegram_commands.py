"""Telegram command catalog for OpenPine.

This module is intentionally independent from the Telegram transport. It only
describes command routing and inline button payloads; callers decide how to run
the returned OpenPine CLI argv.
"""

from __future__ import annotations

from dataclasses import dataclass
import shlex
from typing import Any, Literal


ArgMode = Literal["fixed", "append"]


@dataclass(frozen=True)
class TelegramCommandSpec:
    """One Telegram slash command and its OpenPine CLI mapping."""

    slash: str
    family: str
    title: str
    argv: tuple[str, ...]
    usage: str
    description: str
    arg_mode: ArgMode = "append"


@dataclass(frozen=True)
class TelegramButtonSpec:
    """One Telegram inline button definition."""

    text: str
    callback_data: str


class TelegramCommandError(ValueError):
    """Raised when Telegram text or callback data cannot be mapped."""


def _cmd(
    slash: str,
    family: str,
    title: str,
    argv: tuple[str, ...],
    usage: str,
    description: str,
    arg_mode: ArgMode = "append",
) -> TelegramCommandSpec:
    return TelegramCommandSpec(
        slash=slash,
        family=family,
        title=title,
        argv=argv,
        usage=usage,
        description=description,
        arg_mode=arg_mode,
    )


TELEGRAM_COMMANDS: tuple[TelegramCommandSpec, ...] = (
    _cmd("/help", "meta", "Help", ("help",), "/help [family]", "Show Telegram command help."),
    _cmd("/menu", "meta", "Menu", ("version",), "/menu", "Show main button menu."),
    _cmd("/start", "meta", "Start", ("version",), "/start", "Show main menu."),
    _cmd("/op", "meta", "Raw OpenPine", (), "/op <openpine args>", "Run any OpenPine CLI command.", "append"),
    _cmd("/status", "core", "System status", ("doctor",), "/status", "Run OpenPine doctor.", "fixed"),
    _cmd("/doctor", "core", "Doctor", ("doctor",), "/doctor [--strict] [--deep]", "Run diagnostics."),
    _cmd("/version", "core", "Version", ("version",), "/version", "Show OpenPine version.", "fixed"),
    _cmd("/core_check", "core", "Core check", ("core", "check"), "/core_check", "Check core library imports.", "fixed"),
    _cmd("/init", "core", "Init", ("init",), "/init", "Run interactive OpenPine init.", "fixed"),
    _cmd("/storage", "storage", "Storage schema", ("storage", "storage-schema"), "/storage", "Show storage schema.", "fixed"),
    _cmd("/storage_init", "storage", "Storage init", ("storage", "storage-init"), "/storage_init [--path PATH] [--dry-run]", "Initialize storage."),
    _cmd("/storage_migrate", "storage", "Storage migrate", ("storage", "migrate"), "/storage_migrate [--path PATH]", "Run migrations."),
    _cmd("/storage_verify", "storage", "Storage verify", ("storage", "verify"), "/storage_verify", "Verify storage.", "fixed"),
    _cmd("/storage_backup", "storage", "Storage backup", ("storage", "backup"), "/storage_backup --out FILE.tar.gz", "Create backup."),
    _cmd("/storage_restore", "storage", "Storage restore", ("storage", "restore"), "/storage_restore FILE.tar.gz [--target DIR]", "Restore backup."),
    _cmd("/pines", "pine", "Pine list", ("pine", "list"), "/pines", "List Pine sources.", "fixed"),
    _cmd("/pine_list", "pine", "Pine list", ("pine", "list"), "/pine_list", "List Pine sources.", "fixed"),
    _cmd("/pine_add", "pine", "Pine add", ("pine", "pine-add"), "/pine_add NAME SOURCE_PATH", "Add Pine source."),
    _cmd("/pine_compile", "pine", "Pine compile", ("pine", "pine-compile"), "/pine_compile NAME [--force]", "Compile Pine source."),
    _cmd("/pine_show", "pine", "Pine show", ("pine", "show"), "/pine_show NAME", "Show Pine source."),
    _cmd("/pine_versions", "pine", "Pine versions", ("pine", "versions"), "/pine_versions NAME", "List artifact versions."),
    _cmd("/pine_artifacts", "pine", "Pine artifacts", ("pine", "artifacts"), "/pine_artifacts NAME", "List artifacts."),
    _cmd("/pine_inspect", "pine", "Pine inspect", ("pine", "inspect"), "/pine_inspect NAME", "Inspect active artifact."),
    _cmd("/pine_activate", "pine", "Pine activate", ("pine", "activate"), "/pine_activate NAME ARTIFACT_ID", "Activate artifact."),
    _cmd("/pine_rollback", "pine", "Pine rollback", ("pine", "rollback"), "/pine_rollback NAME [--to-version ARTIFACT_ID]", "Rollback artifact."),
    _cmd("/pine_remove", "pine", "Pine remove", ("pine", "remove"), "/pine_remove NAME", "Remove Pine source."),
    _cmd("/strategies", "strategy", "Strategies", ("strategy", "list"), "/strategies", "List strategies.", "fixed"),
    _cmd("/strategy_list", "strategy", "Strategy list", ("strategy", "list"), "/strategy_list", "List strategies.", "fixed"),
    _cmd("/strategy_show", "strategy", "Strategy show", ("strategy", "show"), "/strategy_show STRATEGY_ID", "Show strategy."),
    _cmd("/strategy_status", "strategy", "Strategy status", ("strategy", "status"), "/strategy_status STRATEGY_ID", "Show strategy status."),
    _cmd("/strategy_create", "strategy", "Strategy create", ("strategy", "create"), "/strategy_create [ID] --pine NAME --symbol BTCUSDT --timeframe 1m [--mode paper] [--param k=v]", "Create strategy."),
    _cmd("/strategy_update", "strategy", "Strategy update", ("strategy", "update"), "/strategy_update STRATEGY_ID --param k=v", "Update strategy params."),
    _cmd("/strategy_pause", "strategy", "Strategy pause", ("strategy", "pause"), "/strategy_pause STRATEGY_ID", "Pause strategy."),
    _cmd("/strategy_resume", "strategy", "Strategy resume", ("strategy", "resume"), "/strategy_resume STRATEGY_ID", "Resume strategy."),
    _cmd("/strategy_remove", "strategy", "Strategy remove", ("strategy", "remove"), "/strategy_remove STRATEGY_ID", "Remove strategy."),
    _cmd("/strategy_backtest", "strategy", "Strategy backtest", ("strategy", "backtest"), "/strategy_backtest STRATEGY_ID [--from DATE] [--to DATE]", "Run backtest."),
    _cmd("/strategy_replay", "strategy", "Strategy replay", ("strategy", "replay"), "/strategy_replay STRATEGY_ID [--from DATE] [--to DATE]", "Run replay."),
    _cmd("/strategy_paper", "strategy", "Strategy paper", ("strategy", "paper"), "/strategy_paper STRATEGY_ID start|stop", "Control paper trading."),
    _cmd("/strategy_live", "strategy", "Strategy live", ("strategy", "live"), "/strategy_live STRATEGY_ID enable|start|stop", "Control live trading."),
    _cmd("/strategy_error_clear", "strategy", "Strategy error clear", ("strategy", "error"), "/strategy_error_clear STRATEGY_ID clear [--to paused|disabled]", "Clear error state."),
    _cmd("/data_status", "data", "Data status", ("data", "status"), "/data_status [SYMBOL] [--exchange binance] [--timeframe 1m]", "Show data pipeline status."),
    _cmd("/data_gaps", "data", "Data gaps", ("data", "gaps"), "/data_gaps SYMBOL TIMEFRAME [--exchange binance] [--market usdm]", "Find data gaps."),
    _cmd("/data_repair", "data", "Data repair", ("data", "repair"), "/data_repair SYMBOL TIMEFRAME --from MS --to MS [--exchange binance]", "Queue repair/backfill."),
    _cmd("/data_backfill", "data", "Data backfill", ("data", "backfill"), "/data_backfill SYMBOL TIMEFRAME --from YYYY-MM-DD [--to YYYY-MM-DD]", "Backfill candles."),
    _cmd("/data_inspect", "data", "Data inspect", ("data", "inspect"), "/data_inspect SYMBOL TIMEFRAME --from YYYY-MM-DD [--to YYYY-MM-DD]", "Inspect candle files."),
    _cmd("/data_providers", "data", "Data providers", ("data", "providers"), "/data_providers", "List built-in data providers.", "fixed"),
    _cmd("/accounts", "accounts", "Accounts", ("accounts", "list"), "/accounts [--strategy STRATEGY_ID]", "List accounts."),
    _cmd("/account_add", "accounts", "Account add", ("accounts", "add"), "/account_add --name N --exchange binance --api-key KEY --secret SECRET [--mode paper]", "Add account."),
    _cmd("/account_test", "accounts", "Account test", ("accounts", "test"), "/account_test NAME", "Test account config."),
    _cmd("/providers", "providers", "Providers", ("providers", "list"), "/providers", "List providers.", "fixed"),
    _cmd("/provider_test", "providers", "Provider test", ("providers", "test"), "/provider_test PROVIDER", "Test provider."),
    _cmd("/jobs", "jobs", "Jobs", ("jobs", "list"), "/jobs", "List jobs.", "fixed"),
    _cmd("/job_show", "jobs", "Job show", ("jobs", "show"), "/job_show JOB_ID", "Show job."),
    _cmd("/job_cancel", "jobs", "Job cancel", ("jobs", "cancel"), "/job_cancel JOB_ID", "Cancel job."),
    _cmd("/job_retry", "jobs", "Job retry", ("jobs", "retry"), "/job_retry JOB_ID", "Retry job."),
    _cmd("/job_enqueue_live_bar", "jobs", "Enqueue live bar", ("jobs", "enqueue-live-bar"), "/job_enqueue_live_bar --strategy ID --bar-time MS [--dry-run]", "Enqueue live-bar job."),
    _cmd("/queue", "jobs", "Queue", ("queue", "status"), "/queue", "Show queue status.", "fixed"),
    _cmd("/workers", "jobs", "Workers", ("workers", "status"), "/workers", "Show worker status.", "fixed"),
    _cmd("/workers_pause", "jobs", "Workers pause", ("workers", "pause"), "/workers_pause", "Pause workers.", "fixed"),
    _cmd("/workers_resume", "jobs", "Workers resume", ("workers", "resume"), "/workers_resume", "Resume workers.", "fixed"),
    _cmd("/state", "state", "State", ("state", "show"), "/state", "Show state policy.", "fixed"),
    _cmd("/state_policy", "state", "State policy", ("state", "policy"), "/state_policy", "Show state policy.", "fixed"),
    _cmd("/state_list", "state", "State list", ("state", "list"), "/state_list [--strategy ID]", "List snapshots."),
    _cmd("/state_invalid", "state", "State invalid", ("state", "invalid"), "/state_invalid", "List invalid state.", "fixed"),
    _cmd("/state_rebuild", "state", "State rebuild", ("state", "rebuild"), "/state_rebuild STRATEGY_ID [--from-bar MS]", "Rebuild state."),
    _cmd("/risk", "risk", "Risk", ("risk", "status"), "/risk [--show-violations]", "Show risk status."),
    _cmd("/risk_show", "risk", "Risk show", ("risk", "show"), "/risk_show [--show-violations]", "Show risk config."),
    _cmd("/risk_status", "risk", "Risk status", ("risk", "status"), "/risk_status [--show-violations]", "Show risk status."),
    _cmd("/kill_switch_on", "risk", "Kill switch on", ("risk", "kill-switch", "on"), "/kill_switch_on", "Enable kill switch.", "fixed"),
    _cmd("/kill_switch_off", "risk", "Kill switch off", ("risk", "kill-switch", "off"), "/kill_switch_off", "Disable kill switch.", "fixed"),
    _cmd("/reports", "reports", "Reports", ("reports", "list"), "/reports", "List reports.", "fixed"),
    _cmd("/report_show", "reports", "Report show", ("reports", "show"), "/report_show REPORT_ID", "Show report."),
    _cmd("/report_export", "reports", "Report export", ("reports", "export"), "/report_export REPORT_ID [--format json|csv]", "Export report."),
    _cmd("/plugins", "plugins", "Plugins", ("plugins", "list"), "/plugins", "List plugins.", "fixed"),
    _cmd("/plugin_enable", "plugins", "Plugin enable", ("plugins", "enable"), "/plugin_enable telegram [--chat-id ID]", "Enable plugin."),
    _cmd("/plugin_test", "plugins", "Plugin test", ("plugins", "test"), "/plugin_test telegram --chat-id ID", "Test plugin."),
    _cmd("/universe", "universe", "Universe", ("universe", "show"), "/universe", "Show universe.", "fixed"),
    _cmd("/universe_active", "universe", "Universe active", ("universe", "active"), "/universe_active", "Show active universe.", "fixed"),
    _cmd("/universe_requirements", "universe", "Universe requirements", ("universe", "requirements"), "/universe_requirements", "Show requirements.", "fixed"),
    _cmd("/streams", "streams", "Streams", ("streams", "status"), "/streams", "Show stream status.", "fixed"),
    _cmd("/streams_plan", "streams", "Streams plan", ("streams", "plan"), "/streams_plan", "Show stream plan.", "fixed"),
    _cmd("/streams_setup", "streams", "Streams setup", ("streams", "setup"), "/streams_setup", "Set up streams.", "fixed"),
    _cmd("/optimizer_dry_run", "optimizer", "Optimizer dry run", ("optimizer", "dry-run"), "/optimizer_dry_run --strategy ID --trials N", "Validate optimizer route."),
    _cmd("/config", "config", "Config", ("config", "show"), "/config", "Show config.", "fixed"),
    _cmd("/config_validate", "config", "Config validate", ("config", "validate"), "/config_validate", "Validate config.", "fixed"),
    _cmd("/service", "service", "Service status", ("service", "status"), "/service", "Show service status.", "fixed"),
    _cmd("/service_install", "service", "Service install", ("service", "install"), "/service_install", "Install systemd service.", "fixed"),
    _cmd("/service_start", "service", "Service start", ("service", "start"), "/service_start", "Start service.", "fixed"),
    _cmd("/service_stop", "service", "Service stop", ("service", "stop"), "/service_stop", "Stop service.", "fixed"),
    _cmd("/service_restart", "service", "Service restart", ("service", "restart"), "/service_restart", "Restart service.", "fixed"),
    _cmd("/service_logs", "service", "Service logs", ("service", "logs"), "/service_logs [-n LINES]", "Show service logs."),
    _cmd("/service_enable", "service", "Service enable", ("service", "enable"), "/service_enable", "Enable service.", "fixed"),
    _cmd("/service_disable", "service", "Service disable", ("service", "disable"), "/service_disable", "Disable service.", "fixed"),
    _cmd("/events_schema_validate", "events", "Event schema validate", ("events", "schema", "validate"), "/events_schema_validate EVENT_TYPE", "Validate event schema."),
)

COMMAND_BY_SLASH: dict[str, TelegramCommandSpec] = {
    spec.slash: spec for spec in TELEGRAM_COMMANDS
}


REQUIRED_FAMILIES: frozenset[str] = frozenset(
    {
        "core",
        "storage",
        "pine",
        "strategy",
        "data",
        "accounts",
        "providers",
        "jobs",
        "state",
        "risk",
        "reports",
        "plugins",
        "universe",
        "streams",
        "optimizer",
        "config",
        "service",
    }
)


def catalog_families() -> set[str]:
    """Return families covered by the Telegram catalog."""

    return {spec.family for spec in TELEGRAM_COMMANDS}


def map_telegram_command(text: str) -> list[str]:
    """Map a Telegram slash command message to OpenPine CLI argv.

    Examples:
        /risk -> ["risk", "status"]
        /strategy_backtest s1 --from 2026-01-01 -> ["strategy", "backtest", ...]
        /op strategy list -> ["strategy", "list"]
    """

    try:
        parts = shlex.split(text.strip())
    except ValueError as exc:
        raise TelegramCommandError(f"invalid command quoting: {exc}") from exc

    if not parts:
        raise TelegramCommandError("empty command")

    slash = parts[0].split("@", 1)[0]
    if not slash.startswith("/"):
        raise TelegramCommandError("message is not a slash command")

    spec = COMMAND_BY_SLASH.get(slash)
    if spec is None:
        raise TelegramCommandError(f"unknown Telegram command: {slash}")

    rest = parts[1:]
    if slash in {"/help", "/menu"}:
        return list(spec.argv) + rest
    if slash == "/op":
        if not rest:
            raise TelegramCommandError("/op requires OpenPine CLI args")
        return rest
    if slash == "/strategy_error_clear":
        if not rest:
            raise TelegramCommandError("/strategy_error_clear requires STRATEGY_ID")
        return ["strategy", "error", rest[0], "clear", *rest[1:]]
    if spec.arg_mode == "fixed" and rest:
        raise TelegramCommandError(f"{slash} does not accept arguments")
    return list(spec.argv) + rest


def _button(text: str, callback_data: str) -> TelegramButtonSpec:
    return TelegramButtonSpec(text=text, callback_data=callback_data)


def inline_keyboard(
    rows: tuple[tuple[TelegramButtonSpec, ...], ...],
) -> dict[str, list[list[dict[str, str]]]]:
    """Convert button specs into Telegram inline_keyboard payload."""

    return {
        "inline_keyboard": [
            [
                {"text": button.text, "callback_data": button.callback_data}
                for button in row
            ]
            for row in rows
        ]
    }


def home_menu_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    """Main menu for common OpenPine actions."""

    return inline_keyboard(
        (
            (_button("Status", "op:status"), _button("Strategies", "op:strategies:list")),
            (_button("📁 Pine Sources", "op:pine:list"), _button("📊 Strategies", "op:strategies:list")),
            (_button("Risk", "op:risk:status"), _button("Data/Jobs", "op:menu:data_jobs")),
            (_button("Reports", "op:reports:list"), _button("Plugins", "op:plugins:list")),
            (_button("Streams", "op:streams:status"), _button("Config", "op:config:show")),
        )
    )


def confirm_delete_keyboard(strategy_id: str) -> dict[str, list[list[dict[str, str]]]]:
    """Confirmation keyboard for strategy deletion."""
    sid = _clean_callback_arg(strategy_id)
    return inline_keyboard(
        (
            (
                _button("✅ Yes, delete", f"op:strat:delete:{sid}"),
                _button("❌ Cancel", "op:strat:cancel_delete"),
            ),
        )
    )


def strategy_list_keyboard(strategies: list[dict[str, Any]]) -> dict[str, list[list[dict[str, str]]]]:
    """Build inline keyboard for strategy list.

    strategies: list of dicts with keys: strategy_id, name, status, mode
    status: running|paused|paper|live
    """
    rows: list[tuple[TelegramButtonSpec, ...]] = []

    for strat in strategies:
        sid = _clean_callback_arg(strat.get("strategy_id", ""))
        name = strat.get("name", sid)
        status = strat.get("status", "unknown")
        mode = strat.get("mode", "paper")

        # Status badge
        status_icon = {
            "running": "🟢",
            "paused": "⏸",
            "paper": "📝",
            "live": "💰",
        }.get(status, "⚪")
        mode_icon = "🐛" if mode == "backtest" else ("📝" if mode == "paper" else "💰")

        # Strategy row: name + status
        rows.append((_button(f"{status_icon} {name}", f"op:strat:show:{sid}"),))

        # Action row: Show, Pause/Resume, Live, Paper, Delete
        pause_resume_label = "Resume" if status == "paused" else "Pause"
        pause_cb = f"op:strat:resume:{sid}" if status == "paused" else f"op:strat:pause:{sid}"
        live_label = "Live stop" if status == "live" else "Live enable"
        live_cb = f"op:strat:live_stop:{sid}" if status == "live" else f"op:strat:live_enable:{sid}"
        paper_label = "Paper stop" if status == "paper" else "Paper start"
        paper_cb = f"op:strat:paper_stop:{sid}" if status == "paper" else f"op:strat:paper_start:{sid}"

        rows.append((
            _button("Show", f"op:strat:show:{sid}"),
            _button(pause_resume_label, pause_cb),
            _button(live_label, live_cb),
            _button(paper_label, paper_cb),
        ))
        rows.append((
            _button("🗑 Delete", f"op:strat:confirm_delete:{sid}"),
        ))

    # Navigation
    rows.append((
        _button("🔄 Refresh", "op:strat:refresh"),
        _button("🏠 Home", "op:home"),
    ))

    return inline_keyboard(tuple(rows))


def pine_list_keyboard(sources: list[dict[str, Any]]) -> dict[str, list[list[dict[str, str]]]]:
    """Build inline keyboard for Pine source list.

    sources: list of dicts with keys: id, name, active_artifact_id
    """
    rows: list[tuple[TelegramButtonSpec, ...]] = []

    for source in sources:
        name = source.get("name", source.get("id", "?"))
        sid = _clean_callback_arg(source.get("id", name))
        active = source.get("active_artifact_id")

        # Source row: name + active indicator
        active_icon = "✅" if active else "⚪"
        rows.append((
            _button(f"{active_icon} {name}", f"op:pine:show:{sid}"),
        ))

        # Action row: Show, Compile, Activate, Remove
        rows.append((
            _button("Show", f"op:pine:show:{sid}"),
            _button("Compile", f"op:pine:compile:{sid}"),
            _button("Activate", f"op:pine:activate:{sid}"),
            _button("Remove", f"op:pine:remove:{sid}"),
        ))

    # Navigation
    rows.append((
        _button("🔄 Refresh", "op:pine:refresh"),
        _button("🏠 Home", "op:home"),
    ))

    return inline_keyboard(tuple(rows))


def strategy_actions_keyboard(strategy_id: str) -> dict[str, list[list[dict[str, str]]]]:
    """Button layout for a single strategy."""

    sid = _clean_callback_arg(strategy_id)
    return inline_keyboard(
        (
            (
                _button("Show", f"op:strategy:show:{sid}"),
                _button("Status", f"op:strategy:status:{sid}"),
            ),
            (
                _button("Backtest", f"op:strategy:backtest:{sid}"),
                _button("Replay", f"op:strategy:replay:{sid}"),
            ),
            (
                _button("Pause", f"op:strategy:pause:{sid}"),
                _button("Resume", f"op:strategy:resume:{sid}"),
            ),
            (
                _button("Paper start", f"op:strategy:paper_start:{sid}"),
                _button("Paper stop", f"op:strategy:paper_stop:{sid}"),
            ),
            (
                _button("Live enable", f"op:strategy:live_enable:{sid}"),
                _button("Live stop", f"op:strategy:live_stop:{sid}"),
            ),
            (_button("Clear error", f"op:strategy:error_clear:{sid}"),),
        )
    )


def risk_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    """Risk controls."""

    return inline_keyboard(
        (
            (_button("Risk status", "op:risk:status"),),
            (
                _button("Kill switch ON", "op:risk:kill_switch_on"),
                _button("Kill switch OFF", "op:risk:kill_switch_off"),
            ),
        )
    )


def data_jobs_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    """Data and job controls."""

    return inline_keyboard(
        (
            (_button("Data status", "op:data:status"), _button("Data plan", "op:data:plan")),
            (_button("Jobs", "op:jobs:list"), _button("Queue", "op:queue:status")),
            (_button("Workers", "op:workers:status"), _button("Providers", "op:providers:list")),
        )
    )


def reports_keyboard() -> dict[str, list[list[dict[str, str]]]]:
    """Report shortcuts."""

    return inline_keyboard(
        (
            (_button("Reports list", "op:reports:list"),),
            (
                _button("Strategy summary", "op:reports:show:strategy_summary"),
                _button("Data coverage", "op:reports:show:data_coverage"),
            ),
            (_button("Worker health", "op:reports:show:worker_health"),),
        )
    )


STATIC_CALLBACKS: dict[str, tuple[str, ...]] = {
    "op:home": (),
    "op:menu": (),
    "op:menu:data_jobs": (),
    "op:status": ("doctor",),
    "op:strategies": ("strategy", "list"),
    "op:pine:list": (),
    "op:strategies:list": (),
    "op:strat:refresh": (),
    "op:strat:cancel_delete": (),
    "op:pine:refresh": (),
    "op:risk:status": ("risk", "status"),
    "op:risk:kill_switch_on": ("risk", "kill-switch", "on"),
    "op:risk:kill_switch_off": ("risk", "kill-switch", "off"),
    "op:data:status": ("data", "status"),
    "op:data:plan": ("data", "plan"),
    "op:jobs:list": ("jobs", "list"),
    "op:queue:status": ("queue", "status"),
    "op:workers:status": ("workers", "status"),
    "op:providers:list": ("providers", "list"),
    "op:reports:list": ("reports", "list"),
    "op:plugins:list": ("plugins", "list"),
    "op:streams:status": ("streams", "status"),
    "op:config:show": ("config", "show"),
}


def map_callback_data(callback_data: str) -> list[str]:
    """Map Telegram callback_data to OpenPine CLI argv.

    Menu-only callbacks return an empty list so the caller can render a menu
    instead of executing a CLI command.
    """

    if callback_data in STATIC_CALLBACKS:
        return list(STATIC_CALLBACKS[callback_data])

    parts = callback_data.split(":")
    if len(parts) < 4 or parts[0] != "op":
        raise TelegramCommandError(f"unknown callback data: {callback_data}")

    domain, action, value = parts[1], parts[2], ":".join(parts[3:])
    if not value:
        raise TelegramCommandError(f"callback data missing argument: {callback_data}")

    if domain == "strategy":
        return _map_strategy_callback(action, value)
    if domain == "strat":
        # Short form for strategy list item actions
        return _map_strat_list_callback(action, value)
    if domain == "pine":
        return _map_pine_callback(action, value)
    if domain == "reports" and action == "show":
        return ["reports", "show", value]
    raise TelegramCommandError(f"unknown callback data: {callback_data}")


def _map_strategy_callback(action: str, strategy_id: str) -> list[str]:
    simple = {
        "show": ("strategy", "show"),
        "status": ("strategy", "status"),
        "pause": ("strategy", "pause"),
        "resume": ("strategy", "resume"),
        "remove": ("strategy", "remove"),
        "backtest": ("strategy", "backtest"),
        "replay": ("strategy", "replay"),
    }
    if action in simple:
        return [*simple[action], strategy_id]
    if action == "paper_start":
        return ["strategy", "paper", strategy_id, "start"]
    if action == "paper_stop":
        return ["strategy", "paper", strategy_id, "stop"]
    if action == "live_enable":
        return ["strategy", "live", strategy_id, "enable"]
    if action == "live_start":
        return ["strategy", "live", strategy_id, "start"]
    if action == "live_stop":
        return ["strategy", "live", strategy_id, "stop"]
    if action == "error_clear":
        return ["strategy", "error", strategy_id, "clear"]
    raise TelegramCommandError(f"unknown strategy callback action: {action}")


def _map_strat_list_callback(action: str, strategy_id: str) -> list[str]:
    """Map strategy list item callbacks (op:strat:*)."""
    simple = {
        "show": ("strategy", "show"),
        "status": ("strategy", "status"),
        "pause": ("strategy", "pause"),
        "resume": ("strategy", "resume"),
        "delete": ("strategy", "remove"),
        "backtest": ("strategy", "backtest"),
        "replay": ("strategy", "replay"),
    }
    if action in simple:
        return [*simple[action], strategy_id]
    if action == "paper_start":
        return ["strategy", "paper", strategy_id, "start"]
    if action == "paper_stop":
        return ["strategy", "paper", strategy_id, "stop"]
    if action == "live_enable":
        return ["strategy", "live", strategy_id, "enable"]
    if action == "live_start":
        return ["strategy", "live", strategy_id, "start"]
    if action == "live_stop":
        return ["strategy", "live", strategy_id, "stop"]
    if action == "error_clear":
        return ["strategy", "error", strategy_id, "clear"]
    raise TelegramCommandError(f"unknown strat list callback action: {action}")


def _map_pine_callback(action: str, name: str) -> list[str]:
    """Map Pine source callbacks (op:pine:*)."""
    simple = {
        "show": ("pine", "show"),
        "compile": ("pine", "pine-compile"),
        "remove": ("pine", "remove"),
        "artifacts": ("pine", "artifacts"),
        "inspect": ("pine", "inspect"),
        "versions": ("pine", "versions"),
    }
    if action in simple:
        return [*simple[action], name]
    if action == "activate":
        # activate needs artifact_id which we don't have from the callback alone
        # Return a placeholder that prompts for artifact selection
        return ["pine", "artifacts", name]
    raise TelegramCommandError(f"unknown pine callback action: {action}")


def _clean_callback_arg(value: str) -> str:
    """Reject callback args that would break colon-delimited callback_data."""

    if not value or ":" in value:
        raise TelegramCommandError("callback argument must be non-empty and cannot contain ':'")
    return value


def generate_help_text(family: str | None = None) -> str:
    """Generate concise help text from the command catalog."""

    specs = TELEGRAM_COMMANDS
    if family:
        specs = tuple(spec for spec in specs if spec.family == family)
        if not specs:
            raise TelegramCommandError(f"unknown help family: {family}")

    lines = ["OpenPine Telegram commands"]
    current_family: str | None = None
    for spec in sorted(specs, key=lambda item: (item.family, item.slash)):
        if spec.family != current_family:
            current_family = spec.family
            lines.append("")
            lines.append(f"{current_family}:")
        lines.append(f"  {spec.usage} - {spec.description}")
    lines.append("")
    lines.append("Fallback: /op <any openpine CLI args>")
    return "\n".join(lines)


__all__ = [
    "COMMAND_BY_SLASH",
    "REQUIRED_FAMILIES",
    "STATIC_CALLBACKS",
    "TELEGRAM_COMMANDS",
    "TelegramButtonSpec",
    "TelegramCommandError",
    "TelegramCommandSpec",
    "catalog_families",
    "confirm_delete_keyboard",
    "data_jobs_keyboard",
    "generate_help_text",
    "home_menu_keyboard",
    "inline_keyboard",
    "map_callback_data",
    "map_telegram_command",
    "pine_list_keyboard",
    "reports_keyboard",
    "risk_keyboard",
    "strategy_actions_keyboard",
    "strategy_list_keyboard",
]
