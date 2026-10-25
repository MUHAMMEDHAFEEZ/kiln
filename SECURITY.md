# Security

* **Updates are signed.** `kiln update` and the automatic updates install a release only when its archive carries
  a valid signature from the project's release key (`share/release-key.pub`, checked with `ssh-keygen -Y verify`).
  An unsigned or wrongly signed release is ignored and Kiln says so. See [docs/RELEASING.md](docs/RELEASING.md).
* **Your accounts' details** (Todoist, Jira, ...) stay on your computer in `~/.config/kiln/accounts.json`, readable
  only by you, and are sent only to the service itself. See [docs/ACCOUNTS.md](docs/ACCOUNTS.md).
* Kiln has no server and sends no telemetry. The only thing it asks the internet for by itself is the list of
  releases on GitHub (turn it off with `updates.mode: "off"`) and the services you connected.

Found a vulnerability? Please report it privately through the repository's *Security → Report a vulnerability*
page instead of opening a public issue.
