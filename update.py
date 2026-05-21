#!/usr/bin/env python3
"""
update.py — Daily refresh of product-analytics.html data arrays from Snowflake.

Reads product-analytics.html, re-queries Snowflake for all metrics, and rewrites
the JS data block (between <!-- DATA-START --> and <!-- DATA-END --> markers,
or between the const ALL_WEEKS line and the closing D = {...} object if markers
are absent).

Environment variables required:
  SNOWFLAKE_ACCOUNT    — e.g. abc12345.us-east-1
  SNOWFLAKE_USER       — service account username
  SNOWFLAKE_PASSWORD   — service account password
  SNOWFLAKE_WAREHOUSE  — defaults to MCP_WH
  SNOWFLAKE_ROLE       — defaults to READER
"""

import os
import re
import sys
import datetime
import snowflake.connector


# ── Snowflake connection ────────────────────────────────────────────────────

def get_conn():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        password=os.environ["SNOWFLAKE_PASSWORD"],
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "MCP_WH"),
        role=os.environ.get("SNOWFLAKE_ROLE", "READER"),
        database="ANALYTICS",
        schema="PROD_INTERNAL_BI",
    )


def fetchall(conn, sql):
    cur = conn.cursor()
    cur.execute(sql)
    return cur.fetchall()


# ── Date helpers ────────────────────────────────────────────────────────────

def last_complete_monday(today):
    """Return the Monday of the most-recent fully-completed ISO week.
    A week is complete once its Sunday has passed (i.e. today > Sunday of that week).
    ISO week: Mon–Sun.
    """
    # Find the Monday of the current week
    days_since_monday = today.weekday()  # Mon=0
    this_monday = today - datetime.timedelta(days=days_since_monday)
    # The most-recently completed week ended last Sunday, so its Monday is one week back
    last_monday = this_monday - datetime.timedelta(weeks=1)
    return last_monday


def week_label(monday_date):
    """'Jan 19' style label for a Monday date."""
    return monday_date.strftime("%-m/%d").lstrip("0")  # fall back below if needed


def week_label_mmm(monday_date):
    """'Jan 19' style label."""
    return monday_date.strftime("%b %-d")


def last_complete_month_start(today):
    """First day of the most-recently completed calendar month."""
    # The current month is incomplete, so go back one month
    first_this_month = today.replace(day=1)
    last_month_end = first_this_month - datetime.timedelta(days=1)
    return last_month_end.replace(day=1)


def month_label(month_start):
    """'Jun' style label."""
    return month_start.strftime("%b")


# ── Query builders ──────────────────────────────────────────────────────────

MC_INTERNAL_CLIENTS = ("'Monte Carlo'", "'Monte Carlo Demo'", "'Monte Carlo Intro'", "'mc-production-tests'")
MC_INTERNAL_ACCOUNT_IDS = (
    "'d72bc4d1-f675-4d72-82a1-418220cca709'",
    "'1a371a1e-42a0-4817-8a25-dc1487a7b0d0'",
    "'8038c636-7b67-4412-a620-a26ca9dfcfa2'",
)

NAMED_CUSTOMERS = {
    "Xometry": "agCustXom",
    "Axios2": "agCustAxios",
    "Morningstar": "agCustMorn",
    "entain": "agCustEnt",
    "Velocity Global": "agCustVel",
}

AGENT_TYPES = {
    "tsa": "tsaAgent",
    "triage": "triageAgent",
    "oa_alert_explain": "oaAlert",
    "oa_sidebar": "oaSidebar",
    "oa_fullpage": "oaFullPage",
    "oa_monitor_explain": "oaMonitor",
}

MCP_CLIENT_TYPES = {
    "claude_code": "mcpClaude",
    "node": "mcpNode",
    "cursor": "mcpCursor",
    "vscode": "mcpVscode",
    "python": "mcpPython",
    # everything else → mcpOther (other + unknown + mcp_remote_proxy + ...)
}

MONITOR_TYPES = {
    "stats": "monStats",
    "custom_sql": "monSql",
    "table_monitor": "monTable",
    "validation": "monValid",
    "volume": "monVol",
    "freshness": "monFresh",
    "metric_comparison": "monMetric",
    "query_perf": "monPerf",
}

DASHBOARD_TABS = {
    "data-operations": "dashDataOps",
    "data-quality": "dashDQ",
    "daily-table-health": "dashDaily",
    "coverage": "dashCoverage",
    "activity": "dashActivity",
    "custom": "dashCustom",
}

ALERT_ACTIONS = {
    "incident_status_update": "alertStatus",
    "incident_owner_update": "alertOwner",
    "comment": "alertComment",
    "incident_detector_feedback": "alertFb",
    "jira_ticket_created": "alertJira",
}

AGENT_MONITOR_TYPES = {
    "agent_evaluation": "agEval",
    "agent_metric": "agMetric",
    "agent_trajectory": "agTraj",
    "agent_validation": "agValidat",
}


