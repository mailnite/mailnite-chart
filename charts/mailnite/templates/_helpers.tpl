{{- define "mailnite.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/*
Resources are named after the CHART, not the release: `mailnite`, never
`<release>-mailnite`. The names are documented — the post-install notes, the
site's Operations page, the deploy pipelines and every runbook say
`statefulset/mailnite` and `svc/mailnite` — and a name that changes with the
release makes each of those wrong for someone.

The trade-off is deliberate: two releases of this chart cannot share a
namespace. That is the right shape for a mail server, which is one identity
over one volume; give a second install its own namespace, or set
fullnameOverride.
*/}}
{{- define "mailnite.fullname" -}}
{{- default (include "mailnite.name" .) .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "mailnite.labels" -}}
app.kubernetes.io/name: {{ include "mailnite.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ include "mailnite.tag" . | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "mailnite.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mailnite.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
The image tag. With autoUpdate on, the CronJob may have moved the live
StatefulSet PAST the chart's default; a later `helm upgrade` must not roll the
server back to it (Helm's three-way merge would otherwise restore the older
tag). So the tag is the NEWER of the configured one and the live one. lookup
is empty under `helm template`, which then renders the configured tag.
*/}}
{{- define "mailnite.tag" -}}
{{- $tag := .Values.image.tag | default .Chart.AppVersion | toString -}}
{{- if .Values.autoUpdate.enabled -}}
{{- $sts := lookup "apps/v1" "StatefulSet" .Release.Namespace (include "mailnite.fullname" .) -}}
{{- if $sts -}}
{{- range $c := $sts.spec.template.spec.containers -}}
{{- if eq $c.name "mailnite" -}}
{{- $live := splitList ":" $c.image | last -}}
{{- $release := "^v?[0-9]+\\.[0-9]+\\.[0-9]+$" -}}
{{- if and (regexMatch $release $live) (regexMatch $release $tag) (semverCompare (printf "> %s" $tag) $live) -}}
{{- $tag = $live -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- end -}}
{{- $tag -}}
{{- end -}}

{{- define "mailnite.image" -}}
{{- printf "%s:%s" .Values.image.repository (include "mailnite.tag" .) -}}
{{- end -}}

{{- define "mailnite.keysSecret" -}}
{{- .Values.keys.existingSecret | default (printf "%s-keys" (include "mailnite.fullname" .)) -}}
{{- end -}}

{{/* The key Secret is minted by the pod itself (init container) unless the operator brings one. */}}
{{- define "mailnite.mintKeys" -}}
{{- if and .Values.keys.enabled (not .Values.keys.existingSecret) -}}true{{- end -}}
{{- end -}}

{{- define "mailnite.mailEdge" -}}
{{- if or (eq .Values.exposure "loadBalancer") (eq .Values.exposure "nodePort") -}}true{{- end -}}
{{- end -}}

{{/*
Route hostnames. Shared hosts get only /mail (the site keeps "/"); dedicated
hosts get every path. Defaults follow the exposure: mail.<domain> belongs to
the gateway only where the gateway is what answers mail.<domain>:443 — behind
a router (nodePort). With loadBalancer or relay it resolves to the LB or the
VDS, which serve it themselves.
*/}}
{{- define "mailnite.sharedHosts" -}}
{{- $explicit := index . 0 -}}
{{- $root := index . 1 -}}
{{- if $explicit -}}
{{- toJson $explicit -}}
{{- else if $root.Values.domain -}}
{{- toJson (list $root.Values.domain) -}}
{{- else -}}
[]
{{- end -}}
{{- end -}}

{{- define "mailnite.dedicatedHosts" -}}
{{- $explicit := index . 0 -}}
{{- $root := index . 1 -}}
{{- if $explicit -}}
{{- toJson $explicit -}}
{{- else if and $root.Values.domain (eq $root.Values.exposure "nodePort") -}}
{{- toJson (list (printf "mail.%s" $root.Values.domain)) -}}
{{- else -}}
[]
{{- end -}}
{{- end -}}

