# Sources, coverage and local verification inputs

| Source | Implemented scope | Evidence and limits |
|---|---|---|
| Kraken Spot | Read-only Trades and Ledgers plus the corresponding official CSVs. Overlap uses account, stream and transaction ID. | Native bounded replay matched four records. Live fills, split fills, transfers and multi-page behavior remain unverified. |
| Binance.com Global Spot | Signed read-only Spot history plus official Spot CSV. The newest exported fill selects a market and UTC day. | The native replay returned one empty API page. Two CSV rows remained unresolved. Matching API/CSV fills passed with fictional fixtures only. Binance Futures income fixtures are development evidence, and the runtime has no Futures connector. |
| Bybit.com Global Unified Spot | Read-only executions for one UTC day across returned Spot markets. Official Spot Trade History CSV has a separate import and encrypted review. | The supplied 75-fill CSV parsed offline. Native CSV import, evidence and reopen passed with fictional data. Live access and API/CSV identity, fees, split fills and pagination remain unverified. Holdings prevents combining Bybit API and CSV snapshots in one selection while overlap is unproven. |
| Ethereum mainnet | Bounded normal transaction and ERC-20 streams, with explicit page limits and failures. | Native public proof used one bounded range. Internal transfers, NFTs, complete wallet history and balance reconstruction are outside scope. |
| Public wallet balances | Up to five addresses and 12 address and network pairs per read across Ethereum, Base, Arbitrum One, BNB Chain, Robinhood Chain and Solana public providers. | Reads to one provider run one at a time and are spaced to stay under its published rate limit, or 10 requests per second where the provider publishes none. Alert watch polls about every 5 minutes. Outflows are checked every 5 minutes and the other checks every 15 minutes. A complete Zerion DeFi scan is reused for 3 hours, and a failed one is retried after 15 minutes. |
| Uniswap v3 | Direct wallet-funded USDC/WETH swap attribution through supported routers, verified receipt logs and historical factory lookup. | One native public swap passed. Other protocols, multihop, wrapping and unsupported routes remain unresolved. Public charts cover one verified Ethereum pool and one Kraken pair. |

Coinbase and Gemini are deferred. Holdings keeps source rows and original evidence separate. It reports an empty bounded result only for the queried streams. Missing or partial history does not establish zero activity, balances or losses. The CSV uses native asset amounts, excludes disputed records and comes with a coverage manifest. No cost basis, historical valuations or tax-ready output.

## Inputs needed for a future local Bybit comparison

- An approved isolated Holdings profile with a system-generated HMAC key for **Bybit.com Global Unified Trading, Read-Only, Spot only**. In Holdings settings in Hermes, enter the key and matching secret into **Bybit API Key** and **Bybit API Secret**. The runtime names are `HOLDINGS_BYBIT_API_KEY` and `HOLDINGS_BYBIT_API_SECRET`. Never include values in chat, screenshots, command arguments or Git.
- The local official **Spot Trade History CSV** from the same account, in the supported `Bybit-Spot-TradeHistory-<start>-<end>.csv` format. The previously approved populated file is already available locally.
- One UTC calendar day represented in the CSV and queryable by that key. Keep inputs in the approved local workflow with its protected vault key. Compare the execution identifier, market, side, timestamp at CSV minute precision, exact quantity, quote value, fee amount and fee asset. A successful empty API page cannot establish overlap. Keep any real results local and report counts and evidence limits only.

## Inputs needed for a future real transfer check

- One **existing** transfer between two accounts or endpoints the operator owns and can confirm locally. Provide both records through approved local sources. Do not initiate a transfer for verification.
- For each leg, supply its original provider reference, account identity, direction, UTC timestamp, signed native amount and exact chain/asset identity. Include the transaction hash if supplied, plus any explicit network fee and fee asset. A shared hash is stronger evidence than amount and time alone. Ticker similarity does not establish a match.
- For an Ethereum wallet leg, the corresponding locally authorized public address, inclusive block range and matching transaction hash, plus the approved profile's scoped Etherscan key if a bounded scan is needed. For the exchange leg, an approved movement record from the same transfer and an adapter that normalizes its chain-aware transfer identity. Current exchange adapters do not provide that normalization, so an exchange export alone does not close the gate. This gap is outside the candidate's scope.

