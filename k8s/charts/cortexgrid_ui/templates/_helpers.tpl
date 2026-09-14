{{/*
Common labels applied to every resource in the cortexgrid_ui chart.
*/}}
{{- define "cortexgrid_ui.labels" -}}
app.kubernetes.io/part-of: cortexgrid-ui
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
