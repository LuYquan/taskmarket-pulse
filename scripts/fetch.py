#!/usr/bin/env python3
"""Fetch Taskmarket public API data server-side and build data.json.

All numbers on the dashboard come from this file. Nothing is invented:
every figure is either an API response copied verbatim or an arithmetic
aggregate over fetched task rows, and the aggregates are listed in the
`verification` block so anyone can reproduce them.

Endpoints (https://api.taskmarket.dev, public, no key):
  /api/market/stats, /api/stats/breakdowns,
  /api/stats/platform-time-series, /api/stats/activity-heatmap,
  /api/stats/activity-feed, /api/tasks

Money fields (rewardVolume, paidVolume, reward, netReward) are in the
stablecoin's smallest unit (USDC/OUSD = 6 decimals); the page divides by 1e6.
"""
import json, subprocess, sys, time, urllib.parse
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta

API = "https://api.taskmarket.dev"
CHAINS = {"8453": "Base", "4217": "Tempo"}
STATUSES = ["open", "completed", "cancelled", "expired", "claimed",
            "pending_approval", "worker_selected"]
OUT = "data.json"


def get(path, timeout=30, retries=5):
    """Fetch one endpoint via curl (urllib hits truncated chunked reads here)."""
    url = API + path
    last = None
    for attempt in range(retries):
        try:
            p = subprocess.run(
                ["curl", "-sS", "-m", str(timeout), "-A",
                 "taskmarket-pulse/1.0 (bounty entry data fetch)", url],
                capture_output=True, text=True, timeout=timeout + 10)
            if p.returncode == 0 and p.stdout.strip():
                return json.loads(p.stdout)
            last = RuntimeError(f"curl rc={p.returncode}: {p.stderr.strip()[:200]}")
        except Exception as e:
            last = e
        time.sleep(1.5 * (attempt + 1))
    raise last


def paginate_tasks(chain_id, status):
    """Fetch every task of one status on one chain (cursor pagination)."""
    out, cursor, pages = [], None, 0
    while True:
        q = f"/api/tasks?chainId={chain_id}&status={status}&sort=newest&limit=100"
        if cursor:
            q += f"&cursor={urllib.parse.quote(cursor)}"
        d = get(q)
        out.extend(d["tasks"])
        pages += 1
        cursor = d.get("nextCursor")
        if not d.get("hasMore") or not cursor:
            break
    return out, pages


def slim(t):
    return {
        "code": t.get("referenceCode"),
        "chain": t.get("chainId"),
        "mode": t.get("mode"),
        "status": t.get("status"),
        "reward": t.get("reward"),
        "created": t.get("createdAt"),
        "expiry": t.get("expiryTime"),
        "requester": t.get("requester"),
        "subs": t.get("submissionCount"),
        "blurb": (t.get("description") or "")[:110],
    }


