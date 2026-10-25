# Releasing Kiln (for the maintainer)

Kiln updates itself from GitHub releases, and it installs a release **only if it carries your signature**. The
signing key stays on your computer, protected by a passphrase only you know. That means a stolen GitHub account,
a leaked `gh` token or a malicious pull request cannot push an update to anybody: without your key the signature
is missing or wrong, and every copy of Kiln refuses it (and warns its user).

## One-time setup

1. Make the release key. Choose a passphrase and remember it:

   ```bash
   ssh-keygen -t ed25519 -f ~/.ssh/kiln-release -C "kiln-release"
   ```

2. Make a **backup key** the same way (`~/.ssh/kiln-release-backup`), and keep it, and a copy of the main key,
   somewhere offline (a USB stick in a drawer). If you ever lose the main key, the backup is how you keep
   publishing updates that existing installs accept.

3. List the public keys that Kiln should trust, one per line, in `share/release-key.pub`, and commit it:

   ```bash
   cat ~/.ssh/kiln-release.pub ~/.ssh/kiln-release-backup.pub > share/release-key.pub
   git add share/release-key.pub && git commit -m "Trust the release keys" && git push
   ```

   Only the public halves go in the repository. Never copy a file without `.pub` into it.

## Every release

1. Bump `share/VERSION`, add a `## X.Y.Z` section to `CHANGELOG.md`, commit and push to `main`.
2. Run:

   ```bash
   tools/release.sh X.Y.Z
   ```

   It runs the tests, builds `kiln-X.Y.Z.tar.gz` from the pushed commit, asks for your passphrase to sign it,
   checks the signature the way Kiln will, then tags and publishes the release with the archive and its `.sig`.
   (`KILN_RELEASE_DRY=1 tools/release.sh X.Y.Z` does everything except tagging and publishing.)

A release made any other way (the GitHub web page, `gh release create` by hand) has no valid signature, so
nobody's Kiln installs it, and Kiln shows "ignored version X, it is not signed" once.

## Also worth doing on GitHub

* Turn on two-factor authentication with a passkey or a security key, and look at
  <https://github.com/settings/security-log> now and then.
* Revoke tokens you do not use at <https://github.com/settings/tokens> and sign out of `gh` on computers you
  do not own.
* Protect `main` and the `v*` tags (Settings → Rules) so history cannot be rewritten.

These protect your account. The signature protects your users even if the account is lost.

## If the key is lost or leaked

* Lost, backup exists: publish with the backup key; nothing changes for users.
* Leaked: generate a new key, publish a release signed by the *old* key that replaces `share/release-key.pub`
  with the new public key, then stop using the old key. Tell users in the release notes.
* Lost, no backup: users have to update by hand once (`git pull`, `./install.sh`) to receive a new key.
