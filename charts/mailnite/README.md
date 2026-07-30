# mailnite (Helm chart)

The **simple single-node** Mailnite: one stateful replica, the embedded badger
store on a PersistentVolume, setup finished **in the browser** on the :8480
service console (`kubectl port-forward`, see the post-install notes). The
consensusdb cluster mode is deliberately not this chart.

```bash
helm install mail ./charts/mailnite          # from a mailnite-chart checkout
kubectl port-forward sts/mailnite 8480:8480   # → http://localhost:8480
```

## Layout

One PVC mounted at `/data`, and `MAIL_HOME_DIR=/data` makes the volume the
layout: `conf/` (config, at-rest key, console credentials — written by
onboarding), `data/` (the badger mail store) and `log/` (rotating log files,
a sibling of the store rather than a child, so logs can be shipped or dropped
without touching the mail). Nothing else needs configuring. `MAIL_CONF_DIR` /
`MAIL_DATA_DIR` / `MAIL_LOG_DIR` override an individual directory when the
three must live apart — the Linux package's case (`/etc`, `/var/lib`,
`/var/log`), not a container's.

## Health probes

Liveness and readiness target the dedicated health port **9090**, bound in
every phase — `GET /healthz` is dependency-free ("OK" while the process
serves, so a wedged store never causes a restart loop) and `GET /readyz` is
store-gated and answers 503 while first-run onboarding is unfinished, so an
unconfigured pod stays alive but takes no traffic. It is a cluster-internal
port: never put it in a Service or an Ingress.

## Secrets

Two composable patterns, both optional:

| Pattern | Values key | How |
|---|---|---|
| env | `envFromSecret` | Every config property maps to an env var (`mail.keeper-url` → `MAIL_KEEPER_URL`; the SMK rides `MAIL_SMK` with `mail.keeper-url: env://MAIL_SMK`). Priority: flags > env > config file. |
| files | `filesSecret` | Mounted read-only at `/data/conf/secrets`; reference entries from the config by **relative path** (resolved against the config dir): `mail.keeper-url: secrets/mailnite.key`, `tls.cert-file: secrets/tls.crt`, `relay.cert-file: secrets/client.crt`. |

## Mail ports

Keep the server's high internal binds (the wizard's Ports step defaults) and
enable the mail Service — it maps `25→2525, 465→2465, 587→2587, 993→2993` at
the edge, so the pod never needs `NET_BIND_SERVICE`:

```bash
helm upgrade mail ./charts/mailnite --reuse-values --set service.mail.enabled=true
```

## Values

See `values.yaml` — image, persistence (size/class), web/mail Services,
resources, `extraEnv`, timezone.
