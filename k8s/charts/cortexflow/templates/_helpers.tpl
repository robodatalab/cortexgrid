{{/*
Common labels applied to every resource in the cortexflow chart.
*/}}
{{- define "cortexflow.labels" -}}
app.kubernetes.io/part-of: cortexflow
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
