# Cursor tunnel fallback

Prefer LAN first: **`http://llm.jarvis.lan/v1`** (see [OpenAI-compatible API](../api/openai-compat.md)). Use a tunnel only if Cursor’s BYOK path cannot reach RFC1918 HTTP.

## When you need this

Cursor may route custom OpenAI base URLs through its cloud. If chat fails with connection errors while `curl` from your laptop to `llm.jarvis.lan` works, expose the same `/v1` surface over HTTPS.

## Cloudflare Tunnel (example)

On a host that can reach **`http://192.168.86.10:80`** (or in-cluster via port-forward):

```bash
# Install cloudflared, then:
cloudflared tunnel --url http://192.168.86.10:80
```

In Cursor:

- Override OpenAI Base URL = `https://<tunnel-host>/v1`
- API key = **`JARVIS_OPENAI_API_KEY`**
- Model id = alias from **`GET /v1/models`**

## ngrok

```bash
ngrok http 192.168.86.10:80
```

Same Cursor settings with the `https://…` forwarding URL + `/v1`.

## Security

- Keep **`JARVIS_OPENAI_API_KEY`** set whenever `/v1` is reachable beyond the trusted LAN.
- Prefer ephemeral tunnels; revoke URLs when done.
- Do not expose raw Ollama (`:11434`) publicly — always front with Jarvis `/v1` so auth, feedback, and metrics stay in place.
