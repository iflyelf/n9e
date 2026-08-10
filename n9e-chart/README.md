# Nightingale (夜莺) Helm Chart

[Nightingale](https://n9e.github.io/) 是一款开源的云原生监控告警平台，支持 Prometheus 协议、多种数据源接入、灵活的告警规则和通知渠道。本 Helm Chart 支持部署 **中心端 (n9e)** 和 **边缘告警引擎 (n9e-edge)** 两种模式。

## 特性

- ✅ 支持中心端和边缘端双模式部署
- ✅ 外置 MySQL/PostgreSQL 和 Redis 连接
- ✅ 水平扩展：HPA + PDB + 滚动更新
- ✅ 全中文注释配置，基于官方配置文档
- ✅ 基于 Bitnami Common 库，遵循 Helm 最佳实践
- ✅ 配置文件渲染为 Secret，安全挂载到 Pod
- ✅ 预置亲和性/反亲和性/污点容忍/资源限制模板

## 前置要求

- Kubernetes 1.19+
- Helm 3.2.0+
- 外置 MySQL 或 PostgreSQL 数据库（生产环境强烈建议）
- 外置 Redis（生产环境强烈建议）
- **（可选）Gateway API**：若需对外暴露服务，需集群已安装 [Gateway API CRD](https://gateway-api.sigs.k8s.io/) (`gateway.networking.k8s.io/v1`) 和支持的控制器（如 nginx-gateway-fabric、Istio、Envoy Gateway、Cilium）

### Gateway API 安装（可选）

若集群尚未安装 Gateway API CRD：

```bash
# 安装 Gateway API CRD（标准通道，推荐）
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.2.1/standard-install.yaml

# 安装支持的网关控制器（以 nginx-gateway-fabric 为例）
kubectl apply -f https://raw.githubusercontent.com/nginxinc/nginx-gateway-fabric/v1.5.0/deploy/crds.yaml
kubectl apply -f https://raw.githubusercontent.com/nginxinc/nginx-gateway-fabric/v1.5.0/deploy/manifests/nginx-gateway.yaml

# 验证安装
kubectl get crd | grep gateway.networking.k8s.io
kubectl get pods -n nginx-gateway
```

其他控制器请参考：[Gateway API 实现列表](https://gateway-api.sigs.k8s.io/implementations/)

## 快速开始

### 1. 添加依赖（本地部署）

Chart 已内置 Bitnami Common 依赖，无需额外配置。

### 2. 部署中心端

```bash
# 创建命名空间
kubectl create namespace n9e

# 安装中心端 n9e
helm install n9e-center ./n9e-chart \
  --namespace n9e \
  --set externalDatabase.host="mysql-cluster-router.mysql.svc.cluster.local" \
  --set externalDatabase.password="your-db-password" \
  --set externalRedis.password="your-redis-password"
```

> Redis 地址默认已指向 redis-cluster 项目部署的 6 节点 Redis Cluster。
> 由于 cluster 地址是逗号分隔的多节点串，`--set` 会把逗号当分隔符，若需覆盖请用 `-f custom-values.yaml` 而非 `--set`。

### 3. 访问 Web 界面

```bash
# 端口转发（默认 ClusterIP 服务）
kubectl port-forward -n n9e svc/n9e-center 17000:17000

# 浏览器访问
http://localhost:17000

# 默认登录账号
用户名: root
密码: root.2020
```

### 4. 部署边缘告警引擎（可选）

边缘告警引擎用于多地域、边缘机房部署，连接到中心端 n9e 拉取规则并在本地执行告警评估。

```bash
# 使用边缘模式配置文件部署
helm install n9e-edge ./n9e-chart \
  -f ./n9e-chart/values-edge.yaml \
  --namespace n9e \
  --set config.edge.centerApi.addrs[0]="http://n9e-center.n9e.svc.cluster.local:17000" \
  --set externalRedis.address="edge-redis.redis.svc.cluster.local:6379" \
  --set externalRedis.password="your-redis-password"
```

**注意**：  
1. 边缘节点需要**独立部署的 Redis**（不能与中心端共用）  
2. 中心端必须开启 `config.http.apiForService.enable=true`  
3. `config.edge.centerApi.basicAuthUser` 和 `basicAuthPass` 需与中心端 `config.http.apiForService.basicAuth` 一致

## 配置说明

### 核心配置项

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `mode` | 部署模式：`center` 或 `edge` | `center` |
| `replicaCount` | 副本数（支持水平扩展） | `2` |
| `image.repository` | 镜像仓库 | `iflyelf/nightingale` |
| `image.tag` | 镜像标签 | `latest-aggregation` |
| `timezone` | 时区 | `Asia/Shanghai` |

### 服务配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `service.type` | 服务类型：ClusterIP/NodePort/LoadBalancer | `ClusterIP` |
| `service.port` | 主服务端口 | `17000`（中心端） |
| `config.http.port` | HTTP 监听端口 | `17000`（中心端）/`19000`（边缘端） |
| `service.ibexPort` | Ibex RPC 端口 | `20090` |

### Gateway API 配置（仅中心端）

> Gateway API **默认关闭**（`gateway.enabled: false`），默认部署不会创建任何 Gateway/HTTPRoute 资源。需要对外暴露服务时再显式启用。

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `gateway.enabled` | 是否启用 Gateway API | `false` |
| `gateway.create` | 是否创建独立 Gateway（false 则引用已有 Gateway） | `true` |
| `gateway.gatewayClassName` | GatewayClass 名称 | `cilium` |
| `gateway.addresses` | 指定 Gateway 对外地址（Cilium LB IP） | `[]` |
| `gateway.infrastructure.labels` | 传递到底层资源的标签（标准字段） | `{}` |
| `gateway.infrastructure.annotations` | 传递到底层资源的注解（如 Cilium LB Service 注解） | `{}` |
| `gateway.listeners` | Gateway 监听器列表（HTTP/HTTPS） | HTTP:80 |
| `gateway.httpRoute.create` | 是否创建 HTTPRoute | `true` |
| `gateway.httpRoute.parentRefs` | 引用的父 Gateway（留空自动引用本 chart 创建的 Gateway） | `[]` |
| `gateway.httpRoute.hostnames` | 匹配的域名列表 | `[n9e.example.com]` |

> **关于亲和性**：标准 Gateway 资源没有 `affinity` 字段。使用 Cilium 时，处理流量的是 `cilium-envoy` DaemonSet（每节点一个），其调度亲和性需在**安装 Cilium 的 Helm values** 中配置（`envoy.affinity`/`nodeSelector`/`tolerations`），不在本 chart 内。详见 [GATEWAY.md 场景 5](./GATEWAY.md)。

### 外置数据库配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `externalDatabase.type` | 数据库类型：mysql/postgres/sqlite | `mysql` |
| `externalDatabase.host` | 数据库主机 | `mysql-cluster-router.mysql.svc.cluster.local` |
| `externalDatabase.port` | 数据库端口 | `3306` |
| `externalDatabase.database` | 数据库名 | `n9e_v6` |
| `externalDatabase.username` | 数据库用户 | `root` |
| `externalDatabase.password` | 数据库密码 | `ysyh!9SKy` |

### 外置 Redis 配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `externalRedis.type` | Redis 类型：standalone/cluster/sentinel | `cluster` |
| `externalRedis.address` | Redis 地址（cluster 用逗号分隔多节点） | 6 节点 Redis Cluster 完整地址 |
| `externalRedis.password` | Redis 密码 | `ysyh!9SKy` |

### 资源限制

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `resources.requests.cpu` | CPU 请求 | `4` |
| `resources.requests.memory` | 内存请求 | `8Gi` |
| `resources.limits.cpu` | CPU 限制 | `4` |
| `resources.limits.memory` | 内存限制 | `8Gi` |

### 自动扩缩容

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `autoscaling.enabled` | 是否启用 HPA | `false` |
| `autoscaling.minReplicas` | 最小副本数 | `2` |
| `autoscaling.maxReplicas` | 最大副本数 | `10` |
| `autoscaling.targetCPUUtilizationPercentage` | CPU 目标使用率 | `80` |

### 安全上下文

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `hostIPC` | 使用宿主机 IPC 命名空间 | `false` |
| `hostNetwork` | 使用宿主机网络命名空间 | `false` |
| `hostPID` | 使用宿主机 PID 命名空间 | `false` |
| `securityContext.runAsUser` | Pod 运行用户 ID | `0`（root，匹配镜像） |
| `containerSecurityContext.privileged` | 是否特权容器 | `false` |

完整配置项请查看 `values.yaml`。

## 常见使用场景

### 使用 NodePort 暴露服务

```bash
helm install n9e-center ./n9e-chart -n n9e \
  --set service.type=NodePort \
  --set service.nodePort=30170
```

### 启用 Gateway API

Gateway API 是 Ingress 的现代化替代方案，需集群已安装 Gateway API CRD（`gateway.networking.k8s.io/v1`）和支持的控制器（如 Cilium、Istio、Envoy Gateway、nginx-gateway-fabric）。

Gateway API **默认关闭**，需显式启用：

```bash
helm install n9e-center ./n9e-chart -n n9e \
  --set gateway.enabled=true \
  --set gateway.gatewayClassName=cilium \
  --set gateway.httpRoute.hostnames[0]=n9e.example.com
```

引用集群已有的共享 Gateway（不创建独立 Gateway）：

```bash
helm install n9e-center ./n9e-chart -n n9e \
  --set gateway.enabled=true \
  --set gateway.create=false \
  --set gateway.httpRoute.parentRefs[0].name=shared-gateway \
  --set gateway.httpRoute.parentRefs[0].namespace=gateway-system \
  --set gateway.httpRoute.hostnames[0]=n9e.example.com
```

### 启用持久化存储（日志/数据）

```bash
helm install n9e-center ./n9e-chart -n n9e \
  --set persistence.logs.enabled=true \
  --set persistence.logs.size=20Gi \
  --set persistence.data.enabled=true \
  --set persistence.data.size=50Gi
```

### 启用 HPA 自动扩缩容

```bash
helm install n9e-center ./n9e-chart -n n9e \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=2 \
  --set autoscaling.maxReplicas=10
```

### 调整反亲和性和节点选择

```bash
# 使用软性反亲和（允许同节点多副本，但尽量分散）
helm install n9e-center ./n9e-chart -n n9e \
  --set affinity.podAntiAffinity.preferredDuringSchedulingIgnoredDuringExecution[0].weight=100 \
  --set affinity.podAntiAffinity.preferredDuringSchedulingIgnoredDuringExecution[0].podAffinityTerm.labelSelector.matchExpressions[0].key=app.kubernetes.io/name \
  --set affinity.podAntiAffinity.preferredDuringSchedulingIgnoredDuringExecution[0].podAffinityTerm.labelSelector.matchExpressions[0].operator=In \
  --set affinity.podAntiAffinity.preferredDuringSchedulingIgnoredDuringExecution[0].podAffinityTerm.labelSelector.matchExpressions[0].values[0]=n9e \
  --set affinity.podAntiAffinity.preferredDuringSchedulingIgnoredDuringExecution[0].podAffinityTerm.topologyKey=kubernetes.io/hostname

# 或修改 values.yaml 中的 affinity 段落后安装
```

## 升级与回滚

```bash
# 升级
helm upgrade n9e-center ./n9e-chart -n n9e -f custom-values.yaml

# 查看历史
helm history n9e-center -n n9e

# 回滚到上一版本
helm rollback n9e-center -n n9e

# 回滚到指定版本
helm rollback n9e-center 1 -n n9e
```

## 卸载

```bash
# 卸载 release（保留 PVC）
helm uninstall n9e-center -n n9e

# 删除 PVC（若启用了持久化）
kubectl delete pvc -n n9e -l app.kubernetes.io/instance=n9e-center
```

## 故障排查

### 查看 Pod 状态

```bash
kubectl get pods -n n9e -l app.kubernetes.io/instance=n9e-center
```

### 查看日志

```bash
# 查看所有副本日志
kubectl logs -n n9e -l app.kubernetes.io/instance=n9e-center -f

# 查看特定 Pod 日志
kubectl logs -n n9e n9e-center-xxxxxxxxxx-xxxxx -f
```

### 进入容器调试

```bash
kubectl exec -it -n n9e n9e-center-xxxxxxxxxx-xxxxx -- sh
```

### 检查配置文件

```bash
# 查看渲染后的 config.toml
kubectl get secret -n n9e n9e-center -o jsonpath='{.data.config\.toml}' | base64 -d
```

### 常见问题

**1. Pod 无法启动，报 `bind: address already in use`**

- 原因：开启了 `hostNetwork: true` 且同节点有端口冲突
- 解决：关闭 `hostNetwork`（默认已关闭）或确保节点反亲和生效

**2. 无法连接 MySQL/Redis**

- 检查 `externalDatabase.host` 和 `externalRedis.address` 是否正确
- 确认密码、数据库名是否匹配
- 验证网络策略是否允许 Pod 访问外部服务

**3. HPA 无法扩容**

- 确认 Metrics Server 已安装：`kubectl get deployment metrics-server -n kube-system`
- 检查 Pod 是否配置了 `resources.requests`

**4. 边缘模式启动失败**

- 确认镜像包含 `n9e-edge` 二进制：`docker run --rm <image> ls /opt/nightingale/ | grep edge`
- 检查 `config.edge.centerApi.addrs` 是否能从 Pod 内访问
- 验证中心端 `config.http.apiForService.enable=true` 已开启
- 确认 BasicAuth 用户名密码与中心端一致

## 架构说明

### 中心端 (n9e)

- 提供 Web UI（仪表盘、告警规则管理）
- 提供 HTTP API（数据查询、配置管理）
- 内置告警引擎（评估告警规则、发送通知）
- 支持 Ibex 故障自愈功能
- 依赖 MySQL/PostgreSQL 存储元数据，Redis 存储会话和心跳

### 边缘告警引擎 (n9e-edge)

- 仅运行告警引擎，不提供 Web UI
- 通过 HTTP API 连接到中心端，拉取告警规则
- 适合边缘机房、多地域部署场景
- 需要独立的 Redis（不能与中心端共用）
- 不依赖数据库（规则从中心端同步）

## 参考资料

- [Nightingale 官方文档](https://flashcat.cloud/docs/content/flashcat-monitor/nightingale-v9/)
- [Nightingale GitHub](https://github.com/ccfos/nightingale)
- [配置文件详解](https://flashcat.cloud/docs/content/flashcat-monitor/nightingale-v9/install/configuration/)
- [边缘机房部署](https://flashcat.cloud/docs/content/flashcat-monitor/nightingale-v9/install/edge/)

## License

本 Helm Chart 继承 Nightingale 的开源协议：[Apache License 2.0](https://github.com/ccfos/nightingale/blob/main/LICENSE)

## 维护者

- iflyelf

如有问题或建议，欢迎提交 Issue。
