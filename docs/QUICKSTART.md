# Get started with Holdings

## Install

Use the Hermes profile that owns the Desktop gateway. Holdings requires Hermes 0.22.7 or newer.

```sh
hermes plugins install HoldingsHQ/holdings-hermes --enable
```

Hermes clones the newest commit on the repository's default branch and warns that it is a custom (unreviewed) source. That warning is expected while Holdings is outside the catalog. After Holdings is listed in the Hermes plugin catalog, `hermes plugins install holdings --enable` installs the catalog's pinned version. Until then that command stops with a message that Holdings is not in the catalog.

Then make sure the profile loads Holdings inside the Hermes process and restart the gateway:

```sh
hermes config set plugins.isolation in_process
hermes gateway restart --detach
```

This is the Hermes default, and it keeps protected reviews and Alert watch available, because the separate plugin host cannot use your Holdings key.

In Desktop, go to **Capabilities → Plugins**, choose **Rescan**, and enable Holdings for Desktop. Open **Holdings** from the sidebar.

### Advanced: install an exact revision from a local checkout

For an offline install, or when a reviewer gave you an exact source revision, run these commands from the Holdings checkout using that profile. The first line installs the commit you have checked out. To install a revision a reviewer gave you, set `HOLDINGS_REV` to that full 40-character SHA instead.

```sh
HOLDINGS_REV=$(git rev-parse HEAD)
test "$(git rev-parse --verify "$HOLDINGS_REV^{commit}")" = "$HOLDINGS_REV" || exit 1
archive=$(mktemp -d)
git archive "$HOLDINGS_REV" | tar -xf - -C "$archive"
hermes plugins validate "$archive" --install-deps
hermes plugins install "file://$PWD" --ref "$HOLDINGS_REV" --enable
```

Stop if validation fails. Hermes may warn about a custom local source. Then enable Holdings for Desktop in **Capabilities → Plugins** as above.

## Set up with your own data

1. **Add a public wallet.** Open **Portfolio → Open Wallets**. Enter a public address and tick the networks it uses, then read its balances. Choose **Add another wallet** for up to five addresses, and Holdings reads up to 12 address and network pairs at once. Holdings never asks for your wallet password or recovery phrase.
2. **Add exchange keys (optional).** Choose **Open exchange key settings** and add the exchange’s read-only key and matching secret in Hermes. Turn off trading and withdrawals. Return to **Sources** to read balances or open a supported activity review. Check the dates and gaps before exporting.
3. **Turn on Alert watch (optional, advanced setup).** Choose **Open the setup guide** and follow the [Holdings key setup](HOLDINGS_KEY_SETUP.md). It takes two copy-paste blocks in Terminal. Then open **Holdings → Alerts**, choose wallets to watch and enable **Alert watch**. The same key lets this profile save supported live reviews.

Until you load a real source, Portfolio shows these steps. If a source is missing, open Wallets or Sources to load it. Use the chart’s **Show latest quote in chat** action for public market data.

## Just looking? Try the demo

1. From the setup screen, choose **Just looking? Try the demo**. No account, exchange key or model is required.
2. Open **Activity** or ask **Where did my ETH go?** in Ask Holdings. Open the cited exchange and wallet records, review the possible transfer, and try confirming or undoing it.
3. Open **Security** or **Alerts** to see fictional findings. Open **Exports** and choose **Save fictional packet** if you want a local copy of the three labeled demo files.
4. Choose **Exit demo** to return to your data. Leaving Holdings or restarting Hermes also clears the demo session. The demo does not save chat, decisions or alerts. Files you explicitly export stay in the folder you chose.

Ask Holdings answers with your active Hermes model when one is set up, and it names the provider and model under each answer. Without a model, Holdings answers on this device. To use your own wallets and exchanges with the model, turn on **Allow private portfolio data in Holdings chat tools** in the Holdings plugin settings.

To ask about the sample in ordinary Hermes chat, invoke `/holdings-sample` explicitly. Its answer is labeled **Fictional sample**. Your configured Hermes model receives the question and bounded fictional evidence for that command.

## Check protected setup

Use `hermes holdings-storage check` from the matching profile to verify the externally supplied Holdings key in memory. The command does not create a snapshot. To test actual recovery, save a supported real review, restart the same profile’s gateway and reopen that review. Use the existing key if the profile already has protected data.
