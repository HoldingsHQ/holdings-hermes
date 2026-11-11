# Holdings submission

Holdings is the HoldingsHQ entry for the Nous Research Hermes Agent Plugin Hackathon.

## What the entry includes

The hackathon asks for an open pull request to the Hermes plugin catalog, a demo video under 90 seconds posted on X with @NousResearch tagged, and the submission form. The form closes on 12 November 2026 at 05:00 Bangkok time (UTC+7).

The form asks for these fields:

| Field | Entry |
|---|---|
| Plugin Title | Holdings |
| Plugin Description | The listing copy below |
| Email Address | The HoldingsHQ contact address |
| Discord Username | The HoldingsHQ Discord username |
| X Demo Post Link | The X post with the demo video |
| GitHub Link to the Plugin PR | The catalog pull request in NousResearch/hermes-agent |

The source code is at https://github.com/HoldingsHQ/holdings-hermes under the MIT License.

## Install

Judges can install Holdings into the Hermes profile that owns their Desktop gateway with this command:

```sh
hermes plugins install HoldingsHQ/holdings-hermes --enable
```

Once Holdings is listed in the Hermes plugin catalog, `hermes plugins install holdings --enable` also works. The [Quickstart](QUICKSTART.md#install) covers enabling it in Desktop and installing from a local checkout.

## Catalog entry

The catalog pull request adds `plugin-catalog/holdings.yaml` to `NousResearch/hermes-agent`. The entry pins the exact release commit, labels it version 0.1.9 and sets the Hermes floor to `>=0.22.7`. It places Holdings on the Desktop shelf in the community tier and lists the nine tools that `plugin.yaml` declares. The pull request description carries the network and storage disclosures from the README section on privacy and storage. Catalog CI and maintainer review happen separately from judging.

## Demo video

The demo video is under 90 seconds and follows the seven locked lines of the video script. It shows the bundled demo, which uses fictional records only.

## Listing copy

Holdings is a Hermes Desktop plugin that shows your crypto wallet and exchange balances in one place. You can review activity against the original records and export records with a note on what each source covers. Ask Holdings answers questions with your Hermes model and checks every cited number against your records. It is read-only and never signs transactions or moves funds. The demo uses fictional records and needs no keys.

See [Source coverage](SOURCES_AND_LIMITATIONS.md) for what each source covers and [Security and privacy](SECURITY_AND_PRIVACY.md) for the network calls and storage.