{{/* Load-balancer annotations: the platform's defaults, the user's on top. */}}
{{- define "mailnite.lbAnnotations" -}}
{{- $a := dict -}}
{{- if eq .Values.platform "gke" -}}
{{- $_ := set $a "cloud.google.com/l4-rbs" "enabled" -}}
{{- else if eq .Values.platform "aks" -}}
{{- with .Values.mail.loadBalancer.ip -}}
{{- $_ := set $a "service.beta.kubernetes.io/azure-load-balancer-ipv4" . -}}
{{- end -}}
{{- else if eq .Values.platform "eks" -}}
{{- $_ := set $a "service.beta.kubernetes.io/aws-load-balancer-type" "external" -}}
{{- $_ := set $a "service.beta.kubernetes.io/aws-load-balancer-scheme" "internet-facing" -}}
{{- $_ := set $a "service.beta.kubernetes.io/aws-load-balancer-nlb-target-type" "ip" -}}
{{- $_ := set $a "service.beta.kubernetes.io/aws-load-balancer-target-group-attributes" "preserve_client_ip.enabled=true" -}}
{{- end -}}
{{- $a = merge (deepCopy .Values.mail.loadBalancer.annotations) $a -}}
{{- if $a -}}
{{- toYaml $a -}}
{{- end -}}
{{- end -}}

{{/* Enabled mail ports, in a stable order. */}}
{{- define "mailnite.mailPorts" -}}
{{- $out := list -}}
{{- range $name := list "smtp" "submissions" "submission" "imaps" "imap" "https" -}}
{{- $p := index $.Values.mail.ports $name -}}
{{- if and $p $p.enabled -}}
{{- $out = append $out (dict "name" $name "port" $p.port "targetPort" $p.targetPort "nodePort" $p.nodePort) -}}
{{- end -}}
{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/*
Fail-fast checks: a wrong combination should stop `helm install`, with the
fix in the message, not surface later as a silent pod or a dead port.
*/}}
{{- define "mailnite.validate" -}}
{{- if ne (int .Values.replicaCount) 1 -}}
{{- fail "replicaCount must be 1: the embedded badger store has a single writer" -}}
{{- end -}}
{{- if not (has .Values.exposure (list "none" "loadBalancer" "nodePort" "relay")) -}}
{{- fail (printf "exposure must be none, loadBalancer, nodePort or relay (got %q)" .Values.exposure) -}}
{{- end -}}
{{- if not (has .Values.platform (list "generic" "gke" "aks" "eks")) -}}
{{- fail (printf "platform must be generic, gke, aks or eks (got %q)" .Values.platform) -}}
{{- end -}}
{{- if and .Values.domain (not (regexMatch "^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\\.)+[a-z]{2,63}$" .Values.domain)) -}}
{{- fail (printf "domain must be a lower-case DNS name like example.com (got %q)" .Values.domain) -}}
{{- end -}}
{{- if and .Values.gateway.name (not .Values.domain) (not .Values.gateway.sharedHosts) (not .Values.gateway.dedicatedHosts) -}}
{{- fail "gateway.name is set but there is nothing to route: set domain (routes <domain>/mail), or list gateway.sharedHosts / gateway.dedicatedHosts" -}}
{{- end -}}
{{- if and .Values.ingress.enabled (not .Values.domain) (not .Values.ingress.sharedHosts) (not .Values.ingress.dedicatedHosts) -}}
{{- fail "ingress.enabled is set but there is nothing to route: set domain, or list ingress.sharedHosts / ingress.dedicatedHosts" -}}
{{- end -}}
{{- if and (eq .Values.platform "eks") .Values.mail.loadBalancer.ip -}}
{{- fail "EKS pins NLB addresses by Elastic IP ALLOCATION, not by IP: drop mail.loadBalancer.ip and set the service.beta.kubernetes.io/aws-load-balancer-eip-allocations annotation (see examples/eks.yaml)" -}}
{{- end -}}
{{- if and (include "mailnite.mailEdge" .) (eq (include "mailnite.mailPorts" .) "[]") -}}
{{- fail (printf "exposure=%s but every mail.ports entry is disabled" .Values.exposure) -}}
{{- end -}}
{{- if and .Values.keys.enabled .Values.keys.existingSecret (not (regexMatch "^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$" .Values.keys.existingSecret)) -}}
{{- fail (printf "keys.existingSecret %q is not a valid Secret name" .Values.keys.existingSecret) -}}
{{- end -}}
{{- end -}}
