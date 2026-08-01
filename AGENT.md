# support.projectmellon.de — Agent Context

## Purpose

Transparent donation & cost tracking page for all projectmellon.de infrastructure.
Shows monthly costs, tracks donations, displays progress towards funding goal.

## Costs (monthly unless noted)

| Item                                   | Cost          | Cycle               |
| -------------------------------------- | ------------- | ------------------- |
| Plex media storage (2× 20 TB)          | 100,00 €      | monthly             |
| Dedicated server (plex + game servers) | 50,00 €       | monthly             |
| Contabo VPS (plex egress/ingress)      | 10,00 €       | monthly             |
| Hetzner VPS                            | 7,00 €        | monthly             |
| Usenet Provider                        | 15,00 €       | monthly             |
| Domains                                | 50,00 €       | yearly (~4,17 €/mo) |
| Usenet Indexer                         | 80,00 €       | yearly (~6,67 €/mo) |
| **Total**                              | **~192,83 €** | monthly             |
| **Total**                              | **~2.314 €**  | yearly              |

## Donation Sources

Donations come in through multiple channels — no single API:

- **IBAN / Dauerauftrag** — recurring bank transfers, manual tracking
- **PayPal (manual)** — one-time or recurring, tracked manually
- **Ko-fi** — has webhook support (already used for E10 via `KOFI_WEBHOOK_SECRET`)
- **Cash / in person** — manual entry

Donations are entered manually via admin UI. No automated bank/PayPal polling.

## Donation Tracking Model

Donations are tracked per calendar month. Surplus rolls over:

```
monthly goal = total monthly costs (≈193 €)
monthly surplus = donations_received - monthly_goal
running balance = previous_balance + monthly_surplus
```

- Each month has its own donations total
- Yearly costs (domains, indexer) are amortized into monthly goal
- If a month gets more than needed, the extra carries forward → future months need less
- If a month falls short, the deficit is covered by previous surplus (or shown as red)
- Running balance is visible on the public page so people see if we're ahead or behind

### Data model sketch

```json
{
  "costs": {
    "monthly": {
      "plex_storage": 100.0,
      "dedicated_server": 50.0,
      "contabo_vps": 10.0,
      "hetzner_vps": 7.0,
      "usenet_provider": 15.0
    },
    "yearly": {
      "domains": 50.0,
      "usenet_indexer": 80.0
    }
  },
  "donations": {
    "2026-01": [
      { "date": "2026-01-05", "amount": 20.0, "source": "paypal", "name": "Max" },
      { "date": "2026-01-15", "amount": 50.0, "source": "iban", "name": "Anna" }
    ],
    "2026-02": []
  },
  "running_balance": 0.0
}
```

## API Keys & Tools (available in ENV)

These are set on the server and available to agent / deployed services:

| Key                       | Purpose                                                                                               |
| ------------------------- | ----------------------------------------------------------------------------------------------------- |
| `CLOUDFLARE_API_TOKEN`    | Cloudflare DNS management (SRV records, CNAME). Zone ID: `70505a13081de0743e8e7fbae48d6611`           |
| `KAGI_API`                | Kagi search API. Used for web research. Docs in `e10/docs/kagi.md`. Endpoints: FastGPT, Search.       |
| `CF_API_TOKEN`            | CurseForge API. Used by itzg/minecraft-server to download modpacks.                                   |
| `KOFI_WEBHOOK_SECRET`     | Ko-fi webhook verification. Used by E10 Flask app and this project at `/api/kofi-webhook`. In `.env`. |
| `RCON_PASSWORD`           | Minecraft RCON password for both PROD and TEST instances.                                             |
| `GRAFANA_PASSWORD`        | Grafana admin login.                                                                                  |
| `AUTH_USER` / `AUTH_PASS` | HTTP basic auth for E10 admin dashboard and support page. In `.env`.                                  |

## Other Services & Tools in the Ecosystem

| Tool                      | What it does                                                                                                                                                              |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **itzg/minecraft-server** | Docker image that runs the Minecraft server. Handles modpack download via CurseForge API.                                                                                 |
| **Caddy**                 | Reverse proxy / TLS termination. Routes `*.projectmellon.de` to internal services. Config: `/home/momo/Caddyfile`. Reload: `sudo systemctl restart caddy-planet.service`. |
| **Ansible**               | Infrastructure automation. Deploys compose files, sets DNS, manages configs. Playbooks in `e10/ansible/`.                                                                 |
| **Borg**                  | Incremental backups. E10 worlds backed up every 30 min. Retention: 48 hourly + 30 daily + 12 weekly.                                                                      |
| **Prometheus + Grafana**  | Monitoring stack. E10 exports TPS/MSPT/players via Flask `/metrics` → Prometheus scrapes → Grafana dashboards.                                                            |
| **Docker Compose**        | Three stacks: `prod/`, `test/`, `shared/` (Prometheus, Grafana, Flask, Caddy).                                                                                            |
| **systemd journal**       | Logging. Tags: `e10-prod`, `e10-test`, `e10-shared`.                                                                                                                      |

## Deployment Target

- **Server:** projectmellon.de (SSH: `ssh projectmellon.de`, no password)
- **This project:** likely deployed as another service in E10's `shared/compose.yaml`, or standalone compose
- **Domain:** `support.projectmellon.de` (new Caddy route needed)
- **Data path:** `/srv/support/` or similar on server

## Tech Stack (this project)

- Python Flask (same as E10 shared/webui)
- Single JSON file for persistence (`data.json`) — no database
- Jinja2 templates, vanilla JS
- Dark theme CSS matching existing projectmellon.de style
- Admin protected by basic auth (reuse `AUTH_USER` / `AUTH_PASS`)
- Public page: cost breakdown, donation progress bar, running balance, supporter list

## Design Constraints

- No external donation platform dependency (Ko-fi is optional bonus)
- Manual entry for non-Ko-fi donations
- All costs in EUR
- Transparent: every cost item listed, no hidden fees
- Privacy: supporters shown by name only if they opt in
