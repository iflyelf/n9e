{{/*
  ============================================================================
  中心端 (n9e) 配置文件 config.toml 渲染模板
  ============================================================================
*/}}
{{- define "n9e.centerConfig" -}}
{{- $c := .Values.config -}}
{{- $db := .Values.externalDatabase -}}
{{- $redis := .Values.externalRedis -}}
[Global]
# 运行模式：release 或 debug
RunMode = {{ $c.global.runMode | quote }}

[Log]
# 日志目录
Dir = {{ $c.log.dir | quote }}
# 日志级别：DEBUG INFO WARNING ERROR
Level = {{ $c.log.level | quote }}
# 日志输出：stdout stderr file
Output = {{ $c.log.output | quote }}

[HTTP]
# HTTP 监听地址
Host = {{ $c.http.host | quote }}
# HTTP 监听端口
Port = {{ int $c.http.port }}
# HTTPS 证书文件路径
CertFile = {{ $c.http.certFile | quote }}
# HTTPS 私钥文件路径
KeyFile = {{ $c.http.keyFile | quote }}
# 是否打印访问日志
PrintAccessLog = {{ $c.http.printAccessLog }}
# 是否启用 pprof 性能分析
PProf = {{ $c.http.pprof }}
# 是否暴露 Prometheus /metrics 接口
ExposeMetrics = {{ $c.http.exposeMetrics }}
# 优雅关闭超时时间（秒）
ShutdownTimeout = {{ int $c.http.shutdownTimeout }}

[HTTP.APIForAgent]
# 是否启用面向 Agent 的 API
Enable = {{ $c.http.apiForAgent.enable }}
{{- if $c.http.apiForAgent.basicAuth }}
[HTTP.APIForAgent.BasicAuth]
{{- range $k, $v := $c.http.apiForAgent.basicAuth }}
{{ $k }} = {{ $v | quote }}
{{- end }}
{{- end }}

[HTTP.APIForService]
# 是否启用面向 Service 的 API（边缘节点接入时需开启）
Enable = {{ $c.http.apiForService.enable }}
[HTTP.APIForService.BasicAuth]
{{- range $k, $v := $c.http.apiForService.basicAuth }}
{{ $k }} = {{ $v | quote }}
{{- end }}

[HTTP.JWTAuth]
# access token 过期时间（分钟）
AccessExpired = {{ int $c.http.jwtAuth.accessExpired }}
# refresh token 过期时间（分钟）
RefreshExpired = {{ int $c.http.jwtAuth.refreshExpired }}
# Redis key 前缀
RedisKeyPrefix = {{ $c.http.jwtAuth.redisKeyPrefix | quote }}

[HTTP.TokenAuth]
# 是否启用 Token 认证
Enable = {{ $c.http.tokenAuth.enable }}

[DB]
# 数据库类型：mysql postgres sqlite
DBType = {{ $db.type | quote }}
# 数据库连接串 DSN
DSN = {{ include "n9e.dbDSN" . | quote }}
# 是否开启调试模式
Debug = {{ $db.debug }}
# 连接最大存活时间（秒）
MaxLifetime = {{ int $db.maxLifetime }}
# 最大打开连接数
MaxOpenConns = {{ int $db.maxOpenConns }}
# 最大空闲连接数
MaxIdleConns = {{ int $db.maxIdleConns }}

[Redis]
# Redis 地址，单机 ip:port，集群/哨兵用逗号分隔
Address = {{ $redis.address | quote }}
# Redis 用户名
Username = {{ $redis.username | quote }}
# Redis 密码
Password = {{ $redis.password | quote }}
# Redis 数据库序号
DB = {{ int $redis.db }}
# Redis 类型：standalone cluster sentinel miniredis
RedisType = {{ $redis.type | quote }}
{{- if eq $redis.type "sentinel" }}
# 哨兵模式主节点名称
MasterName = {{ $redis.masterName | quote }}
# 哨兵用户名
SentinelUsername = {{ $redis.sentinelUsername | quote }}
# 哨兵密码
SentinelPassword = {{ $redis.sentinelPassword | quote }}
{{- end }}
# 是否启用 TLS
UseTLS = {{ $redis.useTLS }}

[Alert]
[Alert.Heartbeat]
# 告警引擎 IP（留空自动探测）
IP = {{ $c.alert.heartbeat.ip | quote }}
# 心跳间隔（毫秒）
Interval = {{ int $c.alert.heartbeat.interval }}
# 告警引擎名称
EngineName = {{ $c.alert.heartbeat.engineName | quote }}

[Alert.Alerting]
# 通知发送并发数
NotifyConcurrency = {{ int $c.alert.alerting.notifyConcurrency }}

[Center]
# 指标元数据配置文件路径
MetricsYamlFile = {{ $c.center.metricsYamlFile | quote }}
# 国际化 Header key
I18NHeaderKey = {{ $c.center.i18nHeaderKey | quote }}

[Center.AnonymousAccess]
# 是否允许匿名查询数据源
PromQuerier = {{ $c.center.anonymousAccess.promQuerier }}
# 是否允许匿名查看告警详情
AlertDetail = {{ $c.center.anonymousAccess.alertDetail }}

[Pushgw]
# 使用数据库中的标签重写上报数据标签
LabelRewrite = {{ $c.pushgw.labelRewrite }}
# 强制使用服务端时间戳
ForceUseServerTS = {{ $c.pushgw.forceUseServerTS }}
{{- range $c.pushgw.writers }}