# ── Weekly queries ──────────────────────────────────────────────────────────

def query_weekly_wau(conn, week_starts):
    """Distinct active users per ISO week from GUT_SUMMARY."""
    min_d = week_starts[0].isoformat()
    max_d = (week_starts[-1] + datetime.timedelta(days=7)).isoformat()
    rows = fetchall(conn, f"""
        SELECT FIRST_WK_DATE, COUNT(DISTINCT MP_EMAIL) AS wau
        FROM GUT_SUMMARY
        WHERE FIRST_WK_DATE >= '{min_d}' AND FIRST_WK_DATE < '{max_d}'
          AND IS_ACTIVE_THIS_WEEK = TRUE
        GROUP BY 1
    """)
    return {r[0]: int(r[1]) for r in rows}


def query_weekly_agent(conn, week_starts):
    """Distinct users per agent type per ISO week from GUT_AGENT_SESSIONS."""
    min_d = week_starts[0].isoformat()
    max_d = (week_starts[-1] + datetime.timedelta(days=7)).isoformat()
    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('week', START_SESSION::DATE) AS wk,
               AGENT_TYPE,
               COUNT(DISTINCT MP_EMAIL) AS users
        FROM GUT_AGENT_SESSIONS
        WHERE START_SESSION >= '{min_d}' AND START_SESSION < '{max_d}'
        GROUP BY 1, 2
    """)
    result = {}
    for (wk, agent_type, users) in rows:
        result.setdefault(wk, {})[agent_type] = int(users)
    return result


def query_weekly_mcp(conn, week_starts):
    """Distinct users per MCP client type per ISO week from GUT_MCP_TOOL_CALLS."""
    min_d = week_starts[0].isoformat()
    max_d = (week_starts[-1] + datetime.timedelta(days=7)).isoformat()
    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('week', DATE) AS wk,
               CLIENT_TYPE,
               COUNT(DISTINCT MP_EMAIL) AS users
        FROM GUT_MCP_TOOL_CALLS
        WHERE DATE >= '{min_d}' AND DATE < '{max_d}'
        GROUP BY 1, 2
    """)
    result = {}
    for (wk, client_type, users) in rows:
        result.setdefault(wk, {})[client_type] = int(users)
    return result


def query_weekly_monitors(conn, week_starts):
    """Monitor creation counts per type per ISO week from GUT_MONITOR_CREATION."""
    min_d = week_starts[0].isoformat()
    max_d = (week_starts[-1] + datetime.timedelta(days=7)).isoformat()
    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('week', DATE::DATE) AS wk,
               TYPE,
               COUNT(*) AS cnt
        FROM GUT_MONITOR_CREATION
        WHERE DATE >= '{min_d}' AND DATE < '{max_d}'
          AND TYPE IN ('stats','custom_sql','table_monitor','validation','volume',
                       'freshness','metric_comparison','query_perf')
        GROUP BY 1, 2
    """)
    result = {}
    for (wk, mon_type, cnt) in rows:
        result.setdefault(wk, {})[mon_type] = int(cnt)
    return result


def query_weekly_agent_monitors(conn, week_starts):
    """Agent monitor creation by customer and type per ISO week."""
    min_d = week_starts[0].isoformat()
    max_d = (week_starts[-1] + datetime.timedelta(days=7)).isoformat()
    mc_clients = ", ".join(MC_INTERNAL_CLIENTS)
    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('week', DATE::DATE) AS wk,
               CLIENT,
               TYPE,
               COUNT(*) AS cnt
        FROM GUT_MONITOR_CREATION
        WHERE DATE >= '{min_d}' AND DATE < '{max_d}'
          AND TYPE IN ('agent_evaluation','agent_metric','agent_trajectory','agent_validation')
          AND CLIENT NOT IN ({mc_clients})
        GROUP BY 1, 2, 3
    """)
    result = {}
    for (wk, client, mon_type, cnt) in rows:
        result.setdefault(wk, {})
        result[wk].setdefault("by_client", {}).setdefault(client or "", 0)
        result[wk]["by_client"][client or ""] += int(cnt)
        result[wk].setdefault("by_type", {}).setdefault(mon_type, 0)
        result[wk]["by_type"][mon_type] += int(cnt)
    return result


