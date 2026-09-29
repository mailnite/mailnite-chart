# mailnite (Helm chart)

One Mailnite server: a single stateful replica, with the embedded badger
store on one PersistentVolume. It is reachable from the internet in whatever
way the cluster allows. Setup finishes **in the browser** on the `:8480`
console over `kubectl port-forward`; the install notes print every step for
your values.

```bash
helm install mailnite oci://ghcr.io/mailnite/charts/mailnite -n mail --create-namespace \
  --set domain=example.com --set exposure=loadBalancer --set platform=gke
```

The repository [README](../../README.md) explains how to pick `exposure`. Ready-made
values are in [`examples/`](examples).

## What gets created

| Object | When | Why |
|---|---|---|
| `statefulset/mailnite` (+ `svc/mailnite-hl`) | always | The server. Pod `mailnite-0`, volume at `/data`, `podManagementPolicy: Parallel` so image updates roll even mid-onboarding. |
| `svc/mailnite` (ClusterIP) | always | Webmail backend for a gateway/ingress (`:8080`), and the console for port-forward (`:8480`, never published). |
| `svc/mailnite-mail` | `exposure` = `loadBalancer` / `nodePort` | The public edge. 25/465/587/993/143 → 2525/2465/2587/2993/2143, and 443 → 8443 for `mail.<domain>`. `externalTrafficPolicy: Local`. |
| `httproute/mailnite-mail-path`, `httproute/mailnite-host` | `gateway.name` | `/mail` on shared hosts, and everything on dedicated hosts. |
| `ingress/mailnite` | `ingress.enabled` | The same, as a classic Ingress. |
| `GCPBackendPolicy`, `HealthCheckPolicy` / `BackendConfig` | `platform=gke` + gateway / ingress | Backend timeout (long downloads), and a health check on `/mail/` (`/` is a 302). |
| `cronjob/mailnite-update` + SA/Role | `autoUpdate.enabled` | Hourly `mailnite k8s update`. |
| SA `mailnite` + Role `mailnite-keys` | always / key minting | The init container's right to read and create the key Secret. |
| `networkpolicy/mailnite`, `poddisruptionbudget/mailnite` | on by default | Ingress to the published ports only; allow-listed egress. One replica, protected from voluntary eviction. |

## The Server Master Key

`MAIL_SMK` unseals the mail store; in a pod the server reads it from the
environment (`env://MAIL_SMK`).

- **Default:** the `keys` init container runs `mailnite k8s keys`. On the first
  start it mints 32 random bytes into Secret `mailnite-keys`; on every later
  start it only verifies the key. The key never goes through values, Helm
  release history, CI or git. The Secret is not a Helm object, so
  `helm uninstall` keeps it.
- **Back it up off the cluster** as soon as the pod has started:
  ```bash
  kubectl -n mail get secret mailnite-keys -o jsonpath='{.data.MAIL_SMK}' | base64 -d
  ```
- **Restore:** if the Secret is lost but the store exists, the init container
  refuses to mint a new key, because a new key could never open that store.
  The pod waits in `Init:Error` with this command in its log:
  ```bash
  kubectl -n mail logs mailnite-0 -c keys
  kubectl -n mail create secret generic mailnite-keys --from-literal=MAIL_SMK='<saved key>'
  ```
- **Bring your own** (CI, GitOps, the mailnite.com way): create the Secret
  yourself and set `keys.existingSecret`. An optional `MAIL_PEPPER` in the
  same Secret carries an existing install's password pepper across a rebuild.

## Updates

`autoUpdate` (on by default) is the Kubernetes twin of the Linux installer's
hourly timer. Each run of the CronJob:

1. reads the StatefulSet's image and `https://get.mailnite.com/v1/mailnite.txt`;
2. does nothing for a digest, a non-release tag (`latest`, `1.5.0-rc1`), an
   older or equal channel version, a new **major** version (unless
   `allowMajor`), or a version that failed before;
3. checks that the new tag is actually **published** in the registry;
4. patches the image (server and key init container). If the server was Ready
   beforehand, it must be Ready again within `waitSeconds`. Otherwise the old
   image returns and the StatefulSet is annotated
   `mailnite.com/update-failed: <version>`, so only a newer release is tried
   next.

`helm upgrade` looks up the live StatefulSet and never renders an image
older than the one the updater installed. To pin a version, set
`autoUpdate.enabled=false` and `image.tag`.

```bash
kubectl -n mail get cronjob mailnite-update
kubectl -n mail create job --from=cronjob/mailnite-update update-now && kubectl -n mail logs -f job/update-now
```

## Security defaults

The pod runs as UID/GID 10001 with `RuntimeDefault` seccomp, no capabilities
and no privilege escalation. The server's own container mounts **no API
token**; only the key init container gets one, in its own projected volume.
The key Role can `get` one Secret by name and `create` it once. The updater
Role can `get`/`patch` one StatefulSet. Low public ports are translated by
Services, so the process never needs `NET_BIND_SERVICE`.

The NetworkPolicy admits only the published ports. The console (8480) is not
among them: port-forward reaches it from inside the pod's network namespace.
Egress is allowed only to DNS, HTTP(S), SMTP delivery and smarthosts, and the
API server (443/6443, for the key step); with `exposure=relay`, SSH plus the
relay's control port (8443 TCP/UDP) are added. Private S3, OIDC, LDAP or
custom smarthost ports go in `networkPolicy.egress.additionalRules`.

## Gotchas worth knowing

- **Settings belong to the console, not to env.** Every property maps to an env
  var, and env outranks the settings file at boot. A console setting you
  also pass as env would silently win over the console's own saves after the
  next restart. Keep `extraEnv` / `envFromSecret` for what the console does
  not own.
- **Behind a gateway or ingress** set Admin → Security → Reverse proxy (the
  gateway's addresses; provider Cloudflare when it sits in front). Otherwise
  rate limits and the login log see a single client: the gateway.
- **Cloudflare:** `mail.<domain>` must be "DNS only"; rules adding a
  Content-Security-Policy must skip `/mail/`.
- **ingress-nginx** limits request bodies to 1 MB by default: set
  `nginx.ingress.kubernetes.io/proxy-body-size` (see `examples/aks.yaml`).
- **Draining the node:** the PodDisruptionBudget blocks voluntary eviction of
  the only replica. Relax it (`podDisruptionBudget.enabled=false`) for the
  drain.
- **Deleting the namespace deletes the claim.** With a StorageClass whose
  reclaim policy is `Delete`, that deletes every mailbox.
  `helm uninstall` alone keeps both the claim and the key Secret.
- **Two installs, two namespaces:** objects are named after the chart
  (`mailnite`), not the release, so the docs' commands stay true.

## Values

See [`values.yaml`](values.yaml): every key is commented, and
[`values.schema.json`](values.schema.json) rejects unknown keys and bad
enums at install time.
