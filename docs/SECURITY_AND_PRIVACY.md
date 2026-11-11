# Security and privacy

The default workspace uses bundled fictional records. Private reviews and public chain scans are explicit operator actions with bounded inputs. This document describes builder verification and the boundaries of the current implementation.

- **Local files.** Hermes grants access to a user-selected Desktop folder. The sample check reads one fixed basename and compares its digest. Supported exchange imports read only the expected filenames in the granted folder and pass the selected data to the authorized local Hermes gateway. Operations release newly acquired grants when finished. Existing grants belong to the operator.
- **Export.** Copy and save are explicit plaintext outputs. The CSV and JSON manifest use separate exclusive writes. If only the first write succeeds, the UI reports a partial export. A dispatched file write or request can finish after navigation or cancellation, even when its result can no longer update the pane.
- **Model data.** Ask Holdings sends the question, up to two earlier questions and up to three bounded Holdings tool results to the active Hermes model through `ctx.llm`. The tools cover balances, activity and one selected record, DeFi and NFT positions, market quotes, security findings, alerts, export coverage and sources. Record text is treated as untrusted data, and answers with links, addresses, unknown citations or numbers missing from the cited records fall back to a local answer. Your own records go to the model only when **Allow private portfolio data in Holdings chat tools** is on. Demo records are fictional and can always be sent. The `/holdings-sample` slash command and the dashboard's `holdings-ask-fixture` command send the question and up to five scoped fictional record projections to Hermes's configured model. The dashboard can include up to two earlier fictional questions for a relevant follow-up. The optional `holdings-ask-transfer` classifier receives a bounded fictional transfer summary and the question. The sample lookup tool exposes scoped fictional facts and evidence. The combined fictional handoff opens an unsent draft and copies a scoped brief for explicit paste and send. Hosted providers may retain prompts and results. Holdings sends private review data to a model only after that opt-in, and it does not control provider retention.
- **Secrets.** Hermes manages optional masked exchange and Etherscan credentials and supplies them to scoped plugin calls. Holdings does not read credential files or call Keychain directly. The protected storage key must have provenance from a supported external Hermes secret source.
- **Storage.** Saved exchange reviews and transfer/conflict decisions are encrypted with AES-GCM before writing. Ciphertext is bound to the owning profile and review mode. Wrong or missing keys keep reviews locked. The managed Hermes runtime supplies cryptography. Old encrypted backups can still be read with their corresponding key. Complete host log/cache/backup and cross-platform review is outside current evidence.
- **Network and operation.** The [Privacy and storage section of the README](../README.md#privacy-and-storage) lists all configured third-party hosts. Signed requests are limited to read-only exchange endpoints and run only after an explicit operator action. Foreground market refreshes carry only fixed public market identities. There is no trading, signing, withdrawals, updater, telemetry, background account sync or plugin-launched shell process.

Treat financial records, keys and diagnostics containing either as private. Keep them out of Git and hosted models.

## Protected key and Desktop startup

Use a protected external source in the profile that owns the encrypted files. When using the Hermes Command Helper, configure it to return only `HOLDINGS_VAULT_KEY` from that profile's dedicated protected store. Make that helper authoritative in the profile's Hermes configuration:

```yaml
secrets:
  command:
    enabled: true
    command: "/absolute/path/to/holdings-only-keychain-helper read"
    override_existing: true
```

The [Holdings key setup](HOLDINGS_KEY_SETUP.md) gives ready-made steps for the Mac Keychain and other secure stores. The example path is a placeholder for an operator-provisioned, noninteractive helper. Keep the key out of the configuration, environment files and arguments. The helper must return the same base64-encoded 32-byte key used to encrypt the existing files. Do not enable this override on a helper that returns unrelated credentials without reviewing its effect on those credentials.

Hermes Desktop starts a detached gateway with the target profile's resolved environment. By default, the Command Helper defers to an inherited value. In the new process, an inherited value has no source provenance. Holdings therefore keeps protected reviews locked. The authoritative helper reads the key again and lets Hermes record its source in the child. The dashboard routes still require Hermes authorization, a loopback peer and the protected external source. The existing ciphertext remains bound to its profile and review mode.

If Backend ready appears but the reviews remain locked, check the profile's helper availability and precedence, then use the normal Desktop restart. A working gateway by itself does not establish access to the encrypted files. See the [Hermes Command Helper documentation](https://hermes-agent.nousresearch.com/docs/user-guide/secrets/command).