def query_weekly_dashboards(conn, week_starts):
    """Monthly active dashboard viewers per tab per ISO week."""
    min_d = week_starts[0].isoformat()
    max_d = (week_starts[-1] + datetime.timedelta(days=7)).isoformat()
    tabs = ", ".join(f"'{t}'" for t in DASHBOARD_TABS)
    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('week', START_SESSION::DATE) AS wk,
               TAB,
               COUNT(DISTINCT MP_EMAIL) AS viewers
        FROM GUT_DASHBOARDS_SESSIONS_FS
        WHERE START_SESSION >= '{min_d}' AND START_SESSION < '{max_d}'
          AND TAB IN ({tabs})
        GROUP BY 1, 2
    """)
    result = {}
    for (wk, tab, viewers) in rows:
        result.setdefault(wk, {})[tab] = int(viewers)
    return result


def query_weekly_alerts(conn, week_starts):
    """Distinct incident IDs per action type per ISO week from GUT_INCIDENTS_ENGAGEMENT."""
    min_d = week_starts[0].isoformat()
    max_d = (week_starts[-1] + datetime.timedelta(days=7)).isoformat()
    actions = ", ".join(f"'{a}'" for a in ALERT_ACTIONS)
    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('week', ACTION_TIME::DATE) AS wk,
               ACTION,
               COUNT(DISTINCT INCIDENT_ID) AS cnt
        FROM GUT_INCIDENTS_ENGAGEMENT
        WHERE ACTION_TIME >= '{min_d}' AND ACTION_TIME < '{max_d}'
          AND ACTION IN ({actions})
        GROUP BY 1, 2
    """)
    result = {}
    for (wk, action, cnt) in rows:
        result.setdefault(wk, {})[action] = int(cnt)
    return result


# ── Monthly queries ─────────────────────────────────────────────────────────

def query_monthly_wau(conn, month_starts):
    """Average of weekly WAU values whose FIRST_WK_DATE falls in each calendar month."""
    min_d = month_starts[0].isoformat()
    # last month's end
    last_start = month_starts[-1]
    if last_start.month == 12:
        max_d = last_start.replace(year=last_start.year + 1, month=1, day=1).isoformat()
    else:
        max_d = last_start.replace(month=last_start.month + 1, day=1).isoformat()

    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('month', FIRST_WK_DATE) AS mo,
               ROUND(AVG(wau)) AS monthly_wau
        FROM (
            SELECT FIRST_WK_DATE, COUNT(DISTINCT MP_EMAIL) AS wau
            FROM GUT_SUMMARY
            WHERE FIRST_WK_DATE >= '{min_d}' AND FIRST_WK_DATE < '{max_d}'
              AND IS_ACTIVE_THIS_WEEK = TRUE
            GROUP BY 1
        )
        GROUP BY 1
    """)
    return {r[0]: int(r[1]) for r in rows}


def query_monthly_agent(conn, month_starts):
    """Distinct users per agent type per calendar month from GUT_AGENT_SESSIONS."""
    min_d = month_starts[0].isoformat()
    last_start = month_starts[-1]
    if last_start.month == 12:
        max_d = last_start.replace(year=last_start.year + 1, month=1, day=1).isoformat()
    else:
        max_d = last_start.replace(month=last_start.month + 1, day=1).isoformat()

    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('month', START_SESSION::DATE) AS mo,
               AGENT_TYPE,
               COUNT(DISTINCT MP_EMAIL) AS users
        FROM GUT_AGENT_SESSIONS
        WHERE START_SESSION >= '{min_d}' AND START_SESSION < '{max_d}'
        GROUP BY 1, 2
    """)
    result = {}
    for (mo, agent_type, users) in rows:
        result.setdefault(mo, {})[agent_type] = int(users)
    return result


def query_monthly_mcp(conn, month_starts):
    """Distinct users per MCP client type per calendar month from GUT_MCP_TOOL_CALLS."""
    min_d = month_starts[0].isoformat()
    last_start = month_starts[-1]
    if last_start.month == 12:
        max_d = last_start.replace(year=last_start.year + 1, month=1, day=1).isoformat()
    else:
        max_d = last_start.replace(month=last_start.month + 1, day=1).isoformat()

    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('month', DATE) AS mo,
               CLIENT_TYPE,
               COUNT(DISTINCT MP_EMAIL) AS users
        FROM GUT_MCP_TOOL_CALLS
        WHERE DATE >= '{min_d}' AND DATE < '{max_d}'
        GROUP BY 1, 2
    """)
    result = {}
    for (mo, client_type, users) in rows:
        result.setdefault(mo, {})[client_type] = int(users)
    return result


def query_monthly_monitors(conn, month_starts):
    """Monitor creation counts per type per calendar month."""
    min_d = month_starts[0].isoformat()
    last_start = month_starts[-1]
    if last_start.month == 12:
        max_d = last_start.replace(year=last_start.year + 1, month=1, day=1).isoformat()
    else:
        max_d = last_start.replace(month=last_start.month + 1, day=1).isoformat()

    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('month', DATE::DATE) AS mo,
               TYPE,
               COUNT(*) AS cnt
        FROM GUT_MONITOR_CREATION
        WHERE DATE >= '{min_d}' AND DATE < '{max_d}'
          AND TYPE IN ('stats','custom_sql','table_monitor','validation','volume',
                       'freshness','metric_comparison','query_perf')
        GROUP BY 1, 2
    """)
    result = {}
    for (mo, mon_type, cnt) in rows:
        result.setdefault(mo, {})[mon_type] = int(cnt)
    return result


