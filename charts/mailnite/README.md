# mailnite (Helm chart)

The **simple single-node** Mailnite: one stateful replica, the embedded badger
store on a PersistentVolume, setup finished **in the browser** on the :8480
service console (`kubectl port-forward`, see the post-install notes). The
consensusdb cluster mode is deliberately not this chart.

```bash
helm install mail ./charts/mailnite          # from a mailnite-chart checkout
kubectl port-forward svc/mail-mailnite 8480:8480   # → http://localhost:8480
```

## Layout

One PVC at `/var/lib/mailnite` — the bare-metal installer's exact layout:
`conf/` (config, at-rest key, console credentials — written by onboarding)
and `data/` (the badger mail store). `MAILNITE_CONFIG`/`MAILNITE_DATA` pin it.

## Secrets

Two composable patterns, both optional:

| Pattern | Values key | How |
|---|---|---|
| env | `envFromSecret` | Every config property maps to an env var (`mail.encryption-key` → `MAIL_ENCRYPTION_KEY`, `crypto.master-key` → `CRYPTO_MASTER_KEY`). Priority: flags > env > config file. |
| files | `filesSecret` | Mounted read-only at `/var/lib/mailnite/conf/secrets`; reference entries from the config by **relative path** (resolved against the config dir): `mail.encryption-key-url: secrets/mailnite.key`, `tls.cert-file: secrets/tls.crt`, `relay.cert-file: secrets/client.crt`. |

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
