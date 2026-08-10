{{/* vim: set filetype=mustache: */}}

{{/*
返回 Nightingale 镜像全名
基于 bitnami common 库的 common.images.image 生成
*/}}
{{- define "n9e.image" -}}
{{ include "common.images.image" (dict "imageRoot" .Values.image "global" .Values.global) }}
{{- end -}}

{{/*
返回镜像拉取 Secrets
*/}}
{{- define "n9e.imagePullSecrets" -}}
{{- include "common.images.pullSecrets" (dict "images" (list .Values.image) "global" .Values.global) -}}
{{- end -}}

{{/*
返回 ServiceAccount 名称
*/}}
{{- define "n9e.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
    {{ default (include "common.names.fullname" .) .Values.serviceAccount.name }}
{{- else -}}
    {{ default "default" .Values.serviceAccount.name }}
{{- end -}}
{{- end -}}

{{/*
返回存储 DB/Redis 密码的 Secret 名称
*/}}
{{- define "n9e.secretName" -}}
{{- printf "%s" (include "common.names.fullname" .) -}}
{{- end -}}

{{/*
返回配置文件 ConfigMap 名称
*/}}
{{- define "n9e.configmapName" -}}
{{- printf "%s-config" (include "common.names.fullname" .) -}}
{{- end -}}

{{/*
判断是否为边缘模式
*/}}
{{- define "n9e.isEdge" -}}
{{- if eq .Values.mode "edge" -}}
true
{{- end -}}
{{- end -}}

{{/*
返回启动命令参数（区分中心端和边缘端）
中心端: n9e
边缘端: n9e-edge
*/}}
{{- define "n9e.command" -}}
{{- if eq .Values.mode "edge" -}}
n9e-edge
{{- else -}}
n9e
{{- end -}}
{{- end -}}

{{/*
返回配置文件挂载的子路径文件名
中心端: config.toml
边缘端: edge.toml
*/}}
{{- define "n9e.configFileName" -}}
{{- if eq .Values.mode "edge" -}}
edge.toml
{{- else -}}
config.toml
{{- end -}}
{{- end -}}

{{/*
构建 MySQL DSN 连接串
*/}}
{{- define "n9e.mysqlDSN" -}}
{{- $db := .Values.externalDatabase -}}
{{- if $db.dsn -}}
{{- $db.dsn -}}
{{- else -}}
{{- printf "%s:%s@tcp(%s:%v)/%s?charset=utf8mb4&collation=utf8mb4_general_ci&parseTime=True&loc=Local" $db.username $db.password $db.host (int $db.port) $db.database -}}
{{- end -}}
{{- end -}}

{{/*
构建 PostgreSQL DSN 连接串
*/}}
{{- define "n9e.postgresDSN" -}}
{{- $db := .Values.externalDatabase -}}
{{- if $db.dsn -}}
{{- $db.dsn -}}
{{- else -}}
{{- printf "host=%s port=%v user=%s dbname=%s password=%s sslmode=disable" $db.host (int $db.port) $db.username $db.database $db.password -}}
{{- end -}}
{{- end -}}

{{/*
返回数据库 DSN（根据类型自动选择）
*/}}
{{- define "n9e.dbDSN" -}}
{{- $db := .Values.externalDatabase -}}
{{- if eq $db.type "mysql" -}}
{{- include "n9e.mysqlDSN" . -}}
{{- else if eq $db.type "postgres" -}}
{{- include "n9e.postgresDSN" . -}}
{{- else -}}
{{- default "n9e.db" $db.dsn -}}
{{- end -}}
{{- end -}}

{{/*
校验配置合法性
*/}}
{{- define "n9e.validateValues" -}}
{{- $messages := list -}}
{{- $messages := append $messages (include "n9e.validateValues.mode" .) -}}
{{- $messages := append $messages (include "n9e.validateValues.edge" .) -}}
{{- $messages := without $messages "" -}}
{{- $message := join "\n" $messages -}}
{{- if $message -}}
{{- printf "\n配置校验失败:\n%s" $message | fail -}}
{{- end -}}
{{- end -}}

{{/*
校验部署模式
*/}}
{{- define "n9e.validateValues.mode" -}}
{{- if not (or (eq .Values.mode "center") (eq .Values.mode "edge")) -}}
n9e: mode
    部署模式 (mode) 必须为 "center" 或 "edge"，当前值为 "{{ .Values.mode }}"
{{- end -}}
{{- end -}}

{{/*
校验边缘模式必填项
*/}}
{{- define "n9e.validateValues.edge" -}}
{{- if eq .Values.mode "edge" -}}
{{- if not .Values.config.edge.centerApi.addrs -}}
n9e: config.edge.centerApi.addrs
    边缘模式必须配置中心端 API 地址 (config.edge.centerApi.addrs)
{{- end -}}
{{- end -}}
{{- end -}}