def query_monthly_agent_monitors(conn, month_starts):
    """Agent monitor creation by customer and type per calendar month."""
    min_d = month_starts[0].isoformat()
    last_start = month_starts[-1]
    if last_start.month == 12:
        max_d = last_start.replace(year=last_start.year + 1, month=1, day=1).isoformat()
    else:
        max_d = last_start.replace(month=last_start.month + 1, day=1).isoformat()
    mc_clients = ", ".join(MC_INTERNAL_CLIENTS)

    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('month', DATE::DATE) AS mo,
               CLIENT,
               TYPE,
               COUNT(*) AS cnt
        FROM GUT_MONITOR_CREATION
        WHERE DATE >= '{min_d}' AND DATE < '{max_d}'
          AND TYPE IN ('agent_evaluation','agent_metric','agent_trajectory','agent_validation')
          AND CLIENT NOT IN ({mc_clients})
        GROUP BY 1, 2, 3
    """)
    result = {}
    for (mo, client, mon_type, cnt) in rows:
        result.setdefault(mo, {})
        result[mo].setdefault("by_client", {}).setdefault(client or "", 0)
        result[mo]["by_client"][client or ""] += int(cnt)
        result[mo].setdefault("by_type", {}).setdefault(mon_type, 0)
        result[mo]["by_type"][mon_type] += int(cnt)
    return result


def query_monthly_dashboards(conn, month_starts):
    """Monthly active dashboard viewers per tab per calendar month."""
    min_d = month_starts[0].isoformat()
    last_start = month_starts[-1]
    if last_start.month == 12:
        max_d = last_start.replace(year=last_start.year + 1, month=1, day=1).isoformat()
    else:
        max_d = last_start.replace(month=last_start.month + 1, day=1).isoformat()
    tabs = ", ".join(f"'{t}'" for t in DASHBOARD_TABS)

    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('month', START_SESSION::DATE) AS mo,
               TAB,
               COUNT(DISTINCT MP_EMAIL) AS viewers
        FROM GUT_DASHBOARDS_SESSIONS_FS
        WHERE START_SESSION >= '{min_d}' AND START_SESSION < '{max_d}'
          AND TAB IN ({tabs})
        GROUP BY 1, 2
    """)
    result = {}
    for (mo, tab, viewers) in rows:
        result.setdefault(mo, {})[tab] = int(viewers)
    return result


def query_monthly_alerts(conn, month_starts):
    """Distinct incident IDs per action type per calendar month."""
    min_d = month_starts[0].isoformat()
    last_start = month_starts[-1]
    if last_start.month == 12:
        max_d = last_start.replace(year=last_start.year + 1, month=1, day=1).isoformat()
    else:
        max_d = last_start.replace(month=last_start.month + 1, day=1).isoformat()
    actions = ", ".join(f"'{a}'" for a in ALERT_ACTIONS)

    rows = fetchall(conn, f"""
        SELECT DATE_TRUNC('month', ACTION_TIME::DATE) AS mo,
               ACTION,
               COUNT(DISTINCT INCIDENT_ID) AS cnt
        FROM GUT_INCIDENTS_ENGAGEMENT
        WHERE ACTION_TIME >= '{min_d}' AND ACTION_TIME < '{max_d}'
          AND ACTION IN ({actions})
        GROUP BY 1, 2
    """)
    result = {}
    for (mo, action, cnt) in rows:
        result.setdefault(mo, {})[action] = int(cnt)
    return result


# ── Revenue query ───────────────────────────────────────────────────────────

def query_revenue(conn, start_date, end_date):
    """Daily Troubleshooting Agent revenue summed across all customers."""
    rows = fetchall(conn, f"""
        SELECT AS_OF_DATE, SUM(TROUBLESHOOTING_AGENT_DAILY_REVENUE) AS rev
        FROM FINANCE_CONSUMPTION_REVENUE_EXPLORER
        WHERE AS_OF_DATE >= '{start_date.isoformat()}' AND AS_OF_DATE <= '{end_date.isoformat()}'
        GROUP BY 1
        ORDER BY 1
    """)
    return {r[0]: r[1] for r in rows}


# ── Data assembly ───────────────────────────────────────────────────────────

def build_week_range(today, n_weeks=52):
    """
    Return list of Monday dates for the most-recent n_weeks complete ISO weeks,
    oldest first. Capped at the start of 2025.
    """
    last_monday = last_complete_monday(today)
    weeks = []
    for i in range(n_weeks - 1, -1, -1):
        weeks.append(last_monday - datetime.timedelta(weeks=i))
    # Filter to >= 2025-01-01
    cutoff = datetime.date(2025, 1, 1)
    return [w for w in weeks if w >= cutoff]


