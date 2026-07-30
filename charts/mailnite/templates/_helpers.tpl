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
app.kubernetes.io/version: {{ .Values.image.tag | default .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "mailnite.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mailnite.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "mailnite.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end -}}
