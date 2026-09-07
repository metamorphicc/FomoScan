from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


DEFAULT_RPC = "https://rpc.mainnet.chain.robinhood.com"
LAUNCHPAD = "0xd0C05B22C36C63eB149DDF6e4C02713d7330f870"
DEPLOY_BLOCK = 0x323A7D0
DEFAULT_CACHE = ".fomo_scan_cache.json"
CACHE_VERSION = 1

LAUNCHED_TOPIC = "0x2d2bd1a5aaf7744f0061b1c255d3c8b41eb5debab892708d5500cb1dcc566ded"
TRADE_TOPIC = "0x1be275ed3beea0f65bdbeeb8302e524f8bc596dc41b1312209c5d90f9de38745"
GRADUATED_TOPIC = "0x8bd520123825f4197a25bbfc5aa04a3ec04a1de0b8589237c60659764fcc4f08"

ETH = 10**18
BPS = 10_000


class RpcError(RuntimeError):
    pass


class RateLimitError(RpcError):
    pass


@dataclass
class ScanResult:
    rows: list[dict[str, Any]]
    latest_block: int | None
    log_count: int
    cached: bool = False
    cache_only: bool = False
    warning: str | None = None


@dataclass
class Launch:
    token: str
    star: str
    creator: str
    name: str
    symbol: str
    description: str
    image_uri: str
    block_number: int
    tx_hash: str
    timestamp: int | None = None
    graduated: bool = False


@dataclass
class TokenStats:
    buy_count: int = 0
    sell_count: int = 0
    unique_buyers: set[str] = field(default_factory=set)
    unique_sellers: set[str] = field(default_factory=set)
    buy_eth: int = 0
    sell_eth: int = 0
    volume_eth: int = 0
    last_trade_block: int = 0
    last_trade_ts: int | None = None
    on_pool_trades: int = 0

    @property
    def net_eth(self) -> int:
        return self.buy_eth - self.sell_eth

    @property
    def trade_count(self) -> int:
        return self.buy_count + self.sell_count

    @property
    def sell_ratio(self) -> float:
        if self.trade_count == 0:
            return 0.0
        return self.sell_count / self.trade_count


