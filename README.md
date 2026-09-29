# mailnite-chart

The Helm chart for [Mailnite](https://mailnite.com) — the complete
self-hosted mail server in one binary: SMTP, IMAP, webmail, calendar and
contacts, one stateful replica over one volume.

```bash
helm install mailnite oci://ghcr.io/mailnite/charts/mailnite \
  -n mail --create-namespace \
  --set domain=example.com --set exposure=relay      # ← your choices, see below
```

The chart builds the infrastructure; the **setup wizard** (the `:8480`
console, over `kubectl port-forward`) configures the mail server. The install
notes print every next step for the exposure you picked: the address to wait
for, the DNS records, the router forwards or the relay setup, and the wizard
choices that match.

## Three decisions

| Value | What it decides |
|---|---|
| `domain` | Your mail domain. `mail.<domain>` becomes the mail host: MX target, certificate name, key-discovery authority, the host emailed links use. |
| `exposure` | How the internet reaches the server — the table below. |
| `gateway.name` / `ingress.enabled` | Optional: webmail on your own site too (`https://<domain>/mail`), through your Gateway API gateway or Ingress. |

### Which `exposure`?

A mail server needs raw TCP on port 25 from the whole internet, which no
HTTP gateway or CDN proxy carries. So the question is: **can this cluster
receive raw TCP on a public IP?**

| Your cluster | `exposure` | What the chart creates |
|---|---|---|
| Cloud with load balancers — GKE, AKS, EKS; bare metal with MetalLB; k3s | `loadBalancer` | One L4 LoadBalancer (static IP recommended): 25, 465, 587, 993, 143 and 443 for `mail.<domain>`. `externalTrafficPolicy: Local`, so SPF/DNSBL see the real client. |
| Bare metal behind a router with a public IP, forwarding ports | `nodePort` | The same ports on fixed NodePorts (30025, …) for the router to forward to. |
| Behind **Cloudflare**, a NAT, a home router — no inbound TCP, or port 25 already taken | `relay` | Nothing public. The server dials **out** to a [mailrelay](https://github.com/mailnite/mailrelay) on a small VDS that owns the public IP; the wizard installs and pairs it over SSH. The VDS stores no mail and no keys. |
| Just trying it | `none` (default) | Nothing public: port-forward only. Never a surprise load balancer. |

`platform` (`generic` \| `gke` \| `aks` \| `eks`) adds what that cloud needs:
load-balancer annotations and static-IP placement, and on GKE the gateway's
`GCPBackendPolicy` (long requests) and `HealthCheckPolicy` (`/mail/`, since
`/` answers 302).

**Outbound mail on clouds:** GKE always blocks port 25 egress, Azure blocks it on
most subscription types, and AWS throttles it until you ask. Receiving is
fine; for sending, set a **smarthost** (587/465) in Admin → Mail.

## Quick starts

Ready-made values in [`charts/mailnite/examples`](charts/mailnite/examples):

| File | Shape |
|---|---|
| [`gke.yaml`](charts/mailnite/examples/gke.yaml) | GKE, own regional static IP, webmail at `https://mail.<domain>/mail` |
| [`aks.yaml`](charts/mailnite/examples/aks.yaml) | AKS, own Standard public IP (+ optional app-routing Ingress) |
| [`eks.yaml`](charts/mailnite/examples/eks.yaml) | EKS, NLB via the AWS Load Balancer Controller, Elastic IPs |
| [`cloudflare-relay.yaml`](charts/mailnite/examples/cloudflare-relay.yaml) | Cluster behind Cloudflare + a Gateway: mail via relay, webmail at `<domain>/mail` **and** `mail.<domain>/mail` |
| [`homelab-nat.yaml`](charts/mailnite/examples/homelab-nat.yaml) | Home lab behind NAT: everything through the relay |
| [`router-nodeport.yaml`](charts/mailnite/examples/router-nodeport.yaml) | Router port-forwards + gateway for 443 (the mailnite.com shape) |

```bash
helm install mailnite oci://ghcr.io/mailnite/charts/mailnite -n mail --create-namespace -f gke.yaml
```

## What makes it hard to break

- **The master key never leaves the cluster.** `MAIL_SMK` seals the store. An
  init container (`mailnite k8s keys`) mints it into a Secret on the first
  start. It never passes through Helm values, release history, CI or git.
  Later starts only verify it. If the store exists but the Secret is gone, the
  init container **refuses** to mint a new key (which could never open that
  store) and prints the restore command. `helm uninstall` keeps the Secret.
  Bring your own with `keys.existingSecret`.
- **Updates like the Linux installer's hourly timer.** A CronJob
  (`mailnite k8s update`) follows the stable channel. It updates only after the
  new tag is actually published, only forward, and never across a major
  version by itself. If the server was Ready and doesn't come back Ready, it
  rolls back and skips that version. `helm upgrade` never rolls an updated
  server back to an older chart default.
- **Wrong values fail the install**, with the fix in the message: a strict
  values schema (a typo'd key is an error, not a silent no-op), a single
  replica, `eks` + a plain IP, a gateway with nothing to route.
- **Least privilege.** The server's container mounts no API token. The key
  init container may read one Secret and create it once. The updater may read
  and patch one StatefulSet. A NetworkPolicy admits only the published ports
  and allows egress only for mail, DNS, HTTP(S), the API server and, in relay
  mode, SSH plus the tunnel.

## Develop

```bash
HELM="helm" KUBECONFORM=kubeconform python3 ci/test_chart.py
```

Renders every example and edge case with each listed Helm binary (CI runs
Helm 3 and 4). Validates the output strictly against the Kubernetes and CRD
schemas (Gateway API, GKE policies). Asserts what each mode must and must not
create, and that bad values fail. CI also installs the chart on kind: key
minting, the console answering, one updater run, and an upgrade that keeps
the key.

Release: bump `version` in `charts/mailnite/Chart.yaml`, tag `vX.Y.Z` —
[`release.yaml`](.github/workflows/release.yaml) pushes
`oci://ghcr.io/mailnite/charts/mailnite`.

---
© Karagatan LLC. Mailnite is a product name of Karagatan LLC.