def build_month_range(today, n_months=24):
    """
    Return list of first-of-month dates for the most-recent n_months complete
    calendar months, oldest first. Capped at 2025-01-01.
    """
    last_month = last_complete_month_start(today)
    months = []
    cur = last_month
    for _ in range(n_months):
        months.append(cur)
        if cur.month == 1:
            cur = cur.replace(year=cur.year - 1, month=12)
        else:
            cur = cur.replace(month=cur.month - 1)
    months.reverse()
    cutoff = datetime.date(2025, 1, 1)
    return [m for m in months if m >= cutoff]


def get_agent_customer_val(by_client, customer_name):
    return by_client.get(customer_name, 0)


def get_agent_other_val(by_client):
    named = set(NAMED_CUSTOMERS.keys())
    return sum(v for k, v in by_client.items() if k not in named)


def build_wd(conn, week_starts):
    """Build the WD dict with arrays indexed by week."""
    print(f"  Querying weekly data for {len(week_starts)} weeks "
          f"({week_starts[0]} – {week_starts[-1]})…")

    wau_data = query_weekly_wau(conn, week_starts)
    agent_data = query_weekly_agent(conn, week_starts)
    mcp_data = query_weekly_mcp(conn, week_starts)
    mon_data = query_weekly_monitors(conn, week_starts)
    ag_mon_data = query_weekly_agent_monitors(conn, week_starts)
    dash_data = query_weekly_dashboards(conn, week_starts)
    alert_data = query_weekly_alerts(conn, week_starts)

    wd = {k: [] for k in [
        "wau",
        "tsaAgent", "triageAgent", "oaAlert", "oaSidebar", "oaFullPage", "oaMonitor",
        "mcpOther", "mcpClaude", "mcpNode", "mcpCursor", "mcpVscode", "mcpPython",
        "monStats", "monSql", "monTable", "monValid", "monVol", "monFresh", "monMetric", "monPerf",
        "dashDataOps", "dashDQ", "dashDaily", "dashCoverage", "dashActivity", "dashCustom",
        "agCustXom", "agCustAxios", "agCustMorn", "agCustEnt", "agCustVel", "agCustOther",
        "agEval", "agMetric", "agTraj", "agValidat",
        "alertStatus", "alertOwner", "alertComment", "alertFb", "alertJira",
    ]}

    for wk in week_starts:
        ag = agent_data.get(wk, {})
        mcp = mcp_data.get(wk, {})
        mon = mon_data.get(wk, {})
        ag_mon = ag_mon_data.get(wk, {})
        by_client = ag_mon.get("by_client", {})
        by_type = ag_mon.get("by_type", {})
        dash = dash_data.get(wk, {})
        alert = alert_data.get(wk, {})

        wd["wau"].append(wau_data.get(wk, 0))

        wd["tsaAgent"].append(ag.get("tsa", 0))
        wd["triageAgent"].append(ag.get("triage", 0))
        wd["oaAlert"].append(ag.get("oa_alert_explain", 0))
        wd["oaSidebar"].append(ag.get("oa_sidebar", 0))
        wd["oaFullPage"].append(ag.get("oa_fullpage", 0))
        wd["oaMonitor"].append(ag.get("oa_monitor_explain", 0))

        # MCP: other = sum of all client types not in the named set
        named_mcp = set(MCP_CLIENT_TYPES.keys())
        mcp_other = sum(v for k, v in mcp.items() if k not in named_mcp)
        wd["mcpOther"].append(mcp_other)
        wd["mcpClaude"].append(mcp.get("claude_code", 0))
        wd["mcpNode"].append(mcp.get("node", 0))
        wd["mcpCursor"].append(mcp.get("cursor", 0))
        wd["mcpVscode"].append(mcp.get("vscode", 0))
        wd["mcpPython"].append(mcp.get("python", 0))

        wd["monStats"].append(mon.get("stats", 0))
        wd["monSql"].append(mon.get("custom_sql", 0))
        wd["monTable"].append(mon.get("table_monitor", 0))
        wd["monValid"].append(mon.get("validation", 0))
        wd["monVol"].append(mon.get("volume", 0))
        wd["monFresh"].append(mon.get("freshness", 0))
        wd["monMetric"].append(mon.get("metric_comparison", 0))
        wd["monPerf"].append(mon.get("query_perf", 0))

        wd["dashDataOps"].append(dash.get("data-operations", 0))
        wd["dashDQ"].append(dash.get("data-quality", 0))
        wd["dashDaily"].append(dash.get("daily-table-health", 0))
        wd["dashCoverage"].append(dash.get("coverage", 0))
        wd["dashActivity"].append(dash.get("activity", 0))
        wd["dashCustom"].append(dash.get("custom", 0))

        wd["agCustXom"].append(get_agent_customer_val(by_client, "Xometry"))
        wd["agCustAxios"].append(get_agent_customer_val(by_client, "Axios2"))
        wd["agCustMorn"].append(get_agent_customer_val(by_client, "Morningstar"))
        wd["agCustEnt"].append(get_agent_customer_val(by_client, "entain"))
        wd["agCustVel"].append(get_agent_customer_val(by_client, "Velocity Global"))
        wd["agCustOther"].append(get_agent_other_val(by_client))

        wd["agEval"].append(by_type.get("agent_evaluation", 0))
        wd["agMetric"].append(by_type.get("agent_metric", 0))
        wd["agTraj"].append(by_type.get("agent_trajectory", 0))
        wd["agValidat"].append(by_type.get("agent_validation", 0))

        wd["alertStatus"].append(alert.get("incident_status_update", 0))
        wd["alertOwner"].append(alert.get("incident_owner_update", 0))
        wd["alertComment"].append(alert.get("comment", 0))
        wd["alertFb"].append(alert.get("incident_detector_feedback", 0))
        wd["alertJira"].append(alert.get("jira_ticket_created", 0))

    return wd


