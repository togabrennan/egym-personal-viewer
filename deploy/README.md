# Run the eGym MCP server

Requires Linux with systemd and Tailscale installed and authenticated.
The examples use `egym-server` as a placeholder for your server's hostname.
The MCP server requires Python 3.10+ and uv; uv installs the pinned MCP SDK
in its script environment. The browser viewer continues to use only stdlib.

1. Clone your repository on egym-server to `~/egym-personal-viewer`.
   Copy your `settings.json` there with `scp`, then run
   `chmod 600 ~/egym-personal-viewer/settings.json`.
   Alternatively use `EGYM_BRAND`, `EGYM_USERNAME`, and `EGYM_PASSWORD`
   in the environment file below.
2. Install uv:

   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. Create the private environment file:

   ```sh
   mkdir -p ~/.config
   (umask 077; touch ~/.config/egym-mcp.env)
   chmod 600 ~/.config/egym-mcp.env
   python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
   $EDITOR ~/.config/egym-mcp.env
   ```

   Put the generated token and your actual Funnel hostname in the file:

   ```ini
   EGYM_MCP_TOKEN=<generated-token>
   EGYM_MCP_ALLOWED_HOSTS=egym-server.<tailnet>.ts.net
   ```

   Do not include the angle brackets. Hosts are comma separated; specify
   actual hostnames, not URLs. Optional credential environment variables
   override `settings.json`. Never commit this file or share the connector URL.

4. Install and start the user service:

   ```sh
   mkdir -p ~/.config/systemd/user
   cp ~/egym-personal-viewer/deploy/egym-mcp.service ~/.config/systemd/user/
   systemctl --user daemon-reload
   loginctl enable-linger "$USER"
   systemctl --user enable --now egym-mcp
   systemctl --user status egym-mcp
   journalctl --user -u egym-mcp -f
   ```

   If Python 3.10+ is unavailable, run `~/.local/bin/uv python install 3.12`.
   Linger keeps the user service running after logout and starts it at boot.

5. Enable public HTTPS forwarding (Funnel must be enabled for your tailnet):

   ```sh
   tailscale funnel --bg 8000
   tailscale funnel status
   ```

   The service binds only to loopback. The endpoint is
   `https://egym-server.<tailnet>.ts.net/<generated-token>/mcp`.
   Secret-path authentication means anyone holding that full URL can read
   the data and trigger a refresh. Host and Origin checks stay enabled;
   add your actual hostname to `EGYM_MCP_ALLOWED_HOSTS` to avoid 421 errors.
   Access logging is disabled and application logs redact the token.

6. Connect clients:
   - **claude.ai / mobile:** Settings → Connectors → Add custom connector,
     enter the full endpoint URL. Select no authentication if prompted.
   - **Claude Code:** `claude mcp add --transport http egym '<full-endpoint-URL>'`.
   - **Claude Desktop:** add the remote connector through its connector settings.

7. Update from the repository directory:

   ```sh
   git pull
   systemctl --user restart egym-mcp
   ```

## Local verification

Use your existing data, or generate demo data in a separate checkout with
`python3 generate_demo.py` (this replaces that checkout's workouts.json).

```sh
export EGYM_MCP_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run mcp_server.py --no-fetch
```

In another terminal run `npx @modelcontextprotocol/inspector`, select
Streamable HTTP and connect to `http://127.0.0.1:8000/<generated-token>/mcp`.
Call all seven tools. The read tools return Markdown, default to pounds,
and accept `unit="kg"` where applicable. `refresh` uses your credentials
and performs a real fetch. `--no-fetch` disables automatic startup and
periodic fetching, while manual refresh remains available.

For a local session without a token, unset `EGYM_MCP_TOKEN` and run
`uv run mcp_server.py --no-fetch --insecure-no-token`; its endpoint is `/mcp`.
Never use that mode with Funnel enabled. Non-loopback binding is rejected.

Before considering deployment complete, verify a wrong token path returns
404, the correct URL works from claude.ai, and the user service and Funnel
survive a reboot. Ask Claude for the latest leg press 1RM and compare it
with the viewer. These checks require access to egym-server and your clients.
