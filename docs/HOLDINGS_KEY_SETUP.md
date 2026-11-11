# Holdings key setup

Alert watch and saved live reviews keep their data on this computer in encrypted files. The Holdings key unlocks them. Hermes gives Holdings that key at startup from a secure store you control, through the [Hermes command helper](https://hermes-agent.nousresearch.com/docs/user-guide/secrets/command) or the [Hermes 1Password source](https://hermes-agent.nousresearch.com/docs/user-guide/secrets/onepassword). Holdings never reads your Keychain or keyring itself.

This setup is optional. Holdings works without it, and only Alert watch and saved reviews need it.

## On a Mac

Open Terminal and paste the first block. It makes a random key and saves it in your login Keychain. It prints nothing when it works. If a Holdings key is already there, it keeps that key and says so, because replacing it would lock your saved alerts.

```sh
security find-generic-password -s hermes-holdings-vault-key >/dev/null 2>&1 \
  && echo "A Holdings key is already in your Keychain, so Holdings kept it." \
  || printf 'add-generic-password -a %s -s hermes-holdings-vault-key -w %s\n' "$USER" "$(openssl rand -base64 32)" | security -i >/dev/null
```

Paste the second block. It tells Hermes to read the key from your Keychain each time Hermes starts.

```sh
hermes config set secrets.command.command 'k=$(security find-generic-password -s hermes-holdings-vault-key -w) && printf "HOLDINGS_VAULT_KEY=%s\n" "$k"'
hermes config set secrets.command.override_existing true
hermes config set secrets.command.helper_timeout_seconds 3
hermes config set secrets.command.enabled true
```

Restart the Hermes gateway so it runs the helper again. Quitting Hermes Desktop is not enough, because the gateway keeps running in the background after Desktop closes.

```sh
hermes gateway restart --detach
```

Then run the check, which should print `PASSED`.

```sh
hermes holdings-storage check
```

The check starts its own Hermes process, so it can pass while the gateway still runs without the key. The Alerts page is the final test. Open **Holdings → Alerts**. If it still says Alert watch is locked, choose **Check again**. When it opens, choose the wallets to watch and turn on **Alert watch**.

### Notes for Mac

- **Named profiles.** If you use a named Hermes profile, add `--profile NAME` after each `hermes` word, for example `hermes --profile work config set ...`.
- **HERMES_HOME.** If you start Hermes with `HERMES_HOME` set, run every command in a Terminal where `HERMES_HOME` is set the same way. Otherwise `hermes config set` writes to your default profile and the gateway restart reaches a different gateway.
- **Existing command helper.** Hermes runs one command helper per profile. If you already use one for other secrets, add the Holdings line to that helper instead of replacing it.
- **Keychain prompt.** An item made by `security` normally lets `/usr/bin/security` read it without asking. If macOS still asks whether `security` may use the item, choose **Always Allow**. Hermes gives the helper 3 seconds and cannot answer a prompt, so a waiting prompt shows up as a failed helper.
- **The -T and -A options.** You can add `-T /usr/bin/security` inside the step 1 printf line to name that tool as trusted, which matches the default. Avoid `-A`, which lets any app read the key without asking.
- **Locked Keychain.** The login Keychain must be unlocked. Start Hermes after you log in. A gateway started over SSH or before login cannot read it.
- **Why printf and security -i.** Step 1 passes the key to `security` through a pipe, so the key never appears in the process list.
- **Why override_existing.** Holdings accepts the key only when Hermes reports that it came from the command helper or 1Password. With `override_existing: true`, a stray `HOLDINGS_VAULT_KEY` line in `.env` cannot hide the helper value.

## On Linux

These blocks use `secret-tool` from libsecret with GNOME Keyring or KWallet. The key goes to `secret-tool` on standard input, so it never appears in the process list.

```sh
secret-tool lookup service hermes-holdings-vault-key >/dev/null 2>&1 \
  && echo "A Holdings key is already in your keyring, so Holdings kept it." \
  || openssl rand -base64 32 | tr -d '\n' | secret-tool store --label="Hermes Holdings key" service hermes-holdings-vault-key
```

```sh
hermes config set secrets.command.command 'k=$(secret-tool lookup service hermes-holdings-vault-key) && printf "HOLDINGS_VAULT_KEY=%s\n" "$k"'
hermes config set secrets.command.override_existing true
hermes config set secrets.command.helper_timeout_seconds 3
hermes config set secrets.command.enabled true
```

Run `hermes gateway restart --detach`, then `hermes holdings-storage check`, then open **Holdings → Alerts**. Your keyring must be unlocked when the gateway starts.

## With 1Password

Use this path if you already use the 1Password CLI with Hermes. The first command sets up the Hermes 1Password source. The second makes the key in your Private vault and passes it through a pipe. The third maps the key for Hermes.

```sh
hermes secrets onepassword setup
op item get "Hermes Holdings key" --vault Private >/dev/null 2>&1 \
  || printf '{"title":"Hermes Holdings key","category":"PASSWORD","fields":[{"id":"password","type":"CONCEALED","purpose":"PASSWORD","label":"password","value":"%s"}]}' "$(openssl rand -base64 32)" | op item create --vault Private - >/dev/null
hermes secrets onepassword set HOLDINGS_VAULT_KEY "op://Private/Hermes Holdings key/password"
```

Run `hermes gateway restart --detach`, then `hermes holdings-storage check`, then open **Holdings → Alerts**.

Holdings does not accept the key from the Hermes Bitwarden source. Bitwarden users can use the command helper with their own non-interactive CLI instead.

## Keep the key safe

Holdings cannot open saved alerts or reviews without the key that encrypted them. Do not delete or replace the key unless you are ready to lose that saved data. Moving to a new computer means moving the key with your Hermes profile.

## If the check says BLOCKED or Alerts stays locked

- If the check passes but Alerts still says Alert watch is locked, the gateway is still running from before the key was ready. Hermes runs the helper once when the gateway starts and does not retry a failed run. Run `hermes gateway restart --detach`, then choose **Check again** on the Alerts page.
- Run the helper command from `config.yaml` yourself in Terminal. Hermes hides its errors on purpose, so this is the quickest way to see them. Do not paste its output anywhere, because it contains the key.
- Make sure you ran the commands in the same Hermes profile that runs Holdings.
