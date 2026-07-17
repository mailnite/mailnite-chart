# mailnite-chart

Helm charts for [Mailnite](https://mailnite.com) — the complete self-hosted
mail server in one binary.

| Chart | What |
|---|---|
| [`charts/mailnite`](charts/mailnite) | The **simple single-node** server: one stateful replica, embedded badger store on a PersistentVolume. Setup finishes in the browser on the `:8480` service console. |

## Install

```bash
git clone https://github.com/mailnite/mailnite-chart
helm install mail mailnite-chart/charts/mailnite

# then finish the installation in your browser:
kubectl port-forward svc/mail-mailnite 8480:8480
# → http://localhost:8480
```

The post-install notes print the full walkthrough: rollout wait, the
port-forward command, in-cluster addresses, and how to enable the public
mail-port Service once onboarding is done.

## Secrets

Two composable patterns (see the chart's `values.yaml`):

- **`envFromSecret`** — config properties as env vars from a Secret
  (`mail.encryption-key` → `MAIL_ENCRYPTION_KEY`, `crypto.master-key` →
  `CRYPTO_MASTER_KEY`, …).
- **`filesSecret`** — a Secret mounted at `/var/lib/mailnite/conf/secrets`;
  reference its entries from the config by **relative path**:
  `mail.encryption-key-url: secrets/mailnite.key`.

## Roadmap

- consensusdb cluster chart (stateless mailnite pair + raft store)
- packaged releases via a proper Helm repository (`helm repo add mailnite …`)

---
© Karagatan LLC. Mailnite is a product name of Karagatan LLC.
