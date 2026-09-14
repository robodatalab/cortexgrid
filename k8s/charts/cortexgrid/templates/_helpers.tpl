{{/*
Common labels applied to every resource in the cortexgrid chart.
*/}}
{{- define "cortexgrid.labels" -}}
app.kubernetes.io/part-of: cortexgrid
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