class JsonRpcClient:
    def __init__(
        self,
        rpc_url: str,
        timeout: float = 20.0,
        retries: int = 3,
        transport: str = "auto",
    ) -> None:
        self.rpc_url = rpc_url
        self.timeout = timeout
        self.retries = retries
        self.transport = transport
        self._request_id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self._request_id += 1
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        )
        data = self._send(payload, method)
        if "error" in data:
            message = data["error"].get("message", data["error"])
            raise RpcError(f"{method} failed: {message}")
        return data.get("result")

    def batch_call(self, calls: list[tuple[str, list[Any]]]) -> list[Any]:
        payload_items = []
        ids = []
        for method, params in calls:
            self._request_id += 1
            ids.append(self._request_id)
            payload_items.append(
                {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
            )
        data = self._send(json.dumps(payload_items), "batch")
        if not isinstance(data, list):
            raise RpcError(f"batch failed: expected list response, got {type(data).__name__}")
        by_id = {item.get("id"): item for item in data if isinstance(item, dict)}
        results = []
        for request_id in ids:
            item = by_id.get(request_id)
            if item is None:
                raise RpcError(f"batch failed: missing response for id {request_id}")
            if "error" in item:
                message = item["error"].get("message", item["error"])
                raise RpcError(f"batch item failed: {message}")
            results.append(item.get("result"))
        return results

    def _send(self, payload: str, label: str) -> Any:
        if self._use_powershell_transport():
            return self._call_powershell(payload, label)
        return self._call_urllib(payload, label)

    def _use_powershell_transport(self) -> bool:
        if self.transport == "powershell":
            return True
        if self.transport == "urllib":
            return False
        return os.name == "nt" and "rpc.mainnet.chain.robinhood.com" in self.rpc_url

    def _call_urllib(self, payload: str, method: str) -> Any:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        last_error: Exception | None = None

        for attempt in range(self.retries):
            req = urllib.request.Request(
                self.rpc_url, data=payload.encode("utf-8"), headers=headers, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                if exc.code == 429:
                    raise RateLimitError(f"RPC rate limited: {body}") from exc
                last_error = RpcError(f"HTTP {exc.code}: {body}")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc

            if attempt + 1 < self.retries:
                time.sleep(0.5 * (2**attempt))

        raise RpcError(f"{method} failed after retries: {last_error}")

    def _call_powershell(self, payload: str, method: str) -> Any:
        command = (
            "$ProgressPreference='SilentlyContinue'; "
            "(Invoke-WebRequest -UseBasicParsing $env:FOMO_RPC_URL -Method Post "
            "-ContentType 'application/json' -Body $env:FOMO_RPC_BODY).Content"
        )
        last_error: Exception | None = None

        for attempt in range(self.retries):
            env = os.environ.copy()
            env["FOMO_RPC_URL"] = self.rpc_url
            env["FOMO_RPC_BODY"] = payload
            try:
                completed = subprocess.run(
                    [
                        "powershell.exe",
                        "-NoProfile",
                        "-ExecutionPolicy",
                        "Bypass",
                        "-Command",
                        command,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                    check=False,
                    env=env,
                )
            except (subprocess.SubprocessError, OSError) as exc:
                last_error = exc
            else:
                if completed.returncode != 0:
                    if "429" in completed.stderr:
                        raise RateLimitError(f"RPC rate limited: {completed.stderr.strip()}")
                    last_error = RpcError(completed.stderr.strip() or completed.stdout.strip())
                else:
                    try:
                        return json.loads(completed.stdout)
                    except json.JSONDecodeError as exc:
                        last_error = exc

            if attempt + 1 < self.retries:
                time.sleep(0.5 * (2**attempt))

        raise RpcError(f"{method} failed after retries via PowerShell: {last_error}")


def strip_0x(value: str) -> str:
    return value[2:] if value.startswith("0x") else value


def hex_to_int(value: str) -> int:
    return int(value, 16)


def topic_to_address(topic: str) -> str:
    raw = strip_0x(topic)
    return "0x" + raw[-40:]


def chunk_words(data: str) -> list[str]:
    raw = strip_0x(data)
    if not raw:
        return []
    return [raw[i : i + 64] for i in range(0, len(raw), 64)]


def word_to_int(word: str) -> int:
    return int(word, 16)


def decode_abi_string(data: str, offset: int) -> str:
    raw = strip_0x(data)
    length_pos = offset * 2
    length = int(raw[length_pos : length_pos + 64], 16)
    start = length_pos + 64
    value_hex = raw[start : start + length * 2]
    return bytes.fromhex(value_hex).decode("utf-8", errors="replace")


def decode_launched(log: dict[str, Any]) -> Launch:
    topics = log["topics"]
    words = chunk_words(log["data"])
    if len(topics) != 4 or len(words) < 4:
        raise ValueError("unexpected Launched log shape")

    offsets = [word_to_int(word) for word in words[:4]]
    return Launch(
        token=topic_to_address(topics[1]),
        star=topic_to_address(topics[2]),
        creator=topic_to_address(topics[3]),
        name=decode_abi_string(log["data"], offsets[0]),
        symbol=decode_abi_string(log["data"], offsets[1]),
        description=decode_abi_string(log["data"], offsets[2]),
        image_uri=decode_abi_string(log["data"], offsets[3]),
        block_number=hex_to_int(log["blockNumber"]),
        tx_hash=log["transactionHash"],
    )


def decode_trade(log: dict[str, Any]) -> tuple[str, str, bool, int, int, int, bool, int, str]:
    topics = log["topics"]
    words = chunk_words(log["data"])
    if len(topics) != 3 or len(words) < 5:
        raise ValueError("unexpected Trade log shape")

    return (
        topic_to_address(topics[1]),
        topic_to_address(topics[2]),
        bool(word_to_int(words[0])),
        word_to_int(words[1]),
        word_to_int(words[2]),
        word_to_int(words[3]),
        bool(word_to_int(words[4])),
        hex_to_int(log["blockNumber"]),
        log["transactionHash"],
    )


def get_logs(client: JsonRpcClient, from_block: int, to_block: int, chunk_size: int) -> list[dict[str, Any]]:
    logs: list[dict[str, Any]] = []
    start = from_block
    while start <= to_block:
        end = min(start + chunk_size - 1, to_block)
        result = client.call(
            "eth_getLogs",
            [
                {
                    "address": LAUNCHPAD,
                    "fromBlock": hex(start),
                    "toBlock": hex(end),
                }
            ],
        )
        logs.extend(result or [])
        start = end + 1
    return logs


def load_cache(path: str) -> dict[str, Any] | None:
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("version") != CACHE_VERSION:
        return None
    if data.get("launchpad", "").lower() != LAUNCHPAD.lower():
        return None
    return data


def save_cache(path: str, latest_block: int, logs: list[dict[str, Any]], timestamps: dict[int, int]) -> None:
    if not path:
        return
    data = {
        "version": CACHE_VERSION,
        "launchpad": LAUNCHPAD,
        "latest_block": latest_block,
        "saved_at": int(time.time()),
        "logs": logs,
        "timestamps": {str(block): timestamp for block, timestamp in timestamps.items()},
    }
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))


def log_key(log: dict[str, Any]) -> tuple[str, str]:
    return (log.get("transactionHash", ""), log.get("logIndex", ""))


def merge_logs(cached_logs: list[dict[str, Any]], fresh_logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {log_key(log): log for log in cached_logs}
    for log in fresh_logs:
        merged[log_key(log)] = log
    return sorted(
        merged.values(),
        key=lambda item: (hex_to_int(item["blockNumber"]), hex_to_int(item["logIndex"])),
    )


def hydrate_timestamps(
    client: JsonRpcClient,
    launches: dict[str, Launch],
    stats: dict[str, TokenStats],
    cache: dict[int, int] | None = None,
) -> None:
    cache = cache if cache is not None else {}
    block_numbers = {launch.block_number for launch in launches.values()}
    block_numbers.update(stat.last_trade_block for stat in stats.values() if stat.last_trade_block)
    missing = sorted(block for block in block_numbers if block not in cache)
    if missing:
        results = client.batch_call([("eth_getBlockByNumber", [hex(block), False]) for block in missing])
        for block, result in zip(missing, results):
            if result and result.get("timestamp"):
                cache[block] = hex_to_int(result["timestamp"])
    for launch in launches.values():
        launch.timestamp = cache.get(launch.block_number)
    for stat in stats.values():
        if stat.last_trade_block:
            stat.last_trade_ts = cache.get(stat.last_trade_block)


def rebuild(logs: list[dict[str, Any]]) -> tuple[dict[str, Launch], dict[str, TokenStats]]:
    launches: dict[str, Launch] = {}
    stats: dict[str, TokenStats] = {}

    for log in sorted(logs, key=lambda item: (hex_to_int(item["blockNumber"]), hex_to_int(item["logIndex"]))):
        topic = log["topics"][0].lower()
        if topic == LAUNCHED_TOPIC:
            launch = decode_launched(log)
            launches[launch.token.lower()] = launch
            stats.setdefault(launch.token.lower(), TokenStats())
        elif topic == TRADE_TOPIC:
            token, trader, is_buy, eth_amount, _token_amount, _price, on_pool, block, _tx = decode_trade(log)
            stat = stats.setdefault(token.lower(), TokenStats())
            stat.volume_eth += eth_amount
            stat.last_trade_block = max(stat.last_trade_block, block)
            if on_pool:
                stat.on_pool_trades += 1
            if is_buy:
                stat.buy_count += 1
                stat.buy_eth += eth_amount
                stat.unique_buyers.add(trader.lower())
            else:
                stat.sell_count += 1
                stat.sell_eth += eth_amount
                stat.unique_sellers.add(trader.lower())
        elif topic == GRADUATED_TOPIC and len(log.get("topics", [])) >= 2:
            token = topic_to_address(log["topics"][1]).lower()
            if token in launches:
                launches[token].graduated = True

    return launches, stats


def eth(value: int) -> float:
    return value / ETH


def score_token(launch: Launch, stat: TokenStats, now: int) -> float:
    age_hours = ((now - launch.timestamp) / 3600) if launch.timestamp else 0
    age_penalty = min(age_hours, 168) * 0.15
    sell_penalty = stat.sell_ratio * 35
    pool_bonus = 15 if launch.graduated or stat.on_pool_trades else 0
    return (
        len(stat.unique_buyers) * 22
        + stat.buy_count * 7
        + min(eth(stat.net_eth) * 18, 80)
        + min(eth(stat.volume_eth) * 4, 60)
        + pool_bonus
        - sell_penalty
        - age_penalty
    )


def explain_token(launch: Launch, stat: TokenStats, now: int) -> str:
    reasons = []
    if launch.timestamp:
        reasons.append(f"age {fmt_age(launch.timestamp, now)}")
    if len(stat.unique_buyers) >= 2:
        reasons.append(f"{len(stat.unique_buyers)} buyers")
    if stat.buy_count:
        reasons.append(f"{stat.buy_count} buys")
    if eth(stat.net_eth) > 0:
        reasons.append(f"+{fmt_eth(stat.net_eth)} ETH net")
    if stat.sell_ratio <= 0.35 and stat.trade_count:
        reasons.append("low sell pressure")
    if launch.graduated or stat.on_pool_trades:
        reasons.append("graduated/pool")
    return ", ".join(reasons) if reasons else "fresh launch"


def fmt_eth(value: int) -> str:
    amount = eth(value)
    if amount == 0:
        return "0"
    if amount < 0.001:
        return f"{amount:.6f}"
    if amount < 1:
        return f"{amount:.4f}"
    return f"{amount:.2f}"


def fmt_age(timestamp: int | None, now: int) -> str:
    if not timestamp:
        return "?"
    seconds = max(0, now - timestamp)
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def short_addr(address: str) -> str:
    return address[:6] + "..." + address[-4:]


def token_url(address: str) -> str:
    return f"https://www.fomopad.app/token/{address}"


def passes_filters(
    launch: Launch,
    stat: TokenStats,
    args: argparse.Namespace,
    now: int,
) -> bool:
    if args.max_age_hours is not None and launch.timestamp:
        if now - launch.timestamp > args.max_age_hours * 3600:
            return False
    if (launch.graduated or stat.on_pool_trades) and args.curve_only:
        return False
    if stat.buy_count < args.min_buys:
        return False
    if len(stat.unique_buyers) < args.min_buyers:
        return False
    if eth(stat.net_eth) < args.min_net_eth:
        return False
    if stat.sell_ratio > args.max_sell_ratio:
        return False
    if args.query:
        needle = args.query.lower()
        haystack = f"{launch.name} {launch.symbol} {launch.description} {launch.token}".lower()
        if needle not in haystack:
            return False
    return True


def rows_for_output(
    launches: dict[str, Launch],
    stats: dict[str, TokenStats],
    args: argparse.Namespace,
    now: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, launch in launches.items():
        stat = stats.get(key, TokenStats())
        if not passes_filters(launch, stat, args, now):
            continue
        rows.append(
            {
                "score": round(score_token(launch, stat, now), 2),
                "symbol": launch.symbol,
                "name": launch.name,
                "token": launch.token,
                "age": fmt_age(launch.timestamp, now),
                "launched_at": datetime.fromtimestamp(launch.timestamp, tz=timezone.utc).isoformat()
                if launch.timestamp
                else None,
                "buys": stat.buy_count,
                "sells": stat.sell_count,
                "buyers": len(stat.unique_buyers),
                "net_eth": round(eth(stat.net_eth), 8),
                "volume_eth": round(eth(stat.volume_eth), 8),
                "sell_ratio": round(stat.sell_ratio, 4),
                "graduated": launch.graduated or stat.on_pool_trades > 0,
                "why": explain_token(launch, stat, now),
                "url": token_url(launch.token),
                "tx": launch.tx_hash,
            }
        )
    rows.sort(key=lambda row: (row["score"], row["buys"], row["buyers"]), reverse=True)
    return rows[: args.limit]


def print_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No tokens matched the filters.")
        return

    headers = ["score", "sym", "age", "buys", "sells", "buyers", "net ETH", "vol ETH", "token", "why"]
    print(" | ".join(headers))
    print("-" * 132)
    for row in rows:
        print(
            " | ".join(
                [
                    f"{row['score']:>5.1f}",
                    f"{row['symbol'][:10]:<10}",
                    f"{row['age']:>6}",
                    f"{row['buys']:>4}",
                    f"{row['sells']:>5}",
                    f"{row['buyers']:>6}",
                    f"{row['net_eth']:>7}",
                    f"{row['volume_eth']:>7}",
                    short_addr(row["token"]),
                    row["why"][:46],
                ]
            )
        )
        print(f"      {row['url']}")


def export_csv(path: str, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["score"])
        writer.writeheader()
        writer.writerows(rows)


def scan_once(args: argparse.Namespace) -> ScanResult:
    client = JsonRpcClient(args.rpc, timeout=args.timeout, retries=args.retries, transport=args.transport)
    cache_data = None if args.no_cache or args.refresh_cache else load_cache(args.cache)
    cached_logs = list(cache_data.get("logs", [])) if cache_data else []
    timestamp_cache = {
        int(block): int(timestamp)
        for block, timestamp in (cache_data.get("timestamps", {}) if cache_data else {}).items()
    }
    warning = None

    try:
        latest = hex_to_int(client.call("eth_blockNumber", []))
    except RateLimitError as exc:
        if cached_logs:
            launches, stats = rebuild(cached_logs)
            hydrate_cached_timestamps(launches, stats, timestamp_cache)
            rows = rows_for_output(launches, stats, args, int(time.time()))
            return ScanResult(
                rows=rows,
                latest_block=cache_data.get("latest_block") if cache_data else None,
                log_count=len(cached_logs),
                cached=True,
                cache_only=True,
                warning=f"RPC rate-limited; showing cached data from {fmt_cache_time(cache_data)}",
            )
        raise exc

    from_block = args.from_block or DEPLOY_BLOCK
    if cached_logs and not args.from_block:
        cached_latest = int(cache_data.get("latest_block", DEPLOY_BLOCK - 1))
        from_block = max(cached_latest + 1, DEPLOY_BLOCK)

    try:
        fresh_logs = get_logs(client, from_block, latest, args.chunk_size) if from_block <= latest else []
        logs = merge_logs(cached_logs, fresh_logs)
    except RateLimitError as exc:
        if not cached_logs:
            raise exc
        logs = cached_logs
        latest = int(cache_data.get("latest_block", latest)) if cache_data else latest
        warning = f"RPC rate-limited while fetching logs; showing cached data from {fmt_cache_time(cache_data)}"

    launches, stats = rebuild(logs)

    try:
        hydrate_timestamps(client, launches, stats, timestamp_cache)
    except RateLimitError:
        hydrate_cached_timestamps(launches, stats, timestamp_cache)
        warning = warning or "RPC rate-limited while fetching block timestamps; ages may be incomplete"

    if not args.no_cache:
        save_cache(args.cache, latest, logs, timestamp_cache)

    now = int(time.time())
    return ScanResult(
        rows=rows_for_output(launches, stats, args, now),
        latest_block=latest,
        log_count=len(logs),
        cached=bool(cached_logs),
        cache_only=logs == cached_logs and bool(cached_logs) and from_block <= latest,
        warning=warning,
    )


def hydrate_cached_timestamps(
    launches: dict[str, Launch],
    stats: dict[str, TokenStats],
    timestamp_cache: dict[int, int],
) -> None:
    for launch in launches.values():
        launch.timestamp = timestamp_cache.get(launch.block_number)
    for stat in stats.values():
        if stat.last_trade_block:
            stat.last_trade_ts = timestamp_cache.get(stat.last_trade_block)


def fmt_cache_time(cache_data: dict[str, Any] | None) -> str:
    if not cache_data or not cache_data.get("saved_at"):
        return "local cache"
    return datetime.fromtimestamp(int(cache_data["saved_at"]), tz=timezone.utc).isoformat()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan fresh FomoPad tokens on Robinhood Chain and rank tradable candidates."
    )
    parser.add_argument("--rpc", default=os.getenv("FOMO_RPC", DEFAULT_RPC), help="Robinhood Chain RPC URL")
    parser.add_argument("--from-block", type=lambda value: int(value, 0), default=None)
    parser.add_argument("--chunk-size", type=int, default=5_000_000, help="eth_getLogs block range per request")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-age-hours", type=float, default=None, help="Only tokens launched within this age")
    parser.add_argument("--min-buys", type=int, default=1)
    parser.add_argument("--min-buyers", type=int, default=1)
    parser.add_argument("--min-net-eth", type=float, default=0.0)
    parser.add_argument("--max-sell-ratio", type=float, default=0.65)
    parser.add_argument("--curve-only", action="store_true", help="Hide graduated tokens")
    parser.add_argument("--query", help="Filter by name, symbol, description, or address")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of a table")
    parser.add_argument("--csv", help="Also export matching rows to CSV")
    parser.add_argument("--watch", type=float, help="Repeat scan every N seconds")
    parser.add_argument("--cache", default=DEFAULT_CACHE, help="Local cache file for logs and timestamps")
    parser.add_argument("--no-cache", action="store_true", help="Disable local cache reads and writes")
    parser.add_argument("--refresh-cache", action="store_true", help="Ignore existing cache and rebuild it")
    parser.add_argument("--min-score", type=float, default=None, help="Only show rows with this score or higher")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument(
        "--transport",
        choices=["auto", "urllib", "powershell"],
        default="auto",
        help="HTTP transport. Auto uses PowerShell for Robinhood RPC on Windows.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.watch is not None and args.watch < 5:
        print("--watch must be at least 5 seconds to avoid hammering the RPC", file=sys.stderr)
        return 2

    while True:
        try:
            result = scan_once(args)
        except RateLimitError as exc:
            print(str(exc), file=sys.stderr)
            return 3
        except RpcError as exc:
            print(f"RPC error: {exc}", file=sys.stderr)
            return 3

        rows = result.rows
        if args.min_score is not None:
            rows = [row for row in rows if row["score"] >= args.min_score]

        if result.warning:
            print(result.warning, file=sys.stderr)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            print_table(rows)
            if rows:
                source = "cache" if result.cache_only else "rpc"
                print(f"\nsource={source} logs={result.log_count} latest_block={result.latest_block}")
        if args.csv:
            export_csv(args.csv, rows)
            print(f"\nCSV exported: {args.csv}")

        if args.watch is None:
            break
        time.sleep(args.watch)
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
