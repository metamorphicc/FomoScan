# FomoPad Fresh Token Scanner

Live terminal panel and scanner for fresh FomoPad tokens on Robinhood Chain.

## Console Panel

Start the cyclic terminal panel:

```powershell
python .\console_panel.py
```

It scans every 30 seconds by default, clears the terminal, prints the current
shortlist, marks tokens that appear for the first time in the running session,
and keeps going until `Ctrl+C`.

Useful runs:

```powershell
python .\console_panel.py --min-buyers 2 --min-score 40
python .\console_panel.py --interval 15 --lookback-blocks 500000
python .\console_panel.py --show-urls
```

For stable long-running monitoring, use your own RPC:

```powershell
$env:FOMO_RPC="https://robinhood-mainnet.g.alchemy.com/v2/YOUR_KEY"
python .\console_panel.py
```

It reads the public `FomoLaunchpad` contract logs directly from RPC, decodes `Launched` and `Trade` events, then ranks tokens by simple tradability signals:

- buy count
- unique buyers
- net ETH flow
- ETH volume
- sell pressure
- age penalty
- human-readable `why` notes

This is read-only. It does not trade, sign transactions, or touch private keys.

The public Robinhood RPC is rate-limited. For stable monitoring, use an Alchemy,
QuickNode, dRPC, Blockdaemon, or Validation Cloud endpoint:

```powershell
$env:FOMO_RPC="https://robinhood-mainnet.g.alchemy.com/v2/YOUR_KEY"
python .\fomo_scan.py
```

## Quick Start

```powershell
python .\fomo_scan.py
```

By default, the first run scans only the latest `200000` blocks. That keeps the
tool focused on fresh launches and avoids hammering the public RPC.

More selective:

```powershell
python .\fomo_scan.py --max-age-hours 24 --min-buyers 3 --min-net-eth 0.02
```

Watch mode:

```powershell
python .\fomo_scan.py --watch 30 --min-buyers 2
```

JSON/CSV output:

```powershell
python .\fomo_scan.py --json
python .\fomo_scan.py --csv .\matches.csv
```

The scanner keeps a local `.fomo_scan_cache.json` by default. After the first
successful run, later scans only request new blocks. If the public RPC hits a
temporary rate limit, the script falls back to the last cached dataset instead
of exiting with an empty result.

## Filters

- `--max-age-hours 24` only keeps tokens launched in the last 24 hours.
- `--min-buys 3` requires at least 3 buy trades.
- `--min-buyers 2` requires at least 2 unique buyers.
- `--min-net-eth 0.05` requires buys minus sells of at least 0.05 ETH.
- `--max-sell-ratio 0.5` rejects tokens where sells are more than half of trades.
- `--min-score 60` only shows stronger candidates.
- `--curve-only` hides graduated tokens.
- `--query TEXT` filters by name, symbol, description, or address.
- `--refresh-cache` rebuilds the local cache from scratch.
- `--no-cache` disables cache reads and writes.
- `--lookback-blocks 500000` changes the first-run recent block window.
- `--all-history` scans from the launchpad deployment block.

## Source

Default network settings are taken from FomoPad public docs:

- Robinhood Chain RPC: `https://rpc.mainnet.chain.robinhood.com`
- FomoLaunchpad: `0xd0C05B22C36C63eB149DDF6e4C02713d7330f870`
- deployment block: `0x323a7d0`

If you want a specific block range:

```powershell
python .\fomo_scan.py --from-block 0x3500000
```

For a full historical rebuild:

```powershell
python .\fomo_scan.py --all-history --refresh-cache
```
