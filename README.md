# Holdings

Holdings is a Hermes Desktop plugin that shows your wallet and exchange balances in one place. You can review activity against the original records, confirm or reject suggested transfers, and export records with a note on what each source covers. It never signs transactions or moves funds. The bundled demo uses fictional records and requires no exchange or model credentials.

## Install

Holdings requires Hermes 0.22.7 or newer. Run this in the profile that owns your Hermes Desktop gateway:

```sh
hermes plugins install HoldingsHQ/holdings-hermes --enable
```

Hermes clones the newest commit on the repository's default branch and warns that it is a custom source, which is expected while Holdings is outside the catalog. Once Holdings is listed in the Hermes plugin catalog, `hermes plugins install holdings --enable` installs the catalog's pinned version instead.

Then make sure the profile loads Holdings inside the Hermes process and restart the gateway:

```sh
hermes config set plugins.isolation in_process
hermes gateway restart --detach
```

This is the Hermes default, and it keeps protected reviews and Alert watch available, because the separate plugin host cannot use your Holdings key.

Then enable Holdings for Desktop under **Capabilities → Plugins**. The [Quickstart](docs/QUICKSTART.md#install) has the full steps and a local checkout option for offline installs.

## Set up with your own data

Open **Holdings → Portfolio** to see your setup progress.

1. **Add a public wallet.** Choose **Open Wallets** and enter a public address. Choose **Add another wallet** for up to five addresses, and Holdings reads up to 12 address and network pairs at once. Holdings reads it without a recovery phrase or exchange key.
2. **Add exchange keys (optional).** Open the Holdings plugin settings in Hermes and add read-only keys for your exchange. Return to **Sources** to load balances.
3. **Turn on Alert watch (optional, advanced setup).** Follow the [Holdings key setup](docs/HOLDINGS_KEY_SETUP.md), then enable Alert watch in **Holdings → Alerts**.

### What a public wallet shows without keys

Without any keys, Holdings reads a short list of well-known tokens on each network from PublicNode's public servers.

| Network | Tokens Holdings reads |
|---|---|
| Ethereum | ETH, WETH, USDC, WBTC, USDT, DAI, LINK, UNI, stETH and wstETH |
| Base | ETH, WETH, USDC, cbBTC, DAI, cbETH, wstETH and AERO |
| Arbitrum One | ETH, WETH, USDC, USDT0, WBTC, DAI, ARB and LINK |
| BNB Chain | BNB, WBNB, Binance-Peg USDC, Binance-Peg USDT, BTCB, Binance-Peg ETH, CAKE and FDUSD |
| Robinhood Chain | ETH, WETH and USDG, which Holdings shows without a price |

Holdings prices these tokens with DefiLlama's public price service and counts a price only when DefiLlama reports it with at least 0.9 confidence in the last hour. If DefiLlama has no usable price, Ethereum ETH and WETH fall back to the GeckoTerminal WETH/USD pool rate and canonical USDC counts at $1. Any other token without a usable price is listed as not priced and left out of the total. Holdings never prices a token outside this list.

### What the Portfolio total counts

The Portfolio shows one total for everything you have added, with your wallets and exchanges listed beside it, and each holding is counted once.

- **Wallets without Zerion:** each wallet is counted from the short public token list, priced with DefiLlama.
- **Wallets with Zerion:** when Zerion loads the tokens for a wallet, Zerion's verified list and prices replace the public list for that wallet, so a token is never added from both. If Zerion cannot load, Holdings goes back to the public list.
- **Exchanges:** Kraken, Binance and Bybit balances are priced with each exchange's own public prices, which need no key. A price counts only when it is recent and the market has trading volume, and where the exchange reports a time it must be from the last hour. USDC and USDT count at $1 and are labeled that way.
- **Not counted:** an asset without a usable price shows "No price yet" and is left out until it has one. DeFi positions and NFTs are listed on their own pages but are not added, because a DeFi position can hold tokens that are already counted in your wallet and an NFT price is only an estimate from recent sales.

Open **How this is counted** under the total to see these rules for your own wallets and which tokens have no price yet.

You can see more by adding optional keys. A Zerion API key shows every token in a wallet, plus DeFi positions and NFTs, and you can create one for free at https://dashboard.zerion.io. An Etherscan API key adds transaction history for Ethereum and Arbitrum wallets, and https://docs.etherscan.io/set-up-your-api-key explains how to create one. Add both keys in the Holdings plugin settings inside Hermes under **Capabilities → Plugins → Holdings → Settings**, and never paste them into chat.

## Just looking? Try the demo

Choose **Just looking? Try the demo** on the setup screen. The demo needs no keys and uses fictional balances, transfers and alerts. **Exit demo**, leaving Holdings, or restarting Hermes clears the session. You can explicitly save a fictional packet to a folder you choose.

Read [Source coverage](docs/SOURCES_AND_LIMITATIONS.md) and [Security and privacy](docs/SECURITY_AND_PRIVACY.md) for the details.

## What is verified

| Source | Verified coverage | Unverified or excluded |
|---|---|---|
| Kraken Spot | One bounded native API/official CSV replay matched four records. | Live fills, split fills, transfers and pagination beyond the tested window. |
| Binance.com Global Spot | A bounded read-only API request completed in Desktop and returned an empty page. Two CSV rows remained unresolved. | Live API/CSV fill overlap, transfers and derivatives. |
| Bybit.com Global Unified Spot | An official 75-fill CSV parsed offline. The separate protected CSV workflow passed with fictional data in Desktop. | Live API access and live API/CSV matching. |
| Ethereum and Uniswap v3 | One bounded Ethereum mainnet scan and one direct wallet-attributed Uniswap v3 swap passed natively. | Complete wallet history, other swap routes and real cross-account transfer matching. |

Fictional combined reviews exercise transfer suggestions, conflict exclusions and saved decisions. Suggestions require human confirmation of ownership and preserve the original rows. The export includes known gaps and disputed references and omits disputed rows from record totals. Holdings does not calculate tax-ready totals. Balance snapshots and provider estimates identify their sources and coverage.

Ask Holdings sends your question to the active Hermes model with a bounded set of Holdings records, and the answer must cite the record or source IDs it used. Holdings checks every citation and number against the records it sent, and shows its own local answer if the model fails or skips citations. The answer names the provider and model that replied. Demo records are fictional. With your own data, nothing private goes to the model until you turn on **Allow private portfolio data in Holdings chat tools** in the plugin settings, and local answers keep working while it is off. In ordinary Hermes chat, use `/holdings-sample` or explicitly request the fictional lookup. Normal holdings tools ask for a real scope when one is missing. Support questions can use your configured model.

## Read-only charts

The Markets strip shares public snapshots with Charts and the **Show latest quote in chat** action. Click a market to open its chart and set the chat context. Optional ticker motion can be paused. Quotes refresh about once a minute while the Holdings window is visible. The strip labels changes measured between completed hourly closes 24 hours apart. Market quotes in chat are local data lookups.

Open **Chart** beside a matching history row. The 24- and 48-hour views show recent completed candles for Kraken Spot BTC/USD or the Ethereum mainnet Uniswap v3 WETH/USDC 0.05% pool at `0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640`. The pool view displays GeckoTerminal's latest completed WETH/USD close and USD volume, with fetch and close times. The demo swap is fictional and references that real pool. Chart prices are separate from the selected history dates.

## Privacy and storage

Protected live reviews and Alert watch use a dedicated external vault key. Demo decisions stay in memory, even when a key is configured. See the [setup guide](docs/QUICKSTART.md).

Depending on the selected action, Holdings contacts these hosts:

- Exchanges: `api.kraken.com`, `api.binance.com`, `api.bybit.com` and `api.bytick.com`
- Public chain data: `api.etherscan.io`, `ethereum-rpc.publicnode.com`, `base-rpc.publicnode.com`, `arbitrum-one-rpc.publicnode.com`, `bsc-rpc.publicnode.com`, `robinhood-rpc.publicnode.com`, `eth.drpc.org`, `rpc.mevblocker.io` and `api.mainnet-beta.solana.com`
- Prices and charts: `api.geckoterminal.com` and `coins.llama.fi`
- Optional wallet services: `api.zerion.io` and `api.gopluslabs.io`
- Market status and protocol incidents: `www.federalreserve.gov` and `api.llama.fi`
- NFT preview images from Zerion results: `token-icons.s3.amazonaws.com` and `nft2-cdn.alchemy.com`

Explorer, Revoke.cash and provider documentation links open only when you choose them. Exchange calls use scoped read-only credentials managed by Hermes. Wallet reads send the selected public identifiers and ranges to the applicable providers. The foreground market strip and charts send only fixed public market identities. Ask Holdings questions reach your configured model provider through Hermes, so Holdings adds no hosts of its own for them. That provider receives fictional demo records, or your own records only after you turn on the private data setting.

Files outside the plugin require an explicit Hermes folder grant. Saved exchange reviews and review decisions use local encrypted storage. Explicit exports and clipboard copies are plaintext. Holdings does not launch shell commands, run a background sync, emit telemetry, self-update, sign or trade. The optional vault Command Helper is an operator-configured Hermes secret source, not a subprocess launched by Holdings.

## Development

Run the Python tests with the Python that Hermes itself uses. Some tests import Hermes and its runtime packages, such as fastapi and cryptography, so a system `python3` without them reports errors. Run these from the Holdings checkout:

```sh
hermes --run-module unittest discover -s tests -q
hermes --run-module unittest tests.vault_checks -q
```

Run all the Desktop tests with Node:

```sh
node --experimental-vm-modules --test tests/*.mjs
git diff --check
```

The release rehearsal uses managed CPython 3.14.9, Node 26.12.1 and the Hermes runtime's cryptography package for protected storage. Holdings declares no additional plugin dependencies. `python3 -B build_demo.py` regenerates the fictional snapshot for development.