def build_d(conn, month_starts):
    """Build the D dict with arrays indexed by month."""
    print(f"  Querying monthly data for {len(month_starts)} months "
          f"({month_starts[0]} – {month_starts[-1]})…")

    wau_data = query_monthly_wau(conn, month_starts)
    agent_data = query_monthly_agent(conn, month_starts)
    mcp_data = query_monthly_mcp(conn, month_starts)
    mon_data = query_monthly_monitors(conn, month_starts)
    ag_mon_data = query_monthly_agent_monitors(conn, month_starts)
    dash_data = query_monthly_dashboards(conn, month_starts)
    alert_data = query_monthly_alerts(conn, month_starts)

    d = {k: [] for k in [
        "wau",
        "tsaAgent", "triageAgent", "oaAlert", "oaSidebar", "oaFullPage", "oaMonitor",
        "mcpOther", "mcpClaude", "mcpNode", "mcpCursor", "mcpVscode", "mcpPython",
        "monStats", "monSql", "monTable", "monValid", "monVol", "monFresh", "monMetric", "monPerf",
        "dashDataOps", "dashDQ", "dashDaily", "dashCoverage", "dashActivity", "dashCustom",
        "agCustXom", "agCustAxios", "agCustMorn", "agCustEnt", "agCustVel", "agCustOther",
        "agEval", "agMetric", "agTraj", "agValidat",
        "alertStatus", "alertOwner", "alertComment", "alertFb", "alertJira",
    ]}

    for mo in month_starts:
        ag = agent_data.get(mo, {})
        mcp = mcp_data.get(mo, {})
        mon = mon_data.get(mo, {})
        ag_mon = ag_mon_data.get(mo, {})
        by_client = ag_mon.get("by_client", {})
        by_type = ag_mon.get("by_type", {})
        dash = dash_data.get(mo, {})
        alert = alert_data.get(mo, {})

        d["wau"].append(wau_data.get(mo, 0))

        d["tsaAgent"].append(ag.get("tsa", 0))
        d["triageAgent"].append(ag.get("triage", 0))
        d["oaAlert"].append(ag.get("oa_alert_explain", 0))
        d["oaSidebar"].append(ag.get("oa_sidebar", 0))
        d["oaFullPage"].append(ag.get("oa_fullpage", 0))
        d["oaMonitor"].append(ag.get("oa_monitor_explain", 0))

        named_mcp = set(MCP_CLIENT_TYPES.keys())
        mcp_other = sum(v for k, v in mcp.items() if k not in named_mcp)
        d["mcpOther"].append(mcp_other)
        d["mcpClaude"].append(mcp.get("claude_code", 0))
        d["mcpNode"].append(mcp.get("node", 0))
        d["mcpCursor"].append(mcp.get("cursor", 0))
        d["mcpVscode"].append(mcp.get("vscode", 0))
        d["mcpPython"].append(mcp.get("python", 0))

        d["monStats"].append(mon.get("stats", 0))
        d["monSql"].append(mon.get("custom_sql", 0))
        d["monTable"].append(mon.get("table_monitor", 0))
        d["monValid"].append(mon.get("validation", 0))
        d["monVol"].append(mon.get("volume", 0))
        d["monFresh"].append(mon.get("freshness", 0))
        d["monMetric"].append(mon.get("metric_comparison", 0))
        d["monPerf"].append(mon.get("query_perf", 0))

        d["dashDataOps"].append(dash.get("data-operations", 0))
        d["dashDQ"].append(dash.get("data-quality", 0))
        d["dashDaily"].append(dash.get("daily-table-health", 0))
        d["dashCoverage"].append(dash.get("coverage", 0))
        d["dashActivity"].append(dash.get("activity", 0))
        d["dashCustom"].append(dash.get("custom", 0))

        d["agCustXom"].append(get_agent_customer_val(by_client, "Xometry"))
        d["agCustAxios"].append(get_agent_customer_val(by_client, "Axios2"))
        d["agCustMorn"].append(get_agent_customer_val(by_client, "Morningstar"))
        d["agCustEnt"].append(get_agent_customer_val(by_client, "entain"))
        d["agCustVel"].append(get_agent_customer_val(by_client, "Velocity Global"))
        d["agCustOther"].append(get_agent_other_val(by_client))

        d["agEval"].append(by_type.get("agent_evaluation", 0))
        d["agMetric"].append(by_type.get("agent_metric", 0))
        d["agTraj"].append(by_type.get("agent_trajectory", 0))
        d["agValidat"].append(by_type.get("agent_validation", 0))

        d["alertStatus"].append(alert.get("incident_status_update", 0))
        d["alertOwner"].append(alert.get("incident_owner_update", 0))
        d["alertComment"].append(alert.get("comment", 0))
        d["alertFb"].append(alert.get("incident_detector_feedback", 0))
        d["alertJira"].append(alert.get("jira_ticket_created", 0))

    return d