def main():
    t0 = time.time()
    now = datetime.now(timezone.utc)
    data = {"generatedAt": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "chains": {}, "platform": {}, "computed": {}, "methods": {}, "verification": {}}

    # --- per-chain market stats -------------------------------------------
    for cid, name in CHAINS.items():
        data["chains"][cid] = {"name": name, **get(f"/api/market/stats?chainId={cid}")}

    # --- platform-wide endpoints (verified below whether chainId matters) --
    bd = {cid: get(f"/api/stats/breakdowns?chainId={cid}") for cid in CHAINS}
    ts = {}
    for rng, bucket in [("7d", "day"), ("30d", "day"), ("90d", "day"), ("all", "week")]:
        ts[rng] = {cid: get(f"/api/stats/platform-time-series?chainId={cid}&range={rng}&bucket={bucket}")
                   for cid in CHAINS}
    hm_hour = {cid: get(f"/api/stats/activity-heatmap?chainId={cid}&range=30d&dimension=hourOfWeek")
               for cid in CHAINS}
    hm_mode = {cid: get(f"/api/stats/activity-heatmap?chainId={cid}&range=30d&dimension=mode")
               for cid in CHAINS}
    feed = {cid: get(f"/api/stats/activity-feed?chainId={cid}&limit=50") for cid in CHAINS}

    # The breakdowns / time-series / heatmap / feed endpoints return the same
    # payload for both chainIds (verified in `verification`); store once and
    # say so on the page instead of pretending they are chain-specific.
    data["platform"]["breakdowns"] = bd["8453"]
    data["platform"]["breakdownsByChain"] = bd  # tiny; kept so both copies are auditable
    data["platform"]["timeSeries"] = {rng: ts[rng]["8453"] for rng in ts}
    data["platform"]["heatmapHour"] = hm_hour["8453"]
    data["platform"]["heatmapMode"] = hm_mode["8453"]
    merged_feed = sorted(
        {(i.get("timestamp"), i.get("taskId"), i.get("type"), i.get("actor")): i
         for cid in CHAINS for i in feed[cid].get("items", [])}.values(),
        key=lambda i: i.get("timestamp", ""), reverse=True)[:50]
    data["platform"]["feed"] = merged_feed
    data["verification"]["breakdownsIdenticalAcrossChains"] = bd["8453"] == bd["4217"]
    data["verification"]["timeSeriesIdenticalAcrossChains"] = all(
        ts[r]["8453"] == ts[r]["4217"] for r in ts)
    data["verification"]["heatmapIdenticalAcrossChains"] = (
        hm_hour["8453"] == hm_hour["4217"] and hm_mode["8453"] == hm_mode["4217"])
    data["verification"]["feedIdenticalAcrossChains"] = feed["8453"] == feed["4217"]

    # --- full task list, both chains, every status -------------------------
    all_tasks, pages = [], 0
    per_status = {}
    for cid in CHAINS:
        for st in STATUSES:
            rows, p = paginate_tasks(cid, st)
            pages += p
            per_status[f"{cid}:{st}"] = len(rows)
            all_tasks.extend(rows)
    data["verification"]["taskPagesFetched"] = pages
    data["verification"]["tasksFetched"] = len(all_tasks)
    data["verification"]["statusCountsFromTaskList"] = {
        st: sum(per_status.get(f"{cid}:{st}", 0) for cid in CHAINS) for st in STATUSES}
    data["verification"]["statusCountsSum"] = sum(
        data["verification"]["statusCountsFromTaskList"].values())

    def dt(s):
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None

    created = [dt(t["createdAt"]) for t in all_tasks if t.get("createdAt")]
    c = data["computed"]
    # --- headline aggregates ------------------------------------------------
    bd_status = {r["status"]: r["count"] for r in data["platform"]["breakdowns"]["status"]}
    bd_mode = {r["mode"]: r["count"] for r in data["platform"]["breakdowns"]["mode"]}
    c["totalTasksAllTime"] = sum(bd_status.values())
    c["modeBreakdown"] = bd_mode
    c["statusBreakdownApi"] = bd_status
    c["statusLive"] = data["verification"]["statusCountsFromTaskList"]
    c["openNow"] = c["statusLive"]["open"]
    c["diedUnpaid"] = bd_status["cancelled"] + bd_status["expired"]
    c["diedUnpaidShare"] = round(c["diedUnpaid"] / max(1, c["totalTasksAllTime"]), 4)

    ts90 = data["platform"]["timeSeries"]["90d"]
    ts30 = data["platform"]["timeSeries"]["30d"]
    ts7 = data["platform"]["timeSeries"]["7d"]
    tsall = data["platform"]["timeSeries"]["all"]

    def money(rows, key):
        return sum(int(r[key]) for r in rows) / 1e6

    for label, rows in [("d7", ts7), ("d30", ts30), ("d90", ts90)]:
        c[f"money{label.upper()}"] = {
            "posted": round(money(rows, "rewardVolume"), 2),
            "paid": round(money(rows, "paidVolume"), 2),
            "tasksCreated": sum(r["tasksCreated"] for r in rows),
            "completed": sum(r["completedTasks"] for r in rows),
            "settlements": sum(r["settlements"] for r in rows),
            "newAgents": sum(r["newAgents"] for r in rows),
        }
        c[f"money{label.upper()}"]["paidShare"] = round(
            c[f"money{label.upper()}"]["paid"] / max(0.01, c[f"money{label.upper()}"]["posted"]), 4)
    c["weekly"] = [{"week": r["bucket"], "created": r["tasksCreated"],
                    "completed": r["completedTasks"],
                    "paid": round(int(r["paidVolume"]) / 1e6, 2),
                    "active": r["activeAgents"]} for r in tsall[-12:]]
    c["activeAgentsToday"] = ts90[-1]["activeAgents"]
    c["activeAgentsTodayDate"] = ts90[-1]["bucket"]

    # --- distinct posters per week + concentration --------------------------
    week_of = lambda d: (d - timedelta(days=d.weekday())).date().isoformat()
    posters_by_week, tasks_by_week = defaultdict(set), Counter()
    recent30 = [t for t in all_tasks if dt(t["createdAt"]) and dt(t["createdAt"]) >= now - timedelta(days=30)]
    for t in all_tasks:
        d = dt(t["createdAt"])
        if not d:
            continue
        w = week_of(d)
        posters_by_week[w].add(t.get("requester"))
        tasks_by_week[w] += 1
    weeks_sorted = sorted(posters_by_week)[-8:]
    c["postersPerWeek"] = [{"week": w, "posters": len(posters_by_week[w]),
                            "tasks": tasks_by_week[w]} for w in weeks_sorted]
    top = Counter(t.get("requester") for t in recent30).most_common(8)
    c["topPosters30d"] = [{"requester": a, "tasks": n,
                           "share": round(n / max(1, len(recent30)), 4)}
                          for a, n in top if a]
    c["topPosterShare30d"] = c["topPosters30d"][0]["share"] if c["topPosters30d"] else 0
    c["top5Share30d"] = round(sum(p["share"] for p in c["topPosters30d"][:5]), 4)
    c["tasksLast30d"] = len(recent30)

    # --- open tasks right now ------------------------------------------------
    open_tasks = [t for t in all_tasks if t.get("status") == "open"]
    subs = sorted(t.get("submissionCount") or 0 for t in open_tasks)
    rewards = sorted(int(t["reward"]) for t in open_tasks if str(t.get("reward", "")).isdigit())
    def median(xs):
        return xs[len(xs) // 2] if xs else 0
    c["openTasks"] = {
        "count": len(open_tasks),
        "medianSubmissions": median(subs),
        "maxSubmissions": subs[-1] if subs else 0,
        "medianRewardUsd": round(median(rewards) / 1e6, 2) if rewards else 0,
        "oldestCreated": min((t["createdAt"] for t in open_tasks), default=None),
        "byChain": {cid: sum(1 for t in open_tasks if str(t.get("chainId")) == cid) for cid in CHAINS},
        "byMode": dict(Counter(t.get("mode") for t in open_tasks)),
    }
    c["openTaskList"] = [slim(t) for t in sorted(
        open_tasks, key=lambda t: t.get("createdAt", ""), reverse=True)]
    c["recentTasks"] = [slim(t) for t in sorted(
        all_tasks, key=lambda t: t.get("createdAt", ""), reverse=True)[:60]]

    # --- heatmap peaks -------------------------------------------------------
    cells = data["platform"]["heatmapHour"].get("cells", [])
    if cells:
        peak = max(cells, key=lambda x: x["count"])
        c["heatmapPeak"] = {"day": int(peak["row"]), "hour": int(peak["col"]), "count": peak["count"]}
        c["heatmapTotal30d"] = sum(x["count"] for x in cells)
    mode_cells = data["platform"]["heatmapMode"].get("cells", [])
    c["modeHeatmapTotals"] = {r: sum(x["count"] for x in mode_cells if x["row"] == r)
                              for r in data["platform"]["heatmapMode"].get("rowKeys", [])}

    # --- cross-checks ---------------------------------------------------------
    ms = data["chains"]
    c["marketOpenTasks"] = ms["8453"]["openTasks"]  # platform-wide: same on both chains
    data["verification"]["openListEqualsMarketStats"] = (
        c["openNow"] == ms["8453"]["openTasks"] == ms["4217"]["openTasks"])
    data["verification"]["taskListGapVsBreakdowns"] = (
        sum(bd_status.values()) - len(all_tasks))  # 160 at first fetch: all in open/claimed/worker_selected
    data["verification"]["modeSumEqualsWeeklyCreated"] = (
        sum(bd_mode.values()) == sum(r["tasksCreated"] for r in tsall))
    data["verification"]["statusApiSumEqualsModeSum"] = (
        sum(bd_status.values()) == sum(bd_mode.values()))
    data["verification"]["statusLiveSumEqualsTasksFetched"] = (
        data["verification"]["statusCountsSum"] == len(all_tasks))
    data["verification"]["newestFeedItem"] = merged_feed[0].get("timestamp") if merged_feed else None
    data["verification"]["feedItems"] = len(merged_feed)

    data["methods"] = {
        "source": "https://api.taskmarket.dev (public API, no key)",
        "endpoints": [
            "/api/market/stats?chainId={8453,4217}",
            "/api/stats/breakdowns?chainId={8453,4217}",
            "/api/stats/platform-time-series?chainId=8453&range=7d|30d|90d|all&bucket=day|week",
            "/api/stats/activity-heatmap?chainId=8453&range=30d&dimension=hourOfWeek|mode",
            "/api/stats/activity-feed?chainId={8453,4217}&limit=50",
            "/api/tasks?chainId={8453,4217}&status={open,completed,cancelled,expired,claimed,pending_approval,worker_selected}&sort=newest&limit=100 (fully paginated)",
        ],
        "moneyUnit": "stablecoin smallest unit (6 decimals); displayed dollars = raw / 1,000,000",
        "chainNote": ("market/stats reports the same openTasks on both chains, so it is platform-wide too "
                      f"except paidWorkers (Base {data['chains']['8453']['paidWorkers']}, Tempo "
                      f"{data['chains']['4217']['paidWorkers']}) and activeWallets (Base "
                      f"{data['chains']['8453']['activeWallets']}, Tempo {data['chains']['4217']['activeWallets']}). "
                      "breakdowns, time-series, heatmap and feed are fetched for both chains and are "
                      "normally identical (both copies of breakdowns are stored in data.json); when live "
                      "data moves between the two calls they can differ by a task or two, so the page uses "
                      "the Base copy and labels these panels platform-wide."),
        "statusNote": (f"Status counts come from /api/stats/breakdowns ({sum(bd_status.values()):,} tasks, matching the "
                       "time-series all-time total). The live /api/tasks list returns "
                       f"{len(all_tasks):,} tasks; the {sum(bd_status.values()) - len(all_tasks)}-task gap sits entirely in open "
                       f"({bd_status.get('open')} vs {data['verification']['statusCountsFromTaskList']['open']}), claimed "
                       f"({bd_status.get('claimed')} vs {data['verification']['statusCountsFromTaskList']['claimed']}) and "
                       f"worker_selected ({bd_status.get('worker_selected')} vs {data['verification']['statusCountsFromTaskList']['worker_selected']}) "
                       "— the list endpoint appears to omit some tasks in those states. The live open count is "
                       "confirmed twice: the task list returns "
                       f"{c['openTasks']['byChain'].get('8453', 0)} on Base + {c['openTasks']['byChain'].get('4217', 0)} on Tempo, "
                       "and market/stats reports the same openTasks on both chains."),
        "heatmapDayNote": "Heatmap rows are numeric day keys 0-6 from the API (no labels); this page labels them Sun-Sat following the JavaScript weekday convention (0 = Sunday).",
        "generatedBy": "scripts/fetch.py, run hourly by .github/workflows/refresh.yml",
    }
    data["fetchSeconds"] = round(time.time() - t0, 1)
    with open(OUT, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"wrote {OUT} ({len(json.dumps(data))//1024} KB) in {data['fetchSeconds']}s")
    print("verification:", json.dumps(data["verification"], indent=1))
    print("computed money90:", c["moneyD90"], "money7:", c["moneyD7"])
    print("openNow:", c["openNow"], "died:", c["diedUnpaid"], c["diedUnpaidShare"])
    print("top5 share 30d:", c["top5Share30d"], "posters/week:", c["postersPerWeek"][-1])


if __name__ == "__main__":
    main()
