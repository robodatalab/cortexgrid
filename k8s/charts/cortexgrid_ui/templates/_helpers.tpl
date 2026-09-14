{{/*
Common labels applied to every resource in the cortexflow_ui chart.
*/}}
{{- define "cortexflow_ui.labels" -}}
app.kubernetes.io/part-of: cortexflow-ui
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