# ── JS serialisation helpers ────────────────────────────────────────────────

def js_array(values, indent=4):
    """Render a Python list as a compact JS array, null for None."""
    items = []
    for v in values:
        if v is None:
            items.append("null")
        else:
            items.append(str(v))
    return "[" + ",".join(items) + "]"


def js_obj_key(key, values, indent=4):
    spaces = " " * indent
    return f"{spaces}{key}: {js_array(values)},"


def render_wd(wd, week_labels):
    lines = []
    lines.append(f"  const ALL_WEEKS = {js_array(week_labels, 0)};")
    lines.append("  const WD = {")
    for key in [
        "wau",
        "tsaAgent", "triageAgent", "oaAlert", "oaSidebar", "oaFullPage", "oaMonitor",
        "mcpOther", "mcpClaude", "mcpNode", "mcpCursor", "mcpVscode", "mcpPython",
        "monStats", "monSql", "monTable", "monValid", "monVol", "monFresh", "monMetric", "monPerf",
        "dashDataOps", "dashDQ", "dashDaily", "dashCoverage", "dashActivity", "dashCustom",
        "agCustXom", "agCustAxios", "agCustMorn", "agCustEnt", "agCustVel", "agCustOther",
        "agEval", "agMetric", "agTraj", "agValidat",
        "alertStatus", "alertOwner", "alertComment", "alertFb", "alertJira",
    ]:
        lines.append(js_obj_key(key, wd[key]))
    lines.append("  };")
    return "\n".join(lines)


def render_d(d, month_labels):
    lines = []
    lines.append(f"  const ALL_MONTHS = {js_array(month_labels, 0)};")
    lines.append("")
    lines.append("  const D = {")
    for key in [
        "wau",
        "tsaAgent", "triageAgent", "oaAlert", "oaSidebar", "oaFullPage", "oaMonitor",
        "mcpOther", "mcpClaude", "mcpNode", "mcpCursor", "mcpVscode", "mcpPython",
        "monStats", "monSql", "monTable", "monValid", "monVol", "monFresh", "monMetric", "monPerf",
        "agCustXom", "agCustAxios", "agCustMorn", "agCustEnt", "agCustVel", "agCustOther",
        "agEval", "agMetric", "agTraj", "agValidat",
        "alertStatus", "alertOwner", "alertComment", "alertFb", "alertJira",
        "dashDataOps", "dashDQ", "dashDaily", "dashCoverage", "dashActivity", "dashCustom",
    ]:
        lines.append(js_obj_key(key, d[key]))
    lines.append("  };")
    return "\n".join(lines)


def render_revenue(rev_by_date, start_date, end_date):
    """Build REV_LABELS and REV_DATA JS lines for the date range."""
    labels = []
    data = []
    cur = start_date
    while cur <= end_date:
        lbl = cur.strftime("%b %-d")
        labels.append(f"'{lbl}'")
        val = rev_by_date.get(cur)
        if val is None:
            data.append("null")
        else:
            # Round to nearest integer dollar
            data.append(str(round(float(val))))
        cur += datetime.timedelta(days=1)

    # Format as wrapped arrays matching the original style
    def chunks(lst, n):
        for i in range(0, len(lst), n):
            yield lst[i:i + n]

    label_rows = list(chunks(labels, 10))
    data_rows = list(chunks(data, 10))

    lbl_lines = ["    " + ",".join(row) + "," for row in label_rows]
    lbl_lines[-1] = lbl_lines[-1].rstrip(",")
    dat_lines = ["    " + ",".join(row) + "," for row in data_rows]
    dat_lines[-1] = dat_lines[-1].rstrip(",")

    lines = []
    lines.append("  const REV_LABELS = [")
    lines.extend(lbl_lines)
    lines.append("  ];")
    lines.append("  // null = no data reported that day; 0 = reported as $0")
    lines.append("  const REV_DATA = [")
    lines.extend(dat_lines)
    lines.append("  ];")
    return "\n".join(lines)


# ── HTML rewrite ────────────────────────────────────────────────────────────

