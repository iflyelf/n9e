# Nightingale (夜莺) Helm Chart

[Nightingale](https://n9e.github.io/) 是一款开源的云原生监控告警平台，支持 Prometheus 协议、多种数据源接入、灵活的告警规则和通知渠道。本 Helm Chart 支持部署 **中心端 (n9e)** 和 **边缘告警引擎 (n9e-edge)** 两种模式。

## 目录

- [特性](#特性)
- [前置要求](#前置要求)
- [快速部署（5分钟）](#快速部署5分钟)
- [生产环境部署](#生产环境部署)
- [Gateway API 使用指南](#gateway-api-使用指南)
- [告警通知脚本配置](#告警通知脚本配置)
- [边缘告警引擎部署](#边缘告警引擎部署)
- [常用运维操作](#常用运维操作)
- [监控与告警](#监控与告警)
- [故障排查](#故障排查)
- [性能调优](#性能调优)
- [安全加固](#安全加固)
- [配置参考](#配置参考)
- [架构说明](#架构说明)
- [参考资料](#参考资料)

## 特性

- ✅ 支持中心端和边缘端双模式部署
- ✅ 外置 MySQL/PostgreSQL 和 Redis 连接
- ✅ 水平扩展：HPA + PDB + 滚动更新
- ✅ 全中文注释配置，基于官方配置文档
- ✅ 基于 Bitnami Common 库，遵循 Helm 最佳实践
- ✅ 配置文件渲染为 Secret，安全挂载到 Pod
- ✅ 预置亲和性/反亲和性/污点容忍/资源限制模板
- ✅ 支持 Gateway API（Ingress 现代化替代方案）

## 前置要求

- Kubernetes 1.19+
- Helm 3.2.0+
- 外置 MySQL 或 PostgreSQL 数据库（生产环境强烈建议）
- 外置 Redis（生产环境强烈建议）
- **MySQL 已创建 `n9e_v6` 数据库**（n9e 会自动建表）
- **（可选）Gateway API**：若需对外暴露服务，需集群已安装 [Gateway API CRD](https://gateway-api.sigs.k8s.io/) 和支持的控制器

## 快速部署（5分钟）

### 前提条件

- Kubernetes 1.19+ 集群
- Helm 3.2.0+
- 已部署 MySQL 和 Redis（外置连接）
- **MySQL 已创建 `n9e_v6` 数据库**（n9e 会自动建表）

### 1. 准备数据库

```bash
# 如果使用 Percona MySQL Cluster,先创建数据库
kubectl exec -it -n mysql mysql-cluster-mysql-0 -c mysql -- \
  mysql -uroot -p'<密码>' -e "CREATE DATABASE IF NOT EXISTS n9e_v6 DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci"

# 获取 Percona 自动生成的密码
kubectl get secret -n mysql mysql-cluster-secrets -o jsonpath='{.data.root}' | base64 -d
echo
```

### 2. 准备命名空间

```bash
kubectl create namespace n9e
```

### 3. 给节点打标签（重要）

Chart 默认在 `values.yaml` 中启用了**硬性**节点亲和性（`nodeAffinity.requiredDuringSchedulingIgnoredDuringExecution`），要求 Pod 只能调度到带有 `application/n9e=nightingale` 标签的节点。**未打标签的集群会导致 Pod 一直处于 `Pending` 状态。**

```bash
# 查看节点
kubectl get nodes

# 给目标节点打标签（替换为实际节点名，可给多个节点打标签）
kubectl label node <node-name> application/n9e=nightingale

# 验证标签
kubectl get nodes -l application/n9e=nightingale
```

> 如果不想使用节点亲和性固定节点，可在部署时关闭：`--set affinity=null`，或在自定义 values 中将 `affinity: {}` 置空。

### 4. 部署中心端

```bash
# 进入 Chart 所在的父目录（包含 n9e-chart 目录的目录）

# 方式一：直接安装（使用 values.yaml 中的默认配置）
helm install n9e-center ./n9e-chart --namespace n9e

# 方式二：覆盖关键配置
helm install n9e-center ./n9e-chart --namespace n9e \
  --set externalDatabase.password="your-mysql-password" \
  --set externalRedis.password="your-redis-password"
```

### 5. 访问 Web UI

```bash
# 端口转发
kubectl port-forward -n n9e svc/n9e-center 17000:17000

# 浏览器访问
http://localhost:17000

# 默认账号
用户名: root
密码: root.2020
```


## 生产环境部署

### 0. Percona MySQL Cluster 集成说明

如果使用 Percona Operator 部署的 MySQL 集群,请注意以下要点:

#### 连接方式选择

Percona Operator 自动创建多个 Service 和端口:

| Service                       | 端口 | 用途            | 推荐场景                         |
| ----------------------------- | ---- | --------------- | -------------------------------- |
| `mysql-cluster-mysql-primary` | 3306 | 直连主节点      | **推荐**: 避免 Router 层权限问题 |
| `mysql-cluster-router`        | 6446 | Router 读写端口 | Group Replication 负载均衡       |
| `mysql-cluster-router`        | 6447 | Router 只读端口 | 只读查询                         |
| `mysql-cluster-router`        | 3306 | Router 标准端口 | ⚠️ 行为不确定,不推荐           |

#### 获取数据库密码

```bash
# Percona Operator 自动生成的 root 密码
kubectl get secret -n mysql mysql-cluster-secrets -o jsonpath='{.data.root}' | base64 -d
echo

# 查看完整的连接配置 (包含推荐的 host/port)
kubectl get secret -n mysql mysql-cluster-psuser-root -o yaml
```

#### 数据库初始化

n9e 首次启动时会自动创建表结构,但需要提前创建数据库:

```bash
# 连接到 MySQL 主节点
kubectl exec -it -n mysql mysql-cluster-mysql-0 -c mysql -- \
  mysql -uroot -p'<从Secret获取的密码>'

# 在 MySQL 中执行
CREATE DATABASE IF NOT EXISTS n9e_v6 DEFAULT CHARSET utf8mb4 COLLATE utf8mb4_unicode_ci;
SHOW DATABASES LIKE 'n9e_v6';
EXIT;
```

**注意**:

- Percona Group Replication 集群中,只有主节点 (primary) 可写
- 副本 (replica) 处于 `super_read_only` 模式
- 不确定哪个是主节点时,使用 `mysql-cluster-mysql-primary` Service (自动指向主节点)

### 1. 准备自定义配置文件

```bash
cp n9e-chart/values.yaml n9e-chart/values-production.yaml
```

### 2. 修改关键配置

编辑 `values-production.yaml`：

```yaml
# 副本数
replicaCount: 3

# 镜像
image:
  repository: iflyelf/nightingale
  tag: "v8.2.0-aggregation"
  pullPolicy: IfNotPresent

# 外置数据库
externalDatabase:
  type: mysql
  # Percona MySQL Cluster 推荐使用 primary 直连或 Router 的读写端口
  # 方式1: 直连 primary (推荐,避免 Router 层权限问题)
  host: "mysql-cluster-mysql-primary.mysql.svc.cluster.local"
  port: 3306
  # 方式2: 使用 Router 读写端口 (Group Replication 负载均衡)
  # host: "mysql-cluster-router.mysql.svc.cluster.local"
  # port: 6446  # 注意是 6446 而非 3306
  database: "n9e_v6"
  username: "root"
  password: "YOUR_MYSQL_PASSWORD"  # 修改为实际密码
  # 提示: Percona Operator 自动生成的密码在 Secret mysql-cluster-secrets 的 root 字段中
  # 获取: kubectl get secret -n mysql mysql-cluster-secrets -o jsonpath='{.data.root}' | base64 -d

# 外置 Redis（Redis Cluster 6 节点分片集群）
externalRedis:
  type: cluster
  # cluster 模式列出所有节点，客户端自动发现拓扑（对应 redis-cluster 项目部署）
  address: "redis-cluster-0.redis-cluster-headless.redis.svc.cluster.local:6379,redis-cluster-1.redis-cluster-headless.redis.svc.cluster.local:6379,redis-cluster-2.redis-cluster-headless.redis.svc.cluster.local:6379,redis-cluster-3.redis-cluster-headless.redis.svc.cluster.local:6379,redis-cluster-4.redis-cluster-headless.redis.svc.cluster.local:6379,redis-cluster-5.redis-cluster-headless.redis.svc.cluster.local:6379"
  password: "YOUR_REDIS_PASSWORD"  # 修改为实际密码
  db: 0  # cluster 模式固定为 0

# 资源限制（按实际需求调整）
resources:
  requests:
    cpu: 4
    memory: 8Gi
  limits:
    cpu: 8
    memory: 16Gi

# 启用 HPA
autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70

# 节点亲和性（固定调度节点）
# 默认要求节点带 application/n9e=nightingale 标签，可按需修改标签键值
affinity:
  nodeAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      nodeSelectorTerms:
        - matchExpressions:
            - key: application/n9e
              operator: In
              values: ["nightingale"]
  # 多副本分散到不同节点，避免单点
  podAntiAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      - labelSelector:
          matchExpressions:
            - key: app.kubernetes.io/name
              operator: In
              values: ["n9e"]
        topologyKey: kubernetes.io/hostname
```

> 部署前务必给目标节点打标签，否则硬性亲和性会导致 Pod `Pending`：
>
> ```bash
> kubectl label node <node-name> application/n9e=nightingale
> ```

### 3. 部署

```bash
helm install n9e-center ./n9e-chart \
  --namespace n9e \
  -f n9e-chart/values-production.yaml
```

### 4. 验证部署

```bash
# 查看 Pod 状态
kubectl get pods -n n9e -l app.kubernetes.io/instance=n9e-center

# 查看服务
kubectl get svc -n n9e

# 查看 Gateway 和 HTTPRoute（如果启用了 Gateway API）
kubectl get gateway,httproute -n n9e

# 检查日志
kubectl logs -n n9e -l app.kubernetes.io/instance=n9e-center -f
```


## Gateway API 使用指南

本章节说明如何使用 Gateway API 替代传统 Ingress 暴露 Nightingale 服务。

> **默认关闭**：Chart 默认不启用 Gateway API（`gateway.enabled: false`），默认部署不会创建任何 Gateway/HTTPRoute 资源。仅当需要对外暴露 Web 界面时，按下文说明显式启用。

### 什么是 Gateway API？

Gateway API 是 Kubernetes 官方推出的下一代流量管理 API，作为 Ingress 的现代化替代方案：

- **表达力更强**：原生支持 header 路由、权重分流、流量镜像等高级功能
- **角色分离**：GatewayClass（集群管理员）、Gateway（运维）、Route（开发）三层设计
- **可移植性好**：标准化 API，不依赖厂商特定注解
- **协议支持广**：HTTP/HTTPS/TLS/TCP/UDP/gRPC

官方文档：https://gateway-api.sigs.k8s.io/

### 前置要求

#### 1. 安装 Gateway API CRD

```bash
# 安装标准 CRD（包含 GatewayClass、Gateway、HTTPRoute 等）
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.2.1/standard-install.yaml

# 验证安装
kubectl get crd | grep gateway.networking.k8s.io
```

预期输出：
```
gatewayclasses.gateway.networking.k8s.io
gateways.gateway.networking.k8s.io
httproutes.gateway.networking.k8s.io
referencegrants.gateway.networking.k8s.io
```

#### 2. 安装支持的网关控制器

Gateway API 需要一个控制器来实际处理流量。以下是常见选择：

**方案 1：nginx-gateway-fabric（推荐新手）**

```bash
# 安装
kubectl apply -f https://raw.githubusercontent.com/nginxinc/nginx-gateway-fabric/v1.5.0/deploy/crds.yaml
kubectl apply -f https://raw.githubusercontent.com/nginxinc/nginx-gateway-fabric/v1.5.0/deploy/manifests/nginx-gateway.yaml

# 验证
kubectl get pods -n nginx-gateway
kubectl get gatewayclass
```

**方案 2：Istio（适合微服务网格）**

```bash
istioctl install --set profile=minimal -y
kubectl get gatewayclass
```

**方案 3：Envoy Gateway**

```bash
helm install eg oci://docker.io/envoyproxy/gateway-helm --version v1.2.4 -n envoy-gateway-system --create-namespace
kubectl get gatewayclass
```

**方案 4：Cilium（CNI 集成）**

```bash
cilium install --set kubeProxyReplacement=true
cilium hubble enable
kubectl get gatewayclass
```

更多实现参考：https://gateway-api.sigs.k8s.io/implementations/

#### 3. 确认 GatewayClass 可用

```bash
kubectl get gatewayclass
```

记住输出的 GatewayClass 名称（如 `nginx`、`istio`、`cilium`），后续需要配置到 `gateway.gatewayClassName`。

### 部署场景

#### 场景 1：独立 Gateway（推荐）

Chart 自己创建独立的 Gateway 和 HTTPRoute，适合 n9e 独占一个网关的场景。

**values.yaml 配置：**

```yaml
gateway:
  enabled: true
  create: true
  gatewayClassName: "nginx"  # 改为你的 GatewayClass 名称
  listeners:
    - name: http
      port: 80
      protocol: HTTP
      allowedRoutes:
        namespaces:
          from: Same
    # HTTPS（需预先创建 TLS Secret）
    - name: https
      port: 443
      protocol: HTTPS
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: n9e-tls
      allowedRoutes:
        namespaces:
          from: Same
  httpRoute:
    create: true
    hostnames:
      - n9e.example.com
    rules:
      - matches:
          - path:
              type: PathPrefix
              value: /
```

**安装命令：**

```bash
helm install n9e-center ./n9e-chart -n n9e \
  --set gateway.enabled=true \
  --set gateway.gatewayClassName=nginx \
  --set gateway.httpRoute.hostnames[0]=n9e.example.com
```

**验证：**

```bash
# 查看资源
kubectl get gateway,httproute -n n9e

# 查看 Gateway 状态（等待 Programmed=True）
kubectl describe gateway -n n9e

# 获取 Gateway 外部地址
kubectl get gateway -n n9e -o jsonpath='{.items[0].status.addresses[0].value}'

# 测试访问（假设外部地址为 192.168.1.100）
curl -H "Host: n9e.example.com" http://192.168.1.100
```

#### 场景 2：引用共享 Gateway

大规模集群通常由运维统一管理一个共享 Gateway，应用只需创建 HTTPRoute 路由到这个 Gateway。

**假设共享 Gateway 已存在：**

```bash
# 查看集群已有 Gateway
kubectl get gateway -A

# 示例输出
NAMESPACE         NAME             CLASS   ADDRESS
gateway-system    shared-gateway   nginx   203.0.113.10
```

**values.yaml 配置：**

```yaml
gateway:
  enabled: true
  create: false  # 不创建独立 Gateway
  httpRoute:
    create: true
    # 显式引用外部 Gateway
    parentRefs:
      - name: shared-gateway
        namespace: gateway-system
        sectionName: http  # 引用 Gateway 的 http 监听器
    hostnames:
      - n9e.example.com
    rules:
      - matches:
          - path:
              type: PathPrefix
              value: /
```

**安装命令：**

```bash
helm install n9e-center ./n9e-chart -n n9e \
  --set gateway.enabled=true \
  --set gateway.create=false \
  --set gateway.httpRoute.parentRefs[0].name=shared-gateway \
  --set gateway.httpRoute.parentRefs[0].namespace=gateway-system \
  --set gateway.httpRoute.hostnames[0]=n9e.example.com
```

**注意事项：**

如果 Gateway 和 HTTPRoute 在不同命名空间，需创建 `ReferenceGrant` 授权跨命名空间引用：

```yaml
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-n9e-to-gateway
  namespace: gateway-system
spec:
  from:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: n9e
  to:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: shared-gateway
```

#### 场景 3：多域名/路径规则

**需求：同时支持多个域名和不同路径前缀。**

```yaml
gateway:
  enabled: true
  httpRoute:
    hostnames:
      - n9e.example.com
      - monitor.example.com
    rules:
      # 规则 1：所有路径
      - matches:
          - path:
              type: PathPrefix
              value: /
      # 规则 2：特定路径用权重分流（金丝雀发布）
      - matches:
          - path:
              type: PathPrefix
              value: /api/v1/
        filters:
          - type: RequestHeaderModifier
            requestHeaderModifier:
              add:
                - name: X-Canary
                  value: "true"
        backendRefs:
          - name: n9e-center
            port: 17000
            weight: 90
          - name: n9e-center-canary
            port: 17000
            weight: 10
```

#### 场景 4：HTTPS + 自动证书

结合 cert-manager 自动签发 TLS 证书。

**前置：安装 cert-manager**

```bash
kubectl apply -f https://github.com/cert-manager/cert-manager/releases/download/v1.16.2/cert-manager.yaml
```

**创建 ClusterIssuer（Let's Encrypt）：**

```yaml
apiVersion: cert-manager.io/v1
kind: ClusterIssuer
metadata:
  name: letsencrypt-prod
spec:
  acme:
    server: https://acme-v02.api.letsencrypt.org/directory
    email: admin@example.com
    privateKeySecretRef:
      name: letsencrypt-prod-key
    solvers:
      - http01:
          gatewayHTTPRoute:
            parentRefs:
              - name: n9e-gateway
                namespace: n9e
                kind: Gateway
```

**创建 Certificate：**

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: n9e-tls
  namespace: n9e
spec:
  secretName: n9e-tls
  issuerRef:
    name: letsencrypt-prod
    kind: ClusterIssuer
  dnsNames:
    - n9e.example.com
```

**values.yaml 配置 HTTPS 监听器：**

```yaml
gateway:
  enabled: true
  listeners:
    - name: https
      port: 443
      protocol: HTTPS
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: n9e-tls
      allowedRoutes:
        namespaces:
          from: Same
  httpRoute:
    hostnames:
      - n9e.example.com
```

#### 场景 5：Cilium 网络模式（亲和性/调度说明）

如果集群使用 Cilium 作为 CNI 和 Gateway API 控制器，调度模型与其他控制器**明显不同**，需要特别理解。

**Cilium 的数据面架构**

- 创建 Gateway 后，cilium-operator 会自动生成一个 `LoadBalancer` 类型的 Service 和 `CiliumEnvoyConfig`
- **实际处理流量的 Envoy 代理是 Cilium 的 DaemonSet**（`cilium-envoy`，或嵌入在 `cilium-agent` 中），**每个节点运行一个实例**
- 因为是 DaemonSet，天然分布在所有节点上，传统的「把副本打散」的 `podAntiAffinity` **不适用**

**亲和性该在哪里配？**

| 需求 | 配置位置 | 说明 |
|------|---------|------|
| 控制 Envoy 代理跑在哪些节点 | **Cilium 自身的 Helm values** | 不在本 chart 内 |
| Gateway 对外 IP / LB 池选择 | 本 chart `gateway.addresses` / `gateway.infrastructure.annotations` | 标准字段 |

**⚠️ 重要：标准 Gateway 资源没有 `affinity` 字段。** Envoy 数据面的调度亲和性需要在**安装 Cilium 时**配置：

```yaml
# 这是 Cilium 的 values.yaml（helm install cilium 时使用），不是 n9e-chart
envoy:
  # 是否启用独立的 cilium-envoy DaemonSet
  enabled: true
  # Envoy DaemonSet 的节点选择器
  nodeSelector:
    ingress-ready: "true"
  # Envoy DaemonSet 的亲和性
  affinity:
    nodeAffinity:
      requiredDuringSchedulingIgnoredDuringExecution:
        nodeSelectorTerms:
          - matchExpressions:
              - key: node-role.kubernetes.io/gateway
                operator: Exists
  # 污点容忍
  tolerations:
    - key: node-role.kubernetes.io/gateway
      operator: Exists
```

修改后需重启 Cilium：`kubectl rollout restart daemonset cilium-envoy -n kube-system`

**本 chart 中 Cilium 相关配置**

```yaml
gateway:
  enabled: true
  gatewayClassName: "cilium"
  # 指定对外 IP（需先配置 CiliumLoadBalancerIPPool）
  addresses:
    - type: IPAddress
      value: "192.168.1.100"
  infrastructure:
    # 传递到自动生成的 LoadBalancer Service 的注解
    annotations:
      io.cilium/lb-ipam-ips: "192.168.1.100"
  httpRoute:
    hostnames:
      - n9e.example.com
```

**前置：配置 Cilium LB-IPAM**

使用 `gateway.addresses` 前，需先创建 IP 池：

```yaml
apiVersion: cilium.io/v2alpha1
kind: CiliumLoadBalancerIPPool
metadata:
  name: n9e-pool
spec:
  blocks:
    - cidr: "192.168.1.0/24"
```

验证 Gateway 是否分配到 IP：

```bash
kubectl get gateway -n n9e
# ADDRESS 列应显示分配的 IP

kubectl get svc -n n9e -l io.cilium/gateway-name
# 查看自动生成的 LoadBalancer Service
```

### Gateway API 故障排查

#### 1. Gateway 状态为 Pending

```bash
kubectl describe gateway -n n9e
```

**常见原因：**
- GatewayClass 不存在或控制器未运行
- 云厂商 LoadBalancer 配额不足
- 监听器配置错误

**解决：**
```bash
# 检查 GatewayClass
kubectl get gatewayclass

# 检查控制器 Pod
kubectl get pods -n nginx-gateway  # 或对应命名空间

# 查看控制器日志
kubectl logs -n nginx-gateway <pod-name>
```

#### 2. HTTPRoute 不生效

```bash
kubectl describe httproute -n n9e
```

**常见原因：**
- `parentRefs` 引用的 Gateway 不存在
- 跨命名空间引用缺少 `ReferenceGrant`
- `hostnames` 与实际请求 Host 不匹配
- `backendRefs` 的 Service 不存在

**解决：**
```bash
# 检查 parentRefs 是否正确
kubectl get gateway <gateway-name> -n <namespace>

# 检查后端 Service
kubectl get svc -n n9e

# 测试路由（使用 Gateway IP）
kubectl get gateway -n n9e -o jsonpath='{.items[0].status.addresses[0].value}'
curl -v -H "Host: n9e.example.com" http://<gateway-ip>
```

#### 3. 跨命名空间引用失败

报错：`invalid cross namespace reference from n9e/n9e-center to gateway-system/shared-gateway`

**解决：创建 ReferenceGrant**

```bash
cat <<EOF | kubectl apply -f -
apiVersion: gateway.networking.k8s.io/v1beta1
kind: ReferenceGrant
metadata:
  name: allow-n9e-routes
  namespace: gateway-system
spec:
  from:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      namespace: n9e
  to:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: shared-gateway
EOF
```

### Gateway API 与 Ingress 对比

| 维度 | Ingress | Gateway API |
|------|---------|-------------|
| 标准化 | 核心功能标准，高级功能依赖注解 | 完全标准化 API |
| 表达力 | 基础 HTTP 路由 | HTTP/TCP/UDP/gRPC + 高级路由 |
| 角色分离 | 无 | GatewayClass/Gateway/Route 三层 |
| 多协议 | 仅 HTTP/HTTPS | 支持 TCP、UDP、TLS passthrough |
| 可移植性 | 差（注解各家不同） | 好（标准字段） |
| 成熟度 | 稳定（v1，2021 GA） | GA（v1.0，2023 GA） |
| 生态 | 广泛（nginx-ingress、traefik 等） | 快速增长（Istio、Cilium、Envoy Gateway） |

### Gateway API 迁移建议

如果你当前使用 Ingress，可以平滑迁移到 Gateway API：

1. **保持 Ingress 运行**，先在测试环境部署 Gateway API
2. **验证功能完整**：确认所有路由、HTTPS、高级功能都正常
3. **灰度切流量**：DNS 或 LoadBalancer 权重逐步切换
4. **完全迁移后删除 Ingress**

大部分控制器都支持 Ingress 和 Gateway API 并存，可以逐步迁移应用。


## 告警通知脚本配置

Nightingale 通过 stdin 管道调用 Python 通知脚本发送告警。本 Chart 提供 5 个内置脚本，以「零改 Chart 模板」的方式挂载进 n9e 主容器运行。

| 脚本                            | 用途                                            |
| ------------------------------- | ----------------------------------------------- |
| `send_nightingale_email.py`     | 邮件通知（含 Grafana 图表渲染）                 |
| `send_nightingale_feishu.py`    | 飞书群机器人 webhook 通知                       |
| `send_nightingale_im.py`        | 飞书应用消息卡片（含告警屏蔽、AI 分析、协同群） |
| `send_nightingale_im_urgent.py` | 飞书电话加急通知                                |
| `send_nightingale_sms.py`       | 短信通知                                        |

### 架构说明

这些脚本必须在 n9e 主容器内可执行（n9e 进程 fork/exec 子进程并通过 stdin 喂入告警 JSON），因此不能用独立 sidecar 容器运行。方案采用：

- **脚本分发**：ConfigMap `n9e-scripts` 挂载到 `/opt/n9e-scripts`
- **依赖安装**：initContainer 用 `pip install --target=/deps requests`，主容器通过 `PYTHONPATH=/deps` 引用
- **敏感配置**：Secret `n9e-scripts-secret` 通过环境变量注入
- **日志目录**：emptyDir 挂载到 `/data/n9e/alerts`（生产建议改用 PVC）

> 前提：n9e 主镜像已内置 `python3`（`iflyelf/nightingale:latest-aggregation` 已确认包含）。脚本仅依赖 `requests`，其余均为 Python 标准库。

### 配置优先级

脚本配置支持三级覆盖，优先级从高到低：

```
payload.params（n9e 告警渠道下发）> 环境变量（Secret 注入）> 脚本内默认值
```

即告警渠道通过 params 下发的配置（如 `callback_server_url`、`feishu_domain`、`grafana_token` 等）优先级最高，可覆盖容器环境变量。

### 部署步骤

**1. 修改敏感配置**

编辑 `manifests/n9e-scripts-secret.yaml`，填入实际的邮箱密码、Grafana Token、飞书域名、回调服务地址等。集群内回调地址建议使用 Service 名称：

```yaml
CALLBACK_SERVER_URL: "http://n9e-gateway.n9e.svc.cluster.local:5000"
```

**2. 如需修改脚本，重新生成 ConfigMap**

脚本源文件位于 `scripts/` 目录。修改后运行生成命令：

```bash
python3 gen-scripts-configmap.py
```

**3. 应用 ConfigMap 和 Secret**

```bash
kubectl apply -f manifests/n9e-scripts-configmap.yaml
kubectl apply -f manifests/n9e-scripts-secret.yaml
```

**4. 部署 Chart（附加 values 片段）**

`-f` 的 values 文件路径需带上 chart 目录前缀（相对当前执行目录）：

```bash
# 在 n9e-chart 的上级目录执行
helm upgrade --install n9e-center ./n9e-chart -n n9e \
  -f ./n9e-chart/values.yaml \
  -f ./n9e-chart/values-notify-scripts.yaml
```

或先进入 chart 目录，用 `.` 指向当前目录：

```bash
cd n9e-chart
helm upgrade --install n9e-center . -n n9e \
  -f values.yaml \
  -f values-notify-scripts.yaml
```

**5. 在 Nightingale 后台配置通知媒介**

脚本路径填写容器内绝对路径：

```
/opt/n9e-scripts/send_nightingale_email.py
/opt/n9e-scripts/send_nightingale_feishu.py
/opt/n9e-scripts/send_nightingale_im.py
/opt/n9e-scripts/send_nightingale_im_urgent.py
/opt/n9e-scripts/send_nightingale_sms.py
```

### 支持的环境变量

| 环境变量                                                  | 说明                      | 涉及脚本                |
| --------------------------------------------------------- | ------------------------- | ----------------------- |
| `LOG_DIR`                                                 | 日志目录                  | 全部                    |
| `IMG_DIR`                                                 | 临时图片目录              | im                      |
| `EMAIL_HOST` / `EMAIL_PORT` / `EMAIL_USER` / `EMAIL_FROM` | SMTP 服务器配置           | email                   |
| `MAIL_PASS`                                               | 邮箱密码                  | email                   |
| `GRAFANA_BASE_URL` / `GRAFANA_TOKEN`                      | Grafana 地址和 Token      | email / im / im_urgent  |
| `GRAFANA_DASHBOARD_UID` / `GRAFANA_DATASOURCE_UID`        | Grafana 仪表盘/数据源 UID | email / im / im_urgent  |
| `FEISHU_DOMAIN`                                           | 飞书开放平台域名          | feishu / im / im_urgent |
| `CALLBACK_SERVER_URL`                                     | 回调服务地址              | feishu / im / im_urgent |
| `DEFAULT_DOMAIN_URL`                                      | N9E 告警详情地址          | feishu / im / im_urgent |
| `SMS_GATEWAY_URL` / `SMS_TEMPLATE_ID` / `SMS_TYPE`        | 短信网关配置              | sms                     |

#### Grafana 配置说明

**获取数据源 UID (`GRAFANA_DATASOURCE_UID`)**

脚本需要指定 Prometheus/VictoriaMetrics 数据源 UID 来渲染告警图表。通过 Grafana API 查询：

```bash
curl -s -H "Authorization: Bearer <GRAFANA_TOKEN>" \
  <GRAFANA_BASE_URL>/api/datasources | jq '.[] | {name, uid, type, url}'
```

示例输出：

```json
{
  "name": "VictoriaMetrics",
  "uid": "P4169E866C3094E38",
  "type": "prometheus",
  "url": "http://vm-vmauth.vm.svc.cluster.local:8427/select/0/prometheus"
}
```

选择目标数据源的 `uid`，填入 Secret 的 `GRAFANA_DATASOURCE_UID`。

**关于 Dashboard UID (`GRAFANA_DASHBOARD_UID`)**

此参数为**历史遗留配置**，脚本实际运行时**不依赖**预先存在的仪表盘。渲染流程如下：

1. 脚本根据告警的 PromQL 动态创建临时 Dashboard（UID 为 `temp_alert_<时间戳>`）
2. 调用 Grafana Render API 生成图片
3. 渲染完成后立即删除临时 Dashboard

因此 `GRAFANA_DASHBOARD_UID` **无需修改**，保持默认值即可。脚本只需确保：

- `GRAFANA_TOKEN` 有创建/删除 Dashboard 的权限（Editor 或 Admin 角色）
- `GRAFANA_DATASOURCE_UID` 指向有效的数据源

### 告警脚本故障排查

**脚本执行提示 `timeout and killed process`（最常见）**

n9e 的通知脚本超时**不在配置文件或 Helm values 中**，而是在 Web 界面的「通知媒介（Script 类型）」设置里，以毫秒为单位，**默认仅 5000ms（5 秒）**。`send_nightingale_im.py` 需要串行完成「获取飞书 token → 创建临时 Dashboard → Grafana 渲染图表 → 上传图片 → 发送卡片」，正常耗时就会超过 5 秒，因此被强制 kill。

**解决方法**（在 n9e Web 界面操作，配置存于数据库，无需改 Chart）：

1. 进入 `告警管理 → 通知媒介`，找到对应的 Script 媒介
2. 将「超时时间」从 `5000` 调大到 `30000`（即 30 秒），保存
3. 若仍偶发超时，可继续调大，或在 Secret 中压缩脚本内部超时（见下方环境变量）

脚本内部各阶段超时**全部环境变量化**，可通过 Secret 微调（默认值已优化）：

| 环境变量 | 默认值 | 说明 | 涉及脚本 |
|---------|--------|------|---------|
| `GRAFANA_RENDER_TIMEOUT` | `15`（秒） | Grafana 图片渲染 / 飞书图片上传 | email / im / im_urgent |
| `GRAFANA_API_TIMEOUT` | `8`（秒） | Grafana 仪表盘增删改查 | email / im / im_urgent |
| `FEISHU_API_TIMEOUT` | `6`（秒） | 飞书 token / 发送消息 / 加急 | im / im_urgent / feishu |
| `FEISHU_WEBHOOK_TIMEOUT` | `8`（秒） | 飞书群机器人 webhook 推送 | feishu |
| `CALLBACK_API_TIMEOUT` | `6`（秒） | 回调服务注册（屏蔽/AI分析/协同群） | im / im_urgent / feishu |
| `SMTP_TIMEOUT` | `10`（秒） | SMTP 邮件发送 | email |
| `SMS_API_TIMEOUT` | `10`（秒） | 短信网关请求 | sms |

> 建议：n9e Script 媒介超时（30s）应大于脚本内部各阶段超时之和，给脚本留出完整执行时间。

**脚本报 `ModuleNotFoundError: No module named 'requests'`**

- 检查 initContainer 是否成功执行：`kubectl logs <pod> -c install-python-deps -n n9e`
- 确认主容器 `PYTHONPATH=/deps` 已设置

**脚本无日志输出**

- 确认 `/data/n9e/alerts` 目录可写（emptyDir 或 PVC 已挂载）
- 查看 n9e 主进程日志中的脚本执行错误

**Grafana 图片渲染失败**

- 确认 Pod 网络可访问 Grafana，且 Grafana 已安装 image-renderer 插件
- 检查 `GRAFANA_TOKEN` 是否有效，且具备创建/删除 Dashboard 的权限（Editor/Admin）
- 检查 `GRAFANA_DATASOURCE_UID` 是否为当前 Grafana 实例中真实存在的 UID（更换 Grafana 实例后 UID 会变化，需用上文的 `/api/datasources` 接口重新获取）
- 图表空白多为数据源 UID 失效或 PromQL 在该数据源无数据所致


## 边缘告警引擎部署

边缘告警引擎用于多地域、边缘机房部署，连接到中心端 n9e 拉取规则并在本地执行告警评估。

### 1. 确认中心端已开启 APIForService

确保中心端配置中 `config.http.apiForService.enable=true`，可通过 helm upgrade 修改：

```bash
helm upgrade n9e-center ./n9e-chart -n n9e \
  --set config.http.apiForService.enable=true \
  --reuse-values
```

### 2. 部署边缘节点

```bash
# 使用预置的边缘配置
helm install n9e-edge ./n9e-chart \
  -f n9e-chart/values-edge.yaml \
  --namespace n9e \
  --set config.edge.centerApi.addrs[0]="http://n9e-center.n9e.svc.cluster.local:17000"
```

### 3. 多边缘节点部署

每个边缘节点使用不同的 engineName：

```bash
# 边缘节点 1
helm install n9e-edge1 ./n9e-chart -f n9e-chart/values-edge.yaml -n n9e \
  --set config.alert.heartbeat.engineName=edge1

# 边缘节点 2
helm install n9e-edge2 ./n9e-chart -f n9e-chart/values-edge.yaml -n n9e \
  --set config.alert.heartbeat.engineName=edge2 \
  --set externalRedis.address="redis-edge2.redis.svc.cluster.local:6379"
```

**注意**：  
1. 边缘节点需要**独立部署的 Redis**（不能与中心端共用）  
2. 中心端必须开启 `config.http.apiForService.enable=true`  
3. `config.edge.centerApi.basicAuthUser` 和 `basicAuthPass` 需与中心端 `config.http.apiForService.basicAuth` 一致

## 常用运维操作

### 升级

```bash
# 查看当前版本
helm list -n n9e

# 升级（修改配置后）
helm upgrade n9e-center ./n9e-chart -n n9e -f values-production.yaml

# 查看升级历史
helm history n9e-center -n n9e
```

### 回滚

```bash
# 回滚到上一版本
helm rollback n9e-center -n n9e

# 回滚到指定版本
helm rollback n9e-center 2 -n n9e
```

### 扩缩容

```bash
# 手动扩容
kubectl scale deployment -n n9e n9e-center --replicas=5

# 或通过 helm upgrade
helm upgrade n9e-center ./n9e-chart -n n9e --set replicaCount=5 --reuse-values
```

### 卸载

```bash
# 卸载（保留 PVC）
helm uninstall n9e-center -n n9e

# 完全清理（包括 PVC）
helm uninstall n9e-center -n n9e
kubectl delete pvc -n n9e -l app.kubernetes.io/instance=n9e-center
```

## 监控与告警

### 暴露 Metrics

n9e 自身监控指标默认已启用（`config.http.exposeMetrics=true`），暴露在 `/metrics` 端点。

### 配置 Prometheus 抓取

```yaml
# ServiceMonitor（需安装 Prometheus Operator）
metrics:
  enabled: true
  serviceMonitor:
    enabled: true
    interval: 30s
```

### 关键指标

- `n9e_alert_rule_count` - 告警规则数量
- `n9e_alert_event_total` - 告警事件总数
- `n9e_pushgw_samples_total` - 接收的时序样本数
- `n9e_http_request_duration_seconds` - HTTP 请求延迟

## 故障排查

### Pod 启动失败

```bash
# 查看事件
kubectl describe pod -n n9e <pod-name>

# 查看日志
kubectl logs -n n9e <pod-name>

# 检查配置
kubectl get secret -n n9e n9e-center -o jsonpath='{.data.config\.toml}' | base64 -d
```

### 无法连接数据库

#### 常见错误 1: `connection refused`

**现象**: `dial tcp <IP>:3306: connect: connection refused`

**原因**: Service 的 Endpoints 为空,或端口配置错误

**排查步骤**:

```bash
# 1. 检查 MySQL Pod 状态
kubectl get pods -n mysql

# 2. 检查 Service Endpoints (关键!)
kubectl get svc -n mysql mysql-cluster-router
kubectl get endpoints -n mysql mysql-cluster-router
# 如果 ENDPOINTS 列为 <none>,说明没有就绪的 Pod

# 3. 检查 Pod 就绪状态
kubectl describe pod -n mysql <mysql-pod-name>

# 4. 查看 MySQL 日志
kubectl logs -n mysql <mysql-pod-name>
```

**解决方案**:

- 等待 MySQL Pod 完全启动 (初始化可能需要几分钟)
- 如果 Pod CrashLoop,检查存储/资源配置
- 确认 Service selector 与 Pod labels 匹配

#### 常见错误 2: `Access denied for user 'root'@'<IP>'`

**现象**: `ERROR 1045 (28000): Access denied for user 'root'@'172.16.x.x' (using password: YES)`

**原因**:

1. 密码不正确 (大小写敏感)
2. root 用户未开启远程访问权限
3. 通过 Router 连接时,MySQL 看到的来源 IP 不是 localhost

**排查步骤**:

```bash
# 1. 获取正确的密码 (Percona Operator 自动生成)
kubectl get secret -n mysql mysql-cluster-secrets -o jsonpath='{.data.root}' | base64 -d
echo

# 2. 查看 Percona 推荐的连接配置
kubectl get secret -n mysql mysql-cluster-psuser-root -o yaml

# 3. 测试直连 primary
kubectl run mysql-client --rm -it --image=mysql:8.0 --restart=Never -n mysql -- \
  mysql -h mysql-cluster-mysql-primary.mysql.svc.cluster.local -P 3306 -uroot -p'<密码>' -e "SELECT 1"

# 4. 测试 Router 读写端口 (6446)
kubectl run mysql-client --rm -it --image=mysql:8.0 --restart=Never -n mysql -- \
  mysql -h mysql-cluster-router.mysql.svc.cluster.local -P 6446 -uroot -p'<密码>' -e "SELECT 1"
```

**解决方案**:

```yaml
# 方案1: 使用 primary 直连 (推荐)
externalDatabase:
  host: "mysql-cluster-mysql-primary.mysql.svc.cluster.local"
  port: 3306
  password: "从 Secret 获取的正确密码"

# 方案2: 使用 Router 的读写端口
externalDatabase:
  host: "mysql-cluster-router.mysql.svc.cluster.local"
  port: 6446  # 读写端口,不是 3306
  password: "从 Secret 获取的正确密码"
```

#### 常见错误 3: `The MySQL server is running with the --super-read-only option`

**现象**: 无法执行 CREATE/INSERT 等写操作

**原因**: 连接到了只读副本 (replica) 或 Router 的只读端口

**解决方案**:

- 确保使用 `mysql-cluster-mysql-primary` (primary) 或 Router 的 `6446` 端口
- 避免直接连 `mysql-cluster-mysql-0/1/2` (可能是副本)
- Router 的 `6447` 是只读端口,`6446` 才是读写端口

#### 测试数据库连通性

```bash
# 从 n9e Pod 内部测试
kubectl exec -n n9e <n9e-pod-name> -- \
  nc -zv mysql-cluster-mysql-primary.mysql.svc.cluster.local 3306

# 检查网络策略
kubectl get networkpolicies -n n9e
kubectl get networkpolicies -n mysql
```

### HPA 不生效

```bash
# 检查 Metrics Server
kubectl get deployment metrics-server -n kube-system

# 查看 HPA 状态
kubectl get hpa -n n9e

# 查看详细信息
kubectl describe hpa -n n9e n9e-center
```

### 常见问题

**1. Pod 无法启动，报 `bind: address already in use`**

- 原因：开启了 `hostNetwork: true` 且同节点有端口冲突
- 解决：关闭 `hostNetwork`（默认已关闭）或确保节点反亲和生效

**2. 无法连接 Redis**

- 检查 `externalRedis.address` 是否正确
- 确认密码是否匹配
- 验证网络策略是否允许 Pod 访问外部服务

**3. HPA 无法扩容**

- 确认 Metrics Server 已安装：`kubectl get deployment metrics-server -n kube-system`
- 检查 Pod 是否配置了 `resources.requests`

**4. 边缘模式启动失败**

- 确认镜像包含 `n9e-edge` 二进制：`docker run --rm <image> ls /opt/nightingale/ | grep edge`
- 检查 `config.edge.centerApi.addrs` 是否能从 Pod 内访问
- 验证中心端 `config.http.apiForService.enable=true` 已开启
- 确认 BasicAuth 用户名密码与中心端一致

## 性能调优

### 数据库连接池

```yaml
externalDatabase:
  maxOpenConns: 200  # 根据并发量调整
  maxIdleConns: 100
  maxLifetime: 7200
```

### Redis 超时

```yaml
externalRedis:
  dialTimeoutMills: 5000
  readTimeoutMills: 3000
  writeTimeoutMills: 3000
```

### 资源限制建议

| 场景                    | CPU  | 内存 |
| ----------------------- | ---- | ---- |
| 小规模（< 1000 台机器） | 2 核 | 4Gi  |
| 中规模（1000-5000 台）  | 4 核 | 8Gi  |
| 大规模（> 5000 台）     | 8 核 | 16Gi |

## 安全加固

### 启用 BasicAuth（Agent 接入）

```yaml
config:
  http:
    apiForAgent:
      basicAuth:
        agent001: "your-hashed-password"
```

### 启用 HTTPS

```yaml
config:
  http:
    certFile: "/etc/n9e/tls/tls.crt"
    keyFile: "/etc/n9e/tls/tls.key"

extraVolumes:
  - name: tls
    secret:
      secretName: n9e-tls-cert

extraVolumeMounts:
  - name: tls
    mountPath: /etc/n9e/tls
    readOnly: true
```

### 网络策略

```yaml
networkPolicy:
  enabled: true
  ingress:
    - from:
      - namespaceSelector:
          matchLabels:
            name: monitoring
      ports:
      - protocol: TCP
        port: 17000
```


## 配置参考

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

> **关于亲和性**：标准 Gateway 资源没有 `affinity` 字段。使用 Cilium 时，处理流量的是 `cilium-envoy` DaemonSet（每节点一个），其调度亲和性需在**安装 Cilium 的 Helm values** 中配置（`envoy.affinity`/`nodeSelector`/`tolerations`），不在本 chart 内。详见 [Gateway API 使用指南](#场景-5cilium-网络模式亲和性调度说明)。

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

### Nightingale 官方文档

- [Nightingale 官方文档](https://flashcat.cloud/docs/content/flashcat-monitor/nightingale-v9/)
- [Nightingale GitHub](https://github.com/ccfos/nightingale)
- [配置文件详解](https://flashcat.cloud/docs/content/flashcat-monitor/nightingale-v9/install/configuration/)
- [边缘机房部署](https://flashcat.cloud/docs/content/flashcat-monitor/nightingale-v9/install/edge/)

### Gateway API 资源

- [Gateway API 官方文档](https://gateway-api.sigs.k8s.io/)
- [Gateway API 实现列表](https://gateway-api.sigs.k8s.io/implementations/)
- [从 Ingress 迁移到 Gateway API](https://gateway-api.sigs.k8s.io/guides/migrating-from-ingress/)
- [nginx-gateway-fabric](https://github.com/nginxinc/nginx-gateway-fabric)
- [Istio Gateway API](https://istio.io/latest/docs/tasks/traffic-management/ingress/gateway-api/)

## License

本 Helm Chart 继承 Nightingale 的开源协议：[Apache License 2.0](https://github.com/ccfos/nightingale/blob/main/LICENSE)

## 维护者

- iflyelf

如有问题或建议，欢迎提交 Issue。

