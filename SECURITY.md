# Security and privacy

Report vulnerabilities using GitHub's **Security → Report a vulnerability** on this
repository when private reporting is enabled. If unavailable, open an issue containing
only a request for a private reporting channel; do not publish exploit details or secrets.
This community project has no promised response SLA. Maintainers should enable private
vulnerability reporting before announcing a release.

The supported deployment is one trusted OS user on a local workstation. Loopback is
not authentication or isolation from other users/processes on that machine. Do not expose
the router through a reverse proxy, public interface or tunnel.

Codex remains responsible for workspace sandboxing and approval prompts. LocalCodex
preserves host instructions; model obedience is not a security boundary. Only known tool
aliases are translated. Unsupported tools fail explicitly. Web content is untrusted;
public-URL validation and SSRF protections must remain in the search adapter.

Model requests target local Ollama. Use `OLLAMA_NO_CLOUD=1` on the Ollama **server** for
defense in depth; setting it only on an already-running client does not reconfigure that
server. Setup checks model metadata for remote models. Web search is optional and sends
queries to external search engines even though SearXNG itself runs locally. Installation,
model downloads and release checks also require network access.

`$LOCAL_CODEX_HOME/state/router.sqlite3` contains conversation input/output for continuation:
seven-day TTL and at most 512 responses by default. Reasoning items/summaries are excluded.
Usage history includes session titles and token counts and persists until removed. Search
results are cached locally with expiry. Database upgrade backups also contain local data;
keep them private and remove them when no longer needed. Expiry is enforced on reads and
pruned on writes/startup; it is not secure erasure of filesystem blocks or backups.

To remove all LocalCodex-managed history, stop all launchers/router processes and use
`codex-local uninstall --purge-data` for a release installation. Development-clone users
manage their `.codex-local` directory themselves. Do not upload that directory in bug reports.