DATA_START_MARKER = "<!-- DATA-START -->"
DATA_END_MARKER = "<!-- DATA-END -->"

# Fallback: match from "const ALL_WEEKS" through "const REV_DATA = [...];  "
# We anchor on the line before the CHARTS.agentRevenue definition
FALLBACK_RE = re.compile(
    r"(  const ALL_WEEKS\s*=.*?)(  CHARTS\.agentRevenue\s*=\s*new Chart)",
    re.DOTALL,
)


def rewrite_html(html, new_data_block):
    """Replace the data block in the HTML. Returns updated HTML string."""
    if DATA_START_MARKER in html and DATA_END_MARKER in html:
        # Marker-based replacement
        start_idx = html.index(DATA_START_MARKER) + len(DATA_START_MARKER)
        end_idx = html.index(DATA_END_MARKER)
        return html[:start_idx] + "\n" + new_data_block + "\n  " + html[end_idx:]

    # Fallback: regex-based. Match from ALL_WEEKS through the REV_DATA closing ];
    # We need to replace everything from the ALL_WEEKS declaration through the
    # REV_DATA closing bracket, which appears just before CHARTS.agentRevenue
    m = FALLBACK_RE.search(html)
    if not m:
        raise ValueError(
            "Could not locate the data block in product-analytics.html. "
            "Expected either <!-- DATA-START --> markers or 'const ALL_WEEKS' near CHARTS.agentRevenue."
        )
    replacement = new_data_block + "\n  "
    return html[: m.start(1)] + replacement + html[m.start(2):]


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    html_path = os.path.join(os.path.dirname(__file__), "product-analytics.html")
    if not os.path.exists(html_path):
        print(f"ERROR: {html_path} not found", file=sys.stderr)
        sys.exit(1)

    today = datetime.date.today()
    print(f"Running update for {today.isoformat()}")

    # Revenue window: Apr 1 of current year through yesterday
    # (or last day with data, which is typically yesterday)
    rev_start = datetime.date(today.year, 4, 1)
    rev_end = today - datetime.timedelta(days=1)
    # If we're before Apr 1, use previous year's Apr 1
    if today < datetime.date(today.year, 4, 1):
        rev_start = datetime.date(today.year - 1, 4, 1)

    week_starts = build_week_range(today)
    month_starts = build_month_range(today)

    print(f"  {len(week_starts)} complete weeks · {len(month_starts)} complete months")
    print(f"  Revenue window: {rev_start} – {rev_end}")

    conn = get_conn()
    try:
        wd = build_wd(conn, week_starts)
        d = build_d(conn, month_starts)

        print("  Querying daily revenue…")
        rev_data = query_revenue(conn, rev_start, rev_end)
    finally:
        conn.close()

    # Build label arrays
    week_labels = [f"'{week_label_mmm(w)}'" for w in week_starts]
    month_labels = [f"'{month_label(m)}'" for m in month_starts]

    # Assemble the new data block
    rev_end_label = rev_end.strftime("%b %-d, %Y")
    rev_start_label = rev_start.strftime("%b %-d")
    parts = []
    parts.append(f"  // Weekly data: {week_label_mmm(week_starts[0])} – "
                 f"{week_label_mmm(week_starts[-1])} "
                 f"({len(week_starts)} complete weeks)")
    parts.append(render_wd(wd, week_labels))
    parts.append("")
    parts.append(f"  // Monthly data: {month_label(month_starts[0])} {month_starts[0].year} – "
                 f"{month_label(month_starts[-1])} {month_starts[-1].year} "
                 f"({len(month_starts)} complete months)")
    parts.append(render_d(d, month_labels))
    parts.append("")
    parts.append(render_revenue(rev_data, rev_start, rev_end))

    new_block = "\n".join(parts)

    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()

    # Add DATA-START/END markers if not present (one-time migration)
    if DATA_START_MARKER not in html:
        m = FALLBACK_RE.search(html)
        if m:
            # Insert markers around the existing block, then rewrite
            block_start = m.start(1)
            block_end = m.start(2)
            html = (
                html[:block_start]
                + DATA_START_MARKER + "\n"
                + html[block_start:block_end]
                + DATA_END_MARKER + "\n  "
                + html[block_end:]
            )

    updated = rewrite_html(html, new_block)

    # Update the revenue date range in the subtitle
    subtitle_re = re.compile(
        r'(Troubleshooting Agent daily revenue.*?<div class="chart-sub">.*?)'
        r'(Apr \d+.*?\d{4})'
        r'(.*?</div>)',
        re.DOTALL,
    )
    rev_subtitle = f"{rev_start_label} – {rev_end_label}, {today.year}"
    updated = subtitle_re.sub(
        lambda m2: m2.group(1) + rev_subtitle + m2.group(3),
        updated,
        count=1,
    )

    with open(html_path, "w", encoding="utf-8") as f:
        f.write(updated)

    print(f"  Wrote {html_path}")


if __name__ == "__main__":
    main()
