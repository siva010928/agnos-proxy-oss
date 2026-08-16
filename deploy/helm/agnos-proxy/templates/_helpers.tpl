{{/*
Chart name (respecting nameOverride), truncated to the 63-char DNS limit.
*/}}
{{- define "agnos-proxy.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name. Used to name every resource so multiple releases can
coexist in one namespace. Respects fullnameOverride; otherwise "<release>-<name>"
(collapsed when the release name already contains the chart name).
*/}}
{{- define "agnos-proxy.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end }}

{{/*
Chart label value: "<name>-<version>" with any "+" (build metadata) made label-safe.
*/}}
{{- define "agnos-proxy.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels applied to every object. Deliberately does NOT pin a component so
per-component resources (gateway/postgres/redis) can add their own without clashing.
*/}}
{{- define "agnos-proxy.labels" -}}
helm.sh/chart: {{ include "agnos-proxy.chart" . }}
app.kubernetes.io/name: {{ include "agnos-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: agnos-proxy
{{- end }}

{{/*
Selector labels for the GATEWAY workload/service. Includes component=gateway so the
gateway Service never accidentally selects the bundled postgres/redis pods (which
share the same name+instance labels).
*/}}
{{- define "agnos-proxy.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agnos-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: gateway
{{- end }}

{{/*
Gateway image reference. Tag falls back to "latest" when image.tag is "" (the registry
publishes :latest and :vX.Y.Z; pin image.tag to a released vX.Y.Z for reproducibility).
*/}}
{{- define "agnos-proxy.image" -}}
{{- $tag := .Values.image.tag | default "latest" -}}
{{- printf "%s:%s" .Values.image.repository $tag -}}
{{- end }}

{{/*
Names for the bundled datastores.
*/}}
{{- define "agnos-proxy.postgres.fullname" -}}
{{- printf "%s-postgres" (include "agnos-proxy.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "agnos-proxy.redis.fullname" -}}
{{- printf "%s-redis" (include "agnos-proxy.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Per-datastore selector labels (component-scoped).
*/}}
{{- define "agnos-proxy.postgres.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agnos-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: postgres
{{- end }}

{{- define "agnos-proxy.redis.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agnos-proxy.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: redis
{{- end }}

{{/*
Name of the Secret the gateway reads: a user-supplied existingSecret if set,
otherwise the chart-managed "<fullname>-secrets".
*/}}
{{- define "agnos-proxy.secretName" -}}
{{- if .Values.existingSecret -}}
{{- .Values.existingSecret -}}
{{- else -}}
{{- printf "%s-secrets" (include "agnos-proxy.fullname" .) -}}
{{- end -}}
{{- end }}

{{/*
Name of the gateway ConfigMap.
*/}}
{{- define "agnos-proxy.configName" -}}
{{- printf "%s-config" (include "agnos-proxy.fullname" .) -}}
{{- end }}

{{/*
Governance DB URL: an explicit governanceDbUrl override wins; otherwise the bundled
Postgres service DNS name (when postgres.enabled); otherwise empty (the gateway will
fail fast at startup with a clear message, which is the desired signal).
*/}}
{{- define "agnos-proxy.governanceDbUrl" -}}
{{- if .Values.governanceDbUrl -}}
{{- .Values.governanceDbUrl -}}
{{- else if .Values.postgres.enabled -}}
{{- printf "postgresql+asyncpg://%s:%s@%s:5432/%s" .Values.postgres.user .Values.postgres.password (include "agnos-proxy.postgres.fullname" .) .Values.postgres.db -}}
{{- end -}}
{{- end }}

{{/*
Redis URL: the bundled Redis service DNS when redis.enabled; else the external
redis.url override (blank => distributed rate-limiting stays off).
*/}}
{{- define "agnos-proxy.redisUrl" -}}
{{- if .Values.redis.enabled -}}
{{- printf "redis://%s:6379" (include "agnos-proxy.redis.fullname" .) -}}
{{- else -}}
{{- .Values.redis.url | default "" -}}
{{- end -}}
{{- end }}

{{/*
Resolve a secret value with cross-upgrade persistence. Order of precedence:
  1. explicit value from values.yaml (.provided)
  2. the value already stored in the live cluster Secret (.existing.data, base64), decoded
  3. a freshly generated random string of length .len
Returns PLAINTEXT; the caller base64-encodes it. Using lookup() (2) is what makes
generated secrets STABLE across `helm upgrade` instead of rotating on every release.
Usage:
  {{ include "agnos-proxy.resolveSecret" (dict "provided" .Values.secrets.x "existing" $existing "key" "KEY" "len" 40) }}
*/}}
{{- define "agnos-proxy.resolveSecret" -}}
{{- if .provided -}}
{{- .provided -}}
{{- else if and .existing (hasKey .existing.data .key) -}}
{{- index .existing.data .key | b64dec -}}
{{- else -}}
{{- randAlphaNum (int .len) -}}
{{- end -}}
{{- end }}