[[Pushgw.Writers]]
# 时序库写入地址
Url = {{ .url | quote }}
# BasicAuth 用户名
BasicAuthUser = {{ .basicAuthUser | quote }}
# BasicAuth 密码
BasicAuthPass = {{ .basicAuthPass | quote }}
{{- if .headers }}
# 附加请求头
Headers = [{{ range $i, $h := .headers }}{{ if $i }}, {{ end }}{{ $h | quote }}{{ end }}]
{{- end }}
# 请求超时（毫秒）
Timeout = {{ int .timeout }}
{{- end }}

[Ibex]
# 是否启用 Ibex 故障自愈服务端
Enable = {{ $c.ibex.enable }}
# Ibex RPC 监听地址
RPCListen = {{ $c.ibex.rpcListen | quote }}
{{- end -}}

{{/*
  ============================================================================
  边缘告警引擎 (n9e-edge) 配置文件 edge.toml 渲染模板
  与中心端差异：无 [Center] 配置，必须配置 [CenterApi]，Redis 独立部署
  ============================================================================
*/}}
{{- define "n9e.edgeConfig" -}}
{{- $c := .Values.config -}}
{{- $redis := .Values.externalRedis -}}
{{- $edge := .Values.config.edge -}}
[Global]
# 运行模式：release 或 debug
RunMode = {{ $c.global.runMode | quote }}

[CenterApi]
# 中心端 API 地址列表
Addrs = [{{ range $i, $a := $edge.centerApi.addrs }}{{ if $i }}, {{ end }}{{ $a | quote }}{{ end }}]
# BasicAuth 用户名（需与中心端 HTTP.APIForService.BasicAuth 一致）
BasicAuthUser = {{ $edge.centerApi.basicAuthUser | quote }}
# BasicAuth 密码
BasicAuthPass = {{ $edge.centerApi.basicAuthPass | quote }}
# 请求超时（毫秒）
Timeout = {{ int $edge.centerApi.timeout }}

[Log]
# 日志目录
Dir = {{ $c.log.dir | quote }}
# 日志级别：DEBUG INFO WARNING ERROR
Level = {{ $c.log.level | quote }}
# 日志输出：stdout stderr file
Output = {{ $c.log.output | quote }}

[HTTP]
# HTTP 监听地址
Host = {{ $c.http.host | quote }}
# HTTP 监听端口
Port = {{ int $c.http.port }}
# HTTPS 证书文件路径
CertFile = {{ $c.http.certFile | quote }}
# HTTPS 私钥文件路径
KeyFile = {{ $c.http.keyFile | quote }}
# 是否打印访问日志
PrintAccessLog = {{ $c.http.printAccessLog }}
# 是否启用 pprof 性能分析
PProf = {{ $c.http.pprof }}
# 是否暴露 Prometheus /metrics 接口
ExposeMetrics = {{ $c.http.exposeMetrics }}
# 优雅关闭超时时间（秒）
ShutdownTimeout = {{ int $c.http.shutdownTimeout }}

[HTTP.APIForAgent]
# 是否启用面向 Agent 的 API
Enable = {{ $c.http.apiForAgent.enable }}

[HTTP.APIForService]
# 边缘节点通常不对外提供 Service API
Enable = {{ $c.http.apiForService.enable }}
[HTTP.APIForService.BasicAuth]
{{- range $k, $v := $c.http.apiForService.basicAuth }}
{{ $k }} = {{ $v | quote }}
{{- end }}

[Alert]
[Alert.Heartbeat]
# 告警引擎 IP（留空自动探测）
IP = {{ $c.alert.heartbeat.ip | quote }}
# 心跳间隔（毫秒）
Interval = {{ int $c.alert.heartbeat.interval }}
# 告警引擎名称（边缘节点建议自定义，如 edge1）
EngineName = {{ $c.alert.heartbeat.engineName | quote }}

[Pushgw]
# 使用数据库中的标签重写上报数据标签
LabelRewrite = {{ $c.pushgw.labelRewrite }}
# 强制使用服务端时间戳
ForceUseServerTS = {{ $c.pushgw.forceUseServerTS }}
{{- range $c.pushgw.writers }}

[[Pushgw.Writers]]
# 时序库写入地址
Url = {{ .url | quote }}
# BasicAuth 用户名
BasicAuthUser = {{ .basicAuthUser | quote }}
# BasicAuth 密码
BasicAuthPass = {{ .basicAuthPass | quote }}
{{- if .headers }}
# 附加请求头
Headers = [{{ range $i, $h := .headers }}{{ if $i }}, {{ end }}{{ $h | quote }}{{ end }}]
{{- end }}
# 请求超时（毫秒）
Timeout = {{ int .timeout }}
{{- end }}

[Ibex]
# 边缘节点默认不启用 Ibex
Enable = {{ $c.ibex.enable }}
# Ibex RPC 监听地址
RPCListen = {{ $c.ibex.rpcListen | quote }}

# 边缘告警引擎需要独立部署的 Redis（不能复用中心端 Redis）
[Redis]
# Redis 地址
Address = {{ $redis.address | quote }}
# Redis 用户名
Username = {{ $redis.username | quote }}
# Redis 密码
Password = {{ $redis.password | quote }}
# Redis 数据库序号
DB = {{ int $redis.db }}
# Redis 类型：standalone cluster sentinel
RedisType = {{ $redis.type | quote }}
{{- if eq $redis.type "sentinel" }}
# 哨兵模式主节点名称
MasterName = {{ $redis.masterName | quote }}
# 哨兵用户名
SentinelUsername = {{ $redis.sentinelUsername | quote }}
# 哨兵密码
SentinelPassword = {{ $redis.sentinelPassword | quote }}
{{- end }}
# 是否启用 TLS
UseTLS = {{ $redis.useTLS }}
{{- end -}}
